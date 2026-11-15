"""Typed autonomous criteria and Core-owned source bounds; offline native framing."""

import asyncio
import copy
import json
from dataclasses import replace

import jsonschema
import pytest
from planner_capacity_fixtures import configure_planner_capacity, dataflow_fixture
from pydantic import ValidationError
from test_core_measured_planner_v19 import configured_main, generate, semantic_wire
from test_core_measured_serial_v17 import NativeFramingMock
from test_verified_capacity_v16 import capacity_graph, prepared

from forge_core.graph_domain import CoreMeasuredGraphWire, GraphWire, OutputCapacityBounds
from forge_core.quest_graph import QuestGraphService

KINDS = (
    "delivery_review",
    "json_review:@delivery",
    "json_review",
    "json_valid",
    "semantic_numeric:scheduler-v1",
)


def proposal_data(wire):
    return CoreMeasuredGraphWire.model_validate(wire).proposal_data(
        resource_limits={"max_total_tokens": 180000}, coordination_reserve_tokens=18000
    )


def criterion_wire(kind, path=None):
    wire = semantic_wire(capacity_graph())
    wire["nodes"][0]["expected_artifact"] = path
    wire["nodes"][0]["success_criteria"] = [{"kind": kind, "artifact_path": path}]
    return wire


@pytest.mark.parametrize("kind", KINDS)
def test_typed_verifier_serializes_the_exact_existing_id(kind):
    path = None if kind in KINDS[:2] else "work/精确 report.json"
    wire = criterion_wire(kind, path)
    before = copy.deepcopy(wire)
    data = proposal_data(wire)
    assert data["nodes"][0]["success_criteria"] == [kind if path is None else kind + ":" + path]
    assert wire == before
    assert "max_source_receipt_bytes" not in data["nodes"][0]["capacity_bounds"]


def test_all_five_typed_verifiers_can_be_explicitly_selected_without_inference():
    wire = criterion_wire("json_review", "work/result.json")
    wire["nodes"][0]["success_criteria"] = [
        {"kind": kind, "artifact_path": None if kind in KINDS[:2] else "work/result.json"}
        for kind in KINDS
    ]
    data = proposal_data(wire)
    assert data["nodes"][0]["success_criteria"] == [
        "delivery_review",
        "json_review:@delivery",
        "json_review:work/result.json",
        "json_valid:work/result.json",
        "semantic_numeric:scheduler-v1:work/result.json",
    ]


@pytest.mark.parametrize("count", [0, 6])
def test_schema_and_validation_bound_criteria_count(count):
    wire = criterion_wire("delivery_review")
    wire["nodes"][0]["success_criteria"] *= count
    with pytest.raises(ValidationError):
        CoreMeasuredGraphWire.model_validate(wire)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wire, CoreMeasuredGraphWire.model_json_schema())


@pytest.mark.parametrize(
    "criterion",
    [
        "delivery_review",
        {"kind": "delivery_review: explain why", "artifact_path": None},
        {"kind": "arbitrary_prose", "artifact_path": None},
        {"kind": "json_review", "artifact_path": "work/result.json", "approved": True},
        {"kind": "delivery_review"},
        {"kind": "delivery_review", "artifact_path": 42},
    ],
)
def test_schema_rejects_legacy_strings_unknown_kinds_and_extra_authority(criterion):
    wire = criterion_wire("delivery_review")
    wire["nodes"][0]["success_criteria"] = [criterion]
    with pytest.raises(ValidationError):
        CoreMeasuredGraphWire.model_validate(wire)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wire, CoreMeasuredGraphWire.model_json_schema())


@pytest.mark.parametrize("kind", KINDS)
def test_path_kind_consistency_is_mandatory(kind):
    path = "work/result.json" if kind in KINDS[:2] else None
    with pytest.raises(ValidationError):
        CoreMeasuredGraphWire.model_validate(criterion_wire(kind, path))


@pytest.mark.parametrize("expected", [None, "work/different.json"])
def test_artifact_criterion_must_bind_the_exact_expected_artifact(expected):
    wire = criterion_wire("json_review", "work/result.json")
    wire["nodes"][0]["expected_artifact"] = expected
    with pytest.raises(ValidationError, match="artifact_verifier_path_must_match"):
        CoreMeasuredGraphWire.model_validate(wire)


def test_production_output_bounds_cannot_supply_a_source_guess():
    schema = CoreMeasuredGraphWire.model_json_schema()
    assert set(schema["$defs"]["OutputCapacityBounds"]["properties"]) == {
        "max_artifact_bytes",
        "max_delivery_bytes",
    }
    wire = semantic_wire(capacity_graph())
    wire["nodes"][0]["capacity_bounds"]["max_source_receipt_bytes"] = 1
    with pytest.raises(ValidationError):
        CoreMeasuredGraphWire.model_validate(wire)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(wire, schema)


@pytest.mark.parametrize("value", [0, 32769, True, 1.0])
def test_output_bounds_remain_strict_and_finite(value):
    with pytest.raises(ValidationError):
        OutputCapacityBounds(max_artifact_bytes=value, max_delivery_bytes=1536)


def test_manual_legacy_wire_keeps_original_string_protocol():
    original = capacity_graph()
    result = GraphWire.model_validate(original).proposal_data()
    assert result["nodes"][0]["success_criteria"] == original["nodes"][0]["success_criteria"]
    assert result["nodes"][0]["capacity_bounds"] == original["nodes"][0]["capacity_bounds"]
    with pytest.raises(ValidationError):
        GraphWire.model_validate(semantic_wire(original))


class NativeScriptedMock(NativeFramingMock):
    def __init__(self, *responses):
        super().__init__(responses[0])
        self.responses = responses

    async def generate(self, request):
        self.graph = self.responses[min(len(self.requests), len(self.responses) - 1)]
        return await super().generate(request)


def test_invalid_typed_criteria_get_one_native_repair_and_keep_exact_selection(tmp_path):
    store, quest = prepared(tmp_path)
    valid = criterion_wire("json_review:@delivery")
    invalid = copy.deepcopy(valid)
    invalid["nodes"][0]["success_criteria"] = [{"kind": "prose", "artifact_path": None}]
    provider = NativeScriptedMock(invalid, valid)
    plan = generate(store, quest, provider)
    assert len(provider.requests) == 2
    assert plan["status"] == "proposed"
    assert plan["nodes"][0]["success_criteria"] == ["json_review:@delivery"]
    assert plan["nodes"][0]["capacity_bounds"]["max_source_receipt_bytes"] > 1024
    source = plan["capacity_contract"]["resource_allocation"]["source_capacity"]
    assert source["version"] == "core-source-capacity-v1"
    assert (
        source["nodes"]["n0"]["max_source_receipt_bytes"]
        == plan["nodes"][0]["capacity_bounds"]["max_source_receipt_bytes"]
    )
    assert source["nodes"]["n0"]["metadata_reserve_bytes"] == 1024
    feedback = json.loads(provider.requests[1].messages[-1]["content"])
    assert feedback["rejected_proposal_error"]
    assert "core_measured" in feedback["required_action"]
    for request in provider.requests:
        native_bytes = len(
            json.dumps(provider._payload(request, False), ensure_ascii=False).encode()
        )
        assert native_bytes + 64 + request.max_output_tokens <= request.context_limit == 65536
    assert QuestGraphService(store).history(quest.id)[0]["status"] == "proposed"
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 0


def test_two_invalid_typed_responses_are_durably_terminal_without_schema_bypass(
    tmp_path, monkeypatch
):
    main, quest, event = configured_main(tmp_path, monkeypatch)
    wire = criterion_wire("delivery_review")
    wire["nodes"][0]["success_criteria"] = [
        {"kind": "untrusted SECRET_MARKER criteria", "artifact_path": None}
    ]
    provider = NativeScriptedMock(dataflow_fixture(wire))
    configure_planner_capacity(main, provider)
    provider.config = replace(provider.config, supports_tools=True, supports_json_schema=True)
    assert not asyncio.run(main.process_pending_plan())
    assert len(provider.requests) == 2
    assert not asyncio.run(main.process_pending_plan())
    assert len(provider.requests) == 2
    notice = next(item for item in main.dispatcher.inbox("planner") if item["event_id"] == event.id)
    assert notice["notice_attempts"] == 1 and notice["notice_dead_at"] is not None
    assert notice["notice_error"].startswith("planning_failed:")
    rejections = [
        json.loads(row["payload"])
        for row in main.store.list_events(limit=100)
        if row["event_type"] == "quest.plan_rejected"
    ]
    assert len(rejections) == 2
    assert all(row["response_sha256"] and row["validation_errors"] for row in rejections)
    assert "SECRET_MARKER" not in json.dumps(rejections)
    assert main.quest_graph.history(quest.id) == []
