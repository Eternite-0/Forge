"""Explicit profile decoding and complete bounded diagnostics; no live results."""

import asyncio
import copy
import json

import pytest
from pydantic import ValidationError
from test_core_measured_serial_v17 import NativeFramingMock
from test_planner_protocol_v23 import diagnostic_setup, graph, node
from test_planner_router_input_v18 import Captured, NativeCapture, route_original_scenario
from test_planner_source_preview_v22 import dependency, setup

from forge_core.planner_capacity_preview_v25 import (
    NODE_COLUMNS,
    decoded_response,
    preview_complete_capacity_v25,
    preview_sources_v25,
)
from forge_core.planner_dataflow_v23 import DataflowGraphWireV23
from forge_core.planner_dataflow_v25 import (
    ACTION_PROFILES,
    OUTPUT_PROFILES,
    DataflowGraphWireV25,
    DataflowNodeWireV25,
)
from forge_core.quest_graph import InvalidGraphProposal, QuestGraphService


def profiled(selected, *, output="inline_1536", action="declared_sources_only_0"):
    value = copy.deepcopy(selected)
    value.pop("capacity_bounds")
    value["output_capacity"] = {"profile": output}
    value["action_evidence"] = {"profile": action}
    return value


@pytest.mark.parametrize("output", OUTPUT_PROFILES)
@pytest.mark.parametrize("action", ACTION_PROFILES)
def test_each_explicit_profile_combination_decodes_exactly_without_semantic_edits(output, action):
    artifact = not output.startswith("inline_")
    old = node(
        "selected",
        expected="work/result.json" if artifact else None,
        tools=["file_write", "file_revise"] if artifact else [],
        bound=123,
    )
    value = profiled(old, output=output, action=action)
    selected = DataflowNodeWireV25.model_validate(value)
    decoded = selected.v23_data()
    old["capacity_bounds"] = {
        "max_artifact_bytes": OUTPUT_PROFILES[output][0],
        "max_delivery_bytes": OUTPUT_PROFILES[output][1],
        "action_evidence_allowance_bytes": ACTION_PROFILES[action],
    }
    assert decoded == old
    with pytest.raises(ValidationError):
        DataflowGraphWireV23.model_validate(graph(value))


@pytest.mark.parametrize(
    "artifact,delivery,allowance", [(1, 1, 0), (32768, 32768, 32768), (2345, 6789, 4321)]
)
def test_custom_bounds_preserve_every_original_integer_and_require_all_fields(
    artifact, delivery, allowance
):
    value = profiled(node("custom"))
    value["output_capacity"] = {
        "profile": "custom",
        "max_artifact_bytes": artifact,
        "max_delivery_bytes": delivery,
    }
    value["action_evidence"] = {"profile": "custom", "action_evidence_allowance_bytes": allowance}
    result = DataflowNodeWireV25.model_validate(value).v23_data()
    assert result["capacity_bounds"] == {
        "max_artifact_bytes": artifact,
        "max_delivery_bytes": delivery,
        "action_evidence_allowance_bytes": allowance,
    }
    for field in ("max_artifact_bytes", "max_delivery_bytes"):
        broken = copy.deepcopy(value)
        broken["output_capacity"].pop(field)
        with pytest.raises(ValidationError):
            DataflowNodeWireV25.model_validate(broken)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_output",
        "missing_action",
        "unknown",
        "extra_bound",
        "bool_custom",
        "over_custom",
        "inline_artifact",
        "artifact_without_path",
        "unknown_verifier",
    ],
)
def test_no_defaults_clamping_fallback_or_contract_repairs(fault):
    value = profiled(node("bad"))
    if fault == "missing_output":
        value.pop("output_capacity")
    elif fault == "missing_action":
        value.pop("action_evidence")
    elif fault == "unknown":
        value["output_capacity"] = {"profile": "shrink_to_fit"}
    elif fault == "extra_bound":
        value["output_capacity"]["max_delivery_bytes"] = 16384
    elif fault in {"bool_custom", "over_custom"}:
        value["action_evidence"] = {
            "profile": "custom",
            "action_evidence_allowance_bytes": True if fault == "bool_custom" else 32769,
        }
    elif fault == "inline_artifact":
        value["expected_artifact"] = "work/result.json"
        value["allowed_tools"] = ["file_write"]
    elif fault == "artifact_without_path":
        value["output_capacity"] = {"profile": "artifact_2048_delivery_1536"}
    else:
        value["success_criteria"] = [{"kind": "PRIVATE_INVALID"}]
    before = copy.deepcopy(value)
    with pytest.raises(ValidationError):
        DataflowNodeWireV25.model_validate(value)
    assert value == before


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize("repair", [False, True])
def test_original_router_requests_and_one_repair_fit_unchanged_native_limits(
    tmp_path, level, repair
):
    store, quest, original, _ = route_original_scenario(tmp_path, level)
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
                semantic_protocol_version="v25",
            )
        )
    assert len(provider.requests) == (2 if repair else 1)
    for request, size in zip(provider.requests, provider.native_sizes, strict=True):
        context = json.loads(request.messages[1]["content"])
        assert context["original_request"]["content"] == original
        assert context["verified_input_inventory"] and context["verified_input_receipt_sizes"]
        assert len(request.messages[1]["content"].encode()) <= 24000
        assert size + request.max_output_tokens <= 65536
        assert "quantitative_planning_guidance" not in context
        assert "inline_1536" in request.messages[0]["content"]
        assert "24000" in request.messages[0]["content"]
    assert QuestGraphService(store).history(quest["id"]) == []


def test_eight_max_key_nodes_keep_every_numeric_row_and_invalid_criterion_stays_unknown(tmp_path):
    store, quest, inventory = setup(tmp_path)
    selected = []
    previous = None
    for index in range(8):
        key = str(index) + "n" * 99
        value = node(
            key,
            inputs=["inputs/unicode.json"] if index == 0 else [],
            predecessors=[dependency(previous, artifact=False)] if previous else [],
            tools=["worker_read_completed_delivery"] if previous else ["file_read"],
            bound=1536,
        )
        selected.append(profiled(value))
        previous = key
    before = copy.deepcopy(selected)
    kwargs = {
        "resource_limits": {
            "max_total_tokens": 630000,
            "max_cost_microunits": None,
            "cost_currency": None,
            "max_duration_seconds": 600,
        },
        "coordination_reserve_tokens": 63000,
    }
    provider = NativeFramingMock({})
    report = preview_complete_capacity_v25(
        QuestGraphService(store),
        store.get_quest(quest.id),
        json.dumps(graph(*selected)),
        inventory,
        provider,
        **kwargs,
    )
    assert report["status"] == "available", report
    assert len(json.dumps(report, ensure_ascii=False).encode()) <= 8192
    assert len(report["nodes"]) == 8
    for observed in report["nodes"]:
        row = dict(zip(NODE_COLUMNS, observed["rows"][0], strict=True))
        assert row["artifact_bytes"] == 1 and row["delivery_bytes"] == 1536
        assert row["action_evidence_bytes"] == 0 and row["minimum_node_tokens"] > 0
    selected[3]["success_criteria"] = [{"kind": "PRIVATE_INVALID"}]
    raw = json.dumps(graph(*selected))
    invalid = preview_complete_capacity_v25(
        QuestGraphService(store), store.get_quest(quest.id), raw, inventory, provider, **kwargs
    )
    assert invalid["nodes"][3]["status"] == "unavailable"
    assert invalid["aggregate_policy_projection"]["status"] == "unknown"
    sources = preview_sources_v25(store, raw, inventory)
    assert sources["status"] == "available" and len(sources["nodes"]) == 8
    assert "PRIVATE_" not in json.dumps(invalid)
    assert selected[0] == before[0] and provider.requests == []
    assert (
        json.loads(decoded_response(json.dumps(graph(before[0]))))["nodes"][0]["capacity_bounds"][
            "max_delivery_bytes"
        ]
        == 1536
    )


def test_rejected_custom_bounds_get_one_compact_repair_without_changing_their_values(tmp_path):
    store, quest, _inventory = diagnostic_setup(tmp_path)
    selected = profiled(
        node(
            "over",
            expected="work/report.json",
            tools=["file_write"],
            kind="json_review:work/report.json",
        )
    )
    selected["output_capacity"] = {
        "profile": "custom",
        "max_artifact_bytes": 32768,
        "max_delivery_bytes": 32768,
    }
    provider = NativeFramingMock(graph(selected))
    with pytest.raises(InvalidGraphProposal):
        asyncio.run(
            QuestGraphService(store).generate(
                quest.id,
                expected_version=0,
                provider=provider,
                provider_id="p",
                model_id="big",
                context_limit=65536,
                output_limit=8192,
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
                semantic_protocol_version="v25",
            )
        )
    assert len(provider.requests) == 2
    repair = json.loads(provider.requests[1].messages[-1]["content"])
    report = repair["capacity_diagnostics"]["complete_capacity_preview"]
    row = dict(zip(report["columns"], report["nodes"][0]["rows"][0], strict=True))
    assert row["artifact_bytes"] == 32768 and row["delivery_bytes"] == 32768
    assert row["violation_indices"]
    assert provider.requests[1].messages[-2]["content"] == json.dumps(graph(selected))
    assert QuestGraphService(store).history(quest.id) == []


def test_exact_model_capacity_choices_survive_core_resource_sizing(tmp_path):
    store, quest, inventory = setup(tmp_path)
    selected = graph(profiled(node("compact", inputs=["inputs/unicode.json"], tools=["file_read"])))
    graph_data = DataflowGraphWireV25.model_validate(selected).proposal_data(
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
        verified_paths=["inputs/unicode.json"],
    )
    from forge_core.constraints import execution_constraints
    from forge_core.source_capacity import bind_source_receipt_bounds

    with store.connection() as connection:
        graph_data["execution_constraints"] = execution_constraints(connection, quest.id)
    expected = copy.deepcopy(graph_data["capacity_contract"]["planner_capacity_choices"])
    bind_source_receipt_bounds(
        store,
        graph_data,
        inventory,
        action_evidence_allowances=graph_data["capacity_contract"]["action_evidence_allowances"],
    )
    QuestGraphService(store)._size_core_resources(
        store.get_quest(quest.id), graph_data, inventory, NativeFramingMock(selected)
    )
    assert graph_data["capacity_contract"]["planner_capacity_choices"] == expected
    assert graph_data["nodes"][0]["capacity_bounds"]["max_delivery_bytes"] == 1536


def test_actual_generate_persists_exact_choices_and_rejects_mismatched_metadata(tmp_path):
    from forge_core.capacity_preflight import validate_plan_contract

    store, quest, _inventory = setup(tmp_path)
    selected = graph(profiled(node("compact", inputs=["inputs/unicode.json"], tools=["file_read"])))
    provider = NativeFramingMock(selected)
    service = QuestGraphService(store)
    plan = asyncio.run(
        service.generate(
            quest.id,
            expected_version=0,
            provider=provider,
            provider_id="p",
            model_id="big",
            context_limit=65536,
            capabilities={"json_schema": True},
            capability_sources={"json_schema": "test_fixture"},
            require_capacity_contract=True,
            resource_allocation_mode="core_measured",
            semantic_protocol_version="v25",
        )
    )
    assert len(provider.requests) == 1
    assert plan["capacity_contract"]["planner_capacity_choices"] == {
        "compact": {
            "output_capacity": {"profile": "inline_1536"},
            "action_evidence": {"profile": "declared_sources_only_0"},
        }
    }
    assert plan["nodes"][0]["capacity_bounds"]["max_delivery_bytes"] == 1536
    with store.connection() as connection:
        row = connection.execute(
            "SELECT graph_json FROM quest_plans WHERE id=?", (plan["id"],)
        ).fetchone()
        persisted = json.loads(row[0])
        assert (
            persisted["capacity_contract"]["planner_capacity_choices"]
            == plan["capacity_contract"]["planner_capacity_choices"]
        )
        broken = copy.deepcopy(persisted)
        broken["capacity_contract"]["planner_capacity_choices"]["compact"]["output_capacity"][
            "profile"
        ] = "inline_2048"
        with pytest.raises(ValueError, match="choice_differs"):
            validate_plan_contract(store, connection, quest.id, broken)
        broken = copy.deepcopy(persisted)
        broken["nodes"][0]["expected_artifact"] = "work/inconsistent.json"
        with pytest.raises(ValueError, match="profile_must_match"):
            validate_plan_contract(store, connection, quest.id, broken)
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 0


def test_unmeasurable_candidate_keeps_all_nodes_explicit_without_crashing_compact_report(tmp_path):
    store, quest, inventory = setup(tmp_path)
    for model in store.list_models():
        store.save_model(model.model_copy(update={"enabled": False}))
    selected = graph(profiled(node("unknown", inputs=["inputs/unicode.json"], tools=["file_read"])))
    report = preview_complete_capacity_v25(
        QuestGraphService(store),
        store.get_quest(quest.id),
        json.dumps(selected),
        inventory,
        NativeFramingMock(selected),
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
    )
    assert report["status"] == "available", report
    assert report["nodes"] == [
        {
            "node": "unknown",
            "status": "unavailable",
            "reason": "no_measurable_candidate",
            "omitted_candidates": 0,
            "unmeasurable_candidates": 0,
        }
    ]
    assert report["aggregate_policy_projection"]["status"] == "unknown"


def production_background_plan(tmp_path, monkeypatch, *, protocol, production_default=False):
    import importlib
    import secrets
    from dataclasses import replace

    from planner_capacity_fixtures import configure_planner_capacity
    from test_event_dispatcher import drain_dispatcher
    from test_router_intelligence_phase2 import ScriptedMock

    from forge_core.domain import ForgeEvent, Quest
    from forge_core.provider_pool import ProviderPool

    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FORGE_MODEL_TOKEN_LIMIT", "630000")
    for name in (
        "FORGE_ROUTER_MODEL_ID",
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="Explicit production capacity protocol", goal="Inspect a bounded input")
    main.store.save_quest(quest)
    selected = graph(profiled(node("analysis", bound=512)))
    if protocol in {"v26", "v28"}:
        selected["nodes"][0]["hypothesis_read_paths"] = []
    if not production_default:
        original_generate = main.quest_graph.generate

        async def historical_generate(*args, **kwargs):
            kwargs["semantic_protocol_version"] = protocol
            return await original_generate(*args, **kwargs)

        monkeypatch.setattr(main.quest_graph, "generate", historical_generate)
    provider = ScriptedMock(selected)
    configure_planner_capacity(main, provider)
    provider.config = replace(provider.config, supports_tools=True, supports_json_schema=True)
    main.router.model_id = "mock"
    main.quest_graph.capacity_providers = ProviderPool({"planner-fixture": provider})
    main.store.append_event(ForgeEvent(event_type="quest.proposed", quest_id=quest.id, payload={}))
    drain_dispatcher(main.dispatcher)
    assert asyncio.run(main.process_pending_plan())
    plan = main.quest_graph.get(quest.id)
    assert plan["capacity_contract"]["planner_capacity_choices"]["analysis"] == {
        "output_capacity": {"profile": "inline_1536"},
        "action_evidence": {"profile": "declared_sources_only_0"},
    }
    assert plan["nodes"][0]["capacity_bounds"]["max_delivery_bytes"] == 1536
    assert (
        json.loads(provider.requests[0].messages[1]["content"])["semantic_protocol_version"]
        == protocol
    )
    assert len(provider.requests) == 1
    return main, quest, provider


def test_production_background_planner_uses_explicit_v25_profiles(tmp_path, monkeypatch):
    production_background_plan(tmp_path, monkeypatch, protocol="v25")
