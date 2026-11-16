"""Offline preadmission failures; real SQLite/tools, no real model calls."""

import asyncio
import json

import pytest
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.domain import ForgeEvent
from forge_core.independent_review import (
    IndependentReviewService,
    _hash,
    _packet,
    _review_input_reservation,
    _review_request_shape,
    _review_reservation,
    _ReviewBoundaryError,
)
from forge_core.provider_pool import ProviderPool
from forge_core.runtime import ModelRequest
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_model_usage import reserve_worker_model_call
from forge_core.worker_runtime import WorkerManager


def pending(tmp_path):
    store, manager, model, run = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery())],
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    return store, manager, model, run, IndependentReviewService(manager)


def observation(store, manager, run_id):
    with store.connection() as c:
        c.execute("BEGIN")
        row = dict(c.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone())
        packet = _packet(store, c, row)
        provider = manager.providers.get(row["provider_id"])
        messages, options, formatting = _review_request_shape(c, row, provider, packet)
    request = ModelRequest(
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        messages=messages,
        max_output_tokens=min(1024, row["output_limit"], row["model_max_output_tokens"]),
        max_retries=0,
        **options,
    )
    native = _review_input_reservation(provider, request, formatting)
    return row, packet, provider, request, formatting, native


def set_remaining(store, run_id, remaining):
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET token_spent=token_limit-? WHERE id=?",
            (remaining, run_id),
        )


def accounting(store):
    tables = (
        "worker_model_reservations",
        "model_budget_attempts",
        "worker_budget_ledger",
        "worker_cost_ledger",
        "action_intents",
        "action_receipts",
        "worker_tool_calls",
    )
    with store.connection() as c:
        return {name: [dict(r) for r in c.execute(f"SELECT * FROM {name}")] for name in tables}


def test_unaffordable_tick_atomically_blocks_without_claim_call_or_judgment(tmp_path, monkeypatch):
    store, manager, model, run, reviewer = pending(tmp_path)
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    *_, request, _formatting, native = observation(store, manager, run["id"])
    set_remaining(store, run["id"], native + request.max_output_tokens - 1)
    before = manager.get(run["id"])
    ledgers = accounting(store)
    calls = len(model.requests)
    assert asyncio.run(reviewer.tick())
    after = manager.get(run["id"])
    assert after == {**before, "state": "blocked"}
    assert len(model.requests) == calls and accounting(store) == ledgers
    with store.connection() as c:
        assert c.execute("SELECT status FROM workers").fetchone()[0] == "blocked"
        assert c.execute("SELECT status FROM task_nodes").fetchone()[0] == "blocked"
        assert not c.execute(
            "SELECT 1 FROM events WHERE event_type IN ('review.started','review.result')"
        ).fetchone()
        events = c.execute(
            "SELECT * FROM events WHERE event_type IN ('review.blocked','worker.blocked')"
        ).fetchall()
        assert {e["event_type"] for e in events} == {"review.blocked", "worker.blocked"}
        assert len(events) == 2
        for event in events:
            data = json.loads(event["payload"])
            assert data["reason_code"] == "review_token_budget"
            assert data["native_input_reservation"] == native
            assert data["minimum_total_reservation"] == native + request.max_output_tokens
            assert data["remaining_cumulative_tokens"] == data["minimum_total_reservation"] - 1
            assert data["generation"] == before["generation"]
            assert "verdict" not in data and "call_id" not in data
            assert c.execute("SELECT 1 FROM outbox WHERE event_id=?", (event["id"],)).fetchone()
    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    assert not asyncio.run(IndependentReviewService(resumed).tick())
    assert len(model.requests) == calls and accounting(reopened) == ledgers


@pytest.mark.parametrize("headroom", [0, 1])
def test_exact_minimum_fits_and_cannot_be_labeled_unaffordable(tmp_path, monkeypatch, headroom):
    store, manager, model, run, reviewer = pending(tmp_path)
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    *_, request, _formatting, native = observation(store, manager, run["id"])
    set_remaining(store, run["id"], native + request.max_output_tokens + headroom)
    row, packet, provider, request, formatting, _ = observation(store, manager, run["id"])
    assert _review_reservation(provider, request, row, formatting) <= (
        row["token_limit"] - row["token_spent"]
    )
    assert not reviewer._block_unaffordable_review(row, packet, provider)
    assert manager.get(run["id"])["state"] == "review_pending" and len(model.requests) == 2


@pytest.mark.parametrize(
    "guard",
    [
        "disabled",
        "remaining",
        "generation",
        "plan",
        "team",
        "packet",
        "new_result",
        "lease",
        "provider",
        "mode",
        "worker_status",
        "node_status",
        "unclosed_claim",
        "incomplete_claim",
        "judgment",
        "pending_tool",
        "unknown_tool",
        "model_reservation",
        "gateway_reserved",
        "gateway_unknown",
        "gateway_rejected",
        "gateway_settled",
        "intent",
        "awaiting_authorization",
        "quest_paused",
        "running_core_tool",
    ],
)
def test_stale_authority_or_unsettled_work_never_converges(tmp_path, monkeypatch, guard):
    store, manager, model, run, reviewer = pending(tmp_path)
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    set_remaining(store, run["id"], 1)
    row, packet, provider, *_ = observation(store, manager, run["id"])
    if guard == "disabled":
        monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    if guard == "provider":
        manager.providers = ProviderPool({"protocol": type(model)([])})
    if guard == "mode":
        provider.execution_mode = "live"
    with store.unit_of_work() as work:
        c = work.connection
        if guard == "remaining":
            c.execute("UPDATE worker_runs SET token_spent=0")
        elif guard == "generation":
            c.execute("UPDATE worker_runs SET generation=generation+1")
        elif guard in {"awaiting_authorization", "quest_paused"}:
            c.execute(f"UPDATE worker_runs SET {guard}=1")
        elif guard == "plan":
            c.execute("UPDATE quests SET plan_revision=plan_revision+1")
        elif guard == "team":
            c.execute("UPDATE team_proposals SET status='revoked'")
        elif guard == "packet":
            packet["delivery"]["summary"] = "tampered snapshot"
        elif guard == "new_result":
            source = c.execute(
                "SELECT payload FROM events WHERE id=?", (packet["result_event_id"],)
            ).fetchone()
            work.append_event(
                ForgeEvent(
                    event_type="worker.result",
                    quest_id=row["quest_id"],
                    worker_id=row["worker_id"],
                    payload=json.loads(source[0]),
                )
            )
        elif guard == "lease":
            c.execute("UPDATE worker_runs SET lease_token='owned',lease_until=9999999999")
        elif guard == "worker_status":
            c.execute("UPDATE workers SET status='running'")
        elif guard == "node_status":
            c.execute("UPDATE task_nodes SET status='running'")
        elif guard in {"unclosed_claim", "incomplete_claim", "judgment"}:
            work.append_event(
                ForgeEvent(
                    event_type="review.result" if guard == "judgment" else "review.started",
                    quest_id=row["quest_id"],
                    worker_id=row["worker_id"],
                    payload={
                        "run_id": row["id"],
                        "result_event_id": packet["result_event_id"],
                        **(
                            {"call_id": "uncommitted", "lease_until": 0}
                            if guard != "incomplete_claim"
                            else {}
                        ),
                    },
                )
            )
        elif guard in {"pending_tool", "unknown_tool", "running_core_tool"}:
            c.execute(
                "INSERT INTO worker_tool_calls VALUES(?,?,?,?,?,?,NULL,NULL)",
                (
                    row["id"],
                    "new-tool",
                    "old-call",
                    "recall_object",
                    "{}",
                    {
                        "pending_tool": "pending",
                        "unknown_tool": "uncertain",
                        "running_core_tool": "running",
                    }[guard],
                ),
            )
        elif guard == "model_reservation":
            reserve_worker_model_call(
                c, {**row, "lease_token": "old-owner"}, "reserved", 10, 10, None, []
            )
        elif guard.startswith("gateway_"):
            c.execute(
                "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,"
                "quest_id,worker_run_id,provider_id,model_id,input_reserved,output_reserved,"
                "state,created_at) VALUES('new','new','worker',?,?,?,?,?,10,10,?,0)",
                (
                    store.workspace.id,
                    row["quest_id"],
                    row["id"],
                    row["provider_id"],
                    row["model_id"],
                    guard.removeprefix("gateway_"),
                ),
            )
        elif guard == "intent":
            c.execute("UPDATE action_intents SET state='uncertain'")
    if guard in {"awaiting_authorization", "quest_paused", "lease"}:
        row, packet, provider, *_ = observation(store, manager, run["id"])
    before = manager.get(run["id"])
    ledgers = accounting(store)
    assert not reviewer._block_unaffordable_review(row, packet, provider)
    assert manager.get(run["id"]) == before and accounting(store) == ledgers
    assert len(model.requests) == 2
    assert not [e for e in store.list_events(limit=1000) if e["event_type"] == "worker.blocked"]


def test_typed_failure_is_specific_and_no_ready_release_occurs(tmp_path, monkeypatch):
    store, manager, _model, run, reviewer = pending(tmp_path)
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    set_remaining(store, run["id"], 1)
    row, packet, provider, request, formatting, _ = observation(store, manager, run["id"])
    with pytest.raises(_ReviewBoundaryError) as caught:
        _review_reservation(provider, request, row, formatting)
    assert caught.value.code == "review_token_budget"
    reviewer._ready = lambda *_: pytest.fail("Convergence must not release or refund claims")
    assert reviewer._block_unaffordable_review(row, packet, provider)
    with store.connection() as c:
        data = json.loads(
            c.execute("SELECT payload FROM events WHERE event_type='review.blocked'").fetchone()[0]
        )
        assert data["packet_sha256"] == _hash(packet)


def test_real_gateway_payload_measurement_without_a_review_http_call(tmp_path, monkeypatch):
    store, manager, _model, run = prepared(tmp_path, [])

    def responder(_body, ordinal):
        if ordinal == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "read",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "work/input.txt"}),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": json.dumps(delivery())}

    with offline_loopback_gateway(store, responder) as (gateway, requests):
        manager.providers = ProviderPool({"protocol": gateway})
        with store.unit_of_work() as work:
            work.connection.execute("UPDATE worker_runs SET execution_mode='live'")
        assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
        monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
        set_remaining(store, run["id"], 1)
        _row, _packet, _provider, request, _formatting, native = observation(
            store, manager, run["id"]
        )
        assert (
            native
            == len(json.dumps(gateway._payload(request, False), ensure_ascii=False).encode("utf-8"))
            + 64
        )
        ledgers = accounting(store)
        assert asyncio.run(IndependentReviewService(manager).tick())
        assert manager.get(run["id"])["state"] == "blocked"
        assert len(requests) == 2 and accounting(store) == ledgers
