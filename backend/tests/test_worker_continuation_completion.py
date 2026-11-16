"""Real durable Thread/read proofs with scripted models; no Provider requests."""

import asyncio
import json

import pytest
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response, tool

from forge_core.domain import DomainError
from forge_core.provider_pool import ProviderPool
from forge_core.superagent_events import TriggerService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_continuations import continuation_feedback, pending_continuations
from forge_core.worker_runtime import WorkerManager


@pytest.fixture
def continuation(delegated):
    store, quest_id, _mandate, graph, teams, service = delegated
    service.prepare(quest_id)
    model = ProtocolModel([])
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams,
    )
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)).fetchone()
    run = manager.start(quest_id, plan["id"], "task", team_digest=team["proposal_digest"], member_index=0)
    events = TriggerService(store)
    events.register_source("continuation", kind="message")
    trigger = events.receive("continuation", "one", "same-task", {"text": "源😊" * 2800})
    events.subscribe(trigger["thread_id"], run["id"])
    return store, manager, model, run, trigger, events


def reads(trigger, *, first=0, size=2048):
    return [tool("read_trigger", {
        "trigger_id": trigger["id"], "offset_chars": offset, "max_chars": size,
    }, ordinal=number) for number, offset in enumerate(range(first, trigger["total_chars"], size), 1)]


def debt(store, run, *, before=100000):
    with store.connection() as c:
        changes = c.total_changes
        result = pending_continuations(store, c, run["id"], before_ordinal=before)
        assert c.total_changes == changes
        return result


def test_injected_preview_cannot_finalize_and_full_reads_preserve_charged_draft(continuation):
    store, manager, model, run, trigger, _events = continuation
    model.responses.extend([response(delivery("charged draft")), *reads(trigger), response(delivery())])
    initial = manager.get(run["id"])
    result = asyncio.run(manager.run(run["id"]))
    assert result["state"] == "review_pending"
    assert result["steps"] == len(model.requests) == 5
    assert result["tool_spent"] == 3 and result["repairs"] == 0
    assert result["token_spent"] == 40 * len(model.requests)
    assert result["step_limit"] == initial["step_limit"]
    assert result["tool_limit"] == initial["tool_limit"]
    assert not debt(store, run)
    with store.connection() as c:
        assert c.execute("SELECT consumed FROM worker_inbox").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM worker_messages WHERE run_id=? AND role='assistant' "
                         "AND json_extract(message_json,'$.content')=?", (run["id"], json.dumps(delivery("charged draft")))).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 1
    second = json.dumps(model.requests[1].messages)
    assert "Thread context remains unread" in second
    assert trigger["id"] in second and "grants no permissions" in second


def test_ignored_read_debt_exhausts_original_steps_without_result(continuation):
    store, manager, model, run, _trigger, _events = continuation
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET step_limit=3 WHERE id=?", (run["id"],))
    model.responses.extend(response(delivery("ignored")) for _ in range(3))
    result = asyncio.run(manager.run(run["id"]))
    assert result["state"] == "blocked" and result["steps"] == result["step_limit"] == 3
    assert result["token_spent"] == 120 and result["tool_spent"] == 0
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM worker_messages WHERE role='assistant'").fetchone()[0] == 3


def test_superagent_event_arriving_during_final_inference_requires_full_read(continuation):
    store, manager, model, run, trigger, events = continuation
    model.responses.extend([*reads(trigger), response(delivery("draft before late event"))])
    original = model.generate
    late = []

    async def receive_during_final(request):
        if len(model.requests) == 3:
            late.append(events.receive("continuation", "late", "same-task", {"text": "new task detail"}))
            model.responses.extend([
                tool("read_trigger", {"trigger_id": late[0]["id"], "max_chars": 4096}, 99),
                response(delivery()),
            ])
        return await original(request)

    model.generate = receive_during_final
    result = asyncio.run(manager.run(run["id"]))
    assert result["state"] == "review_pending" and result["steps"] == 6
    assert result["token_spent"] == 240 and result["tool_spent"] == 4 and result["repairs"] == 0
    assert not debt(store, run)
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM worker_messages WHERE role='assistant'").fetchone()[0] == 6
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM superagent_trigger_deliveries WHERE run_id=?", (run["id"],)).fetchone()[0] == 2


@pytest.mark.parametrize("tamper", ["partial", "duplicate", "proof", "transcript", "unknown", "hash", "late", "order"])
def test_full_read_requires_complete_scoped_proofs_before_final_draft(continuation, tamper):
    store, manager, model, run, trigger, _events = continuation
    model.responses.extend([*reads(trigger), response(delivery())])
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    before = 100000
    with store.unit_of_work() as work:
        c = work.connection
        calls = c.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        if tamper in {"partial", "duplicate"}:
            c.execute("DELETE FROM worker_tool_calls WHERE call_id=?", (calls[-1]["call_id"],))
            if tamper == "duplicate":
                # A duplicate successful first page cannot supply the last page.
                c.execute("UPDATE worker_tool_calls SET arguments_json=?,result_json=? WHERE call_id=?",
                          (calls[0]["arguments_json"], calls[0]["result_json"], calls[1]["call_id"]))
        elif tamper == "proof":
            c.execute("UPDATE evidence SET trusted=0 WHERE kind='worker_core_source'")
        elif tamper == "transcript":
            c.execute("DELETE FROM worker_messages WHERE role='tool'")
        elif tamper == "unknown":
            c.execute("UPDATE worker_tool_calls SET state='uncertain'")
        elif tamper == "hash":
            c.execute("UPDATE worker_tool_calls SET result_json=json_set(result_json,'$.payload_sha256',?)", ('0' * 64,))
        elif tamper == "late":
            before = c.execute("SELECT MAX(ordinal) FROM worker_messages WHERE role='tool'").fetchone()[0]
        else:
            c.execute("UPDATE worker_messages SET ordinal=ordinal+1000 WHERE role='assistant'")
    assert debt(store, run, before=before)


def test_current_source_corruption_fails_closed(continuation):
    store, manager, model, run, trigger, _events = continuation
    model.responses.append(response(delivery()))
    original = model.generate

    async def corrupt(request):
        with store.unit_of_work() as work:
            work.connection.execute("UPDATE superagent_triggers SET payload_json='{}' WHERE id=?", (trigger["id"],))
        return await original(request)

    model.generate = corrupt
    result = asyncio.run(manager.run(run["id"]))
    assert result["state"] == "blocked" and result["token_spent"] == 40
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM worker_messages WHERE role='assistant'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 0
        payload = c.execute("SELECT payload FROM events WHERE event_type='worker.blocked'").fetchone()[0]
        assert json.loads(payload)["reason_code"] == "continuation_source_unavailable"


@pytest.mark.parametrize("malformed", [[], {"tool_calls": [None]}, {"tool_calls": None}])
def test_corrupt_invocation_transcript_blocks_and_keeps_charged_draft(continuation, malformed):
    store, manager, model, run, trigger, _events = continuation
    model.responses.extend([*reads(trigger), response(delivery("charged final draft"))])
    original = model.generate

    async def corrupt_final(request):
        if len(model.requests) == 3:
            with store.unit_of_work() as work:
                work.connection.execute(
                    "UPDATE worker_messages SET message_json=? WHERE run_id=? AND ordinal="
                    "(SELECT MIN(ordinal) FROM worker_messages WHERE run_id=? AND role='assistant')",
                    (json.dumps(malformed), run["id"], run["id"]),
                )
        return await original(request)

    model.generate = corrupt_final
    result = asyncio.run(manager.run(run["id"]))
    assert result["state"] == "blocked" and result["token_spent"] == 160
    assert result["steps"] == 4 and result["tool_spent"] == 3 and result["repairs"] == 0
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM worker_messages WHERE role='assistant'").fetchone()[0] == 4
        payload = c.execute("SELECT payload FROM events WHERE event_type='worker.blocked'").fetchone()[0]
        assert json.loads(payload)["reason_code"] == "continuation_source_unavailable"


def test_actual_out_of_scope_handback_discharges_only_proved_delivery(continuation):
    store, manager, model, run, trigger, _events = continuation
    model.responses.extend([
        tool("handback_trigger", {"trigger_id": trigger["id"], "reason": "outside current assignment"}),
        response(delivery()),
    ])
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    assert not debt(store, run)
    with store.unit_of_work() as work:
        work.connection.execute("DELETE FROM evidence WHERE kind='worker_core_source'")
    assert debt(store, run)


def test_complete_read_survives_generation_change_and_legacy_remains_unaffected(continuation):
    store, manager, model, run, trigger, _events = continuation
    model.responses.extend([*reads(trigger), response(delivery())])
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET generation=generation+2 WHERE id=?", (run["id"],))
    assert not debt(store, run)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_capsules SET capsule_json=json_remove(capsule_json,'$.superagent') WHERE run_id=?", (run["id"],))
    assert not debt(store, run)  # Legacy contracts acquire no new obligations/tools.


@pytest.mark.parametrize("tamper", ["inbox", "envelope", "event", "revoked", "foreign_run"])
def test_unread_delivery_source_is_verified_before_any_feedback(continuation, tamper):
    store, _manager, _model, run, trigger, _events = continuation
    with store.unit_of_work() as work:
        c = work.connection
        if tamper == "inbox":
            c.execute("DELETE FROM worker_inbox")
        elif tamper == "envelope":
            c.execute("UPDATE worker_inbox SET payload_json='{}'")
        elif tamper == "event":
            c.execute("UPDATE events SET payload='{}' WHERE event_type='trigger.received'")
        elif tamper == "revoked":
            c.execute("INSERT INTO revoked_memory_sources VALUES (?)", ("trigger:" + trigger["id"],))
        else:
            c.execute("INSERT INTO conversations VALUES ('other','now')")
            c.execute("UPDATE quests SET conversation_id='other' WHERE id=?", (run["quest_id"],))
    with pytest.raises(DomainError):
        debt(store, run)


def test_single_complete_page_is_accepted_and_forged_handback_is_not(continuation):
    store, manager, model, run, trigger, events = continuation
    small = events.receive("continuation", "two", "same-task", {"text": "bounded"})
    model.responses.extend([
        *reads(trigger), tool("read_trigger", {"trigger_id": small["id"], "max_chars": 4096}, 99),
        response(delivery()),
    ])
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    assert not debt(store, run)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE superagent_trigger_deliveries SET handed_back=1,handback_reason='forged'")
        work.connection.execute("DELETE FROM worker_tool_calls")
    assert debt(store, run)
    assert small["total_chars"] < 4096


def test_feedback_is_bounded_and_never_contains_source_bodies():
    pending = [{"trigger_id": f"trigger_{i}", "payload_sha256": "a" * 64,
                "total_chars": 100000, "next_offset_chars": 0} for i in range(1000)]
    feedback = continuation_feedback(pending)
    assert len(feedback.encode()) < 2400 and '"omitted":992' in feedback
    assert "recall_object" not in feedback
