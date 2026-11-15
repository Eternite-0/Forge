"""Serial admission preserves the original cap, complete cycles and future work."""

import copy

import pytest
from pydantic import ValidationError
from test_planner_capacity_v17 import handoff_graph
from test_router_intelligence_phase2 import ScriptedMock
from test_verified_capacity_v16 import generate, prepared

from forge_core.budget_policy import serial_start_allowance, validate_serial_policy
from forge_core.domain import DomainError
from forge_core.graph_domain import GraphProposal
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelCallContext
from forge_core.team_composition import TeamCompositionService


def serial_graph():
    data = handoff_graph()
    data.update(
        max_total_tokens=180000,
        coordination_reserve_tokens=18000,
        serial_capacity_policy={"version": "serial-capacity-v1", "shared_reserve_tokens": 100000},
    )
    for node in data["nodes"]:
        node["budget_tokens"] = 25000
    return data


def serial_team(tmp_path):
    store, quest = prepared(
        tmp_path,
        content='Assess inputs/unicode.json. Task execution limits: {"max_total_tokens":180000,"max_nodes":2,"max_members":2}',
    )
    graph = serial_graph()
    plan = generate(store, quest, ScriptedMock(graph))
    service = QuestGraphService(store)
    service.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    gateway = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="http://127.0.0.1:8788/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            allow_insecure_loopback=True,
            supports_tools=True,
            supports_json_schema=True,
        )
    )
    teams = TeamCompositionService(store, capacity_providers=ProviderPool({"p": gateway}))
    roles = [
        {
            "id": "research" if node["key"] == "n0" else "reviewer",
            "task_node_key": node["key"],
            "tools": node["allowed_tools"],
            "input_tokens": 22952,
            "output_tokens": 2048,
        }
        for node in graph["nodes"]
    ]
    tools = sorted({tool for node in graph["nodes"] for tool in node["allowed_tools"]})
    limits = {
        "max_members": 2,
        "max_depth": 1,
        "max_concurrency": 1,
        "max_total_tokens": 150000,
        "allowed_tools": tools,
    }
    team = teams.propose(quest.id, roles, limits, strategy="greedy", expected_version=2)
    teams.approve(quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user")
    ledger = ModelCallBudgetLedger(store.path, token_limit=180000, concurrency_limit=1)
    return store, quest, plan, team, roles, limits, teams, ledger


@pytest.mark.parametrize(
    "mutation", ["branch", "cycle_component", "failure", "cost", "overcap", "unknown_version"]
)
def test_serial_policy_is_explicit_token_only_connected_chain(mutation):
    graph = serial_graph()
    if mutation == "branch":
        graph["nodes"].append({**graph["nodes"][1], "key": "third"})
        graph["edges"].append({"source": "n0", "target": "third", "condition": "success"})
    elif mutation == "cycle_component":
        graph["nodes"].extend([{**graph["nodes"][0], "key": key} for key in ("c", "d")])
        graph["edges"].extend(
            [
                {"source": "c", "target": "d", "condition": "success"},
                {"source": "d", "target": "c", "condition": "success"},
            ]
        )
    elif mutation == "failure":
        graph["edges"][0]["condition"] = "failure"
    elif mutation == "cost":
        graph.update(max_cost_microunits=100, cost_currency="USD")
    elif mutation == "overcap":
        graph["serial_capacity_policy"]["shared_reserve_tokens"] = 170000
    else:
        graph["serial_capacity_policy"]["version"] = "serial-unbounded"
    with pytest.raises(DomainError):
        validate_serial_policy(graph)


def test_serial_plan_and_team_share_one_reserve_without_relabeling_baselines(tmp_path):
    store, quest, plan, team, _roles, _limits, _teams, ledger = serial_team(tmp_path)
    assert sum(node["budget_tokens"] for node in plan["nodes"]) == 50000
    assert sum(member["estimated_tokens"] for member in team["members"]) == 50000
    assert all(member["execution_token_limit"] == 125000 for member in team["members"])
    assert team["limits"]["max_total_tokens"] == 150000
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT * FROM quest_plans WHERE id=?", (plan["id"],)
        ).fetchone()
        allowance = serial_start_allowance(
            store,
            work.connection,
            work.get_quest(quest.id),
            row,
            plan["nodes"][0],
            team["members"][0],
            ledger,
        )
        assert allowance["baseline_tokens"] == 25000
        assert allowance["shared_reserve_tokens"] == 100000
        assert allowance["future_baseline_tokens"] == 25000
        assert 25000 <= allowance["execution_token_limit"] <= 125000
        assert (
            allowance["minimum_current_cycle"]
            >= team["members"][0]["capacity_preflight"]["minimum_node_tokens"]
        )


@pytest.mark.parametrize("mutation", ["unknown", "opening", "member", "future_baseline", "ledger"])
def test_serial_admission_fails_closed_without_refunding_original_ledger(tmp_path, mutation):
    store, quest, plan, team, _roles, _limits, _teams, ledger = serial_team(tmp_path)
    member = copy.deepcopy(team["members"][0])
    if mutation == "unknown":
        ledger.admit(
            ModelCallContext(
                call_id="unknown",
                purpose="planner",
                workspace_id=store.workspace.id,
                quest_id=quest.id,
            ),
            "p",
            "big",
            input_tokens=100,
            output_tokens=100,
            remote_timeout=1,
        )
    elif mutation in {"opening", "future_baseline"}:
        with store.unit_of_work() as work:
            work.connection.execute(
                "INSERT INTO model_budget_opening VALUES (?,?,?,?,?,?)",
                ("opening", store.workspace.id, quest.id, 100000, None, None),
            )
    elif mutation == "member":
        member["execution_token_limit"] += 1
    else:
        ledger = ModelCallBudgetLedger(store.path, token_limit=1000000, concurrency_limit=1)
    with store.unit_of_work() as work:
        before = work.connection.total_changes
        row = work.connection.execute(
            "SELECT * FROM quest_plans WHERE id=?", (plan["id"],)
        ).fetchone()
        with pytest.raises(DomainError):
            serial_start_allowance(
                store,
                work.connection,
                work.get_quest(quest.id),
                row,
                plan["nodes"][0],
                member,
                ledger,
            )
        assert work.connection.total_changes == before


@pytest.mark.parametrize("mutation", ["parallel", "partial_baseline", "unfunded_pool", "cost"])
def test_serial_team_cannot_weaken_approved_policy(tmp_path, mutation):
    store, quest, _plan, _team, roles, limits, teams, _ledger = serial_team(tmp_path)
    if mutation == "parallel":
        limits["max_concurrency"] = 2
    elif mutation == "partial_baseline":
        roles[0]["input_tokens"] -= 1
    elif mutation == "unfunded_pool":
        limits["max_total_tokens"] = 50000
    else:
        limits.update(max_cost_microunits=100000, currency="USD")
    with pytest.raises(DomainError):
        teams.propose(
            quest.id,
            roles,
            limits,
            strategy="greedy",
            expected_version=store.get_quest(quest.id)["version"],
        )


def test_ordinary_graph_sum_contract_is_unchanged():
    graph = serial_graph()
    graph.pop("serial_capacity_policy")
    for node in graph["nodes"]:
        node["required_capabilities"] = {}
        node["budget_tokens"] = 100000
    with pytest.raises((ValidationError, DomainError)):
        GraphProposal.model_validate(graph)
