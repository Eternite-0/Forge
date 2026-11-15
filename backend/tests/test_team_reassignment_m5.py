import pytest

from forge_core.domain import DomainError, ModelProfile, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.store import Store
from forge_core.team_composition import CompositionLimits, RoleSpec, TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def prepared_team(tmp_path):
    class MockProvider:
        execution_mode = "mock"

    store = Store(tmp_path / "reassign.sqlite3")
    quest = Quest(title="Partial team", goal="Preserve completed work")
    store.save_quest(quest)
    graph = QuestGraphService(store)
    plan = graph.propose(
        quest.id,
        {
            "nodes": [
                {"key": key, "objective": key, "success_criteria": ["Done"], "budget_tokens": 1000}
                for key in ("a", "b")
            ],
            "edges": [],
            "max_total_tokens": 2000,
        },
        expected_version=0,
    )
    graph.approve(
        quest.id, plan["digest"], plan["revision"], expected_version=1, actor="local_user"
    )
    store.save_model(
        ModelProfile(
            id="m",
            provider="p",
            model_id="m",
            display_name="M",
            context_window=4096,
            max_output_tokens=1000,
        )
    )
    roles = [
        RoleSpec(id=role, task_node_key=key, input_tokens=100, output_tokens=100)
        for role, key in (("coding", "a"), ("reviewer", "b"))
    ]
    limits = CompositionLimits(max_members=2, max_depth=1, max_concurrency=2, max_total_tokens=400)
    service = TeamCompositionService(store)
    team = service.propose(quest.id, roles, limits, strategy="greedy", expected_version=2)
    service.approve(
        quest.id, plan["revision"], team["proposal_digest"], expected_version=2, actor="local_user"
    )
    manager = WorkerManager(
        store, ProviderPool({"p": MockProvider()}), ToolEngine(store), team_authority=service
    )
    for index, key in enumerate(("a", "b")):
        manager.start(
            quest.id, plan["id"], key, team_digest=team["proposal_digest"], member_index=index
        )
    with store.connection() as connection:
        runs = [
            dict(row) for row in connection.execute("SELECT * FROM worker_runs ORDER BY node_key")
        ]
    return store, quest, plan, roles, limits, service, team, manager, runs


def test_reassignment_preserves_completed_node_and_historical_approval(tmp_path):
    store, quest, plan, roles, limits, service, old, manager, runs = prepared_team(tmp_path)
    with store.unit_of_work() as work:
        for run, state in zip(runs, ("succeeded", "blocked"), strict=True):
            work.connection.execute("UPDATE worker_runs SET state=? WHERE id=?", (state, run["id"]))
            work.connection.execute(
                "UPDATE task_nodes SET status=? WHERE plan_id=? AND node_key=?",
                (state, plan["id"], run["node_key"]),
            )
    version = store.get_quest(quest.id)["version"]
    replacement = service.propose_reassignment(
        quest.id,
        runs[1]["id"],
        "Replace blocked reviewer",
        roles,
        limits,
        strategy="coverage",
        expected_version=version,
    )
    service.approve(
        quest.id,
        plan["revision"],
        replacement["proposal_digest"],
        expected_version=version,
        actor="local_user",
    )
    with store.unit_of_work() as work:
        completed = work.connection.execute(
            "SELECT * FROM task_nodes WHERE plan_id=? AND node_key='a'", (plan["id"],)
        ).fetchone()
        assert completed["status"] == "succeeded" and completed["worker_id"] == runs[0]["worker_id"]
        assert (
            service.get_historical_approved(
                quest.id,
                plan["revision"],
                old["proposal_digest"],
                connection=work.connection,
                run_id=runs[0]["id"],
            )["status"]
            == "superseded"
        )
        with pytest.raises(DomainError):
            service.get_approved(
                quest.id, plan["revision"], old["proposal_digest"], connection=work.connection
            )
    manager.start(
        quest.id, plan["id"], "b", team_digest=replacement["proposal_digest"], member_index=1
    )
    with store.connection() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM worker_runs WHERE node_key='a'").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM worker_runs WHERE node_key='b'").fetchone()[0]
            == 2
        )


def test_reassignment_rejects_unsettled_actions_without_mutating_old_team(tmp_path):
    store, quest, plan, roles, limits, service, old, _, runs = prepared_team(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='blocked' WHERE id=?", (runs[1]["id"],)
        )
        work.connection.execute(
            "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) VALUES (?,?,?,?,?,'uncertain')",
            (runs[1]["id"], "uncertain", "uncertain", "file_write", "{}"),
        )
    with pytest.raises(DomainError, match="settled"):
        service.propose_reassignment(
            quest.id,
            runs[1]["id"],
            "Unsafe",
            roles,
            limits,
            strategy="coverage",
            expected_version=store.get_quest(quest.id)["version"],
        )
    with store.unit_of_work() as work:
        assert (
            service.get_approved(
                quest.id, plan["revision"], old["proposal_digest"], connection=work.connection
            )["status"]
            == "approved"
        )


@pytest.mark.parametrize("mutation", ["before_approval", "wrong_run", "revoked", "tampered_body"])
def test_historical_team_rejects_unbound_or_invalid_execution(tmp_path, mutation):
    store, quest, plan, _, _, service, old, _, runs = prepared_team(tmp_path)
    with store.unit_of_work() as work:
        run_id = runs[0]["id"]
        if mutation == "before_approval":
            work.connection.execute(
                "UPDATE worker_runs SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (run_id,),
            )
        elif mutation == "wrong_run":
            run_id = "forged"
        elif mutation == "revoked":
            work.connection.execute(
                "UPDATE team_proposals SET status='revoked' WHERE proposal_digest=?",
                (old["proposal_digest"],),
            )
        else:
            work.connection.execute(
                "UPDATE team_proposals SET body_json='{}' WHERE proposal_digest=?",
                (old["proposal_digest"],),
            )
        with pytest.raises(DomainError):
            service.get_historical_approved(
                quest.id,
                plan["revision"],
                old["proposal_digest"],
                connection=work.connection,
                run_id=run_id,
            )


def test_historical_approval_survives_registry_change_but_cannot_authorize_execution(tmp_path):
    store, quest, plan, _, _, service, old, _, runs = prepared_team(tmp_path)
    model = store.list_models()[0]
    model.enabled = False
    store.save_model(model)
    with store.unit_of_work() as work:
        assert (
            service.get_historical_approved(
                quest.id,
                plan["revision"],
                old["proposal_digest"],
                connection=work.connection,
                run_id=runs[0]["id"],
            )["status"]
            == "approved"
        )
        with pytest.raises(DomainError, match="stale"):
            service.get_approved(
                quest.id, plan["revision"], old["proposal_digest"], connection=work.connection
            )


def test_reassignment_does_not_refund_quest_allocations_and_is_bounded(tmp_path):
    store, quest, plan, roles, limits, service, _old, manager, runs = prepared_team(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='succeeded' WHERE id=?", (runs[0]["id"],)
        )
        work.connection.execute(
            "UPDATE task_nodes SET status='succeeded' WHERE plan_id=? AND node_key='a'",
            (plan["id"],),
        )
    run = runs[1]
    for index in range(3):
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE worker_runs SET state='blocked' WHERE id=?", (run["id"],)
            )
        version = store.get_quest(quest.id)["version"]
        team = service.propose_reassignment(
            quest.id,
            run["id"],
            f"replacement {index}",
            roles,
            limits,
            strategy="coverage",
            expected_version=version,
        )
        service.approve(
            quest.id,
            plan["revision"],
            team["proposal_digest"],
            expected_version=version,
            actor="local_user",
        )
        manager.start(
            quest.id, plan["id"], "b", team_digest=team["proposal_digest"], member_index=1
        )
        with store.connection() as connection:
            run = dict(
                connection.execute(
                    "SELECT * FROM worker_runs WHERE node_key='b' ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            )
            allocation = connection.execute(
                "SELECT tokens_allocated FROM worker_budget_ledger WHERE scope='quest' AND scope_id=?",
                (quest.id,),
            ).fetchone()[0]
            assert allocation == 400 + 200 * (index + 1)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='blocked' WHERE id=?", (run["id"],))
    with pytest.raises(DomainError, match="limit"):
        service.propose_reassignment(
            quest.id,
            run["id"],
            "fourth",
            roles,
            limits,
            strategy="coverage",
            expected_version=store.get_quest(quest.id)["version"],
        )


def test_historical_team_can_be_rechecked_after_quest_completion(tmp_path):
    store, quest, plan, _, _, service, old, _, runs = prepared_team(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status='completed' WHERE id=?", (quest.id,))
        assert (
            service.get_historical_approved(
                quest.id,
                plan["revision"],
                old["proposal_digest"],
                connection=work.connection,
                run_id=runs[0]["id"],
            )["status"]
            == "approved"
        )
        with pytest.raises(DomainError):
            service.get_approved(
                quest.id, plan["revision"], old["proposal_digest"], connection=work.connection
            )
