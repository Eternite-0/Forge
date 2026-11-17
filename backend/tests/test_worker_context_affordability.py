"""Offline context/receipt fixtures and Mock HTTP; no real Provider evidence."""

import asyncio
import hashlib
import json

import pytest
from test_model_compaction_v29 import http_response, mock_gateway
from test_worker_context_tail_phase2 import oversized_tail
from test_worker_intelligence_phase2 import delivery, prepared, response
from test_worker_model_compaction_v29 import seeded

from forge_core.context_service import ContextService, _hash, _json, _worker_handoff_input
from forge_core.domain import DomainError
from forge_core.store import Store
from forge_core.worker_model_compaction import WorkerModelCompactor

pytest_plugins = ["test_worker_runtime"]


def prior_handoff_with_settled_tail(graph):
    store, run, _ = oversized_tail(graph)
    service = ContextService(store)
    selected = service._worker_context(
        run["id"], input_budget=6000, source_readback_available=True, _prepare=True
    )
    service._worker_context(
        run["id"],
        input_budget=6000,
        source_readback_available=True,
        _expected=selected["snapshot"],
        _handoff={
            "text": "Earlier analysis. " * 85,
            "input_digest": _hash(_worker_handoff_input(selected, selected["selected"])),
        },
        _max_sources=len(selected["selected"]),
    )
    with store.unit_of_work() as work:
        c = work.connection
        # Synthetic settled receipt fixtures, each with a unique call identity.
        intent = dict(c.execute("SELECT * FROM action_intents WHERE id='intent-1'").fetchone())
        intent.update(id="intent-later", call_id="call-later")
        c.execute(
            "INSERT INTO action_intents VALUES (" + ",".join("?" for _ in intent) + ")",
            tuple(intent.values()),
        )
        output = {"content": "settled response " * 155}
        result = _json(output)
        c.execute(
            "INSERT INTO action_receipts VALUES (?,?,?,?,?,?)",
            (
                "receipt-later",
                "intent-later",
                "mock",
                result,
                hashlib.sha256(result.encode()).hexdigest(),
                "now",
            ),
        )
        c.execute(
            "INSERT INTO worker_tool_calls VALUES (?,?,?,?,?,?,?,?)",
            (
                run["id"],
                "call-later",
                "model-later",
                "read",
                intent["parameters_json"],
                "succeeded",
                "receipt-later",
                result,
            ),
        )
        messages = [
            {"role": "assistant", "content": "Old delivery. " * 350},
            {"role": "user", "content": "Revise with the existing source."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "model-later",
                        "type": "function",
                        "function": {"name": "read", "arguments": intent["parameters_json"]},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "model-later", "content": result},
        ]
        start = c.execute(
            "SELECT max(ordinal)+1 FROM worker_messages WHERE run_id=?", (run["id"],)
        ).fetchone()[0]
        for ordinal, message in enumerate(messages, start):
            c.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (run["id"], ordinal, message["role"], _json(message)),
            )
    return store, run, list(range(start, start + len(messages)))


def test_existing_handoff_is_counted_before_whole_settled_tail_selection(graph):
    store, run, tail = prior_handoff_with_settled_tail(graph)
    service = ContextService(store)
    with store.connection() as c:
        before = c.execute("SELECT count(*) FROM context_archives").fetchone()[0]
    result = service._worker_context(
        run["id"], input_budget=6000, source_readback_available=True, _prepare=True
    )
    assert [row["ordinal"] for row in result["selected"]] == tail
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM context_archives").fetchone()[0] == before


@pytest.mark.parametrize("unsafe", ["pending", "uncertain", "missing_receipt"])
def test_handoff_fallback_keeps_unsettled_or_unreceipted_tail_live(graph, unsafe):
    store, run, _ = prior_handoff_with_settled_tail(graph)
    with store.unit_of_work() as work:
        if unsafe == "pending":
            work.connection.execute(
                "UPDATE worker_tool_calls SET state='pending' WHERE call_id='call-later'"
            )
        elif unsafe == "uncertain":
            work.connection.execute(
                "UPDATE action_intents SET state='uncertain' WHERE id='intent-later'"
            )
        else:
            work.connection.execute(
                "UPDATE worker_tool_calls SET receipt_id=NULL WHERE call_id='call-later'"
            )
    with pytest.raises(DomainError, match="context budget"):
        ContextService(store)._worker_context(
            run["id"], input_budget=6000, source_readback_available=True, _prepare=True
        )


def test_registry_capacity_does_not_become_a_cumulative_execution_grant(tmp_path, monkeypatch):
    save_model = Store.save_model

    def operator_profile(self, profile):
        return save_model(self, profile.model_copy(update={"context_window": 100000000}))

    monkeypatch.setattr(Store, "save_model", operator_profile)
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    store, manager, model, worker = prepared(tmp_path, [response(delivery())])
    seen = []
    original = ContextService.build_worker_context

    def observe(self, run_id, **kwargs):
        seen.append(kwargs["input_budget"])
        return original(self, run_id, **kwargs)

    monkeypatch.setattr(ContextService, "build_worker_context", observe)
    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == "review_pending"
    assert model.requests[0].context_limit == 100000000
    assert 0 < seen[0] < worker["token_limit"] < 100000000
    assert outcome["token_limit"] == worker["token_limit"]
    assert outcome["token_spent"] == 40
    with store.connection() as c:
        reservation = c.execute("SELECT reserved_tokens FROM worker_model_reservations").fetchone()[
            0
        ]
    assert reservation <= worker["token_limit"]


def test_compactor_selects_an_affordable_whole_prefix_without_widening_budget(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    store, manager, worker, token, _ = seeded(tmp_path, chars=6000)
    seen = []

    def handler(request):
        body = json.loads(request.content)
        packet = json.loads(body["messages"][1]["content"])
        seen.extend(item["ordinal"] for item in packet["messages"])
        return http_response("Retain the source references and finish the original task.")

    gateway = mock_gateway(store, handler)
    result = asyncio.run(
        WorkerModelCompactor(
            manager, gateway, worker["id"], token, context_limit=64000, output_limit=128
        ).compact(input_budget=6000, source_readback_available=True)
    )
    assert result["source_count"] and seen[0] == 2
    assert seen[-1] < 7
    assert manager.get(worker["id"])["token_limit"] == worker["token_limit"]
    with store.connection() as c:
        reserved = c.execute("SELECT reserved_tokens FROM worker_model_reservations").fetchone()[0]
    assert reserved <= worker["token_limit"]


def test_large_model_window_cannot_admit_an_unaffordable_compaction(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    store, manager, worker, token, _ = seeded(tmp_path, chars=6000)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET token_spent=token_limit-500 WHERE id=?", (worker["id"],)
        )
    requests = []

    def handler(request):
        requests.append(request)
        raise AssertionError("An unaffordable summary must not reach HTTP")

    gateway = mock_gateway(store, handler)
    with pytest.raises(DomainError, match="compaction token allocation exhausted"):
        asyncio.run(
            WorkerModelCompactor(
                manager, gateway, worker["id"], token, context_limit=64000, output_limit=128
            ).compact(input_budget=6000, source_readback_available=True)
        )
    assert not requests
    with store.connection() as c:
        assert not c.execute("SELECT 1 FROM worker_model_reservations").fetchone()
        assert not c.execute("SELECT 1 FROM context_archives").fetchone()
    assert manager.get(worker["id"])["token_spent"] == worker["token_limit"] - 500
