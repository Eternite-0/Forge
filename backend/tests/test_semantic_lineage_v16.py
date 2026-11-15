"""Real fixed-suite evidence across approved DAGs; scripted model is offline.

The fixture explicitly stages succeeded state to isolate read-only inherited
evidence checks. It is not a real Provider or live task-acceptance measurement.
"""

import json

import pytest
from test_semantic_evidence_m7 import _fixture, _verify

from forge_core.domain import DomainError
from forge_core.evidence import CompletionGate
from forge_core.quest_graph import QuestGraphService
from forge_core.semantic_evidence import SemanticVerifier
from forge_core.tool_engine import ToolEngine


def completed_source(tmp_path):
    fixture = _fixture(tmp_path)
    result = _verify(fixture)
    assert result["passed"]
    store, quest, plan, run, _receipt = fixture
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='succeeded' WHERE id=?", (run["id"],))
        work.connection.execute(
            "UPDATE task_nodes SET status='succeeded' WHERE plan_id=?", (plan["id"],)
        )
        work.connection.execute(
            "UPDATE workers SET status='succeeded' WHERE id=?", (run["worker_id"],)
        )
    args = {
        "quest_id": quest.id,
        "plan_id": plan["id"],
        "plan_revision": plan["plan_revision"],
        "worker_id": run["worker_id"],
        "worker_run_id": run["id"],
        "semantic_run_id": result["semantic_run_id"],
    }
    return fixture, args


def approve_successor(store, quest, parent):
    with store.connection() as connection:
        raw = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE id=?", (parent["id"],)
            ).fetchone()[0]
        )
    graphs = QuestGraphService(store)
    staged = graphs.propose_remaining(
        quest.id, raw, expected_version=store.get_quest(quest.id)["version"]
    )
    return graphs.approve(
        quest.id,
        staged["digest"],
        staged["plan_revision"],
        expected_version=store.get_quest(quest.id)["version"],
        actor="local_user",
    )


def snapshot(connection):
    return {
        table: [
            dict(row) for row in connection.execute("SELECT * FROM " + table + " ORDER BY rowid")
        ]
        for table in (
            "worker_runs",
            "worker_tool_calls",
            "action_receipts",
            "action_intents",
            "semantic_test_runs",
            "evidence",
        )
    }


def test_fixed_suite_lineage_revalidates_original_proof_and_completion_without_new_execution(
    tmp_path,
):
    fixture, args = completed_source(tmp_path)
    store, quest, parent, _run, _ = fixture
    with store.connection() as connection:
        before = snapshot(connection)
    successor = approve_successor(store, quest, parent)
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        assert SemanticVerifier(store).validate_evidence(connection, **args)
        assert snapshot(connection) == before and connection.total_changes == 0
    with store.unit_of_work() as work:
        CompletionGate(store).check(work, work.get_quest(quest.id))
        assert snapshot(work.connection) == before
        assert (
            work.connection.execute(
                "SELECT status FROM quest_plans WHERE id=?", (parent["id"],)
            ).fetchone()[0]
            == "superseded"
        )
        assert successor["plan_revision"] == 2


def test_inherited_historical_proof_never_authorizes_new_semantic_execution(tmp_path, monkeypatch):
    fixture, args = completed_source(tmp_path)
    store, quest, parent, _run, _ = fixture
    approve_successor(store, quest, parent)

    def unexpected(*_args):
        pytest.fail("Historical proof must not authorize another subprocess")

    monkeypatch.setattr(ToolEngine, "_run_process", unexpected)
    with store.connection() as connection:
        before = snapshot(connection)
    with pytest.raises(DomainError):
        _verify(fixture)
    with store.connection() as connection:
        connection.execute("BEGIN")
        assert SemanticVerifier(store).validate_evidence(connection, **args)
        assert snapshot(connection) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "goal",
        "current_approval",
        "target_state",
        "source_state",
        "source_contract",
        "artifact",
        "report",
        "missing_lineage",
    ],
)
def test_semantic_lineage_rejects_changed_authority_sources_or_evidence(tmp_path, mutation):
    fixture, args = completed_source(tmp_path)
    store, quest, parent, run, _ = fixture
    successor = approve_successor(store, quest, parent)
    with store.unit_of_work() as work:
        connection = work.connection
        if mutation == "goal":
            connection.execute("UPDATE quests SET goal='changed goal' WHERE id=?", (quest.id,))
        elif mutation == "current_approval":
            connection.execute(
                "UPDATE quest_plans SET status='proposed' WHERE id=?", (successor["id"],)
            )
        elif mutation == "target_state":
            connection.execute(
                "UPDATE task_nodes SET status='failed' WHERE plan_id=?", (successor["id"],)
            )
        elif mutation == "source_state":
            connection.execute(
                "UPDATE worker_runs SET state='review_pending' WHERE id=?", (run["id"],)
            )
        elif mutation == "source_contract":
            connection.execute(
                "UPDATE task_nodes SET objective='changed contract' WHERE plan_id=?",
                (parent["id"],),
            )
        elif mutation == "artifact":
            (tmp_path / "work/scheduler.py").write_text("result = 0", encoding="utf-8")
        elif mutation == "report":
            connection.execute(
                "UPDATE semantic_test_runs SET report_json='{}' WHERE id=?",
                (args["semantic_run_id"],),
            )
        else:
            connection.execute(
                "UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (quest.id,)
            )
    with store.connection() as connection:
        connection.execute("BEGIN")
        with pytest.raises(DomainError):
            SemanticVerifier(store).validate_evidence(connection, **args)
        assert connection.total_changes == 0
