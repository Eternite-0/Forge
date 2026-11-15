"""Semantic-only autonomous plans and bounded durable rejection; offline models."""

import asyncio
import copy
import importlib
import json
import secrets

import pytest
from planner_capacity_fixtures import configure_planner_capacity, dataflow_fixture
from pydantic import ValidationError
from test_core_measured_serial_v17 import NativeFramingMock
from test_event_dispatcher import drain_dispatcher
from test_planner_capacity_v17 import handoff_graph
from test_planner_router_input_v18 import Captured, NativeCapture, route_original_scenario
from test_router_intelligence_phase2 import ScriptedMock
from test_verified_capacity_v16 import capacity_graph, prepared

from forge_core.capacity_preflight import discover_input_inventory
from forge_core.domain import ForgeEvent, Quest
from forge_core.graph_domain import CoreMeasuredGraphWire
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderError
from forge_core.quest_graph import InvalidGraphProposal, QuestGraphService
from forge_core.source_capacity import bind_source_receipt_bounds
from forge_core.team_composition import TeamCompositionService


def semantic_wire(graph):
    nodes = []
    for node in graph["nodes"]:
        node = copy.deepcopy(node)
        node.pop("budget_tokens")
        node.setdefault("required_predecessor_deliveries", [])
        node["success_criteria"] = [
            typed_criterion(criterion) for criterion in node["success_criteria"]
        ]
        node["capacity_bounds"].pop("max_source_receipt_bytes")
        nodes.append(node)
    return {"allocation_mode": "core_measured", "nodes": nodes, "edges": graph["edges"]}


def typed_criterion(criterion):
    if criterion in {"delivery_review", "json_review:@delivery"}:
        return {"kind": criterion, "artifact_path": None}
    for kind in ("semantic_numeric:scheduler-v1", "json_review", "json_valid"):
        if criterion.startswith(kind + ":"):
            return {"kind": kind, "artifact_path": criterion[len(kind) + 1 :]}
    raise ValueError("Unsupported explicit test verifier")


def generate(store, quest, provider, **kwargs):
    return asyncio.run(
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
            **kwargs,
        )
    )


@pytest.mark.parametrize("shape", ["single", "chain", "branch"])
def test_core_protocol_quantifies_single_chain_and_independent_branches(tmp_path, shape):
    store, quest = prepared(
        tmp_path,
        content='Assess inputs/unicode.json. Task execution limits: {"max_total_tokens":360000,"max_nodes":3,"max_members":3}',
    )
    graph = capacity_graph() if shape == "single" else handoff_graph()
    if shape == "branch":
        last = copy.deepcopy(graph["nodes"][1])
        last["key"] = "branch"
        graph["nodes"].append(last)
        graph["edges"].append({"source": "n0", "target": "branch", "condition": "success"})
    wire = semantic_wire(graph)
    original = copy.deepcopy(wire)
    expected_data = CoreMeasuredGraphWire.model_validate(wire).proposal_data(
        resource_limits={"max_total_tokens": 360000}, coordination_reserve_tokens=36000
    )
    bind_source_receipt_bounds(
        store, expected_data, discover_input_inventory(store, "Assess inputs/unicode.json.")
    )
    provider = NativeFramingMock(wire)
    plan = generate(store, quest, provider)
    assert wire == original
    assert len(provider.requests) == 1 and plan["status"] == "proposed"
    assert plan["max_total_tokens"] == 360000
    assert plan["coordination_reserve_tokens"] == 36000
    observation = plan["capacity_contract"]["resource_allocation"]
    assert observation["mode"] == "core_measured" and observation["fixed_point_verified"] is True
    assert observation["shape"] == (
        "strict_success_chain" if shape == "chain" else "independent_cycles"
    )
    for expected, actual in zip(wire["nodes"], plan["nodes"], strict=True):
        expected = {
            **expected,
            "required_capabilities": {
                item["name"]: item["minimum"] for item in expected["required_capabilities"]
            },
            "success_criteria": [
                item["kind"]
                if item["artifact_path"] is None
                else item["kind"] + ":" + item["artifact_path"]
                for item in expected["success_criteria"]
            ],
            "capacity_bounds": next(
                node["capacity_bounds"]
                for node in expected_data["nodes"]
                if node["key"] == expected["key"]
            ),
        }
        assert all(actual[key] == value for key, value in expected.items())
    if shape != "chain":
        assert "serial_capacity_policy" not in plan
        for node in plan["nodes"]:
            item = plan["capacity_contract"]["nodes"][node["key"]]
            assert (
                node["budget_tokens"]
                == item["minimum_node_tokens"]
                + observation["role_framing_policy_cycle_calls"][node["key"]] * 1024
            )
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


def test_production_schema_has_no_model_resource_fields_or_legacy_mode():
    schema = CoreMeasuredGraphWire.model_json_schema()
    assert set(schema["properties"]) == {"allocation_mode", "nodes", "edges"}
    assert schema["properties"]["allocation_mode"]["const"] == "core_measured"
    assert "budget_tokens" not in schema["$defs"]["CoreMeasuredNodeWire"]["properties"]
    for value in [schema, *schema["$defs"].values()]:
        if value.get("type") == "object":
            assert set(value["properties"]) == set(value["required"])
    wire = semantic_wire(capacity_graph())
    wire["max_total_tokens"] = 1000000
    with pytest.raises(ValidationError):
        CoreMeasuredGraphWire.model_validate(wire)
    wire.pop("max_total_tokens")
    wire["allocation_mode"] = "model_declared"
    with pytest.raises(ValidationError):
        CoreMeasuredGraphWire.model_validate(wire)


def test_core_uses_original_operator_intersection_without_accepting_model_caps(tmp_path):
    store, quest = prepared(tmp_path)
    provider = NativeFramingMock(semantic_wire(capacity_graph()))
    plan = generate(store, quest, provider, operator_max_total_tokens=120000)
    assert plan["max_total_tokens"] == 120000
    assert plan["coordination_reserve_tokens"] == 12000
    assert sum(node["budget_tokens"] for node in plan["nodes"]) + 12000 <= 120000


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize("repair", [False, True])
def test_semantic_protocol_uses_complete_actual_router_shapes_in_both_native_requests(
    tmp_path, level, repair
):
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
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
            )
        )
    assert len(provider.requests) == (2 if repair else 1)
    for request, measured in zip(provider.requests, provider.native_sizes, strict=True):
        data = json.loads(request.messages[1]["content"])
        assert data["original_request"]["content"] == original
        assert data["goal"] == observed["goal"]
        assert data["user_constraints"]["items"] and data["verified_input_inventory"]
        assert "serial_capacity_option" not in data
        assert data["resource_policy"]["allocation_mode"] == "core_measured"
        assert len(request.messages[0]["content"].encode()) < 3200
        assert measured + request.max_output_tokens <= 65536
        assert set(request.response_format["json_schema"]["schema"]["properties"]) == {
            "allocation_mode",
            "nodes",
            "edges",
        }


def test_selected_team_is_remeasured_and_approved_with_explicit_stage_margin(tmp_path):
    store, quest = prepared(tmp_path)
    provider = NativeFramingMock(semantic_wire(capacity_graph()))
    plan = generate(store, quest, provider)
    QuestGraphService(store).approve(
        quest.id, plan["digest"], 1, expected_version=1, actor="local_user"
    )
    node = plan["nodes"][0]
    teams = TeamCompositionService(store, capacity_providers=ProviderPool({"p": provider}))
    team = teams.propose(
        quest.id,
        [
            {
                "id": "research",
                "task_node_key": node["key"],
                "tools": node["allowed_tools"],
                "responsibility": node["objective"]
                + " Preserve exact sources and distinguish limitations." * 6,
                "input_tokens": node["budget_tokens"] - 2048,
                "output_tokens": 2048,
            }
        ],
        {
            "max_members": 1,
            "max_depth": 1,
            "max_concurrency": 1,
            "max_total_tokens": node["budget_tokens"],
            "allowed_tools": node["allowed_tools"],
        },
        strategy="greedy",
        expected_version=2,
    )
    actual = team["members"][0]["capacity_preflight"]
    original = plan["capacity_contract"]["nodes"][node["key"]]
    assert actual["initial_work_tokens"] > original["initial_work_tokens"]
    assert actual["minimum_node_tokens"] <= node["budget_tokens"]
    teams.approve(quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user")
    assert len(provider.requests) == 1


def test_once_repaired_self_input_rejection_keeps_concrete_semantic_diagnostics(tmp_path):
    store, quest = prepared(tmp_path)
    wire = semantic_wire(capacity_graph())
    wire["nodes"][0]["expected_artifact"] = "work/future.json"
    wire["nodes"][0]["required_input_paths"] = ["work/future.json"]
    provider = ScriptedMock(wire)
    with pytest.raises(InvalidGraphProposal):
        generate(store, quest, provider)
    assert len(provider.requests) == 2
    feedback = json.loads(provider.requests[1].messages[-1]["content"])
    invalid = feedback["capacity_diagnostics"]["semantic_diagnostics"]["invalid_input_paths"]
    assert invalid[0]["path"] == "work/future.json" and invalid[0]["self_output"] is True
    assert QuestGraphService(store).history(quest.id) == []


def test_core_preserves_large_output_bounds_and_context_rejection(tmp_path):
    store, quest = prepared(tmp_path)
    wire = semantic_wire(capacity_graph())
    wire["nodes"][0]["capacity_bounds"]["max_delivery_bytes"] = 32768
    before = copy.deepcopy(wire)
    provider = ScriptedMock(wire)
    with pytest.raises(InvalidGraphProposal):
        generate(store, quest, provider)
    assert wire == before and len(provider.requests) == 2
    feedback = json.loads(provider.requests[1].messages[-1]["content"])
    assert feedback["capacity_diagnostics"]["allocation_mode"] == "core_measured"
    assert (
        "serial_capacity_option" not in feedback["capacity_diagnostics"]["rejected_candidates"][0]
    )


def configured_main(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("FORGE_ROUTER_MODEL_ID", raising=False)
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    generate = main.quest_graph.generate

    async def generate_legacy_contract(*args, **kwargs):
        # These historical transport/lifecycle fixtures deliberately emit V21.
        # Keep their wire and original assertions independent of the current
        # production Planner protocol while retaining every real admission gate.
        kwargs["semantic_protocol_version"] = "v21"
        return await generate(*args, **kwargs)

    monkeypatch.setattr(main.quest_graph, "generate", generate_legacy_contract)
    quest = Quest(title="Bounded production planning", goal="Preserve the original goal")
    main.store.save_quest(quest)
    event = ForgeEvent(event_type="quest.proposed", quest_id=quest.id, payload={})
    main.store.append_event(event)
    drain_dispatcher(main.dispatcher)
    main.router.model_id = "mock"
    return main, quest, event


def test_background_final_invalid_proposal_is_terminal_after_exactly_two_calls(
    tmp_path, monkeypatch
):
    main, quest, event = configured_main(tmp_path, monkeypatch)
    wire = semantic_wire(capacity_graph())
    wire["nodes"][0]["required_input_paths"] = ["work/missing.json"]
    provider = ScriptedMock(dataflow_fixture(wire))
    configure_planner_capacity(main, provider)
    assert not asyncio.run(main.process_pending_plan())
    notice = next(item for item in main.dispatcher.inbox("planner") if item["event_id"] == event.id)
    assert notice["notice_attempts"] == 1
    assert notice["notice_dead_at"] is not None
    assert notice["notice_error"].startswith("planning_failed:")
    assert notice["notice_token"] is None and notice["processed_at"] is None
    assert len(provider.requests) == 2
    assert not asyncio.run(main.process_pending_plan())
    assert len(provider.requests) == 2
    rejections = [
        json.loads(row["payload"])
        for row in main.store.list_events(limit=100)
        if row["event_type"] == "quest.plan_rejected"
    ]
    assert len(rejections) == 2 and all(row["response_sha256"] for row in rejections)
    assert all(
        row["capacity_diagnostics"]["semantic_diagnostics"]["invalid_input_paths"]
        for row in rejections
    )
    assert main.quest_graph.history(quest.id) == []


@pytest.mark.parametrize("code,expected_attempts", [("timeout", 1), ("concurrency_unavailable", 0)])
def test_transport_and_unsent_busy_keep_existing_retry_accounting(
    tmp_path, monkeypatch, code, expected_attempts
):
    main, _quest, event = configured_main(tmp_path, monkeypatch)

    class FailedTransport(ScriptedMock):
        async def generate(self, request):
            self.requests.append(request)
            raise ProviderError(code)

    provider = FailedTransport({})
    configure_planner_capacity(main, provider)
    assert not asyncio.run(main.process_pending_plan())
    notice = next(item for item in main.dispatcher.inbox("planner") if item["event_id"] == event.id)
    assert notice["notice_attempts"] == expected_attempts
    assert notice["notice_dead_at"] is None
    assert len(provider.requests) == 1
