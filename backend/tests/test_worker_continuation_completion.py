"""Real durable Thread/read proofs with scripted models; no Provider requests."""

import asyncio
import json
from collections import Counter
from pathlib import Path

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


def reads(trigger, *, first=0, size=2048, ordinal_start=1):
    return [tool("read_trigger", {
        "trigger_id": trigger["id"], "offset_chars": offset, "max_chars": size,
    }, ordinal=number) for number, offset in enumerate(
        range(first, trigger["total_chars"], size), ordinal_start
    )]


def debt(store, run, *, before=100000):
    with store.connection() as c:
        changes = c.total_changes
        result = pending_continuations(store, c, run["id"], before_ordinal=before)
        assert c.total_changes == changes
        return result


def pending_in_request(request):
    """Inspect the current Core metadata, not inbox previews or historic feedback."""
    content = next(
        message["content"] for message in reversed(request.messages)
        if message.get("content", "").startswith("Core delivery boundary:")
    )
    return json.loads(content[content.index('{"omitted":'):])


def authority_snapshot(store, manager, run):
    current = manager.get(run["id"])
    limits = {key: current[key] for key in (
        "step_limit", "tool_limit", "token_limit", "output_limit", "deadline",
        "allowed_tools_json", "cost_limit_microunits", "plan_digest", "team_digest",
    )}
    with store.connection() as c:
        grants = [dict(row) for row in c.execute(
            "SELECT * FROM task_grants WHERE quest_id=? ORDER BY rowid", (run["quest_id"],)
        )]
        approvals = [dict(row) for row in c.execute("SELECT * FROM approvals ORDER BY rowid")]
    return limits, grants, approvals


def test_first_model_round_sees_real_pending_without_counting_metadata_as_read(continuation):
    store, manager, model, run, trigger, _events = continuation
    original = model.generate
    model.responses.extend([*reads(trigger), response(delivery())])

    async def inspect_first(request):
        if not model.requests:
            pending = debt(store, run)
            assert pending == [{
                "trigger_id": trigger["id"], "payload_sha256": trigger["payload_sha256"],
                "total_chars": trigger["total_chars"], "next_offset_chars": 0,
            }]
            assert pending_in_request(request) == {"pending": pending, "omitted": 0}
            with store.connection() as c:
                # Consuming the inbox did not manufacture a read or an action.
                assert c.execute("SELECT consumed FROM worker_inbox").fetchone()[0] == 1
                assert c.execute("SELECT COUNT(*) FROM worker_tool_calls").fetchone()[0] == 0
                assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
                assert c.execute("SELECT COUNT(*) FROM evidence WHERE kind='worker_core_source'").fetchone()[0] == 0
            feedback = next(message["content"] for message in reversed(request.messages)
                            if message.get("content", "").startswith("Core delivery boundary:"))
            assert "源😊" not in feedback
            assert "apply task-relevant additions to the final artifact" in feedback
            assert "Reading alone does not satisfy a requirement" in feedback
        return await original(request)

    model.generate = inspect_first
    result = asyncio.run(manager.run(run["id"]))
    assert result["state"] == "review_pending"
    assert result["steps"] == 4 and result["tool_spent"] == 3
    assert result["token_spent"] == 160 and not debt(store, run)


def test_late_pending_is_visible_before_next_action_and_tracks_real_pages(continuation):
    store, manager, model, run, trigger, events = continuation
    before = authority_snapshot(store, manager, run)
    scope = before[1][0]["resource_scope"]
    model.responses.extend([
        *reads(trigger), tool("directory_list", {"path": scope}, ordinal=10),
    ])
    original = model.generate
    late = []
    seen_offsets = []

    async def arrive_during_action(request):
        step = len(model.requests)
        if step == 3:
            # A real event commits while inference is in flight, not at final delivery.
            late.append(events.receive("continuation", "mid-action", "same-task", {
                "text": "补充😊" * 1900, "requirement": "retain the final suffix",
            }))
            assert late[0]["id"] not in json.dumps(request.messages)
            model.responses.extend([
                tool("directory_list", {"path": scope}, ordinal=11),
                *[tool("read_trigger", {
                    "trigger_id": late[0]["id"], "offset_chars": offset, "max_chars": 2048,
                }, ordinal=20 + index) for index, offset in enumerate(
                    range(0, late[0]["total_chars"], 2048)
                )],
                response(delivery("All pages read; additions remain within the original mandate")),
            ])
        if late and step >= 4 and debt(store, run):
            pending = debt(store, run)
            assert pending_in_request(request) == {"pending": pending, "omitted": 0}
            assert pending[0]["trigger_id"] == late[0]["id"]
            seen_offsets.append(pending[0]["next_offset_chars"])
            if step == 4:
                with store.connection() as c:
                    assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 0
                    assert c.execute("SELECT COUNT(*) FROM worker_tool_calls WHERE tool_name='directory_list'").fetchone()[0] == 1
                    assert c.execute("SELECT COUNT(*) FROM worker_tool_calls WHERE json_extract(arguments_json,'$.trigger_id')=?",
                                     (late[0]["id"],)).fetchone()[0] == 0
                # The next response selects another real action; pending was already visible.
                assert model.responses[0].tool_calls[0]["name"] == "directory_list"
        return await original(request)

    model.generate = arrive_during_action
    result = asyncio.run(manager.run(run["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 0
    assert seen_offsets == [0, 0, 2048, 4096]
    assert result["steps"] == len(model.requests) == 9
    assert result["tool_spent"] == 8 and result["token_spent"] == 360
    assert authority_snapshot(store, manager, run) == before
    assert not debt(store, run)
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
        pages = c.execute(
            "SELECT result_json FROM worker_tool_calls WHERE tool_name='read_trigger' "
            "AND json_extract(arguments_json,'$.trigger_id')=? ORDER BY rowid", (late[0]["id"],)
        ).fetchall()
        payload = c.execute("SELECT payload_json FROM superagent_triggers WHERE id=?", (late[0]["id"],)).fetchone()[0]
    assert ''.join(json.loads(page[0])["text"] for page in pages) == payload


@pytest.mark.parametrize("arrival", ["inference", "tool_execution"])
def test_late_thread_notice_waits_for_complete_multi_tool_batch(continuation, monkeypatch, arrival):
    store, manager, model, run, trigger, events = continuation
    before = authority_snapshot(store, manager, run)
    scope = before[1][0]["resource_scope"]
    child = Path(scope) / "batch-child"
    child.mkdir()
    batch = [
        {"id": "batch-list-root", "name": "directory_list", "arguments": {"path": scope}},
        {"id": "batch-list-child", "name": "directory_list", "arguments": {"path": str(child)}},
    ]
    model.responses.append(response("Inspect both directories", calls=batch))
    original_generate = model.generate
    original_execute = manager.tools.execute
    original_checkpoint = manager._checkpoint
    late, executions, checkpoints, offsets, batch_roles = [], [], [], [], []
    callback_errors = []

    def receive_late():
        late.append(events.receive("continuation", "multi-tool-late", "same-task", {
            "text": "late requirement😊" * 300, "requirement": "retain the final suffix",
        }))
        model.responses.extend([
            response(delivery("charged draft without reading either source")),
            *reads(trigger, ordinal_start=100),
            *reads(late[0], ordinal_start=200),
            response(delivery("Both original and late sources fully read")),
        ])

    def observe_checkpoint(run_id, token):
        result = original_checkpoint(run_id, token)
        if late and result[2]:
            with store.connection() as c:
                inbox = c.execute(
                    "SELECT consumed FROM worker_inbox WHERE run_id=? "
                    "AND json_extract(payload_json,'$.trigger_id')=?",
                    (run_id, late[0]["id"]),
                ).fetchone()
                delivered = c.execute(
                    "SELECT COUNT(*) FROM superagent_trigger_deliveries "
                    "WHERE run_id=? AND trigger_id=?", (run_id, late[0]["id"]),
                ).fetchone()[0]
            checkpoints.append((delivered, inbox[0] if inbox else None))
        return result

    async def execute_real_tool(**kwargs):
        executions.append((kwargs["tool_name"], kwargs["parameters"]["path"]))
        if arrival == "tool_execution" and len(executions) == 1:
            with store.connection() as c:
                assert c.execute(
                    "SELECT state FROM worker_tool_calls WHERE call_id=?", (kwargs["call_id"],)
                ).fetchone()[0] == "running"
            receive_late()
        return await original_execute(**kwargs)

    def current_pending_in_request(request):
        # Current Core metadata is prepended; a rejected draft also leaves
        # historical boundary feedback later in the transcript. Do not read
        # that stale snapshot when checking the next turn's durable read debt.
        content = next(message["content"] for message in request.messages
                       if message.get("content", "").startswith("Core delivery boundary:"))
        return json.loads(content[content.index('{"omitted":'):])

    async def inspect_request(request):
        step = len(model.requests)
        if step == 0 and arrival == "inference":
            # Commit while this scripted inference coroutine is still awaiting.
            async def arrive():
                await asyncio.sleep(0)
                receive_late()
            await asyncio.create_task(arrive())
            assert late[0]["id"] not in json.dumps(request.messages)
        if step == 1:
            messages = request.messages
            start = next(i for i, message in enumerate(messages)
                         if message.get("role") == "assistant" and message.get("tool_calls"))
            exchange = messages[start:start + 3]
            batch_roles.extend(message["role"] for message in messages[start:])
            assert [message["role"] for message in exchange] == ["assistant", "tool", "tool"], (
                "Thread user notice split an unsettled assistant/tool batch: "
                + repr([message["role"] for message in messages[start:]])
            )
            assert [call["id"] for call in exchange[0]["tool_calls"]] == [
                call["id"] for call in batch
            ]
            assert [message["tool_call_id"] for message in exchange[1:]] == [
                call["id"] for call in batch
            ]
            notices = [i for i, message in enumerate(messages)
                       if message.get("role") == "user"
                       and message.get("content", "").startswith("Core-delivered untrusted Thread")
                       and late[0]["id"] in message["content"]]
            assert len(notices) == 1 and notices[0] > start + 2
            assert checkpoints and all(item == (1, 0) for item in checkpoints)
            assert Counter(executions) == Counter([
                ("directory_list", scope), ("directory_list", str(child)),
            ])
            with store.connection() as c:
                assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
            pending = current_pending_in_request(request)["pending"]
            assert {item["trigger_id"] for item in pending} == {trigger["id"], late[0]["id"]}
            assert all(item["next_offset_chars"] == 0 for item in pending)
        if step == 2:
            # Consuming the notice and charging a delivery draft must NOT satisfy the read gate.
            assert {item["trigger_id"] for item in debt(store, run)} == {
                trigger["id"], late[0]["id"],
            }
            with store.connection() as c:
                assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 0
        if late and step >= 2 and debt(store, run):
            assert current_pending_in_request(request) == {"pending": debt(store, run), "omitted": 0}
            offsets.extend(item["next_offset_chars"] for item in debt(store, run)
                           if item["trigger_id"] == late[0]["id"])
        return await original_generate(request)

    async def inspect_without_swallowing_assertions(request):
        try:
            return await inspect_request(request)
        except Exception as exc:
            # Worker masks model errors; preserve test failures for the pytest traceback.
            callback_errors.append(exc)
            raise

    monkeypatch.setattr(manager, "_checkpoint", observe_checkpoint)
    monkeypatch.setattr(manager.tools, "execute", execute_real_tool)
    monkeypatch.setattr(model, "generate", inspect_without_swallowing_assertions)
    result = asyncio.run(manager.run(run["id"]))
    if callback_errors:
        raise callback_errors[0]
    assert batch_roles[:3] == ["assistant", "tool", "tool"], (
        "Thread user notice split an unsettled assistant/tool batch: " + repr(batch_roles)
    )
    assert result["state"] == "review_pending" and result["repairs"] == 0
    pages = len(reads(trigger)) + len(reads(late[0]))
    assert result["steps"] == len(model.requests) == pages + 3
    assert result["tool_spent"] == pages + 2
    assert result["token_spent"] == 40 * len(model.requests)
    assert authority_snapshot(store, manager, run) == before
    assert not debt(store, run)
    assert Counter(executions) == Counter([
        ("directory_list", scope), ("directory_list", str(child)),
    ])
    assert 0 in offsets and max(offsets) == (len(reads(late[0])) - 1) * 2048
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 1
        for source in (trigger, late[0]):
            body = c.execute("SELECT payload_json FROM superagent_triggers WHERE id=?",
                             (source["id"],)).fetchone()[0]
            actual = c.execute(
                "SELECT result_json FROM worker_tool_calls WHERE tool_name='read_trigger' "
                "AND json_extract(arguments_json,'$.trigger_id')=? ORDER BY rowid", (source["id"],),
            ).fetchall()
            assert ''.join(json.loads(page[0])["text"] for page in actual) == body


@pytest.mark.parametrize("tamper", ["payload", "inbox", "event", "revoked"])
def test_first_round_source_tamper_blocks_without_model_request(continuation, tamper):
    store, manager, model, run, trigger, _events = continuation
    before = authority_snapshot(store, manager, run)
    with store.unit_of_work() as work:
        c = work.connection
        if tamper == "payload":
            c.execute("UPDATE superagent_triggers SET payload_json='{}' WHERE id=?", (trigger["id"],))
        elif tamper == "inbox":
            c.execute("DELETE FROM worker_inbox")
        elif tamper == "event":
            c.execute("UPDATE events SET payload='{}' WHERE event_type='trigger.received'")
        else:
            c.execute("INSERT INTO revoked_memory_sources VALUES (?)", ("trigger:" + trigger["id"],))
    result = asyncio.run(manager.run(run["id"]))
    assert result["state"] == "blocked"
    assert not model.requests
    assert result["steps"] == result["tool_spent"] == result["token_spent"] == 0
    assert authority_snapshot(store, manager, run) == before
    with store.connection() as c:
        payload = c.execute("SELECT payload FROM events WHERE event_type='worker.blocked'").fetchone()[0]
        assert json.loads(payload)["reason_code"] == "continuation_source_unavailable"
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


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
