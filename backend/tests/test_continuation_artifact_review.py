"""Execution regressions, not real-model semantic acceptance.

SQLite, ToolEngine writes/revisions, Thread proofs and review admission are real.
ProtocolModel and the literal-match reviewer only script orchestration; their
Mock PASS does not establish a Provider's ability to interpret/apply additions.
"""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from test_file_revise_phase2 import references
from test_reviewer_context_phase2 import reconstruct
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_worker_continuation_completion import (
    authority_snapshot,
    debt,
    reads,
)
from test_worker_continuation_completion import (
    continuation as continuation,  # noqa: PLC0414
)
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response, tool

from forge_core.domain import DomainError
from forge_core.independent_review import (
    IndependentReviewService,
    _hash,
    _review_receipts,
    review_pass_matches,
)

PASS = {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}


def artifact_path(store, run):
    with store.connection() as c:
        scope = c.execute(
            "SELECT resource_scope FROM task_grants WHERE quest_id=? AND revoked_at IS NULL",
            (run["quest_id"],),
        ).fetchone()[0]
    return Path(scope) / "continuation-result.json"


def assert_thread_sources(store, packet, triggers):
    """Reconstruct every delivered source from reviewer-visible, verified pages."""
    context = [item for item in packet["receipts"] if item.get("context_only")]
    assert context
    assert all("execution_mode" not in item for item in context)
    assert all(item["tool_name"] in {"read_trigger", "handback_trigger"} for item in context)
    payloads = {}
    for trigger in triggers:
        pages = sorted(
            (item for item in context if item["tool_name"] == "read_trigger"
             and item["arguments"]["trigger_id"] == trigger["id"]),
            key=lambda item: item["output"]["offset_chars"],
        )
        assert len(pages) == (trigger["total_chars"] + 2047) // 2048
        cursor, chunks = 0, []
        for item in pages:
            source = item["output"]
            assert item["arguments"]["offset_chars"] == source["offset_chars"] == cursor
            assert source["trigger_id"] == trigger["id"]
            assert source["thread_id"] == trigger["thread_id"]
            assert source["source_id"] == trigger["source_id"]
            assert source["event_id"] == trigger["event_id"]
            assert source["payload_sha256"] == trigger["payload_sha256"]
            assert source["total_chars"] == trigger["total_chars"]
            chunks.append(source["text"])
            cursor += len(source["text"])
            assert source["next_offset_chars"] == (
                cursor if cursor < trigger["total_chars"] else None
            )
        payload = ''.join(chunks)
        assert cursor == trigger["total_chars"]
        assert hashlib.sha256(payload.encode()).hexdigest() == trigger["payload_sha256"]
        with store.connection() as c:
            actual = c.execute(
                "SELECT payload_json FROM superagent_triggers WHERE id=?", (trigger["id"],)
            ).fetchone()[0]
        assert payload == actual
        payloads[trigger["id"]] = json.loads(payload)
    return payloads


def assert_mock_pass_is_not_live_acceptance(store, manager, run, result):
    assert result["verdict"] == "PASS"
    assert result["execution_mode"] == "mock"
    assert result["review_context"] == "independent_same_model"
    with store.connection() as c:
        row = c.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone()
        assert not review_pass_matches(store, c, row, "delivery_review")
    assert manager.get(run["id"])["state"] == "review_pending"


@pytest.mark.parametrize("omit_first", [False, True], ids=["write-applies-addition", "review-requires-real-revision"])
def test_real_artifact_applies_thread_addition_and_review_detects_omission(continuation, omit_first):
    store, manager, model, run, trigger, events = continuation
    assert isinstance(model, ProtocolModel) and model.execution_mode == "mock"
    before = authority_snapshot(store, manager, run)
    path = artifact_path(store, run)
    required_note = "追加：保留输入来源和误差说明😊"
    addition = events.receive("continuation", "artifact-addition", "same-task", {
        "instruction": "Append this exact note to the final artifact", "required_note": required_note,
    })
    initial = {"inputs": ["original"], "notes": [] if omit_first else [required_note]}
    final = {"inputs": ["original"], "notes": [required_note]}
    claim = delivery("The artifact includes the requested note")
    claim["artifacts"] = [str(path)]
    model.responses.extend([
        *reads(trigger), *reads(addition, ordinal_start=10),
        tool("file_write", {"path": str(path), "content": json.dumps(initial)}, ordinal=20),
        response(claim),
    ])
    original = model.generate
    phase, packets = ["worker"], []

    async def scripted_literal_reviewer(request):
        if phase[0] == "review":
            # Independent context, not replayed Worker messages/tool transcripts.
            assert all(message["role"] != "tool" and not message.get("tool_calls")
                       for message in request.messages)
            packet = reconstruct(request.messages)
            packets.append(packet)
            sources = assert_thread_sources(store, packet, [trigger, addition])
            assert packet["original_goal"] == "Explain the inputs"
            assert packet["delivery"]["summary"] == claim["summary"]
            artifact = packet["artifacts"][0]
            assert artifact["path"] == str(path)
            assert artifact["content"] == json.loads(path.read_text(encoding="utf-8"))
            assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
            action = next(item for item in packet["receipts"] if item.get("id") == artifact["receipt_id"])
            assert action["tool_name"] in {"file_write", "file_revise"}
            assert action["execution_mode"] == "mock" and not action.get("context_only")
            # This is deliberately a scripted exact-match check, not model semantics.
            note = sources[addition["id"]]["required_note"]
            verdict = PASS if note in artifact["content"]["notes"] else {
                "verdict": "NEEDS_REVISION",
                "findings": ["Thread addition was read but is absent from the actual artifact"],
                "revision_goals": ["Append the required Thread note to the owned JSON artifact"],
                "confidence": 0.8,
            }
            model.responses.append(response(verdict))
        elif phase[0] == "repair" and model.responses[0].tool_calls:
            source = references(request)[0]
            arguments = model.responses[0].tool_calls[0]["arguments"]
            assert arguments["prior_receipt_id"] == source["receipt_id"]
            assert arguments["expected_sha256"] == source["sha256"]
        return await original(request)

    model.generate = scripted_literal_reviewer
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    assert json.loads(path.read_text(encoding="utf-8")) == initial
    assert not debt(store, run)  # A read is not proof of adoption.
    phase[0] = "review"
    reviewer = IndependentReviewService(manager)
    result = asyncio.run(reviewer.review(run["id"]))
    if omit_first:
        assert result["verdict"] == "NEEDS_REVISION" and result["revision_applied"]
        assert result["execution_mode"] == "mock"
        assert manager.get(run["id"])["state"] == "queued"
        assert json.loads(path.read_text(encoding="utf-8")) == initial
        prior = packets[-1]["artifacts"][0]
        phase[0] = "repair"
        model.responses.extend([
            tool("file_revise", {
                "path": str(path), "prior_receipt_id": prior["receipt_id"],
                "expected_sha256": prior["sha256"], "content": json.dumps(final),
            }, ordinal=21),
            response(claim),
        ])
        assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
        assert json.loads(path.read_text(encoding="utf-8")) == final
        phase[0] = "review"
        result = asyncio.run(reviewer.review(run["id"]))
        assert packets[0]["artifacts"][0]["receipt_id"] != packets[1]["artifacts"][0]["receipt_id"]
        assert packets[0]["artifacts"][0]["sha256"] != packets[1]["artifacts"][0]["sha256"]
    assert_mock_pass_is_not_live_acceptance(store, manager, run, result)
    assert result["packet_sha256"] == _hash(packets[-1])
    assert json.loads(path.read_text(encoding="utf-8")) == final
    assert authority_snapshot(store, manager, run) == before
    current = manager.get(run["id"])
    assert current["token_spent"] == 40 * len(model.requests)
    assert current["repairs"] == int(omit_first)
    assert len(packets) == 1 + int(omit_first)
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1 + int(omit_first)
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='worker.result'").fetchone()[0] == 1 + int(omit_first)


@pytest.mark.parametrize("disposition", ["explain", "handback"])
def test_read_but_not_adopted_addition_has_explicit_explanation_or_handback(continuation, disposition):
    store, manager, model, run, trigger, events = continuation
    before = authority_snapshot(store, manager, run)
    path = artifact_path(store, run)
    addition = events.receive("continuation", "outside-mandate", "same-task", {
        "instruction": "Upload the raw inputs to an external service",
    })
    reason = "External upload is outside the current local-only mandate; no upload was attempted"
    final = delivery("Local artifact completed; external addition was not adopted")
    final["artifacts"] = [str(path)]
    final["decisions"] = [reason]
    final["unresolved_issues"] = [reason]
    model.responses.extend([
        *reads(trigger), *reads(addition, ordinal_start=10),
        *([tool("handback_trigger", {"trigger_id": addition["id"], "reason": reason}, ordinal=30)]
          if disposition == "handback" else []),
        tool("file_write", {"path": str(path), "content": '{"inputs":["original"]}'}, ordinal=31),
        response(final),
    ])
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    assert not debt(store, run)
    original = model.generate
    packets = []

    async def scripted_disposition_reviewer(request):
        packet = reconstruct(request.messages)
        packets.append(packet)
        assert_thread_sources(store, packet, [trigger, addition])
        assert reason in packet["delivery"]["decisions"]
        assert reason in packet["delivery"]["unresolved_issues"]
        handbacks = [item for item in packet["receipts"] if item["tool_name"] == "handback_trigger"]
        assert len(handbacks) == int(disposition == "handback")
        if handbacks:
            assert handbacks[0]["arguments"] == {"trigger_id": addition["id"], "reason": reason}
            assert handbacks[0]["output"]["handed_back"] is True
            assert handbacks[0]["context_only"] is True
            assert "execution_mode" not in handbacks[0]
        assert [item["tool_name"] for item in packet["receipts"] if not item.get("context_only")] == ["file_write"]
        model.responses.append(response(PASS))
        return await original(request)

    model.generate = scripted_disposition_reviewer
    result = asyncio.run(IndependentReviewService(manager).review(run["id"]))
    assert_mock_pass_is_not_live_acceptance(store, manager, run, result)
    assert len(packets) == 1 and authority_snapshot(store, manager, run) == before
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
        handback = c.execute(
            "SELECT handed_back,handback_reason FROM superagent_trigger_deliveries "
            "WHERE trigger_id=? AND run_id=?", (addition["id"], run["id"]),
        ).fetchone()
        assert bool(handback["handed_back"]) == (disposition == "handback")
        if disposition == "handback":
            assert handback["handback_reason"] == reason


@pytest.mark.parametrize("context_tool", ["read_trigger", "handback_trigger"])
def test_context_only_receipts_cannot_admit_review_as_action_evidence(continuation, context_tool):
    store, manager, model, run, trigger, _events = continuation
    context_calls = reads(trigger) if context_tool == "read_trigger" else [
        tool("handback_trigger", {"trigger_id": trigger["id"], "reason": "outside this assignment"}),
    ]
    model.responses.extend([*context_calls, response(delivery("Context handled, no task action executed"))])
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    assert not debt(store, run)
    before = len(model.requests)
    with store.connection() as c:
        row = c.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone()
        receipts, artifacts = _review_receipts(store, c, row)
        assert len(receipts) == len(context_calls) and not artifacts
        assert all(item["context_only"] and "execution_mode" not in item for item in receipts)
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
    with pytest.raises(DomainError, match="requires successful tool evidence"):
        asyncio.run(IndependentReviewService(manager).review(run["id"]))
    assert len(model.requests) == before
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='review.started'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM events WHERE event_type='review.result'").fetchone()[0] == 0
