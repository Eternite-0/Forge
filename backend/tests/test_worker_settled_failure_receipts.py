"""Offline reasoning with real governed failures; no Provider is called."""

import asyncio
import hashlib
import json

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.domain import DomainError
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_recovery import settled_read_failure
from forge_core.worker_runtime import WorkerManager


@pytest.mark.parametrize("bound", ["no_repairs", "repairs_spent", "steps_spent"])
@pytest.mark.parametrize("name", ["directory_list", "file_read"])
def test_settled_local_failure_keeps_receipt_when_correction_is_exhausted(tmp_path, bound, name):
    store, manager, model, worker = prepared(
        tmp_path,
        [tool(name, {"path": "work/missing"})],
        tools=[name],
        max_repairs=0 if bound == "no_repairs" else 2,
    )
    with store.unit_of_work() as work:
        if bound == "repairs_spent":
            work.connection.execute("UPDATE worker_runs SET repairs=2 WHERE id=?", (worker["id"],))
        elif bound == "steps_spent":
            work.connection.execute(
                "UPDATE worker_runs SET steps=step_limit-1 WHERE id=?", (worker["id"],)
            )

    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == "blocked" and outcome["tool_spent"] == 1
    assert outcome["repairs"] == (2 if bound == "repairs_spent" else 0)
    assert outcome["lease_token"] is None and outcome["lease_until"] is None
    assert len(model.requests) == 1 and outcome["token_spent"] == 40
    with store.connection() as connection:
        call = connection.execute("SELECT * FROM worker_tool_calls").fetchone()
        receipt = connection.execute("SELECT * FROM action_receipts").fetchone()
        assert call["state"] == "failed" and call["receipt_id"] == receipt["id"]
        result = json.loads(call["result_json"])
        assert result["state"] == "failed" and result["output"] == json.loads(receipt["result_json"])
        assert settled_read_failure(connection, worker["id"], call, require_repair=False)
        assert not settled_read_failure(connection, worker["id"], call)
        assert connection.execute("SELECT state FROM action_intents").fetchone()[0] == "failed"
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE json_extract(payload,'$.phase')='self_repair'"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT status FROM workers").fetchone()[0] == "blocked"
        assert connection.execute("SELECT status FROM task_nodes").fetchone()[0] == "blocked"
        persisted_call = dict(call)
        events_before = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
        max_repairs=manager.max_repairs,
    )
    assert asyncio.run(resumed.run(worker["id"]))["state"] == "blocked"
    with pytest.raises(DomainError, match="Unresolved tool result"):
        resumed.retry(worker["id"])
    with reopened.connection() as connection:
        assert dict(connection.execute("SELECT * FROM worker_tool_calls").fetchone()) == persisted_call
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
    assert len(model.requests) == 1


def test_unknown_action_keeps_original_intent_and_never_replays_after_reopen(tmp_path, monkeypatch):
    store, manager, model, worker = prepared(
        tmp_path,
        [tool("numeric_ast", {"source": "result = 1", "inputs": {}})],
        tools=["numeric_ast"],
        max_repairs=0,
    )
    effects = []

    def unknown(intent, *_arguments):
        effects.append(intent["id"])
        raise RuntimeError("Unknown subprocess outcome")

    monkeypatch.setattr(manager.tools, "_perform", unknown)
    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == "blocked" and outcome["repairs"] == 0
    assert len(effects) == len(model.requests) == 1
    with store.connection() as connection:
        intent = dict(connection.execute("SELECT * FROM action_intents").fetchone())
        call = dict(connection.execute("SELECT * FROM worker_tool_calls").fetchone())
        assert intent["state"] == call["state"] == "uncertain"
        assert call["receipt_id"] is None and call["result_json"] is None
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0

    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
        max_repairs=0,
    )
    assert asyncio.run(resumed.run(worker["id"]))["state"] == "blocked"
    with pytest.raises(DomainError, match="Unresolved tool result"):
        resumed.retry(worker["id"])
    with reopened.connection() as connection:
        assert dict(connection.execute("SELECT * FROM action_intents").fetchone()) == intent
        assert dict(connection.execute("SELECT * FROM worker_tool_calls").fetchone()) == call
    assert len(effects) == len(model.requests) == 1


def test_settled_failure_still_supports_original_bounded_correction(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/missing"}),
            tool("file_read", {"path": "work/input.txt"}, 2),
            response(delivery()),
        ],
    )
    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == "review_pending" and outcome["repairs"] == 1
    with store.connection() as connection:
        calls = connection.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        assert [call["state"] for call in calls] == ["failed", "succeeded"]
        assert settled_read_failure(connection, worker["id"], calls[0])
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
    assert len(model.requests) == 3


@pytest.mark.parametrize("tamper", ["receipt_id", "intent_id", "call_id", "output", "state"])
def test_unbound_or_uncertain_failure_never_becomes_a_settled_call(tmp_path, monkeypatch, tamper):
    store, manager, model, worker = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/missing"})],
        max_repairs=0,
    )
    execute = manager.tools.execute

    async def changed(**arguments):
        result = await execute(**arguments)
        assert result["state"] == "failed"
        result[tamper] = {"error_code": "invented"} if tamper == "output" else (
            "uncertain" if tamper == "state" else "invented"
        )
        return result

    monkeypatch.setattr(manager.tools, "execute", changed)
    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == "blocked" and outcome["repairs"] == 0
    assert len(model.requests) == 1
    with store.connection() as connection:
        call = connection.execute("SELECT * FROM worker_tool_calls").fetchone()
        assert call["state"] == "uncertain"
        assert call["receipt_id"] is None and call["result_json"] is None
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1


@pytest.mark.parametrize(
    "corruption",
    [
        "result_digest",
        "invalid_result_json",
        "parameters",
        "invalid_parameters_json",
        "noncanonical_parameters",
        "parameter_digest",
        "foreign_run",
        "plan_revision",
        "intent_mode",
        "receipt_mode",
        "unknown_intent",
    ],
)
def test_corrupt_storage_cannot_bind_failure_or_leave_running_worker(tmp_path, monkeypatch, corruption):
    store, manager, model, worker = prepared(
        tmp_path, [tool("file_read", {"path": "work/missing"})], max_repairs=0
    )
    execute = manager.tools.execute

    async def corrupted(**arguments):
        result = await execute(**arguments)
        assert result["state"] == "failed"
        # Deliberately simulate corrupt storage beyond the normal immutable
        # binding guards, following the existing artifact readback tests.
        with store.unit_of_work() as work:
            connection = work.connection
            connection.execute("DROP TRIGGER action_receipts_immutable_update")
            connection.execute("DROP TRIGGER action_intents_immutable_binding")
            if corruption == "result_digest":
                connection.execute("UPDATE action_receipts SET result_digest=?", ("0" * 64,))
            elif corruption == "invalid_result_json":
                connection.execute(
                    "UPDATE action_receipts SET result_json=?,result_digest=?",
                    ("broken", hashlib.sha256(b"broken").hexdigest()),
                )
            elif corruption in {"parameters", "invalid_parameters_json", "noncanonical_parameters"}:
                value = {
                    "parameters": '{"path":"work/input.txt"}',
                    "invalid_parameters_json": "broken",
                    "noncanonical_parameters": '{"path":NaN}',
                }[corruption]
                connection.execute("UPDATE action_intents SET parameters_json=?", (value,))
            elif corruption == "parameter_digest":
                connection.execute("UPDATE action_intents SET parameter_digest=?", ("0" * 64,))
            elif corruption == "foreign_run":
                source = dict(connection.execute("SELECT * FROM worker_runs").fetchone())
                source["id"] = "run_foreign"
                names = ",".join(source)
                placeholders = ",".join("?" for _ in source)
                connection.execute(
                    f"INSERT INTO worker_runs ({names}) VALUES ({placeholders})", tuple(source.values())
                )
                connection.execute("UPDATE action_intents SET run_id='run_foreign'")
            elif corruption == "plan_revision":
                connection.execute("UPDATE action_intents SET plan_revision=plan_revision+1")
            elif corruption == "intent_mode":
                connection.execute("UPDATE action_intents SET execution_mode='live'")
            elif corruption == "receipt_mode":
                connection.execute("UPDATE action_receipts SET execution_mode='live'")
            else:
                connection.execute("UPDATE action_intents SET state='uncertain'")
        return result

    monkeypatch.setattr(manager.tools, "execute", corrupted)
    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == "blocked" and outcome["repairs"] == 0
    assert len(model.requests) == 1
    with store.connection() as connection:
        call = connection.execute("SELECT * FROM worker_tool_calls").fetchone()
        assert call["state"] == "uncertain" and call["receipt_id"] is None
        assert call["result_json"] is None
        if corruption == "unknown_intent":
            assert connection.execute("SELECT state FROM action_intents").fetchone()[0] == "uncertain"
