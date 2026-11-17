"""Mock model batches with separately approved real CPython executions."""

import asyncio
import hashlib
import json

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response

from forge_core.domain import DomainError
from forge_core.governance import GovernanceService


def waiting_batch(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["python_unittest"])
    directory = tmp_path / "work" / worker["quest_id"]
    directory.mkdir()
    source = b"VALUE = 3\n"
    (directory / "candidate.py").write_bytes(source)
    calls = []
    for index in range(2):
        module = f"test_candidate_{index}"
        test = (
            b"import unittest\nfrom candidate import VALUE\n"
            b"class Check(unittest.TestCase):\n"
            b"    def test_value(self):\n"
            b"        self.assertEqual(VALUE, 3)\n"
        )
        (directory / f"{module}.py").write_bytes(test)
        calls.append(
            {
                "id": f"batch-{index}",
                "name": "python_unittest",
                "arguments": {
                    "path": "work/" + worker["quest_id"],
                    "manifest": {
                        "candidate.py": hashlib.sha256(source).hexdigest(),
                        f"{module}.py": hashlib.sha256(test).hexdigest(),
                    },
                    "test_module": module,
                },
            }
        )
    model.responses[:] = [response("", calls=calls), response(delivery())]
    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == "blocked" and outcome["awaiting_authorization"] == 1
    return store, manager, model, worker


def approve_waiting(store, worker):
    governance = GovernanceService(store)
    pending = [
        item
        for item in governance.list_approvals(worker["quest_id"])
        if item["status"] == "PENDING"
    ]
    assert len(pending) == 1
    approval = pending[0]
    governance.decide_approval(
        approval["id"],
        "APPROVED",
        "local_user",
        expected_digest=approval["parameter_digest"],
        expected_plan_revision=approval["plan_revision"],
    )
    return approval["id"]


def test_batch_resumes_in_order_with_two_exact_approvals_and_two_actual_receipts(tmp_path):
    store, manager, model, worker = waiting_batch(tmp_path)
    with pytest.raises(DomainError, match="not ready"):
        manager.retry(worker["id"])
    first = approve_waiting(store, worker)
    assert manager.retry(worker["id"])["tool_spent"] == 2
    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == "blocked" and outcome["awaiting_authorization"] == 1
    with store.connection() as c:
        calls = c.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        assert [call["state"] for call in calls] == ["succeeded", "pending"]
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
    assert GovernanceService(store).get_approval(first)["consumed_at"]
    assert len(model.requests) == 1
    with pytest.raises(DomainError, match="not ready"):
        manager.retry(worker["id"])
    second = approve_waiting(store, worker)
    assert first != second
    manager.retry(worker["id"])
    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == "review_pending" and outcome["tool_spent"] == 2
    assert outcome["repairs"] == 0 and len(model.requests) == 2
    with store.connection() as c:
        calls = c.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        assert [call["state"] for call in calls] == ["succeeded", "succeeded"]
        assert len({call["receipt_id"] for call in calls}) == 2
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
        assert [json.loads(call["result_json"])["output"]["tests_run"] for call in calls] == [1, 1]
        retries = c.execute("SELECT payload FROM events WHERE event_type='worker.retry'").fetchall()
        assert len(retries) == 2
        assert all(json.loads(item[0])["retry_kind"] == "authorization" for item in retries)
    with pytest.raises(DomainError):
        manager.retry(worker["id"])
    assert GovernanceService(store).get_approval(second)["consumed_at"]


@pytest.mark.parametrize("tamper", ["arguments", "transcript", "count", "unknown", "intent"])
def test_batch_does_not_resume_changed_or_unknown_operations(tmp_path, tamper):
    store, manager, _model, worker = waiting_batch(tmp_path)
    approval = approve_waiting(store, worker)
    with store.unit_of_work() as work:
        c = work.connection
        if tamper == "arguments":
            c.execute(
                "UPDATE worker_tool_calls SET arguments_json='{}' WHERE model_call_id='batch-1'"
            )
        elif tamper == "transcript":
            c.execute("UPDATE worker_messages SET message_json='{}' WHERE role='assistant'")
        elif tamper == "count":
            c.execute("UPDATE worker_runs SET tool_spent=tool_spent-1")
        elif tamper == "unknown":
            c.execute(
                "UPDATE worker_tool_calls SET state='uncertain' WHERE model_call_id='batch-1'"
            )
        else:
            c.execute(
                """INSERT INTO action_intents
                SELECT 'batch-unknown','batch-unknown',quest_id,worker_id,id,plan_id,plan_revision,
                'python_unittest','{}','fixture','fixture','R2',NULL,execution_mode,
                'executing','fixture','fixture' FROM worker_runs WHERE id=?""",
                (worker["id"],),
            )
    with pytest.raises(DomainError):
        manager.retry(worker["id"])
    assert GovernanceService(store).get_approval(approval)["consumed_at"] is None
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
        assert c.execute("SELECT state FROM worker_runs").fetchone()[0] == "blocked"
