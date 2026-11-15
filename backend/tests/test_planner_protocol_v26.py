"""Explicit V26 wire/native framing and admission probes; no live model calls."""

import asyncio
import copy
import json

import pytest
from pydantic import ValidationError
from test_composer_resources_v21 import graph_fixture, semantic_team
from test_core_measured_serial_v17 import NativeFramingMock
from test_planner_protocol_v23 import graph, node
from test_planner_protocol_v25 import production_background_plan, profiled
from test_planner_router_input_v18 import Captured, NativeCapture, route_original_scenario
from test_planner_source_preview_v22 import setup

from forge_core.composer_resources import SemanticTeamWire, safe_team_rejection_diagnostics
from forge_core.domain import DomainError, Quest
from forge_core.planner_capacity_preview_v26 import preview_complete_capacity_v26
from forge_core.planner_dataflow_v25 import DataflowNodeWireV25, validate_capacity_choices
from forge_core.planner_dataflow_v26 import (
    ACTION_PROFILES_V26,
    OUTPUT_PROFILES_V26,
    DataflowGraphWireV26,
    DataflowNodeWireV26,
)
from forge_core.quest_graph import InvalidGraphProposal, QuestGraphService


def v26_node(
    *args, hypotheses=(), output="inline_1536", action="declared_sources_only_0", **kwargs
):
    result = profiled(node(*args, **kwargs), output=output, action=action)
    result["hypothesis_read_paths"] = list(hypotheses)
    return result


@pytest.mark.parametrize("output", OUTPUT_PROFILES_V26)
@pytest.mark.parametrize("action", ACTION_PROFILES_V26)
def test_all_explicit_v26_profiles_preserve_exact_bytes_and_old_protocol(output, action):
    selected = v26_node(
        "selected",
        output=output,
        action=action,
        expected=None if output.startswith("inline_") else "work/result.json",
        tools=[] if output.startswith("inline_") else ["file_write", "file_revise"],
    )
    before = copy.deepcopy(selected)
    typed = DataflowNodeWireV26.model_validate(selected)
    result = DataflowNodeWireV25.model_validate(typed.v25_data()).v23_data()
    assert result["capacity_bounds"] == {
        "max_artifact_bytes": OUTPUT_PROFILES_V26[output][0],
        "max_delivery_bytes": OUTPUT_PROFILES_V26[output][1],
        "action_evidence_allowance_bytes": ACTION_PROFILES_V26[action],
    }
    assert selected == before and "hypothesis_read_paths" not in result
    with pytest.raises(ValidationError):
        DataflowNodeWireV25.model_validate(selected)


@pytest.mark.parametrize("kind", ["json_review:work/result.json", "json_valid:work/result.json"])
def test_path_bearing_or_mixed_v25_verifiers_cannot_enter_v26(kind):
    selected = v26_node(
        "artifact",
        expected="work/result.json",
        tools=["file_write"],
        output="artifact_128_delivery_1024",
        kind=kind,
    )
    with pytest.raises(ValidationError):
        DataflowNodeWireV26.model_validate(selected)
    selected["success_criteria"] = [{"kind": "json_review"}]
    decoded = DataflowGraphWireV26.model_validate(graph(selected)).proposal_data(
        resource_limits={"max_total_tokens": 180000},
        coordination_reserve_tokens=18000,
        verified_paths=[],
    )
    assert decoded["nodes"][0]["success_criteria"] == ["json_review:work/result.json"]
    assert decoded["capacity_contract"]["hypothesis_read_paths"] == {"artifact": []}
    validate_capacity_choices(decoded)


@pytest.mark.parametrize("fault", ["missing", "escape", "zero", "no_tool", "verified", "duplicate"])
def test_hypothesis_scope_never_infers_path_authority_or_budget(fault):
    selected = v26_node(
        "read",
        hypotheses=["inputs/recalled.json"],
        tools=["file_read"],
        action="bounded_test_receipts_2048",
    )
    if fault == "missing":
        selected.pop("hypothesis_read_paths")
    elif fault == "escape":
        selected["hypothesis_read_paths"] = ["../outside.json"]
    elif fault == "zero":
        selected["action_evidence"] = {"profile": "declared_sources_only_0"}
    elif fault == "no_tool":
        selected["allowed_tools"] = []
    elif fault == "verified":
        selected["verified_input_paths"] = ["inputs/recalled.json"]
    else:
        selected["hypothesis_read_paths"] *= 2
    with pytest.raises((ValidationError, DomainError)):
        DataflowNodeWireV26.model_validate(selected)


def test_exact_hypothesis_map_and_explicit_choices_survive_native_resource_fixed_point(tmp_path):
    from test_verified_capacity_v16 import prepared

    store, quest = prepared(
        tmp_path,
        content='Assess the sourced hypothesis inputs/not-prefetched.json. Task execution limits: {"max_total_tokens":630000,"max_nodes":3,"max_members":3}',
    )
    quest = Quest(
        title="Explicit recovered source",
        goal="Assess the unverified hypothesis inputs/not-prefetched.json",
        metadata=quest.metadata,
    )
    store.save_quest(quest)
    inventory = []
    selected = graph(
        v26_node(
            "fresh",
            hypotheses=["inputs/not-prefetched.json"],
            tools=["file_read"],
            action="bounded_test_receipts_2048",
        )
    )
    value = DataflowGraphWireV26.model_validate(selected).proposal_data(
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
        verified_paths=[item["path"] for item in inventory],
    )
    from forge_core.constraints import execution_constraints
    from forge_core.source_capacity import bind_source_receipt_bounds

    with store.connection() as connection:
        value["execution_constraints"] = execution_constraints(connection, quest.id)

    expected = copy.deepcopy(value["capacity_contract"])
    bind_source_receipt_bounds(
        store,
        value,
        inventory,
        action_evidence_allowances=value["capacity_contract"]["action_evidence_allowances"],
    )
    provider = NativeFramingMock(selected)
    QuestGraphService(store)._size_core_resources(
        store.get_quest(quest.id), value, inventory, provider
    )
    contract = value["capacity_contract"]
    for key in ("planner_capacity_choices", "hypothesis_read_paths", "planner_capacity_protocol"):
        assert contract[key] == expected[key]
    assert provider.requests == [] and value["nodes"][0]["required_input_paths"] == []
    assert not (tmp_path / "inputs/not-prefetched.json").exists()
    validate_capacity_choices(value)
    tampered = copy.deepcopy(value)
    tampered["capacity_contract"]["planner_capacity_choices"]["fresh"]["action_evidence"] = {
        "profile": "bounded_test_receipts_3072"
    }
    with pytest.raises(ValueError, match="choice_differs"):
        validate_capacity_choices(tampered)


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize("repair", [False, True])
def test_original_router_prompts_and_one_v26_repair_fit_native_limit(tmp_path, level, repair):
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
                semantic_protocol_version="v26",
            )
        )
    assert len(provider.requests) == (2 if repair else 1)
    for request, size in zip(provider.requests, provider.native_sizes, strict=True):
        context = json.loads(request.messages[1]["content"])
        assert context["original_request"]["content"] == original
        assert context["semantic_protocol_version"] == "v26"
        assert size + request.max_output_tokens <= 65536
    assert QuestGraphService(store).history(quest["id"]) == []


def test_v26_diagnostics_keep_illegal_path_verifier_unknown_and_measure_valid_node(tmp_path):
    store, quest, inventory = setup(tmp_path)
    selected = graph(
        v26_node("good", tools=["file_read"]),
        v26_node(
            "bad",
            expected="work/result.json",
            tools=["file_write"],
            kind="json_review:work/result.json",
            output="artifact_128_delivery_1024",
        ),
    )
    provider = NativeFramingMock(selected)
    report = preview_complete_capacity_v26(
        QuestGraphService(store),
        store.get_quest(quest.id),
        json.dumps(selected),
        inventory,
        provider,
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
    )
    assert report["status"] == "available", report
    assert report["nodes"][0]["status"] == "available"
    assert report["nodes"][1]["status"] == "unavailable"
    assert report["aggregate_policy_projection"]["status"] == "unknown"
    assert provider.requests == [] and len(json.dumps(report).encode()) <= 8192


def test_hypothesis_not_explicit_in_current_goal_is_rejected_before_allocation(tmp_path):
    store, quest, _inventory = setup(tmp_path)
    selected = graph(
        v26_node(
            "read",
            hypotheses=["inputs/invented.json"],
            tools=["file_read"],
            action="small_receipts_1024",
        )
    )
    provider = NativeFramingMock(selected)
    service = QuestGraphService(store)
    with pytest.raises(InvalidGraphProposal):
        asyncio.run(
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
                semantic_protocol_version="v26",
            )
        )
    assert len(provider.requests) == 2 and service.history(quest.id) == []
    assert not (tmp_path / "inputs/invented.json").exists()


def test_historical_production_background_preserves_explicit_v26_protocol(tmp_path, monkeypatch):
    main, quest, _provider = production_background_plan(tmp_path, monkeypatch, protocol="v26")
    contract = main.quest_graph.get(quest.id)["capacity_contract"]
    assert contract["planner_capacity_protocol"] == "v26"
    assert contract["hypothesis_read_paths"] == {"analysis": []}


@pytest.mark.parametrize(
    "message,reason",
    [
        ("Invalid role count or duplicate roles", "role_count_or_duplicate_ids"),
        ("Role weakens approved task acceptance criteria", "role_criteria_weakened"),
        ("Role weakens approved task capability requirements", "role_capability_minimum_weakened"),
        ("No eligible model for at least one role: PRIVATE_VALUE", "no_eligible_model_for_role"),
        ("PRIVATE_UNKNOWN_VALUE", "unclassified_rejection"),
    ],
)
def test_composer_safe_reason_codes_preserve_fixed_root_cause_without_raw_text(message, reason):
    result = safe_team_rejection_diagnostics(DomainError(message))
    assert result["reason"] == reason
    assert "PRIVATE" not in json.dumps(result)
    assert len(json.dumps(result).encode()) < 4096


def test_composer_fixed_reason_can_be_observed_from_original_semantic_parser():
    proposal = semantic_team(graph_fixture())
    proposal["roles"][0]["required_capabilities"] = [
        {"name": "research", "minimum": 1},
        {"name": "research", "minimum": 2},
    ]
    with pytest.raises(ValueError) as failure:
        SemanticTeamWire.model_validate(proposal).inputs(
            graph_fixture(), operator_max_concurrency=1
        )
    assert safe_team_rejection_diagnostics(failure.value)["reason"] == "duplicate_capability"
