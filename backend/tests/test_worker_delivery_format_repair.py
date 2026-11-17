"""Synthetic final-reply correction never edits an already written artifact."""

import asyncio
import json

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.context_service import ContextService
from forge_core.domain import DomainError
from forge_core.independent_review import IndependentReviewService
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_delivery import commit_worker_response, reply_format_repair_active
from forge_core.worker_runtime import WorkerManager


@pytest.mark.parametrize("malformed", ["schema", "syntax"])
@pytest.mark.parametrize("attempt_write", [False, True])
def test_final_format_repair_preserves_artifact(tmp_path, malformed, attempt_write):
    final = delivery("Wrote hello and read back the matching bytes; review pending.")
    bad = {**final, "status": "done"} if malformed == "schema" else "{not JSON"
    replies = [
        tool("file_write", {"path": "work/result.txt", "content": "hello"}),
        tool("file_read", {"path": "work/result.txt"}, ordinal=2),
        response(bad),
    ]
    if attempt_write:
        replies.append(
            tool("file_write", {"path": "work/result.txt", "content": "WRONG"}, ordinal=3)
        )
    else:
        replies.append(response(final))
    store, manager, model, worker = prepared(tmp_path, replies, tools=["file_write", "file_read"])
    result = asyncio.run(manager.run(worker["id"]))
    assert (tmp_path / "work/result.txt").read_bytes() == b"hello"
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 2
    request = model.requests[-1]
    assert not any(t["name"] in {"file_write", "file_revise"} for t in request.tools)
    repair = next(
        json.loads(m["content"])["repair_request"]
        for m in reversed(request.messages)
        if m["role"] == "user" and '"repair_request"' in m.get("content", "")
    )
    assert "reply only" in repair["instruction"]
    assert result["state"] == ("blocked" if attempt_write else "review_pending")


@pytest.mark.parametrize("new_inbox", [False, True])
def test_format_repair_reopens_and_passive_context_does_not_restore_writes(
    tmp_path, monkeypatch, new_inbox
):
    final = delivery("Original source inspected; review pending.")
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            response({**final, "status": "done"}),
        ],
        tools=["file_read", "file_write"],
    )
    original_repair = manager._repair

    def pause_after_repair(*args, **kwargs):
        result = original_repair(*args, **kwargs)
        manager.control(worker["id"], "pause")
        return result

    monkeypatch.setattr(manager, "_repair", pause_after_repair)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "paused"
    reopened = Store(store.path, workspace=store.workspace)
    with reopened.connection() as c:
        assert c.execute("SELECT next_action FROM worker_capsules").fetchone()[0] == "format_reply"
    if new_inbox:
        with reopened.unit_of_work() as work:
            work.connection.execute(
                "INSERT INTO worker_inbox VALUES (?,?,?,0)",
                (
                    worker["id"],
                    "followup",
                    json.dumps({"kind": "task_revision", "note": "Reassess original scope"}),
                ),
            )
    model.responses.append(response(final))
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    resumed.control(worker["id"], "resume")
    assert asyncio.run(resumed.run(worker["id"]))["state"] == "review_pending"
    assert "file_write" not in {t["name"] for t in model.requests[-1].tools}
    with reopened.connection() as c:
        assert c.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
        assert c.execute("SELECT next_action FROM worker_capsules").fetchone()[0] == "review"


def test_format_repair_can_recall_exact_archived_source_without_restoring_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    first = tool("file_read", {"path": "work/input.txt"})
    first.text = "Synthetic historical explanation " + "x" * 20000
    final = delivery("Original source inspected; review pending.")
    store, manager, model, worker = prepared(
        tmp_path, [first, response({**final, "status": "done"})], tools=["file_read", "file_write"]
    )
    original_repair = manager._repair

    def pause_after_repair(*args, **kwargs):
        result = original_repair(*args, **kwargs)
        manager.control(worker["id"], "pause")
        return result

    monkeypatch.setattr(manager, "_repair", pause_after_repair)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "paused"
    service = ContextService(store)
    service.build_worker_context(worker["id"], input_budget=10000, source_readback_available=True)
    original_source = service.read_source("worker", worker["id"], 2)["message_json"]
    model.responses.extend(
        [
            tool("worker_read_context_source", {"ordinal": 2, "max_chars": 100}, ordinal=2),
            response(final),
        ]
    )
    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    resumed.control(worker["id"], "resume")
    assert asyncio.run(resumed.run(worker["id"]))["state"] == "review_pending"
    for request in model.requests[-2:]:
        assert "file_write" not in {t["name"] for t in request.tools}
        assert "worker_read_context_source" in {t["name"] for t in request.tools}
    observed = [
        json.loads(m["content"]) for m in model.requests[-1].messages if m["role"] == "tool"
    ][-1]
    assert observed["source_id"] == 2 and observed["content"] == original_source[:100]
    assert (
        ContextService(reopened).read_source("worker", worker["id"], 2)["message_json"]
        == original_source
    )
    with reopened.connection() as c:
        assert c.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1


def test_incomplete_early_answer_without_successful_tools_keeps_regular_repair(tmp_path):
    _store, manager, model, worker = prepared(
        tmp_path,
        [
            response("{bad"),
            tool("file_read", {"path": "work/input.txt"}),
            response(delivery()),
        ],
    )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert "file_read" in {t["name"] for t in model.requests[1].tools}


def test_independent_review_revision_restores_original_tools_after_format_correction(tmp_path):
    final = delivery()
    _store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            response({**final, "status": "done"}),
            response(final),
            response(
                {
                    "verdict": "NEEDS_REVISION",
                    "findings": ["Need original detail"],
                    "revision_goals": ["Inspect the source detail"],
                    "confidence": 0.7,
                }
            ),
            tool("file_read", {"path": "work/input.txt"}, ordinal=2),
            response(final),
        ],
    )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert manager.get(worker["id"])["state"] == "queued"
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert "file_read" in {t["name"] for t in model.requests[-2].tools}


def test_commit_boundary_rejects_mutation_even_with_stale_original_allowed_list(tmp_path):
    store, manager, _, worker = prepared(tmp_path, [], tools=["file_write"])
    token = manager._claim(worker["id"])
    # Core-owned persistent format state; model-supplied text cannot set it.
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_capsules SET next_action='format_reply' WHERE run_id=?", (worker["id"],)
        )
    with pytest.raises(DomainError, match="cannot admit action tools"):
        commit_worker_response(
            manager,
            run_id=worker["id"],
            token=token,
            row=manager.get(worker["id"]),
            calls=[
                {
                    "id": "unexpected-write",
                    "name": "file_write",
                    "arguments": {"path": "work/output.txt", "content": "WRONG"},
                }
            ],
            allowed=["file_write"],
            text="",
            final_value=None,
        )
    assert not (tmp_path / "work/output.txt").exists()
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0


@pytest.mark.parametrize("attempt_write", [False, True])
def test_passive_inbox_during_corrected_final_preserves_format_restriction(tmp_path, attempt_write):
    final = delivery()
    replies = [
        tool("file_read", {"path": "work/input.txt"}),
        response({**final, "status": "done"}),
        response(final),
    ]
    replies.append(
        tool("file_write", {"path": "work/accidental.txt", "content": "WRONG"}, ordinal=2)
        if attempt_write
        else response(final)
    )
    store, manager, model, worker = prepared(tmp_path, replies, tools=["file_read", "file_write"])
    generate = model.generate

    async def with_inbox(request):
        result = await generate(request)
        if len(model.requests) == 3:
            with store.unit_of_work() as work:
                # A self-declared revision in untrusted context is not authority.
                work.connection.execute(
                    "INSERT INTO worker_inbox VALUES (?,?,?,0)",
                    (
                        worker["id"],
                        "passive-update",
                        json.dumps({"kind": "task_revision", "progress": "more context"}),
                    ),
                )
        return result

    model.generate = with_inbox
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == ("blocked" if attempt_write else "review_pending")
    assert len(model.requests) == 4
    assert "more context" in json.dumps(model.requests[-1].messages)
    assert "file_write" not in {t["name"] for t in model.requests[-1].tools}
    assert not (tmp_path / "work/accidental.txt").exists()
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 1


def test_missing_format_capsule_fails_deliberately(tmp_path):
    store, _, _, _ = prepared(tmp_path, [])
    with store.connection() as c, pytest.raises(DomainError, match="Worker capsule missing"):
        reply_format_repair_active(c, "missing-run")
