"""V21 single-declaration wire; no network calls or model-generated acceptance claim."""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_core_measured_serial_v17 import NativeFramingMock
from test_verified_capacity_v16 import prepared

from forge_core.capacity_preflight import discover_input_inventory
from forge_core.graph_domain import GraphProposal
from forge_core.planner_dataflow import DataflowGraphWire
from forge_core.quest_graph import InvalidGraphProposal, QuestGraphService
from forge_core.source_capacity import bind_source_receipt_bounds

ROOT = Path(__file__).resolve().parents[2]


def encoded_fixture(graph):
    """Explicit encoder for already selected fixtures; never used by production."""
    nodes = []
    all_artifacts = {
        node["expected_artifact"]: node["key"]
        for node in graph["nodes"]
        if node.get("expected_artifact")
    }
    for original in graph["nodes"]:
        node = copy.deepcopy(original)
        node.pop("budget_tokens")
        node["required_capabilities"] = [
            {"name": key, "minimum": value} for key, value in node["required_capabilities"].items()
        ]
        node["capacity_bounds"].pop("max_source_receipt_bytes")
        node["success_criteria"] = [
            {
                "kind": criterion
                if criterion in {"delivery_review", "json_review:@delivery"}
                else criterion[: -(len(node["expected_artifact"]) + 1)]
            }
            for criterion in node["success_criteria"]
        ]
        inputs = node.pop("required_input_paths")
        deliveries = node.pop("required_predecessor_deliveries", [])
        node["verified_input_paths"] = [path for path in inputs if path not in all_artifacts]
        node["predecessors"] = []
        for edge in graph["edges"]:
            if edge["target"] == node["key"]:
                producer = next(item for item in graph["nodes"] if item["key"] == edge["source"])
                node["predecessors"].append(
                    {
                        "node_key": edge["source"],
                        "condition": edge["condition"],
                        "read_artifact": producer.get("expected_artifact") in inputs,
                        "delivery_sources": [key for key in deliveries if key == producer["key"]],
                    }
                )
        assert set(deliveries) == {
            key for item in node["predecessors"] for key in item["delivery_sources"]
        }, "Fixture must choose an explicit dependency for ancestor deliveries"
        nodes.append(node)
    return {"allocation_mode": "core_measured", "nodes": nodes}


def golden(level=3):
    fixtures = json.loads(
        (ROOT / "backend/tests/fixtures/phase2_constructed_dataflow_v21.json").read_text(
            encoding="utf-8"
        )
    )
    return fixtures[level - 1]["graph"]


def canonical(wire, **kwargs):
    verified_paths = {
        path
        for level in (1, 2, 3)
        for node in golden(level)["nodes"]
        for path in node["required_input_paths"]
        if path.startswith("inputs/")
    }
    return DataflowGraphWire.model_validate(wire).proposal_data(
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
        verified_paths=kwargs.pop("verified_paths", verified_paths),
        **kwargs,
    )


@pytest.mark.parametrize("level", [1, 2, 3])
def test_selected_constructed_graph_roundtrips_semantics_and_exact_edges(level):
    graph = golden(level)
    wire = encoded_fixture(graph)
    original = copy.deepcopy(wire)
    result = canonical(wire)
    assert wire == original
    assert result["edges"] == graph["edges"]
    for actual, previous in zip(result["nodes"], graph["nodes"], strict=True):
        expected = {key: value for key, value in previous.items() if key != "budget_tokens"}
        expected["capacity_bounds"] = {
            key: value
            for key, value in expected["capacity_bounds"].items()
            if key != "max_source_receipt_bytes"
        }
        assert {key: value for key, value in actual.items() if key != "budget_tokens"} == expected


def test_ancestor_deliveries_do_not_add_transitive_edges_or_break_serial_shape():
    wire = encoded_fixture(golden(2))
    wire["nodes"][2]["predecessors"][0]["delivery_sources"] = ["research", "architect"]
    result = canonical(wire)
    assert result["nodes"][2]["required_predecessor_deliveries"] == [
        "research",
        "architect",
    ]
    assert result["edges"] == golden(2)["edges"]


@pytest.mark.parametrize("kind", ["unknown", "self", "forward", "cycle", "duplicate"])
def test_invalid_dependency_is_rejected_without_reordering_or_repair(kind):
    wire = encoded_fixture(golden(2))
    dep = wire["nodes"][1]["predecessors"][0]
    if kind == "unknown":
        dep["node_key"] = "missing"
    elif kind == "self":
        dep["node_key"] = "architect"
    elif kind in {"forward", "cycle"}:
        if kind == "forward":
            wire["nodes"][2]["predecessors"] = []
        wire["nodes"][0]["predecessors"] = [
            {
                "node_key": "writer",
                "condition": "success",
                "read_artifact": False,
                "delivery_sources": [],
            }
        ]
    else:
        wire["nodes"][1]["predecessors"].append(copy.deepcopy(dep))
    before = copy.deepcopy(wire)
    with pytest.raises(ValueError):
        canonical(wire)
    assert wire == before


@pytest.mark.parametrize(
    "key,value", [("edges", []), ("max_total_tokens", 1000000), ("approved", True)]
)
def test_model_cannot_supply_parallel_edges_resources_or_approval(key, value):
    wire = encoded_fixture(golden(1))
    wire[key] = value
    with pytest.raises(ValidationError):
        canonical(wire)


@pytest.mark.parametrize(
    "node_index,field,value",
    [
        (0, "allowed_tools", ["process_arbitrary"]),
        (0, "allowed_tools", ["directory_list"]),
        (1, "allowed_tools", ["file_read"]),
        (2, "allowed_tools", ["worker_read_completed_delivery"]),
        (2, "expected_artifact", None),
        (0, "budget_tokens", 999999),
        (0, "required_input_paths", ["work/future.json"]),
    ],
)
def test_unselected_authority_and_missing_contract_tools_are_rejected(node_index, field, value):
    wire = encoded_fixture(golden(2))
    wire["nodes"][node_index][field] = value
    with pytest.raises(ValidationError):
        canonical(wire)


def test_artifact_reference_creates_its_one_edge_and_exact_producer_path():
    wire = encoded_fixture(golden(3))
    result = canonical(wire)
    experiment = result["nodes"][4]
    assert experiment["required_input_paths"] == ["work/l3-scheduler.py"]
    assert experiment["required_predecessor_deliveries"] == ["coding"]
    assert [edge for edge in result["edges"] if edge["target"] == "experiment"] == [
        {"source": "coding", "target": "experiment", "condition": "success"}
    ]
    wire["nodes"][3]["expected_artifact"] = None
    wire["nodes"][3]["success_criteria"] = [{"kind": "delivery_review"}]
    with pytest.raises(ValueError, match="producer_has_no_expected_artifact"):
        canonical(wire)


def test_completed_success_ancestor_contracts_are_preserved_exactly():
    graph = golden(2)
    wire = encoded_fixture(graph)
    wire["nodes"] = wire["nodes"][2:]
    wire["nodes"][0]["predecessors"][0]["delivery_sources"] = ["research", "architect"]
    completed = copy.deepcopy(graph["nodes"][:2])
    edges = copy.deepcopy(graph["edges"][:1])
    before = copy.deepcopy((completed, edges))
    result = canonical(wire, completed_nodes=completed, completed_edges=edges)
    assert result["nodes"][-2:] == completed
    assert result["edges"] == graph["edges"]
    assert result["nodes"][0]["required_predecessor_deliveries"] == [
        "research",
        "architect",
    ]
    assert (completed, edges) == before


def test_non_ancestor_delivery_and_failure_output_reads_are_rejected():
    wire = encoded_fixture(golden(2))
    wire["nodes"][1]["predecessors"][0]["delivery_sources"] = ["writer"]
    with pytest.raises(ValueError, match="success_ancestor"):
        canonical(wire)
    wire = encoded_fixture(golden(2))
    wire["nodes"][1]["predecessors"][0]["condition"] = "failure"
    with pytest.raises(ValidationError, match="output_reads_require_success"):
        canonical(wire)


@pytest.mark.parametrize("condition", ["success", "failure", "always"])
def test_control_only_dependency_preserves_the_selected_condition(condition):
    wire = encoded_fixture(golden(2))
    wire["nodes"][1]["predecessors"][0].update(condition=condition, delivery_sources=[])
    result = canonical(wire)
    assert result["edges"][0]["condition"] == condition
    assert result["nodes"][1]["required_predecessor_deliveries"] == []


def test_v21_native_generate_has_only_single_declarations_and_normal_approval(tmp_path):
    store, quest = prepared(tmp_path)
    node = encoded_fixture(golden(1))["nodes"][0]
    node["verified_input_paths"] = ["inputs/unicode.json"]
    node["required_capabilities"] = []
    provider = NativeFramingMock({"allocation_mode": "core_measured", "nodes": [node]})
    plan = asyncio.run(
        QuestGraphService(store).generate(
            quest.id,
            expected_version=0,
            provider=provider,
            provider_id="p",
            model_id="big",
            capabilities={"json_schema": True},
            capability_sources={"json_schema": "test_fixture"},
            context_limit=65536,
            require_capacity_contract=True,
            resource_allocation_mode="core_measured",
            semantic_protocol_version="v21",
        )
    )
    assert plan["status"] == "proposed"
    assert plan["edges"] == []
    assert len(provider.requests) == 1
    schema = provider.requests[0].response_format["json_schema"]["schema"]
    assert set(schema["properties"]) == {"allocation_mode", "nodes"}
    assert "artifact_path" not in schema["$defs"]["DataflowVerifierWire"]["properties"]
    assert plan["nodes"][0]["success_criteria"] == ["delivery_review"]
    QuestGraphService(store).approve(
        quest.id, plan["digest"], 1, expected_version=1, actor="local_user"
    )
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 0


def test_v21_rejects_legacy_wire_twice_without_falling_back(tmp_path):
    store, quest = prepared(tmp_path)
    provider = NativeFramingMock({"allocation_mode": "core_measured", "nodes": [], "edges": []})
    with pytest.raises(InvalidGraphProposal):
        asyncio.run(
            QuestGraphService(store).generate(
                quest.id,
                expected_version=0,
                provider=provider,
                provider_id="p",
                model_id="big",
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                context_limit=65536,
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
                semantic_protocol_version="v21",
                output_limit=8192,
            )
        )
    assert len(provider.requests) == 2
    assert QuestGraphService(store).history(quest.id) == []


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize("repair", [False, True])
def test_v21_full_actual_router_shape_and_native_schema_fit_both_stages(tmp_path, level, repair):
    from test_planner_router_input_v18 import Captured, NativeCapture, route_original_scenario

    store, quest, original, observed = route_original_scenario(tmp_path, level)
    provider = NativeCapture(repair=repair)
    with pytest.raises(Captured):
        asyncio.run(
            QuestGraphService(store).generate(
                quest["id"],
                expected_version=quest["version"],
                provider=provider,
                provider_id="p",
                model_id="native",
                context_limit=65536,
                output_limit=8192,
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
                semantic_protocol_version="v21",
            )
        )
    assert len(provider.requests) == (2 if repair else 1)
    for request, size in zip(provider.requests, provider.native_sizes, strict=True):
        context = json.loads(request.messages[1]["content"])
        assert context["original_request"]["content"] == original
        assert context["goal"] == observed["goal"]
        assert context["user_constraints"]["items"] and context["verified_input_inventory"]
        assert context["semantic_protocol_version"] == "v21"
        assert size + request.max_output_tokens <= request.context_limit == 65536
        assert len(request.messages[0]["content"].encode()) < 3200
        assert set(request.response_format["json_schema"]["schema"]["properties"]) == {
            "allocation_mode",
            "nodes",
        }


def test_v21_schema_has_one_source_for_paths_criteria_and_edges():
    schema = DataflowGraphWire.model_json_schema()
    for value in [schema, *schema["$defs"].values()]:
        if value.get("type") == "object":
            assert value["additionalProperties"] is False
            assert set(value["properties"]) == set(value["required"])
    properties = schema["$defs"]["DataflowNodeWire"]["properties"]
    assert (
        not {"required_input_paths", "required_predecessor_deliveries", "edges", "budget_tokens"}
        & properties.keys()
    )
    wire = encoded_fixture(golden(1))
    wire["nodes"][0]["success_criteria"][0]["artifact_path"] = None
    with pytest.raises(ValidationError):
        canonical(wire)


def test_verified_inventory_channel_rejects_unknown_and_future_artifact_paths():
    wire = encoded_fixture(golden(3))
    wire["nodes"][4]["predecessors"][0]["read_artifact"] = False
    wire["nodes"][4]["verified_input_paths"] = ["work/l3-scheduler.py"]
    before = copy.deepcopy(wire)
    with pytest.raises(ValueError):
        canonical(wire)
    assert wire == before


def test_actual_generation_cannot_read_artifact_through_a_control_only_verified_path(tmp_path):
    from forge_core.capacity_preflight import CapacityPreflightError

    store, quest = prepared(
        tmp_path,
        content='Inspect inputs/unicode.json. Task execution limits: {"max_total_tokens":360000,"max_nodes":2,"max_members":2}',
    )
    producer = encoded_fixture(golden(1))["nodes"][0]
    producer.update(
        key="producer",
        verified_input_paths=["inputs/unicode.json"],
        required_capabilities=[],
        expected_artifact="work/producer.json",
        allowed_tools=["file_read", "file_write", "file_revise"],
        success_criteria=[{"kind": "json_review"}],
    )
    consumer = copy.deepcopy(producer)
    consumer.update(
        key="consumer",
        expected_artifact=None,
        verified_input_paths=["work/producer.json"],
        allowed_tools=["file_read"],
        success_criteria=[{"kind": "delivery_review"}],
        predecessors=[
            {
                "node_key": "producer",
                "condition": "success",
                "read_artifact": False,
                "delivery_sources": [],
            }
        ],
    )
    wire = {"allocation_mode": "core_measured", "nodes": [producer, consumer]}
    with pytest.raises(CapacityPreflightError, match="verified inventory files only"):
        canonical(wire, verified_paths=["inputs/unicode.json"])
    provider = NativeFramingMock(wire)
    with pytest.raises(InvalidGraphProposal):
        asyncio.run(
            QuestGraphService(store).generate(
                quest.id,
                expected_version=0,
                provider=provider,
                provider_id="p",
                model_id="big",
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                context_limit=65536,
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
                semantic_protocol_version="v21",
            )
        )
    assert len(provider.requests) == 2
    feedback = json.loads(provider.requests[1].messages[-1]["content"])
    diagnostics = feedback["capacity_diagnostics"]["semantic_diagnostics"]
    assert diagnostics["scope"] == "v21_verified_inventory_selection"
    assert diagnostics["invalid_input_paths"] == [
        {"node": "consumer", "path": "work/producer.json", "source_field": "verified_input_paths"}
    ]
    assert QuestGraphService(store).history(quest.id) == []
    assert wire["nodes"][1]["predecessors"][0]["read_artifact"] is False


def test_edge_limit_rejects_explicit_dense_dag_without_dropping_dependencies():
    base = encoded_fixture(golden(1))["nodes"][0]
    base.update(verified_input_paths=[], allowed_tools=[])
    wire = {"allocation_mode": "core_measured", "nodes": []}
    for index in range(8):
        node = {
            **copy.deepcopy(base),
            "key": "n" + str(index),
            "predecessors": [
                {
                    "node_key": "n" + str(parent),
                    "condition": "success",
                    "read_artifact": False,
                    "delivery_sources": [],
                }
                for parent in range(index)
            ],
        }
        wire["nodes"].append(node)
    before = copy.deepcopy(wire)
    with pytest.raises(ValueError, match="source_or_edge_limit"):
        canonical(wire)
    assert wire == before


@pytest.mark.parametrize("level", [1, 2, 3])
def test_constructed_original_router_source_capacity_is_feasible(tmp_path, level):
    from test_planner_router_input_v18 import route_original_scenario

    from forge_core.providers import ProviderConfig, ProviderGateway

    store, quest, original, _ = route_original_scenario(tmp_path, level)
    graph = golden(level)
    wire = encoded_fixture(graph)
    limits = {
        key: graph[key]
        for key in (
            "max_total_tokens",
            "max_cost_microunits",
            "cost_currency",
            "max_duration_seconds",
        )
    }
    data = DataflowGraphWire.model_validate(wire).proposal_data(
        resource_limits=limits,
        coordination_reserve_tokens=graph["coordination_reserve_tokens"],
        verified_paths=[
            path
            for node in graph["nodes"]
            for path in node["required_input_paths"]
            if path.startswith("inputs/")
        ],
    )
    from forge_core.constraints import execution_constraints

    with store.connection() as connection:
        data["execution_constraints"] = execution_constraints(connection, quest["id"])
    inventory = discover_input_inventory(store, original)
    binder = bind_source_receipt_bounds(store, data, inventory)
    provider = ProviderGateway(
        ProviderConfig(
            id="wb-local",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            supports_json_schema=True,
            reasoning_effort="none",
        )
    )
    service = QuestGraphService(store)
    service._size_core_resources(quest, data, inventory, provider)
    service._capacity_preflight(quest, data, inventory, provider)
    GraphProposal.model_validate(data)
    assert set(binder["nodes"]) == {node["key"] for node in data["nodes"]}
    assert data["edges"] == graph["edges"]
    assert {item["path"] for item in inventory} <= {
        path for node in data["nodes"] for path in node["required_input_paths"]
    }
    assert all(
        node["capacity_bounds"]["max_source_receipt_bytes"] <= 65536 for node in data["nodes"]
    )
