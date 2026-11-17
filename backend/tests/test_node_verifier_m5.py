"""Verifier contract tests with a scripted test-only live provenance stub.

No real model call is claimed: only local ToolEngine/verifier subprocesses are real.
The separate opt-in live_completion_probe.py performs real provider validation.
"""

import asyncio
import json

import pytest

from forge_core.domain import DomainError, ModelProfile, ProtocolSupport, Quest
from forge_core.evidence import CompletionGate, NodeVerifier
from forge_core.governance import GovernanceService
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelResponse
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def reviewed_artifact(tmp_path, *, criterion=None, conditional=False):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    (tmp_path / "work").mkdir()
    artifact = tmp_path / "work/result.json"
    quest = Quest(title="Verifier contract fixture", goal="Create valid JSON")
    store.save_quest(quest)
    nodes = [
        {
            "key": "n",
            "objective": quest.goal,
            "success_criteria": [criterion or "json_valid:" + str(artifact)],
            "budget_tokens": 16000,
            "allowed_tools": ["file_write"],
        }
    ]
    if conditional:
        nodes.append(
            {
                "key": "failure_only",
                "objective": "Run on failure",
                "success_criteria": ["must never execute"],
                "budget_tokens": 100,
                "allowed_tools": [],
            }
        )
    graph = QuestGraphService(store)
    plan = graph.propose(
        quest.id,
        {
            "nodes": nodes,
            "edges": [{"source": "n", "target": "failure_only", "condition": "failure"}]
            if conditional
            else [],
            "max_total_tokens": 16100,
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
            display_name="Test only provenance stub",
            context_window=32000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    teams = TeamCompositionService(store)
    roles = [
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
    ]
    if conditional:
        roles.append(
            {
                "id": "reviewer",
                "task_node_key": "failure_only",
                "tools": [],
                "input_tokens": 50,
                "output_tokens": 50,
                "max_steps": 1,
                "max_tool_calls": 0,
                "max_duration_seconds": 60,
            }
        )
    team = teams.propose(
        quest.id,
        roles,
        {
            "max_members": 2 if conditional else 1,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 12100,
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

    class ScriptedProvenanceStub:
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
                        "arguments": {"path": "work/result.json", "content": '{"ok":true}'},
                    }
                ],
                usage={"input_tokens": 30, "output_tokens": 20},
                finish_reason="stop" if self.count == 2 else "tool_calls",
            )

    manager = WorkerManager(
        store,
        ProviderPool({"fixture": ScriptedProvenanceStub()}),
        ToolEngine(store),
        team_authority=teams,
    )
    run = manager.start(
        quest.id, plan["id"], "n", team_digest=team["proposal_digest"], member_index=0
    )
    outcome = asyncio.run(manager.run(run["id"]))
    assert outcome["state"] == "review_pending"
    return store, quest, plan, run, artifact


def test_independent_json_positive_path_settles_worker_and_rechecks_artifact(tmp_path):
    store, quest, _, run, artifact = reviewed_artifact(tmp_path)
    assert NodeVerifier(store).verify(quest.id, "n")["status"] == "completed"
    verified = [event for event in store.list_events() if event["event_type"] == "task.verified"]
    assert len(verified) == 1
    assert verified[0]["conversation_id"] == store.get_quest(quest.id)["conversation_id"]
    assert json.loads(verified[0]["payload"])["run_id"] == run["id"]
    assert json.loads(verified[0]["payload"])["execution_mode"] == "live"
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM worker_runs WHERE id=?", (run["id"],)).fetchone()[
                0
            ]
            == "succeeded"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM execution_runs WHERE kind='json_validation' AND exit_code=0"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM evidence WHERE kind IN ('test','review') AND trusted=1"
            ).fetchone()[0]
            == 2
        )
    artifact.write_text('{"changed":true}', encoding="utf-8")
    with store.unit_of_work() as work, pytest.raises(DomainError):
        CompletionGate(store).check(work, work.get_quest(quest.id))


def test_unverifiable_natural_language_never_completes(tmp_path):
    store, quest, _, _, _ = reviewed_artifact(tmp_path, criterion="This algorithm is correct")
    with pytest.raises(DomainError, match="independent verifier"):
        NodeVerifier(store).verify(quest.id, "n")
    assert store.get_quest(quest.id)["status"] == "running"


def test_conditionally_unused_failure_branch_is_skipped(tmp_path):
    store, quest, plan, _, _ = reviewed_artifact(tmp_path, conditional=True)
    assert NodeVerifier(store).verify(quest.id, "n")["status"] == "completed"
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT status FROM task_nodes WHERE plan_id=? AND node_key='failure_only'",
                (plan["id"],),
            ).fetchone()[0]
            == "skipped"
        )


def test_forged_root_skip_is_rejected(tmp_path):
    store, quest, plan, _, _ = reviewed_artifact(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE task_nodes SET status='skipped' WHERE plan_id=?", (plan["id"],)
        )
        with pytest.raises(DomainError, match="Root task"):
            CompletionGate(store).check(work, work.get_quest(quest.id))


def test_background_verifier_blocks_unsupported_once_without_repeating_checks(tmp_path):
    store, quest, _, _, _ = reviewed_artifact(tmp_path, criterion="arbitrary semantic claim")
    verifier = NodeVerifier(store)
    assert verifier.tick()
    assert not verifier.tick()
    with store.connection() as connection:
        row = connection.execute("SELECT * FROM node_verification_attempts").fetchone()
        assert row["state"] == "blocked" and row["attempts"] == 1
        assert connection.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
    assert store.get_quest(quest.id)["status"] == "running"


def test_expired_background_claim_cannot_finish_node(tmp_path, monkeypatch):
    import time

    from forge_core.evidence import JsonVerifier

    store, quest, plan, run, _ = reviewed_artifact(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO node_verification_attempts(plan_id,node_key,worker_id,attempts,next_attempt,state,claim_token) VALUES (?,?,?,1,?,'checking','old')",
            (plan["id"], "n", run["worker_id"], time.time() + 60),
        )
    original = JsonVerifier.verify

    def expire_during_check(*args):
        result = original(*args)
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE node_verification_attempts SET claim_token='new',next_attempt=?",
                (time.time() + 60,),
            )
        return result

    monkeypatch.setattr(JsonVerifier, "verify", expire_during_check)
    with pytest.raises(DomainError, match="claim is stale"):
        NodeVerifier(store).verify(quest.id, "n", claim=(plan["id"], run["worker_id"], "old"))
    assert store.get_quest(quest.id)["status"] == "running"
    with store.connection() as connection:
        assert connection.execute("SELECT status FROM task_nodes").fetchone()[0] == "review_pending"


def test_unrelated_quest_version_change_during_check_does_not_block_completion(
    tmp_path, monkeypatch
):
    from forge_core.evidence import JsonVerifier

    store, quest, _, _, _ = reviewed_artifact(tmp_path)
    original = JsonVerifier.verify

    def increment_during_check(*args):
        result = original(*args)
        with store.unit_of_work() as work:
            work.connection.execute("UPDATE quests SET version=version+1 WHERE id=?", (quest.id,))
        return result

    monkeypatch.setattr(JsonVerifier, "verify", increment_during_check)
    assert NodeVerifier(store).verify(quest.id, "n")["status"] == "completed"
