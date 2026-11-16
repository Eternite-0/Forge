"""Core provenance with real SQLite/tools and explicitly scripted model responses.

The scripted adapter's live tag exercises completion gates; no Provider is called
and these tests are not real Provider acceptance evidence.
"""

import asyncio
import json
from dataclasses import replace

import pytest
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response, tool

from forge_core.domain import DomainError, ForgeEvent, Quest
from forge_core.evidence import NodeVerifier, _verify_worker_context_call
from forge_core.independent_review import IndependentReviewService, _review_receipts
from forge_core.memory import MemoryService
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderGateway
from forge_core.superagent_events import TriggerService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_core_provenance import _json
from forge_core.worker_runtime import WorkerManager


@pytest.fixture
def completed(delegated):
    store, quest_id, _, graph, teams, service = delegated
    service.prepare(quest_id)

    class OfflineLiveProtocol(ProtocolModel):
        execution_mode = "live"

    model = OfflineLiveProtocol([])
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)).fetchone()
    run = manager.start(
        quest_id, plan["id"], "task", team_digest=team["proposal_digest"], member_index=0
    )
    events = TriggerService(store)
    events.register_source("review-source", kind="message")
    trigger = events.receive("review-source", "1", "topic", {"text": "original " * 400})
    events.subscribe(trigger["thread_id"], run["id"])

    def execute(name="read_trigger", arguments=None):
        if arguments is None:
            arguments = {"trigger_id": trigger["id"], "offset_chars": 100, "max_chars": 1024}
        model.responses.extend(
            [
                tool(name, arguments),
                tool(
                    "file_write",
                    {"path": f"work/{quest_id}/result.json", "content": '{"ok":true}'},
                    2,
                ),
                # Completion now requires full delivered Thread consumption;
                # keep the original partial call and its tamper checks intact.
                tool("read_trigger", {"trigger_id": trigger["id"], "max_chars": 4096}, 3),
                response(delivery()),
                response(
                    {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
                ),
            ]
        )
        assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
        with store.connection() as c:
            call = dict(
                c.execute(
                    "SELECT * FROM worker_tool_calls WHERE run_id=? AND tool_name=?",
                    (run["id"], name),
                ).fetchone()
            )
        return call

    return store, manager, model, run, trigger, events, execute


@pytest.mark.parametrize("name", ["read_trigger", "recall_object", "handback_trigger"])
def test_real_core_checkpoints_pass_review_and_completion_without_fabricated_action_receipts(
    completed, name
):
    store, manager, model, run, trigger, _, execute = completed
    arguments = (
        {"reference": f"quest:{run['quest_id']}"}
        if name == "recall_object"
        else {
            "trigger_id": trigger["id"],
            **({"reason": "outside this assignment"} if name == "handback_trigger" else {}),
        }
    )
    call = execute(name, arguments)
    with store.connection() as c:
        before = c.total_changes
        assert _verify_worker_context_call(store, c, run["id"], call)
        receipts, _ = _review_receipts(store, c, run)
        assert len(receipts) == 1 and receipts[0]["tool_name"] == "file_write"
        assert c.total_changes == before
        assert call["receipt_id"] is None
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1

    def respond(body, _ordinal):
        assert body["response_format"]["type"] == "json_schema"
        verdict = model.responses.pop(0)
        return {"content": verdict.text}

    with offline_loopback_gateway(store, respond) as (endpoint, _requests):
        gateway = ProviderGateway(
            replace(endpoint.config, id="mock-provider", supports_json_schema=True),
            credential_resolver=lambda _: "offline-fixture",
            budget_ledger=ModelCallBudgetLedger(
                store.path, token_limit=500000, concurrency_limit=1
            ),
        )
        manager.providers = ProviderPool({"mock-provider": gateway})
        assert asyncio.run(IndependentReviewService(manager).review(run["id"]))["verdict"] == "PASS"
    NodeVerifier(store).verify(run["quest_id"], "task")
    assert manager.get(run["id"])["state"] == "succeeded"


@pytest.mark.parametrize(
    "tamper",
    [
        "arguments",
        "result_and_tool",
        "assistant",
        "tool",
        "proof",
        "trusted",
        "delivery",
        "inbox",
        "trigger_payload",
        "event",
    ],
)
def test_core_source_provenance_rejects_forged_calls_transcripts_and_actual_delivery(
    completed, tamper
):
    store, _, _, run, trigger, _, execute = completed
    call = execute()
    with store.unit_of_work() as work:
        c = work.connection
        if tamper == "arguments":
            c.execute(
                "UPDATE worker_tool_calls SET arguments_json='{}' WHERE call_id=?",
                (call["call_id"],),
            )
        elif tamper == "result_and_tool":
            result = json.loads(call["result_json"])
            result["text"] = "fabricated input"
            c.execute(
                "UPDATE worker_tool_calls SET result_json=? WHERE call_id=?",
                (_json(result), call["call_id"]),
            )
            c.execute(
                "UPDATE worker_messages SET message_json=json_set(message_json,'$.content',?) WHERE run_id=? AND role='tool' AND json_extract(message_json,'$.tool_call_id')=?",
                (_json(result), run["id"], call["model_call_id"]),
            )
        elif tamper in {"assistant", "tool"}:
            c.execute(
                "DELETE FROM worker_messages WHERE run_id=? AND role=? AND "
                + (
                    "json_extract(message_json,'$.tool_calls[0].id')=?"
                    if tamper == "assistant"
                    else "json_extract(message_json,'$.tool_call_id')=?"
                ),
                (run["id"], tamper, call["model_call_id"]),
            )
        elif tamper == "proof":
            c.execute(
                "UPDATE evidence SET payload=json_set(payload,'$.run_id','other-run') WHERE kind='worker_core_source'"
            )
        elif tamper == "trusted":
            c.execute("UPDATE evidence SET trusted=0 WHERE kind='worker_core_source'")
        elif tamper == "delivery":
            c.execute("DELETE FROM superagent_trigger_deliveries WHERE run_id=?", (run["id"],))
        elif tamper == "inbox":
            c.execute("UPDATE worker_inbox SET payload_json='{}' WHERE run_id=?", (run["id"],))
        elif tamper == "trigger_payload":
            c.execute(
                "UPDATE superagent_triggers SET payload_json='{}' WHERE id=?", (trigger["id"],)
            )
        elif tamper == "event":
            c.execute("UPDATE events SET payload='{}' WHERE id=?", (trigger["event_id"],))
        current = c.execute(
            "SELECT * FROM worker_tool_calls WHERE call_id=?", (call["call_id"],)
        ).fetchone()
        assert not _verify_worker_context_call(store, c, run["id"], current)
        with pytest.raises(DomainError, match="context receipt"):
            _review_receipts(store, c, run)


def test_read_trigger_historical_projection_survives_handback_and_records_only_read_page(completed):
    store, _, _, run, trigger, events, execute = completed
    call = execute()
    events.handback(run["id"], trigger["id"], "new assignment")
    with store.connection() as c:
        assert _verify_worker_context_call(store, c, run["id"], call)
        proof = json.loads(
            c.execute("SELECT payload FROM evidence WHERE kind='worker_core_source'").fetchone()[0]
        )
        assert proof["page"]["offset_chars"] == 100
        assert proof["page"]["next_offset_chars"] == 1124
        assert proof["page"]["total_chars"] > 1124


def test_dynamic_recall_keeps_historical_result_but_current_revocation_is_enforced(completed):
    store, _, _, run, _, _, execute = completed
    reference = f"quest:{run['quest_id']}"
    call = execute("recall_object", {"reference": reference})
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET priority=priority+1 WHERE id=?", (run["quest_id"],)
        )
        assert _verify_worker_context_call(store, work.connection, run["id"], call)
    MemoryService(store).revoke_source(reference)
    with store.connection() as c:
        assert not _verify_worker_context_call(store, c, run["id"], call)


def test_handback_checkpoint_failure_rolls_back_delivery_and_event(completed, monkeypatch):
    store, manager, model, run, trigger, _, _ = completed

    def fail(*args):
        raise DomainError("Checkpoint rejected")

    monkeypatch.setattr("forge_core.worker_core_provenance.checkpoint", fail)
    model.responses.extend(
        [tool("handback_trigger", {"trigger_id": trigger["id"], "reason": "outside task"})]
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "blocked"
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT handed_back FROM superagent_trigger_deliveries WHERE run_id=?", (run["id"],)
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='trigger.handed_back'"
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute("SELECT COUNT(*) FROM evidence WHERE kind='worker_core_source'").fetchone()[0]
            == 0
        )


def test_immutable_recalled_evidence_is_bound_to_actual_task_and_content(completed):
    store, _, _, run, _, _, execute = completed
    event = ForgeEvent(
        event_type="test.observation", quest_id=run["quest_id"], payload={"actual": "source"}
    )
    store.append_event(event)
    call = execute("recall_object", {"reference": f"event:{event.id}"})
    with store.unit_of_work() as work:
        c = work.connection
        assert _verify_worker_context_call(store, c, run["id"], call)
        assert not _verify_worker_context_call(store, c, "another-run", call)
        c.execute("UPDATE events SET payload='{}' WHERE id=?", (event.id,))
        assert not _verify_worker_context_call(store, c, run["id"], call)


def test_cross_task_recall_cannot_mint_core_source_proof(completed):
    store, manager, model, run, _, _, _ = completed
    other = Quest(title="Other task", goal="Unrelated confidential source")
    store.save_quest(other)
    event = ForgeEvent(
        event_type="test.observation", quest_id=other.id, payload={"secret": "other"}
    )
    store.append_event(event)
    model.responses.extend([tool("recall_object", {"reference": f"event:{event.id}"})])
    assert asyncio.run(manager.run(run["id"]))["state"] == "blocked"
    with store.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM evidence WHERE kind='worker_core_source'").fetchone()[0]
            == 0
        )
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


def test_missing_core_checkpoint_is_never_reconstructed_from_claims(completed):
    store, _, _, run, _, _, execute = completed
    call = execute()
    with store.unit_of_work() as work:
        work.connection.execute("DELETE FROM evidence WHERE kind='worker_core_source'")
        assert not _verify_worker_context_call(store, work.connection, run["id"], call)


@pytest.mark.parametrize("tamper", ["reason", "event"])
def test_handback_review_is_readonly_and_binds_its_actual_delivery_event(completed, tamper):
    store, _, _, run, trigger, _, execute = completed
    call = execute("handback_trigger", {"trigger_id": trigger["id"], "reason": "separate task"})
    with store.unit_of_work() as work:
        c = work.connection
        before = c.total_changes
        assert _verify_worker_context_call(store, c, run["id"], call)
        assert c.total_changes == before
        if tamper == "reason":
            c.execute(
                "UPDATE superagent_trigger_deliveries SET handback_reason='forged' WHERE run_id=?",
                (run["id"],),
            )
        else:
            c.execute(
                "UPDATE events SET payload=json_set(payload,'$.run_id','forged') WHERE event_type='trigger.delivery_handed_back'"
            )
        assert not _verify_worker_context_call(store, c, run["id"], call)
