import json

import pytest

from forge_core.domain import DomainError, Quest, WorkerSession
from forge_core.evidence import CompletionGate, JsonVerifier, _approval_valid
from forge_core.settings import Workspace
from forge_core.store import Store


def test_json_verifier_executes_fixed_validator_and_records_actual_result(tmp_path):
    store = Store(str(tmp_path / "state.sqlite3"), workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Verify", goal="Validate JSON")
    store.save_quest(quest)
    worker = WorkerSession(quest_id=quest.id, role="producer")
    store.save_worker(worker)
    artifact = tmp_path / "result.json"
    artifact.write_text('{"value": 1}', encoding="utf-8")
    result = JsonVerifier(store).verify(quest.id, worker.id, artifact)
    assert result["exit_code"] == 0
    with store.connection() as connection:
        run = connection.execute(
            "SELECT * FROM execution_runs WHERE id=?", (result["run_id"],)
        ).fetchone()
        assert run["kind"] == "json_validation"
        assert run["worker_id"] == worker.id
        assert json.loads(run["command_json"])[1:3] == ["-I", "-c"]
        assert run["provenance"] == "fixed_json_validator_v1"
    with store.unit_of_work() as work:
        gate = CompletionGate(store)
        record = work.get_quest(quest.id)
        check_id = f"json_valid:{artifact.resolve()}"
        assert gate._verified_check(work, record, "test", check_id)
        assert gate._verified_check(work, record, "review", check_id)
    artifact.write_text("{", encoding="utf-8")
    with store.unit_of_work() as work:
        assert not gate._verified_check(work, work.get_quest(quest.id), "test", check_id)
    failure = JsonVerifier(store).verify(quest.id, worker.id, artifact)
    assert failure["exit_code"] != 0
    with store.connection() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM evidence WHERE kind='test'").fetchone()[0] == 1
        )


def test_completion_gate_rejects_absent_requirements(tmp_path):
    store = Store(str(tmp_path / "state.sqlite3"), workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Unverified", goal="Must not finish")
    store.save_quest(quest)
    with store.unit_of_work() as work, pytest.raises(DomainError, match="requirements"):
        CompletionGate(store).check(work, work.get_quest(quest.id))


def test_verifier_limits_reads_and_strips_secrets(tmp_path, monkeypatch):
    import subprocess

    store = Store(str(tmp_path / "state.sqlite3"), workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Bounded", goal="Limit validator input")
    store.save_quest(quest)
    worker = WorkerSession(quest_id=quest.id, role="producer")
    store.save_worker(worker)
    artifact = tmp_path / "large.json"
    artifact.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(DomainError, match="limit"):
        JsonVerifier(store).verify(quest.id, worker.id, artifact)
    artifact.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FORGE_CORE_SECRET", "sentinel")
    original = subprocess.run

    def inspect(*args, **kwargs):
        assert "FORGE_CORE_SECRET" not in kwargs["env"]
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", inspect)
    assert JsonVerifier(store).verify(quest.id, worker.id, artifact)["exit_code"] == 0


def test_approval_validity_is_measured_at_consumption():
    quest = {"id": "q", "plan_revision": 4}
    approval = {
        "quest_id": "q",
        "plan_revision": 4,
        "status": "APPROVED",
        "policy_version": "1",
        "decided_at": "2020-01-01T00:00:00+00:00",
        "consumed_at": "2020-01-01T00:01:00+00:00",
        "expires_at": "2020-01-01T00:02:00+00:00",
    }
    assert _approval_valid(approval, quest)
    approval["consumed_at"] = approval["expires_at"]
    assert not _approval_valid(approval, quest)
    approval["consumed_at"] = "2020-01-01T00:01:00+00:00"
    approval["status"] = "REVOKED"
    assert not _approval_valid(approval, quest)
