"""New-source admission accounts for full Core handoffs and paid coordination."""

import copy
import json

import pytest
from pydantic import ValidationError
from test_router_intelligence_phase2 import ScriptedMock
from test_verified_capacity_v16 import capacity_graph, generate, prepared

from forge_core.capacity_preflight import coordination_budget_snapshot
from forge_core.domain import DomainError
from forge_core.graph_domain import (
    CapacityGraphWire,
    GraphProposal,
    GraphWire,
    graph_contract_dump,
    success_ancestors,
)
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelCallContext
from forge_core.team_composition import TeamCompositionService


def handoff_graph():
    graph = capacity_graph()
    graph.update(max_total_tokens=360000, coordination_reserve_tokens=36000)
    first = graph["nodes"][0]
    first.update(budget_tokens=140000, required_predecessor_deliveries=[])
    second = copy.deepcopy(first)
    second.update(
        key="reader",
        objective="Independently assess the complete approved predecessor findings.",
        required_input_paths=[],
        required_predecessor_deliveries=[first["key"]],
        allowed_tools=["worker_read_completed_delivery"],
        capacity_bounds={
            "max_source_receipt_bytes": 8192,
            "max_artifact_bytes": 1,
            "max_delivery_bytes": 3072,
        },
    )
    graph["nodes"].append(second)
    graph["edges"] = [{"source": first["key"], "target": "reader", "condition": "success"}]
    return graph


@pytest.mark.parametrize("wire", [GraphWire, CapacityGraphWire])
def test_all_exposed_wire_properties_are_required_without_defaults(wire):
    schema = wire.model_json_schema()
    objects = [schema, *schema["$defs"].values()]
    for value in objects:
        if value.get("type") == "object":
            assert set(value["properties"]) == set(value["required"])
            assert all("default" not in field for field in value["properties"].values())


@pytest.mark.parametrize("mutation", ["missing_edge", "failure_edge", "duplicate", "missing_tool"])
def test_handoff_contract_requires_direct_success_edge_and_explicit_tool(mutation):
    raw = handoff_graph()
    if mutation == "missing_edge":
        raw["edges"] = []
    elif mutation == "failure_edge":
        raw["edges"][0]["condition"] = "failure"
    elif mutation == "duplicate":
        raw["nodes"][1]["required_predecessor_deliveries"] *= 2
    else:
        raw["nodes"][1]["allowed_tools"] = []
    for node in raw["nodes"]:
        node["required_capabilities"] = {}
    with pytest.raises(ValidationError):
        GraphProposal.model_validate(raw)


def test_full_handoff_is_measured_as_source_and_bound_to_approved_member(tmp_path):
    store, quest = prepared(
        tmp_path,
        content='Assess inputs/unicode.json. Task execution limits: {"max_total_tokens":360000,"max_nodes":2,"max_members":2}',
    )
    graph = handoff_graph()
    service = QuestGraphService(store)
    plan = generate(store, quest, ScriptedMock(graph))
    measured = plan["capacity_contract"]["nodes"]["reader"]
    producer_bound = graph["nodes"][0]["capacity_bounds"]["max_delivery_bytes"]
    assert measured["future_input_bytes_bound"] >= producer_bound + 2048
    assert measured["known_receipt_bytes"] == 2
    assert plan["nodes"][1]["required_input_paths"] == []
    service.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    teams = TeamCompositionService(store, capacity_providers=ProviderPool({"p": ScriptedMock({})}))
    tools = sorted({tool for node in graph["nodes"] for tool in node["allowed_tools"]})
    roles = [
        {
            "id": "research" if node["key"] == "n0" else "reviewer",
            "task_node_key": node["key"],
            "tools": node["allowed_tools"],
            "input_tokens": 137952,
            "output_tokens": 2048,
        }
        for node in graph["nodes"]
    ]
    team = teams.propose(
        quest.id,
        roles,
        {
            "max_members": 2,
            "max_depth": 1,
            "max_concurrency": 1,
            "max_total_tokens": 280000,
            "allowed_tools": tools,
        },
        strategy="greedy",
        expected_version=2,
    )
    reader = next(item for item in team["members"] if item["task_node_key"] == "reader")
    assert reader["required_predecessor_deliveries"] == ["n0"]
    assert reader["review_envelope"]["max_source_bytes"] == 8192
    assert reader["verified_input_inventory"] == []


def test_explicit_success_ancestor_handoffs_preserve_a_strict_chain_and_full_sources(tmp_path):
    store, quest = prepared(
        tmp_path,
        content='Assess inputs/unicode.json. Task execution limits: {"max_total_tokens":630000,"max_nodes":3,"max_members":3}',
    )
    graph = handoff_graph()
    graph.update(max_total_tokens=630000, coordination_reserve_tokens=63000)
    final = copy.deepcopy(graph["nodes"][1])
    final.update(
        key="final", budget_tokens=200000, required_predecessor_deliveries=["n0", "reader"]
    )
    final["capacity_bounds"]["max_source_receipt_bytes"] = 16384
    graph["nodes"].append(final)
    graph["edges"].append({"source": "reader", "target": "final", "condition": "success"})
    plan = generate(store, quest, ScriptedMock(graph))
    assert plan["edges"] == graph["edges"]
    assert success_ancestors(graph["edges"], "final") == {"n0", "reader"}
    measured = plan["capacity_contract"]["nodes"]["final"]
    assert measured["future_input_bytes_bound"] >= sum(
        node["capacity_bounds"]["max_delivery_bytes"] + 2048 for node in graph["nodes"][:2]
    )


@pytest.mark.parametrize("condition", ["failure", "always"])
def test_ancestor_handoff_cannot_cross_a_non_success_edge(condition):
    graph = handoff_graph()
    graph["edges"][0]["condition"] = condition
    final = copy.deepcopy(graph["nodes"][1])
    final.update(key="final", required_predecessor_deliveries=["n0"])
    graph["nodes"][1]["required_predecessor_deliveries"] = []
    graph["nodes"].append(final)
    graph["edges"].append({"source": "reader", "target": "final", "condition": "success"})
    graph.update(max_total_tokens=630000, coordination_reserve_tokens=63000)
    for node in graph["nodes"]:
        node["required_capabilities"] = {}
    with pytest.raises(ValidationError, match="approved success ancestor"):
        GraphProposal.model_validate(graph)


def test_paid_coordination_reduces_future_reserve_without_refunding_usage(tmp_path):
    store, quest = prepared(tmp_path)
    ledger = ModelCallBudgetLedger(store.path, token_limit=180000, concurrency_limit=1)
    context = ModelCallContext(
        call_id="settled-plan",
        purpose="planner",
        workspace_id=store.workspace.id,
        quest_id=quest.id,
    )
    admitted = ledger.admit(
        context, "p", "big", input_tokens=5000, output_tokens=1000, remote_timeout=1
    )
    # Establish known settled usage through the real ledger's settlement API.
    ledger.settle(admitted, {"input_tokens": 4000, "output_tokens": 500})
    with store.connection() as connection:
        before = [dict(row) for row in connection.execute("SELECT * FROM model_budget_attempts")]
        measured = coordination_budget_snapshot(
            connection, store, quest.id, capacity_graph(), ledger
        )
        after = [dict(row) for row in connection.execute("SELECT * FROM model_budget_attempts")]
    assert before == after
    assert measured["remaining"] == 175500
    assert measured["future_coordination_reserve_tokens"] == 13500
    assert measured["max_worker_allocations_now"] == 162000


def test_legacy_graph_canonical_bytes_do_not_gain_handoff_fields():
    raw = capacity_graph()
    for key in ("capacity_bounds", "required_input_paths"):
        raw["nodes"][0].pop(key)
    for node in raw["nodes"]:
        node["required_capabilities"] = {}
    decoded = graph_contract_dump(GraphProposal.model_validate(raw))
    assert "required_predecessor_deliveries" not in decoded["nodes"][0]
    assert "worker_read_completed_delivery" not in json.dumps(decoded)


def test_planner_repair_gets_every_node_capacity_failure_and_serial_option(tmp_path):
    store, quest = prepared(
        tmp_path,
        content='Assess inputs/unicode.json. Task execution limits: {"max_total_tokens":360000,"max_nodes":2,"max_members":2}',
    )
    invalid = handoff_graph()
    for node in invalid["nodes"]:
        node["budget_tokens"] = 20000
    provider = ScriptedMock(invalid)
    with pytest.raises(DomainError, match="Invalid graph proposal"):
        generate(store, quest, provider)
    assert len(provider.requests) == 2
    feedback = json.loads(provider.requests[1].messages[-1]["content"])
    failures = feedback["capacity_diagnostics"]["all_rejected_nodes"]
    assert {item["node"] for item in failures} == {"n0", "reader"}
    for item in failures:
        policy = item["rejected_candidates"][0]["serial_capacity_option"]
        assert policy["suggested_policy"]["version"] == "serial-capacity-v1"
        assert policy["current_node_initial_baseline_suggestion"] > 2048
        assert "instead of null" in policy["required_changes"]
    assert QuestGraphService(store).history(quest.id) == []
