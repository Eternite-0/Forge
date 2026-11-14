"""Real SQLite/tool subprocess tests; scripted model is test-only provenance."""

import asyncio
import json
import shutil
import sqlite3
import sys

import pytest
from test_semantic_suite_m7 import CORRECT

from forge_core.domain import DomainError, ModelProfile, ProtocolSupport, Quest
from forge_core.governance import GovernanceService
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelResponse
from forge_core.semantic_evidence import SemanticVerifier
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def _fixture(tmp_path, source=CORRECT):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    (tmp_path / "work").mkdir()
    quest = Quest(title="semantic fixture", goal="selection contract")
    store.save_quest(quest)
    graph = QuestGraphService(store)
    plan = graph.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": "write candidate",
                    "success_criteria": ["semantic_numeric:scheduler-v1:work/scheduler.py"],
                    "budget_tokens": 16000,
                    "allowed_tools": ["file_write"],
                }
            ],
            "edges": [],
            "max_total_tokens": 16000,
        },
        expected_version=0,
    )
    graph.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    store.save_model(
        ModelProfile(
            id="fixture",
            provider="fixture",
            model_id="scripted",
            display_name="Test-only stub",
            context_window=32000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    teams = TeamCompositionService(store)
    team = teams.propose(
        quest.id,
        [
            {
                "id": "coding",
                "task_node_key": "n",
                "tools": ["file_write"],
                "input_tokens": 9000,
                "output_tokens": 3000,
                "max_steps": 3,
                "max_tool_calls": 1,
                "max_duration_seconds": 60,
            }
        ],
        {
            "max_members": 1,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 12000,
            "max_total_tool_calls": 1,
            "allowed_tools": ["file_write"],
        },
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(
        quest.id,
        plan["plan_revision"],
        team["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")

    class Model:
        execution_mode = "live"
        count = 0

        async def generate(self, request):
            self.count += 1
            return ModelResponse(
                text='{"summary":"fixture"}' if self.count == 2 else "",
                tool_calls=[]
                if self.count == 2
                else [
                    {
                        "id": "write",
                        "name": "file_write",
                        "arguments": {"path": "work/scheduler.py", "content": source},
                    }
                ],
                usage={"input_tokens": 30, "output_tokens": 20},
                finish_reason="stop" if self.count == 2 else "tool_calls",
            )

    manager = WorkerManager(
        store, ProviderPool({"fixture": Model()}), ToolEngine(store), team_authority=teams
    )
    run = manager.start(
        quest.id, plan["id"], "n", team_digest=team["proposal_digest"], member_index=0
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    with store.connection() as c:
        receipt = c.execute("SELECT id FROM action_receipts").fetchone()[0]
    return store, quest, plan, run, receipt


def _verify(fixture):
    store, quest, plan, run, receipt = fixture
    return SemanticVerifier(store).verify(
        quest.id,
        run["worker_id"],
        "work/scheduler.py",
        run_id=run["id"],
        plan_id=plan["id"],
        plan_revision=plan["plan_revision"],
        receipt_id=receipt,
    )


def test_real_receipt_and_independent_subprocess_produce_trusted_evidence(tmp_path):
    fixture = _fixture(tmp_path)
    result = _verify(fixture)
    assert result["passed"] is True and result["suite_id"] == "scheduler-v1"
    with fixture[0].connection() as c:
        assert c.execute("SELECT count(*) FROM semantic_test_runs").fetchone()[0] == 1
        assert (
            c.execute(
                "SELECT count(*) FROM evidence WHERE trusted=1 AND kind IN ('test','review')"
            ).fetchone()[0]
            == 2
        )


def test_wrong_candidate_never_creates_trusted_semantic_evidence(tmp_path):
    fixture = _fixture(tmp_path, "result = 0")
    result = _verify(fixture)
    assert result["passed"] is False
    with fixture[0].connection() as c:
        assert c.execute("SELECT count(*) FROM semantic_test_runs").fetchone()[0] == 1
        assert (
            c.execute(
                "SELECT count(*) FROM evidence WHERE trusted=1 AND kind IN ('test','review')"
            ).fetchone()[0]
            == 0
        )
        c.execute("BEGIN")
        with pytest.raises(DomainError):
            SemanticVerifier(fixture[0]).validate_evidence(
                c,
                quest_id=fixture[1].id,
                plan_id=fixture[2]["id"],
                plan_revision=fixture[2]["plan_revision"],
                worker_id=fixture[3]["worker_id"],
                worker_run_id=fixture[3]["id"],
                semantic_run_id=result["semantic_run_id"],
            )


def test_completed_quest_can_revalidate_and_command_tampering_fails(tmp_path):
    fixture = _fixture(tmp_path)
    result = _verify(fixture)
    store, quest, plan, run, _ = fixture
    args = {
        "quest_id": quest.id,
        "plan_id": plan["id"],
        "plan_revision": plan["plan_revision"],
        "worker_id": run["worker_id"],
        "worker_run_id": run["id"],
        "semantic_run_id": result["semantic_run_id"],
    }
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status='completed' WHERE id=?", (quest.id,))
        assert SemanticVerifier(store).validate_evidence(work.connection, **args)
        work.connection.execute(
            'UPDATE semantic_test_runs SET command_json=\'["C:/fake.exe","-I","-m","forge_core.semantic_suite"]\' WHERE id=?',
            (result["semantic_run_id"],),
        )
        with pytest.raises(DomainError):
            SemanticVerifier(store).validate_evidence(work.connection, **args)


def test_revision_change_during_subprocess_rolls_back(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    original = ToolEngine._run_process

    def changing(*args):
        result = original(*args)
        with fixture[0].unit_of_work() as work:
            work.connection.execute(
                "UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (fixture[1].id,)
            )
        return result

    monkeypatch.setattr(ToolEngine, "_run_process", changing)
    with pytest.raises(DomainError):
        _verify(fixture)
    with fixture[0].connection() as c:
        assert c.execute("SELECT count(*) FROM semantic_test_runs").fetchone()[0] == 0


def test_tampered_artifact_and_stale_revision_are_rejected(tmp_path):
    fixture = _fixture(tmp_path)
    (tmp_path / "work/scheduler.py").write_text("result = 0", encoding="utf-8")
    with pytest.raises(DomainError):
        _verify(fixture)
    with fixture[0].unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (fixture[1].id,)
        )
    with pytest.raises(DomainError):
        _verify(fixture)


def test_provenance_survives_executable_relocation(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    result = _verify(fixture)
    store, quest, plan, run, _ = fixture
    args = {
        "quest_id": quest.id,
        "plan_id": plan["id"],
        "plan_revision": plan["plan_revision"],
        "worker_id": run["worker_id"],
        "worker_run_id": run["id"],
        "semantic_run_id": result["semantic_run_id"],
    }
    relocated = tmp_path / "relocated-python.exe"
    shutil.copyfile(sys.executable, relocated)
    monkeypatch.setattr(sys, "executable", str(relocated))
    with store.unit_of_work() as work:
        assert SemanticVerifier(store).validate_evidence(work.connection, **args)


@pytest.mark.parametrize("tamper", ["command", "provenance", "missing", "duplicate"])
def test_provenance_tampering_and_legacy_missing_proof_fail_closed(tmp_path, tamper):
    fixture = _fixture(tmp_path)
    result = _verify(fixture)
    store, quest, plan, run, _ = fixture
    args = {
        "quest_id": quest.id,
        "plan_id": plan["id"],
        "plan_revision": plan["plan_revision"],
        "worker_id": run["worker_id"],
        "worker_run_id": run["id"],
        "semantic_run_id": result["semantic_run_id"],
    }
    with store.unit_of_work() as work:
        c = work.connection
        if tamper == "command":
            c.execute(
                "UPDATE semantic_test_runs SET command_json=?",
                (json.dumps(["fake", "-I", "-m", "forge_core.semantic_suite"]),),
            )
        elif tamper == "missing":
            c.execute("DELETE FROM evidence WHERE kind='semantic_provenance'")
        elif tamper == "duplicate":
            c.execute(
                "INSERT INTO evidence SELECT id || '_duplicate',quest_id,kind,trusted,payload,created_at FROM evidence WHERE kind='semantic_provenance'"
            )
        else:
            row = c.execute(
                "SELECT id,payload FROM evidence WHERE kind='semantic_provenance'"
            ).fetchone()
            payload = json.loads(row["payload"])
            payload["executor_sha256"] = "0" * 64
            c.execute("UPDATE evidence SET payload=? WHERE id=?", (json.dumps(payload), row["id"]))
        with pytest.raises(DomainError):
            SemanticVerifier(store).validate_evidence(c, **args)


def test_provenance_insert_failure_rolls_back_semantic_run(tmp_path):
    fixture = _fixture(tmp_path)
    with fixture[0].unit_of_work() as work:
        work.connection.execute(
            "CREATE TRIGGER fail_semantic_provenance BEFORE INSERT ON evidence WHEN NEW.kind='semantic_provenance' BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        _verify(fixture)
    with fixture[0].connection() as c:
        assert c.execute("SELECT count(*) FROM semantic_test_runs").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT count(*) FROM evidence WHERE kind IN ('semantic_provenance','test','review')"
            ).fetchone()[0]
            == 0
        )
