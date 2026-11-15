"""Offline protocols exercise approval-bound Worker limits before side effects."""

import asyncio
import hashlib
import json

import pytest
from test_review_envelope_phase2 import envelope, new_pending
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.domain import DomainError
from forge_core.independent_review import IndependentReviewService
from forge_core.team_composition import TeamComposerEngine, TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_envelope import cycle_allowance


def test_oversized_artifact_repairs_before_any_file_or_intent(tmp_path, monkeypatch):
    store, manager, model, worker = new_pending(
        tmp_path,
        monkeypatch,
        env=envelope(max_artifact_bytes=32),
        tools=["file_write"],
        responses=[
            tool("file_write", {"path": "work/oversized.json", "content": '"' + "x" * 100 + '"'}),
            response(delivery()),
        ],
    )
    assert manager.get(worker["id"])["repairs"] == 1
    assert len(model.requests) == 2 and not (tmp_path / "work/oversized.json").exists()
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 0
    assert "artifact bytes" in model.requests[-1].messages[-1]["content"]


def test_undeclared_source_never_enters_a_tool_receipt(tmp_path, monkeypatch):
    (tmp_path / "other.txt").write_text("UNDECLARED_PRIVATE_SENTINEL", encoding="utf-8")
    store, manager, model, worker = new_pending(
        tmp_path,
        monkeypatch,
        responses=[tool("file_read", {"path": "other.txt"}), response(delivery())],
    )
    assert manager.get(worker["id"])["repairs"] == 1
    assert "UNDECLARED_PRIVATE_SENTINEL" not in repr(model.requests)
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 0


def test_actual_read_rechecks_identity_after_queued_batch_admission(tmp_path, monkeypatch):
    store, manager, model, worker = new_pending(tmp_path, monkeypatch)
    model.responses.append(
        response(
            {
                "verdict": "NEEDS_REVISION",
                "findings": ["Recheck source."],
                "revision_goals": ["Recheck the exact input source."],
                "confidence": 0.8,
            }
        )
    )
    assert asyncio.run(IndependentReviewService(manager).review(worker["id"]))["revision_applied"]
    original = ToolEngine._perform
    changed = []

    def perform(self, intent, lease_token, cancelled):
        if intent["tool_name"] == "file_read" and not changed:
            changed.append(True)
            (tmp_path / "work/input.txt").write_text("CHANGED_SOURCE_SENTINEL", encoding="utf-8")
        return original(self, intent, lease_token, cancelled)

    monkeypatch.setattr(ToolEngine, "_perform", perform)
    model.responses.append(tool("file_read", {"path": "work/input.txt"}, ordinal=2))
    result = asyncio.run(manager.run(worker["id"]))
    assert changed and result["state"] == "blocked"
    assert "CHANGED_SOURCE_SENTINEL" not in repr(model.requests)
    with store.connection() as connection:
        rows = connection.execute("SELECT state FROM action_intents ORDER BY rowid").fetchall()
        assert [row["state"] for row in rows] == ["succeeded", "failed"]
        receipts = connection.execute("SELECT result_json FROM action_receipts").fetchall()
        assert all("CHANGED_SOURCE_SENTINEL" not in row[0] for row in receipts)


def test_revised_worker_rejects_capsule_contract_tampering_before_another_model_call(
    tmp_path, monkeypatch
):
    store, manager, model, worker = new_pending(tmp_path, monkeypatch)
    model.responses.append(
        response(
            {
                "verdict": "NEEDS_REVISION",
                "findings": ["Clarify source."],
                "revision_goals": ["State exact input source."],
                "confidence": 0.8,
            }
        )
    )
    assert asyncio.run(IndependentReviewService(manager).review(worker["id"]))["revision_applied"]
    before = len(model.requests)
    with store.unit_of_work() as work:
        capsule = json.loads(
            work.connection.execute(
                "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (worker["id"],)
            ).fetchone()[0]
        )
        capsule["review_envelope"]["max_delivery_bytes"] += 1
        work.connection.execute(
            "UPDATE worker_capsules SET capsule_json=? WHERE run_id=?",
            (json.dumps(capsule), worker["id"]),
        )
    with pytest.raises(DomainError, match="differs from the approved team"):
        asyncio.run(manager.run(worker["id"]))
    assert len(model.requests) == before


def test_fixed_verification_capacity_repairs_real_python_failure_without_model_reviews(
    tmp_path, monkeypatch
):
    candidate = TeamComposerEngine._candidate
    propose = TeamCompositionService.propose
    capacity = {
        "version": "worker-capacity-v1",
        "max_source_bytes": 5000,
        "max_artifact_bytes": 1500,
        "max_delivery_bytes": 1536,
        "correction_input_tokens": 18000,
        "correction_output_tokens": 3000,
        "correction_calls": 2,
    }

    def bound(self, *args, **kwargs):
        value = candidate(self, *args, **kwargs)
        source = (tmp_path / "work/input.txt").read_bytes()
        value.update(
            worker_capacity=capacity,
            required_input_paths=["work/input.txt"],
            verified_input_inventory=[
                {
                    "path": "work/input.txt",
                    "bytes": len(source),
                    "sha256": hashlib.sha256(source).hexdigest(),
                }
            ],
        )
        return value

    def funded(self, quest_id, roles, limits, **kwargs):
        roles = [{**role, "input_tokens": 57000} for role in roles]
        return propose(self, quest_id, roles, {**limits, "max_total_tokens": 60000}, **kwargs)

    monkeypatch.setattr(TeamComposerEngine, "_candidate", bound)
    monkeypatch.setattr(TeamCompositionService, "propose", funded)
    suite = {"cases": [{"inputs": {"a": 2, "b": 3}, "expected": 5}]}
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_write", {"path": "work/add.py", "content": "def add(a,b):\n return a-b\n"}),
            tool(
                "python_test", {"path": "work/add.py", "function": "add", "suite": suite}, ordinal=2
            ),
        ],
        tools=["file_write", "file_revise", "python_test"],
    )
    # The repair model chooses a new revision and then runs the actual isolated
    # harness in the same batch; it cannot replace the failed receipt.
    original_generate = model.generate

    async def generate(request):
        if not model.responses:
            with store.connection() as connection:
                receipt = connection.execute(
                    "SELECT r.id,r.result_json FROM action_receipts r JOIN action_intents i "
                    "ON i.id=r.intent_id WHERE i.tool_name='file_write'"
                ).fetchone()
            prior = json.loads(receipt["result_json"])
            if len(model.requests) == 2:
                model.responses.append(
                    response(
                        "",
                        calls=[
                            {
                                "id": "fix-3",
                                "name": "file_revise",
                                "arguments": {
                                    "path": "work/add.py",
                                    "prior_receipt_id": receipt["id"],
                                    "expected_sha256": prior["sha256"],
                                    "content": "def add(a,b):\n return a+b\n",
                                },
                            },
                            {
                                "id": "test-4",
                                "name": "python_test",
                                "arguments": {
                                    "path": "work/add.py",
                                    "function": "add",
                                    "suite": suite,
                                },
                            },
                        ],
                    )
                )
            else:
                model.responses.append(response(delivery("Corrected and tested add.py.")))
        return await original_generate(request)

    model.generate = generate
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        calls = connection.execute(
            "SELECT tool_name,state FROM worker_tool_calls ORDER BY rowid"
        ).fetchall()
        assert [(row[0], row[1]) for row in calls] == [
            ("file_write", "succeeded"),
            ("python_test", "failed"),
            ("file_revise", "succeeded"),
            ("python_test", "succeeded"),
        ]
        with pytest.raises(DomainError, match="correction call count exhausted"):
            cycle_allowance(connection, manager.get(worker["id"]), capacity)
        assert (
            connection.execute(
                "SELECT count(*) FROM events WHERE event_type LIKE 'review.%'"
            ).fetchone()[0]
            == 0
        )
        capsule = json.loads(
            connection.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
        )
        assert "review_envelope" not in capsule and capsule["worker_capacity"] == capacity
    assert "a+b" in (tmp_path / "work/add.py").read_text(encoding="utf-8")
