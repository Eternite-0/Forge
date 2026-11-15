"""Offline integration of real contracts/SQLite. Models are explicitly Mock."""

import json

import pytest

from forge_core.domain import ConversationMessage, DomainError, ModelProfile, ProtocolSupport, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.superagent_delegation import (
    DelegationAuthority,
    SuperagentDelegation,
    issue_mandate,
)
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


@pytest.fixture
def delegated(tmp_path):
    store = Store(tmp_path / "data" / "forge.sqlite3", workspace=Workspace.from_path(tmp_path))
    store.ensure_router()
    quest = Quest(
        title="Research", goal="Explain the inputs", metadata={"task_kind": "long_research"}
    )
    source = ConversationMessage(role="user", content="Explain the inputs")
    with store.unit_of_work() as work:
        work.add_message(source)
        work.save_quest(quest)
        mandate = issue_mandate(work, quest.id, source.id)
    store.save_model(
        ModelProfile(
            id="test-model",
            display_name="Mock model",
            provider="mock-provider",
            model_id="mock-model",
            enabled=True,
            context_window=65536,
            max_output_tokens=8192,
            tool_calling_support=ProtocolSupport(supported=True, source="test"),
            structured_output_support=ProtocolSupport(supported=True, source="test"),
        )
    )
    graph = QuestGraphService(store)
    teams = TeamCompositionService(store)
    service = SuperagentDelegation(store, graph, teams)
    return store, quest.id, mandate, graph, teams, service


def test_conversational_task_creates_internal_contract_without_human_approval(delegated):
    store, quest_id, mandate, graph, _teams, service = delegated
    assert service.prepare(quest_id)
    plan = graph.get(quest_id)
    assert plan["status"] == "approved" and len(plan["nodes"]) == 1
    assert plan["approved_by"] == f"router_mandate:{mandate}"
    assert store.get_quest(quest_id)["status"] == "approved"
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)).fetchone()
        assert team["status"] == "approved" and team["approved_by"] == plan["approved_by"]
        assert json.loads(team["body_json"])["members"][0]["model_id"] == "mock-model"
        grant = c.execute("SELECT * FROM task_grants WHERE quest_id=?", (quest_id,)).fetchone()
        assert grant["resource_scope"].endswith(quest_id)
        assert c.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert service.prepare(quest_id)
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM quest_plans").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM team_proposals").fetchone()[0] == 1


def test_actor_string_cannot_forge_host_delegation(delegated):
    store, quest_id, mandate, graph, _teams, service = delegated
    service.prepare(quest_id)
    plan = graph.get(quest_id)
    with pytest.raises(DomainError, match="authenticated"):
        graph.approve(
            quest_id,
            plan["digest"],
            plan["plan_revision"],
            expected_version=store.get_quest(quest_id)["version"],
            actor=f"router_mandate:{mandate}",
        )


def test_mandate_never_authorizes_external_action_scope(delegated):
    _store, quest_id, mandate, graph, _teams, _service = delegated
    authority = DelegationAuthority(mandate)
    plan = graph.propose(
        quest_id,
        {
            "nodes": [
                {
                    "key": "bad",
                    "objective": "Network",
                    "success_criteria": ["delivery_review"],
                    "budget_tokens": 50000,
                    "allowed_tools": ["browser_get"],
                }
            ],
            "edges": [],
        },
        expected_version=0,
    )
    with pytest.raises(DomainError, match="mandate"):
        graph.approve(
            quest_id,
            plan["digest"],
            plan["plan_revision"],
            expected_version=1,
            actor=authority.actor,
            _delegation=authority,
        )
    assert graph.get(quest_id)["status"] == "proposed"


def test_revoked_mandate_stops_new_worker_admission(delegated):
    store, quest_id, mandate, _graph, teams, service = delegated
    service.prepare(quest_id)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE delegation_mandates SET state='revoked' WHERE id=?", (mandate,)
        )
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)).fetchone()
    with store.unit_of_work() as work, pytest.raises(DomainError, match="mandate"):
        teams.get_approved(
            quest_id, team["plan_revision"], team["proposal_digest"], connection=work.connection
        )


def test_scheduler_can_start_hidden_worker_from_autonomous_contract(delegated):
    class Mock:
        execution_mode = "mock"

        async def generate(self, request):
            raise AssertionError("This test only admits a hidden worker")

    store, quest_id, _mandate, graph, teams, service = delegated
    service.prepare(quest_id)
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": Mock()}), ToolEngine(store), team_authority=teams
    )
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)).fetchone()
    session = manager.start(
        quest_id, plan["id"], "task", team_digest=team["proposal_digest"], member_index=0
    )
    assert session["state"] == "queued"
    assert store.list_workers(quest_id)[0]["hidden"]
    assert len(store.list_messages()) == 1


def set_assignments(store, quest_id, assignments):
    with store.unit_of_work() as work:
        metadata = json.loads(work.get_quest(quest_id)["metadata"])
        metadata["independent_dispatches"] = assignments
        work.connection.execute(
            "UPDATE quests SET metadata=? WHERE id=?", (json.dumps(metadata), quest_id)
        )


@pytest.mark.parametrize("count", [2, 3, 4])
def test_independent_assignments_share_host_caps_and_recover_without_duplication(delegated, count):
    store, quest_id, mandate, graph, _teams, service = delegated
    assignments = [
        {"objective": f"Inspect independent source {index}", "role": "research"}
        for index in range(count)
    ]
    set_assignments(store, quest_id, assignments)
    with store.connection() as c:
        original = dict(
            c.execute("SELECT * FROM delegation_mandates WHERE id=?", (mandate,)).fetchone()
        )
    assert service.prepare(quest_id)
    plan = graph.get(quest_id)
    assert plan["edges"] == []
    assert [node["objective"] for node in plan["nodes"]] == [
        item["objective"] for item in assignments
    ]
    with store.connection() as c:
        proposal = c.execute(
            "SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)
        ).fetchone()
        body = json.loads(proposal["body_json"])
        policy = json.loads(original["policy_json"])
        members = body["members"]
        assert len(members) == count and len({item["role_id"] for item in members}) == count
        assert all(item["dynamic"] for item in members)
        assert [member["task_node_key"] for member in members] == [
            node["key"] for node in plan["nodes"]
        ]
        assert sum(node["budget_tokens"] for node in plan["nodes"]) == policy["max_tokens"]
        assert sum(member["estimated_tokens"] for member in members) <= policy["max_tokens"]
        assert sum(member["max_tool_calls"] for member in members) == policy["max_tools"]
        assert sum(member["max_duration_seconds"] for member in members) == policy["max_seconds"]
        assert body["limits"]["max_concurrency"] == min(count, 2)
        assert all(member["input_contract"] and member["output_schema"] for member in members)
        after = c.execute("SELECT * FROM delegation_mandates WHERE id=?", (mandate,)).fetchone()
        assert after["source_message_id"] == original["source_message_id"]
        assert after["source_sha256"] == original["source_sha256"]
    reopened = SuperagentDelegation(
        Store(store.path, workspace=store.workspace), graph, service.teams
    )
    assert reopened.prepare(quest_id)
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM quest_plans").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM team_proposals").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_assignment_roles_use_registry_capabilities_for_model_selection(delegated):
    store, quest_id, _mandate, _graph, _teams, service = delegated
    for identity, capabilities in (
        ("programmer", {"coding": 95, "mathematics": 5}),
        ("mathematician", {"coding": 5, "mathematics": 95}),
    ):
        store.save_model(
            ModelProfile(
                id=identity,
                display_name="Explicit Mock specialist " + identity,
                provider="mock-provider",
                model_id=identity,
                capabilities=capabilities,
                enabled=True,
                context_window=65536,
                max_output_tokens=8192,
                tool_calling_support=ProtocolSupport(supported=True, source="test"),
                structured_output_support=ProtocolSupport(supported=True, source="test"),
            )
        )
    set_assignments(
        store,
        quest_id,
        [
            {"objective": "Inspect the implementation", "role": "coding"},
            {"objective": "Check the mathematical argument", "role": "math"},
        ],
    )
    assert service.prepare(quest_id)
    with store.connection() as c:
        body = json.loads(c.execute("SELECT body_json FROM team_proposals").fetchone()[0])
    assert [member["model_id"] for member in body["members"]] == ["programmer", "mathematician"]
    assert body["candidate_audit"]["coding_1"]["selected"] == "programmer"
    assert body["candidate_audit"]["math_2"]["selected"] == "mathematician"


def test_each_assignment_admits_a_hidden_worker_with_its_own_objective_and_environment(delegated):
    class Mock:
        execution_mode = "mock"

        async def generate(self, request):
            raise AssertionError("This test admits Workers without model inference")

    store, quest_id, _mandate, graph, teams, service = delegated
    set_assignments(
        store,
        quest_id,
        [
            {"objective": "Inspect data source A", "role": "research"},
            {"objective": "Inspect data source B", "role": "research"},
        ],
    )
    assert service.prepare(quest_id)
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": Mock()}), ToolEngine(store), team_authority=teams
    )
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)).fetchone()
    sessions = [
        manager.start(
            quest_id,
            plan["id"],
            node["key"],
            team_digest=team["proposal_digest"],
            member_index=index,
        )
        for index, node in enumerate(plan["nodes"])
    ]
    with store.connection() as c:
        capsules = [
            json.loads(
                c.execute(
                    "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (run["id"],)
                ).fetchone()[0]
            )
            for run in sessions
        ]
    assert [item["objective"] for item in capsules] == [
        "Inspect data source A",
        "Inspect data source B",
    ]
    assert len({item["execution_environment"]["cwd"] for item in capsules}) == 2
    assert len(store.list_workers(quest_id)) == 2
    assert all(worker["hidden"] for worker in store.list_workers(quest_id))
    assert len(store.list_messages()) == 1


@pytest.mark.parametrize(
    "assignments",
    [
        [{"objective": "Too many", "role": "research"}] * 5,
        [{"objective": "   ", "role": "research"}],
        [{"objective": "Task", "role": "unregistered"}],
        [{"objective": "Task", "role": "research", "permission": "all"}],
    ],
)
def test_invalid_persisted_assignments_never_create_contracts(delegated, assignments):
    store, quest_id, _mandate, _graph, _teams, service = delegated
    set_assignments(store, quest_id, assignments)
    with pytest.raises(DomainError, match="dispatches"):
        service.prepare(quest_id)
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM quest_plans").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM delegation_contracts").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("estimated_tokens", 250001), ("max_tool_calls", 17), ("max_duration_seconds", 451)],
)
def test_team_authority_rejects_combined_overrun_even_when_each_member_fits(
    delegated, field, value
):
    store, quest_id, mandate, _graph, _teams, service = delegated
    set_assignments(
        store,
        quest_id,
        [{"objective": f"Independent task {index}", "role": "research"} for index in range(4)],
    )
    service.prepare(quest_id)
    with store.connection() as c:
        row = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)).fetchone()
        body = json.loads(row["body_json"])
        for member in body["members"]:
            member[field] = value
        with pytest.raises(DomainError, match="mandate"):
            DelegationAuthority(mandate).check(c, quest_id, "team", 1, "modified", body)


def test_changed_assignment_cannot_reinterpret_an_existing_plan(delegated):
    store, quest_id, _mandate, _graph, _teams, service = delegated
    assert service.prepare(quest_id)
    set_assignments(store, quest_id, [{"objective": "A different task", "role": "math"}])
    with pytest.raises(DomainError, match="contracts changed"):
        service.prepare(quest_id)
