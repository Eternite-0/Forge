"""V28 internal contract, bounded diagnostics and explicit serialization policy."""

import asyncio
import copy
import json

import pytest
from pydantic import ValidationError
from test_composer_resources_v21 import graph_fixture, semantic_team
from test_core_measured_serial_v17 import NativeFramingMock
from test_planner_protocol_v23 import graph
from test_planner_protocol_v26 import v26_node
from test_planner_router_input_v18 import Captured, NativeCapture, route_original_scenario
from test_planner_source_preview_v22 import setup

from forge_core.composer_resources import SemanticTeamWire, safe_team_rejection_diagnostics
from forge_core.domain import DomainError
from forge_core.planner_capacity_preview_v26 import preview_complete_capacity_v26
from forge_core.planner_resource_policy import INTERNAL_OBJECTIVE_INPUT_REFERENCE
from forge_core.quest_graph import QuestGraphService


def generate(service, quest, provider, *, protocol="v28"):
    return asyncio.run(
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
            semantic_protocol_version=protocol,
        )
    )


@pytest.mark.parametrize("length", [1001, 2000])
def test_valid_long_objective_with_no_initial_paths_is_preserved_in_plan_and_native_preview(
    tmp_path, length
):
    store, quest, inventory = setup(tmp_path)
    selected = graph(v26_node("complete", tools=["file_read"]))
    selected["nodes"][0]["objective"] = "原" * length
    before = copy.deepcopy(selected)
    provider = NativeFramingMock(selected)
    service = QuestGraphService(store)
    report = preview_complete_capacity_v26(
        service,
        store.get_quest(quest.id),
        json.dumps(selected),
        inventory,
        provider,
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
    )
    assert report["status"] == "available", report
    assert report["nodes"][0]["status"] == "available", report
    plan = generate(service, quest, provider, protocol="v26")
    assert plan["nodes"][0]["objective"] == "原" * length
    assert plan["nodes"][0]["required_input_paths"] == []
    assert selected == before
    assert INTERNAL_OBJECTIVE_INPUT_REFERENCE != plan["nodes"][0]["objective"]
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["input_contract", "acceptance_criteria"])
@pytest.mark.parametrize("invalid", [" ", "PRIVATE_" + "x" * 1000])
def test_root_role_validator_diagnostic_identifies_only_known_invalid_field(field, invalid):
    proposal = semantic_team(graph_fixture())
    proposal["roles"][0][field] = [invalid]
    with pytest.raises(ValidationError) as failure:
        SemanticTeamWire.model_validate(proposal).inputs(
            graph_fixture(), operator_max_concurrency=1
        )
    report = safe_team_rejection_diagnostics(failure.value)
    assert report["validation_reasons"] == ["role_contract_item_blank_or_too_long"]
    assert report["role_validation"] == [
        {"field": field, "index": 0, "reason": "role_contract_item_blank_or_too_long"}
    ]
    assert "PRIVATE" not in json.dumps(report)
    assert len(json.dumps(report).encode()) < 4096


def test_unknown_role_diagnostic_omits_model_selected_id():
    proposal = semantic_team(graph_fixture())
    proposal["roles"][0]["id"] = "PRIVATE_SELECTED_ID"
    with pytest.raises(ValidationError) as failure:
        SemanticTeamWire.model_validate(proposal).inputs(
            graph_fixture(), operator_max_concurrency=1
        )
    report = safe_team_rejection_diagnostics(failure.value)
    assert report["validation_reasons"] == ["unknown_role_requires_dynamic_contract"]
    assert report["role_validation"] == [
        {"field": "id", "reason": "unknown_role_requires_dynamic_contract"}
    ]
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize(
    "message,expected",
    [
        (
            "Team plus coordination reserve exceeds current residual budget",
            "team_residual_budget_insufficient",
        ),
        (
            "Approved team would exceed current residual budget",
            "approved_team_residual_budget_insufficient",
        ),
        ("Team max_depth exceeds source-bound task limit", "source_depth_limit_exceeded"),
    ],
)
def test_fixed_residual_and_source_limit_messages_have_persistable_reason_codes(message, expected):
    report = safe_team_rejection_diagnostics(DomainError(message))
    assert report == {"kind": "invalid_team_proposal", "reason": expected}


@pytest.mark.parametrize("invalid_marker", [None, "unknown-wire"])
def test_rebuilding_capacity_cannot_downgrade_an_explicit_invalid_source_marker(
    tmp_path, invalid_marker
):
    store, quest, inventory = setup(tmp_path)
    provider = NativeFramingMock(graph(v26_node("complete", tools=["file_read"])))
    service = QuestGraphService(store)
    plan = generate(service, quest, provider, protocol="v26")
    plan["capacity_contract"]["source_wire_format"] = invalid_marker
    with pytest.raises(DomainError, match="source wire format"):
        service._capacity_preflight(store.get_quest(quest.id), plan, inventory, provider)


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize("repair", [False, True])
def test_original_l1_l3_v28_requests_and_one_repair_fit_native_context(tmp_path, level, repair):
    store, quest, original, _ = route_original_scenario(tmp_path, level)
    provider = NativeCapture(repair=repair)
    with pytest.raises(Captured):
        asyncio.run(
            QuestGraphService(store, composer_capacity_measurer=lambda *_: None).generate(
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
                semantic_protocol_version="v28",
            )
        )
    assert len(provider.requests) == (2 if repair else 1)
    for request, size in zip(provider.requests, provider.native_sizes, strict=True):
        context = json.loads(request.messages[1]["content"])
        assert context["semantic_protocol_version"] == "v28"
        assert context["original_request"]["content"] == original
        assert size + request.max_output_tokens <= 65536
        assert "core-source-raw-text-v1" in request.messages[0]["content"]
