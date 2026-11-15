"""Explicit Core sizing measures resources, never supplies a semantic plan."""

import copy
import json

import pytest
from test_planner_capacity_v17 import handoff_graph
from test_router_intelligence_phase2 import ScriptedMock
from test_verified_capacity_v16 import generate, prepared

from forge_core.capacity_preflight import verified_input_inventory
from forge_core.domain import DomainError
from forge_core.graph_domain import CapacityGraphWire
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.quest_graph import QuestGraphService
from forge_core.team_composition import TeamCompositionService


class NativeFramingMock(ProviderGateway):
    """Mock output plus production native serializer; no network calls."""

    def __init__(self, graph):
        super().__init__(
            ProviderConfig(
                id="p",
                kind="openai_compatible",
                base_url="https://example.invalid/v1",
                credential_ref="FORGE_PROVIDER_KEY_TEST",
                supports_tools=True,
                supports_json_schema=True,
            )
        )
        self.graph = graph
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return type("MockResponse", (), {"text": json.dumps(self.graph), "usage": {}})()


def measured_graph():
    graph = handoff_graph()
    graph["allocation_mode"] = "core_measured_serial"
    graph["serial_capacity_policy"] = None
    # Deliberately impossible guesses: only explicit Core mode may replace them.
    for node in graph["nodes"]:
        node["budget_tokens"] = 900000
    return graph


def prepared_chain(tmp_path):
    return prepared(
        tmp_path,
        content='Assess inputs/unicode.json. Task execution limits: {"max_total_tokens":360000,"max_nodes":2,"max_members":2}',
    )


def test_explicit_native_sizing_preserves_every_semantic_field_and_needs_approval(tmp_path):
    store, quest = prepared_chain(tmp_path)
    graph = measured_graph()
    provider = NativeFramingMock(graph)
    before = copy.deepcopy(graph)
    plan = generate(store, quest, provider)
    assert graph == before
    assert len(provider.requests) == 1
    assert plan["status"] == "proposed"
    assert store.get_quest(quest.id)["status"] != "approved"
    assert plan["edges"] == graph["edges"]
    for node, expected in zip(plan["nodes"], graph["nodes"], strict=True):
        expected = {
            **expected,
            "required_capabilities": {
                item["name"]: item["minimum"] for item in expected["required_capabilities"]
            },
        }
        actual = {key: node[key] for key in expected if key != "budget_tokens"}
        assert actual == {key: value for key, value in expected.items() if key != "budget_tokens"}
        assert (
            node["budget_tokens"]
            == plan["capacity_contract"]["nodes"][node["key"]]["initial_work_tokens"] + 4096
        )
    shared = plan["serial_capacity_policy"]["shared_reserve_tokens"]
    assert shared == max(
        item["minimum_node_tokens"]
        - item["initial_work_tokens"]
        + (2 + item["review_envelope"]["correction_calls"]) * 4096
        for item in plan["capacity_contract"]["nodes"].values()
    )
    assert (
        sum(node["budget_tokens"] for node in plan["nodes"])
        + shared
        + plan["coordination_reserve_tokens"]
        <= graph["max_total_tokens"]
    )
    provenance = plan["capacity_contract"]["resource_allocation"]
    assert provenance["fixed_point_verified"] is True
    assert 2 <= provenance["iterations"] <= 8
    assert all(
        item["initial_worker_request_measurement"]["native_input_reservation"] > 0
        for item in plan["capacity_contract"]["nodes"].values()
    )
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["total", "raw_packet", "context", "branch", "legacy"])
def test_impossible_measured_semantics_are_rejected_without_changing_bounds(tmp_path, mutation):
    store, quest = prepared_chain(tmp_path)
    graph = measured_graph()
    if mutation == "total":
        graph.update(max_total_tokens=30000, coordination_reserve_tokens=3000)
    elif mutation == "raw_packet":
        graph["nodes"][0]["capacity_bounds"]["max_source_receipt_bytes"] = 65536
    elif mutation == "context":
        profile = store.list_models()[0]
        profile.context_window = 4096
        store.save_model(profile)
    elif mutation == "branch":
        graph["edges"] = []
    else:
        graph["allocation_mode"] = "model_declared"
    before = copy.deepcopy(graph)
    provider = ScriptedMock(graph)
    with pytest.raises(DomainError, match="Invalid graph proposal"):
        generate(store, quest, provider)
    assert len(provider.requests) == 2
    assert graph == before
    assert QuestGraphService(store).history(quest.id) == []


def test_measured_wire_cannot_skip_runtime_resource_quantification():
    with pytest.raises(ValueError, match="native capacity measurement"):
        CapacityGraphWire.model_validate(measured_graph()).proposal()


def test_actual_team_contract_is_remeasured_with_approved_role_cycle_headroom(tmp_path):
    store, quest = prepared_chain(tmp_path)
    graph = measured_graph()
    provider = NativeFramingMock(graph)
    plan = generate(store, quest, provider)
    QuestGraphService(store).approve(
        quest.id, plan["digest"], 1, expected_version=1, actor="local_user"
    )
    teams = TeamCompositionService(store, capacity_providers=ProviderPool({"p": provider}))
    roles = [
        {
            "id": "research" if index == 0 else "reviewer",
            "task_node_key": node["key"],
            "tools": node["allowed_tools"],
            "responsibility": node["objective"] + " Preserve exact sourced conclusions." * 16,
            "input_tokens": node["budget_tokens"] - 2048,
            "output_tokens": 2048,
        }
        for index, node in enumerate(plan["nodes"])
    ]
    total = (
        sum(node["budget_tokens"] for node in plan["nodes"])
        + plan["serial_capacity_policy"]["shared_reserve_tokens"]
    )
    team = teams.propose(
        quest.id,
        roles,
        {
            "max_members": 2,
            "max_depth": 1,
            "max_concurrency": 1,
            "max_total_tokens": total,
            "allowed_tools": sorted(
                {tool for node in plan["nodes"] for tool in node["allowed_tools"]}
            ),
        },
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user")
    for member in team["members"]:
        original = plan["capacity_contract"]["nodes"][member["task_node_key"]]
        actual = member["capacity_preflight"]
        assert actual["initial_work_tokens"] > original["initial_work_tokens"]
        assert actual["minimum_node_tokens"] <= member["execution_token_limit"]
        assert actual["native_envelope_fixed_point_iterations"] >= 2
    assert len(provider.requests) == 1


def test_nonconverging_measurement_is_bounded_and_rejected(tmp_path, monkeypatch):
    store, quest = prepared_chain(tmp_path)
    service = QuestGraphService(store)
    calls = []

    def changing_measurement(_quest, data, _inventory, _provider, **_kwargs):
        calls.append(1)
        data["capacity_contract"] = {
            "nodes": {
                node["key"]: {
                    "initial_work_tokens": node["budget_tokens"] + 1,
                    "minimum_node_tokens": node["budget_tokens"] + 10001,
                    "criterion_mode": "independent_model_review",
                    "review_envelope": {"correction_calls": 1},
                }
                for node in data["nodes"]
            }
        }

    monkeypatch.setattr(service, "_capacity_preflight", changing_measurement)
    data = CapacityGraphWire.model_validate(measured_graph()).proposal_data()
    data.pop("allocation_mode")
    with pytest.raises(DomainError, match="eight deterministic passes"):
        service._size_serial_resources(dict(store.get_quest(quest.id)), data, [], ScriptedMock({}))
    assert len(calls) == 8


def test_remaining_sizing_keeps_completed_baseline_and_original_pool(tmp_path):
    store, quest = prepared_chain(tmp_path)
    graph = measured_graph()
    plan = generate(store, quest, NativeFramingMock(graph))
    with store.connection() as connection:
        data = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE id=?", (plan["id"],)
            ).fetchone()[0]
        )
    original = copy.deepcopy(data["nodes"][0])
    policy = copy.deepcopy(data["serial_capacity_policy"])
    with store.connection() as connection:
        before = connection.total_changes
    observation = QuestGraphService(store)._size_serial_resources(
        dict(store.get_quest(quest.id)),
        data,
        verified_input_inventory(store, ["inputs/unicode.json"]),
        NativeFramingMock(graph),
        completed_keys=[original["key"]],
        preserved_policy=policy,
    )
    assert data["nodes"][0] == original
    assert data["serial_capacity_policy"] == policy
    assert observation["shared_reserve_preserved"] is True
    assert observation["completed_baselines_preserved"] == [original["key"]]
    with store.connection() as connection:
        assert connection.total_changes == before
