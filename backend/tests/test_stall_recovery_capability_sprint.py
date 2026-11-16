"""Programmatic SQLite/Mock regressions; no live Provider or old run database."""

import asyncio
import json

import pytest
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.agent_protocol import AgentMessageType, validate_payload
from forge_core.dispatcher import Dispatcher
from forge_core.domain import DomainError, ForgeEvent
from forge_core.independent_review import IndependentReviewService
from forge_core.provider_pool import ProviderPool
from forge_core.store import Store
from forge_core.superagent_swarm import expand_experts, reassign_expert
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.watchdog import Watchdog
from forge_core.worker_model_usage import reserve_worker_model_call
from forge_core.worker_runtime import WorkerManager

REVISION = {
    "verdict": "NEEDS_REVISION",
    "findings": ["Needs correction."],
    "revision_goals": ["Correct this requirement within the approved assignment."],
    "confidence": 0.8,
}


def pending(tmp_path, *, max_repairs=0):
    store, manager, model, run = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery()), response(REVISION)],
        max_repairs=max_repairs,
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    reviewer = IndependentReviewService(manager)
    block_exhausted = reviewer._block_exhausted_revision
    reviewer._block_exhausted_revision = lambda *_args: False
    request_revision = manager.request_revision
    manager.request_revision = lambda *_args: None  # Crash boundary before revision admission.
    asyncio.run(reviewer.review(run["id"]))
    manager.request_revision = request_revision
    reviewer._block_exhausted_revision = block_exhausted
    event = next(
        e for e in reversed(store.list_events(limit=1000)) if e["event_type"] == "review.result"
    )
    return store, manager, model, run, reviewer, event


@pytest.mark.parametrize("bound", ["repair", "steps", "deadline", "token", "review_rounds"])
def test_only_current_settled_result_at_actual_limit_converges(tmp_path, monkeypatch, bound):
    store, manager, model, run, reviewer, event = pending(
        tmp_path, max_repairs=0 if bound == "repair" else 4
    )
    if bound == "deadline":
        manager.clock = lambda: manager.get(run["id"])["deadline"] + 1
    elif bound == "review_rounds":
        reviewer.max_rounds = 1
    elif bound in {"steps", "token"}:
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE worker_runs SET "
                + ("steps=step_limit" if bound == "steps" else "token_spent=token_limit")
                + " WHERE id=?",
                (run["id"],),
            )
        if bound == "token":
            monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    before = manager.get(run["id"])
    calls = len(model.requests)
    with store.connection() as c:
        receipts = [dict(r) for r in c.execute("SELECT * FROM action_receipts")]
        reservations = [dict(r) for r in c.execute("SELECT * FROM worker_model_reservations")]
    assert reviewer._block_exhausted_revision(run["id"], event["id"])
    assert not reviewer._block_exhausted_revision(run["id"], event["id"])
    after = manager.get(run["id"])
    assert after["state"] == "blocked" and len(model.requests) == calls
    for key in ("repairs", "steps", "token_spent", "tool_spent", "generation", "deadline"):
        assert before[key] == after[key]
    with store.connection() as c:
        assert c.execute("SELECT status FROM workers").fetchone()[0] == "blocked"
        assert c.execute("SELECT status FROM task_nodes").fetchone()[0] == "blocked"
        assert [dict(r) for r in c.execute("SELECT * FROM action_receipts")] == receipts
        assert [
            dict(r) for r in c.execute("SELECT * FROM worker_model_reservations")
        ] == reservations
        blocked = c.execute(
            "SELECT id,payload FROM events WHERE event_type='worker.blocked'"
        ).fetchall()
        assert len(blocked) == 1
        payload = json.loads(blocked[0]["payload"])
        assert payload["review_event_id"] == event["id"]
        assert payload["result_event_id"] == json.loads(event["payload"])["result_event_id"]
        assert payload["failure_category"] == "budget"
        assert c.execute("SELECT 1 FROM outbox WHERE event_id=?", (blocked[0]["id"],)).fetchone()


@pytest.mark.parametrize(
    "guard",
    [
        "remaining",
        "disabled_tokens",
        "new_result",
        "later_pass",
        "packet",
        "plan",
        "team",
        "wrong_worker",
        "wrong_mode",
        "unknown_model",
        "unknown_gateway",
        "unknown_tool",
        "unknown_intent",
        "running",
        "stale_claim",
    ],
)
def test_exhaustion_never_overrides_stale_authority_or_unknown_fences(tmp_path, monkeypatch, guard):
    store, manager, model, run, reviewer, event = pending(
        tmp_path, max_repairs=4 if guard in {"remaining", "disabled_tokens"} else 0
    )
    payload = json.loads(event["payload"])
    with store.unit_of_work() as work:
        c = work.connection
        row = c.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone()
        if guard == "disabled_tokens":
            monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
            c.execute("UPDATE worker_runs SET token_spent=token_limit WHERE id=?", (run["id"],))
        elif guard == "new_result":
            original = c.execute(
                "SELECT * FROM events WHERE id=?", (payload["result_event_id"],)
            ).fetchone()
            work.append_event(
                ForgeEvent(
                    event_type="worker.result",
                    quest_id=row["quest_id"],
                    worker_id=row["worker_id"],
                    payload=json.loads(original["payload"]),
                )
            )
        elif guard == "later_pass":
            work.append_event(
                ForgeEvent(
                    event_type="review.result",
                    quest_id=row["quest_id"],
                    worker_id=row["worker_id"],
                    payload={**payload, "verdict": "PASS"},
                )
            )
        elif guard == "packet":
            payload["packet_sha256"] = "0" * 64
            c.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), event["id"]))
        elif guard == "plan":
            c.execute("UPDATE quests SET plan_revision=plan_revision+1")
        elif guard == "team":
            c.execute("UPDATE team_proposals SET status='revoked'")
        elif guard == "wrong_worker":
            c.execute("UPDATE events SET worker_id='another-worker' WHERE id=?", (event["id"],))
        elif guard == "wrong_mode":
            payload["execution_mode"] = "live"
            c.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), event["id"]))
        elif guard == "unknown_model":
            reserve_worker_model_call(
                c, {**dict(row), "lease_token": "old-owner"}, "unknown-model", 100, 10, None, []
            )
        elif guard == "unknown_gateway":
            c.execute(
                "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at) VALUES('unknown','unknown','worker',?,?,?,?,?,10,10,'unknown',0)",
                (
                    store.workspace.id,
                    row["quest_id"],
                    row["id"],
                    row["provider_id"],
                    row["model_id"],
                ),
            )
        elif guard == "unknown_tool":
            c.execute(
                "INSERT INTO worker_tool_calls VALUES(?,?,?,?,?,'uncertain',NULL,NULL)",
                (run["id"], "unknown-tool", "old-model", "file_write", "{}"),
            )
        elif guard == "unknown_intent":
            # No matching tool row is required to preserve an externally uncertain intent.
            c.execute("UPDATE action_intents SET state='uncertain'")
        elif guard == "running":
            c.execute(
                "UPDATE worker_runs SET state='running',lease_token='new-owner',lease_until=9999999999 WHERE id=?",
                (run["id"],),
            )
        elif guard == "stale_claim":
            work.append_event(
                ForgeEvent(
                    event_type="review.started",
                    quest_id=row["quest_id"],
                    worker_id=row["worker_id"],
                    payload={**payload, "call_id": "new-review-claim"},
                )
            )
    before = manager.get(run["id"])
    calls = len(model.requests)
    assert not reviewer._block_exhausted_revision(run["id"], event["id"])
    assert manager.get(run["id"]) == before and len(model.requests) == calls
    assert not [e for e in store.list_events(limit=1000) if e["event_type"] == "worker.blocked"]


def test_existing_blocked_marker_recovers_after_restart_once(tmp_path):
    store, manager, model, run, _reviewer, event = pending(tmp_path)
    store.append_event(
        ForgeEvent(
            event_type="review.revision_blocked",
            quest_id=run["quest_id"],
            worker_id=run["worker_id"],
            payload={"run_id": run["id"], "review_event_id": event["id"]},
        )
    )
    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
        max_repairs=0,
    )
    service = IndependentReviewService(resumed)
    calls = len(model.requests)
    assert asyncio.run(service.tick())
    assert resumed.get(run["id"])["state"] == "blocked"
    assert not asyncio.run(service.tick()) and len(model.requests) == calls


@pytest.mark.parametrize("unknown", [False, True])
def test_stall_help_is_valid_delivered_and_observation_only(tmp_path, unknown):
    store, manager, model, run = prepared(tmp_path, [], max_repairs=4)
    manager.clock = lambda: 100
    manager._claim(run["id"])
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET deadline=1000,lease_until=900 WHERE id=?", (run["id"],)
        )
        if unknown:
            row = work.connection.execute(
                "SELECT * FROM worker_runs WHERE id=?", (run["id"],)
            ).fetchone()
            reserve_worker_model_call(work.connection, row, "unknown", 100, 10, None, [])
    Watchdog(store, clock=lambda: 100, worker_progress_timeout=20).tick()
    Watchdog(store, clock=lambda: 125, worker_progress_timeout=20).tick()
    event = next(
        e for e in store.list_events(limit=1000) if e["event_type"] == "worker.help_requested"
    )
    payload = json.loads(event["payload"])
    assert validate_payload("worker.help_requested", payload) == 2
    assert (
        payload["requested_role"] == "router" and payload["requires_approved_reassignment"] is True
    )
    assert payload["unsettled_effect_or_call"] is unknown
    dispatcher = Dispatcher(store, clock=lambda: 125)
    dispatcher.register("router", replay_from_seq=event["seq"] - 1)
    lease = dispatcher.claim("router")
    item = dispatcher.deliver(lease)
    assert item["event_id"] == event["id"]
    assert item["agent_message"]["message_type"] == AgentMessageType.HELP_REQUEST
    assert len(dispatcher.inbox("router")) == 1
    with store.connection() as c:
        state = c.execute(
            "SELECT state,generation,lease_token,lease_until FROM worker_runs WHERE id=?",
            (run["id"],),
        ).fetchone()
        assert state["state"] == "running" and state["lease_until"] == 900
        assert c.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM team_proposals").fetchone()[0] == 1
        if unknown:
            assert (
                c.execute("SELECT state FROM worker_model_reservations").fetchone()[0] == "reserved"
            )
    assert not model.requests
    bad = {**payload}
    bad.pop("reason")
    with pytest.raises(DomainError, match="Help requires"):
        validate_payload("worker.help_requested", bad)


@pytest.mark.parametrize("tamper", [None, "unknown", "identity", "purpose", "duplicate"])
def test_http_fixture_requires_exact_settled_native_reviewer_proof(tmp_path, tamper):
    """Real local HTTP/Gateway/ledger plumbing; scripted fixture, never a real model."""
    store, manager, _model, run = prepared(tmp_path, [], max_repairs=0)

    def responder(_body, ordinal):
        if ordinal == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "read-actual-file",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "work/input.txt"}),
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": json.dumps(delivery() if ordinal == 2 else REVISION),
        }

    with offline_loopback_gateway(store, responder) as (gateway, requests):
        manager.providers = ProviderPool({"protocol": gateway})
        with store.unit_of_work() as work:
            # Declare the real local HTTP execution path before any model/tool call.
            work.connection.execute(
                "UPDATE worker_runs SET execution_mode='live' WHERE id=?", (run["id"],)
            )
        assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
        service = IndependentReviewService(manager)
        finalizer = service._block_exhausted_revision
        service._block_exhausted_revision = lambda *_args: False
        revision = manager.request_revision
        manager.request_revision = lambda *_args: None
        asyncio.run(service.review(run["id"]))
        service._block_exhausted_revision = finalizer
        manager.request_revision = revision
        event = next(
            e for e in reversed(store.list_events(limit=1000)) if e["event_type"] == "review.result"
        )
        call_id = json.loads(event["payload"])["call_id"]
        with store.unit_of_work() as work:
            c = work.connection
            if tamper == "unknown":
                c.execute(
                    "UPDATE model_budget_attempts SET state='reserved' WHERE call_id=?", (call_id,)
                )
            elif tamper == "identity":
                c.execute(
                    "UPDATE model_budget_attempts SET model_id='different' WHERE call_id=?",
                    (call_id,),
                )
            elif tamper == "purpose":
                c.execute(
                    "UPDATE model_budget_attempts SET purpose='worker' WHERE call_id=?", (call_id,)
                )
            elif tamper == "duplicate":
                record = dict(
                    c.execute(
                        "SELECT * FROM model_budget_attempts WHERE call_id=?", (call_id,)
                    ).fetchone()
                )
                record["attempt_id"] = "duplicate-review-attempt"
                c.execute(
                    "INSERT INTO model_budget_attempts("
                    + ",".join(record)
                    + ") VALUES ("
                    + ",".join("?" for _ in record)
                    + ")",
                    list(record.values()),
                )
        assert service._block_exhausted_revision(run["id"], event["id"]) is (tamper is None)
        assert manager.get(run["id"])["state"] == (
            "blocked" if tamper is None else "review_pending"
        )
        assert len(requests) == 3


def test_watchdog_help_cannot_authorize_reassignment_or_expansion(delegated):
    store, quest_id, _mandate, graph, teams, service = delegated
    assert service.prepare(quest_id)
    from test_worker_intelligence_phase2 import ProtocolModel

    model = ProtocolModel([])
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    with store.connection() as c:
        digest = c.execute(
            "SELECT proposal_digest FROM team_proposals WHERE status='approved'"
        ).fetchone()[0]
    run = manager.start(
        quest_id, graph.get(quest_id)["id"], "task", team_digest=digest, member_index=0
    )
    manager.clock = lambda: 100
    manager._claim(run["id"])
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET deadline=1000,lease_until=900 WHERE id=?", (run["id"],)
        )
    Watchdog(store, clock=lambda: 100, worker_progress_timeout=20).tick()
    Watchdog(store, clock=lambda: 125, worker_progress_timeout=20).tick()
    event = next(
        e for e in store.list_events(limit=1000) if e["event_type"] == "worker.help_requested"
    )
    # Even after a later stopped state, an assessment event has no model request transcript.
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='blocked',lease_token=NULL,lease_until=NULL WHERE id=?",
            (run["id"],),
        )
        work.connection.execute(
            "UPDATE workers SET status='blocked' WHERE id=?", (run["worker_id"],)
        )
        work.connection.execute(
            "UPDATE task_nodes SET status='blocked' WHERE worker_id=?", (run["worker_id"],)
        )
    with pytest.raises(DomainError):
        reassign_expert(service, event["id"])
    with pytest.raises(DomainError):
        expand_experts(service, event["id"])
    assert not model.requests
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM team_proposals").fetchone()[0] == 1
