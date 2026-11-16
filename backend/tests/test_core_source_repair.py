"""Mock model mistakes, actual governed outputs and Core no-effect source proofs."""

import asyncio
import json

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.context_service import ContextService
from forge_core.domain import DomainError
from forge_core.independent_review import _review_receipts
from forge_core.worker_recovery import settled_preexecution_rejection, unresolved_calls


def archived_worker(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write"])
    with store.unit_of_work() as work:
        for index in range(4):
            message = {
                "role": "assistant",
                "content": "PRIVATE_ARCHIVE_SENTINEL " + "x" * (8000 if index < 2 else 10),
            }
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (worker["id"], index + 2, "assistant", json.dumps(message)),
            )
    ContextService(store).build_worker_context(worker["id"], input_budget=6000)
    with store.connection() as c:
        assert c.execute(
            "SELECT 1 FROM context_archive_sources WHERE scope='worker' AND scope_id=?",
            (worker["id"],),
        ).fetchone()
    return store, manager, model, worker


def test_unarchived_ordinal_rejection_repairs_to_real_write_without_source_leak(tmp_path):
    store, manager, model, worker = archived_worker(tmp_path)
    model.responses[:] = [
        tool("worker_read_context_source", {"ordinal": 999}),
        tool("file_write", {"path": "work/result.json", "content": '{"actual":true}'}, 2),
        response(delivery()),
    ]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert (tmp_path / "work/result.json").read_text() == '{"actual":true}'
    with store.connection() as c:
        call = c.execute("SELECT * FROM worker_tool_calls ORDER BY rowid LIMIT 1").fetchone()
        assert call["state"] == "failed" and call["receipt_id"] is None
        assert settled_preexecution_rejection(c, worker["id"], call)
        assert not unresolved_calls(c, worker["id"])
        output = json.loads(call["result_json"])["output"]
        assert output["error_code"] == "source_read_rejected" and output["available_archive_refs"]
        assert 999 not in output["available_archive_refs"]
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
        receipts, _ = _review_receipts(store, c, c.execute("SELECT * FROM worker_runs").fetchone())
        assert (
            receipts[0]["state"] == "rejected_before_execution"
            and receipts[0]["receipt_id"] is None
        )
    feedback = json.loads(model.requests[1].messages[-1]["content"])
    assert feedback["repair_request"]["reason_code"] == "source_read_rejected"
    assert "PRIVATE_ARCHIVE_SENTINEL" not in json.dumps(feedback)


def test_repeated_unarchived_ordinal_errors_stop_after_existing_two_repairs(tmp_path):
    store, manager, model, worker = archived_worker(tmp_path)
    model.responses[:] = [
        tool("worker_read_context_source", {"ordinal": 999}, index) for index in range(1, 4)
    ]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == manager.max_repairs == 2
    assert len(model.requests) == 3 and result["tool_spent"] == 3
    with store.connection() as c:
        calls = c.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        assert [settled_preexecution_rejection(c, worker["id"], call) for call in calls] == [
            True,
            True,
            False,
        ]
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("unknown", ["intent", "compaction"])
def test_unknown_tool_or_compaction_prevents_core_source_repair(tmp_path, monkeypatch, unknown):
    import forge_core.worker_runtime as runtime

    store, manager, model, worker = archived_worker(tmp_path)
    model.responses[:] = [tool("worker_read_context_source", {"ordinal": 999})]

    def rejected(_store, run_id, _arguments, *, connection=None):
        assert connection is None
        with store.unit_of_work() as work:
            if unknown == "intent":
                work.connection.execute(
                    """INSERT INTO action_intents
                    SELECT 'unknown','unknown',quest_id,worker_id,id,plan_id,plan_revision,
                    'file_write','{}','fixture','fixture','R1',NULL,execution_mode,
                    'uncertain','fixture','fixture' FROM worker_runs WHERE id=?""",
                    (run_id,),
                )
            else:
                work.connection.execute(
                    """INSERT INTO worker_model_reservations
                    (call_id,run_id,generation,lease_token,provider_id,model_id,execution_mode,
                    context_json,reserved_tokens,output_limit,cost_scopes_json,state,created_at)
                    SELECT 'unknown-compaction',id,generation,lease_token,provider_id,model_id,
                    execution_mode,'{"purpose":"context_compaction"}',100,10,'[]','reserved','fixture'
                    FROM worker_runs WHERE id=?""",
                    (run_id,),
                )
        raise DomainError("Never echo private source text")

    monkeypatch.setattr(runtime, "_read_worker_source_slice", rejected)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == 0 and len(model.requests) == 1
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
        assert not c.execute(
            "SELECT 1 FROM events WHERE event_type='worker.progress' AND json_extract(payload,'$.phase')='self_repair'"
        ).fetchone()


def test_core_source_rejection_proof_rejects_changed_archive_hint(tmp_path):
    store, manager, model, worker = archived_worker(tmp_path)
    model.responses[:] = [
        tool("worker_read_context_source", {"ordinal": 999}),
        response(delivery()),
    ]
    assert asyncio.run(manager.run(worker["id"]))["repairs"] == 1
    with store.unit_of_work() as work:
        c = work.connection
        call = c.execute("SELECT * FROM worker_tool_calls").fetchone()
        assert settled_preexecution_rejection(c, worker["id"], call)
        changed = json.loads(call["result_json"])
        changed["output"]["available_archive_refs"] = [999]
        c.execute("UPDATE worker_tool_calls SET result_json=?", (json.dumps(changed),))
        assert not settled_preexecution_rejection(
            c, worker["id"], c.execute("SELECT * FROM worker_tool_calls").fetchone()
        )
