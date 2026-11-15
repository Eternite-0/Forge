"""Constructed invalid responses prove independent diagnostics, never live acceptance."""

import asyncio
import copy
import json

import pytest
from pydantic import ValidationError
from test_core_measured_serial_v17 import NativeFramingMock
from test_verified_capacity_v16 import prepared

from forge_core.capacity_preflight import discover_input_inventory
from forge_core.planner_dataflow import DataflowGraphWire
from forge_core.planner_source_preview import MAX_REPORT_BYTES, preview_source_capacity
from forge_core.quest_graph import InvalidGraphProposal, QuestGraphService
from forge_core.source_capacity import measure_required_sources


def node(
    key, *, expected=None, inputs=(), predecessors=(), tools=(), kind="delivery_review", bound=32768
):
    return {
        "key": key,
        "objective": "PRIVATE_OBJECTIVE_MARKER must never enter diagnostics",
        "success_criteria": [{"kind": kind}],
        "required_capabilities": [],
        "expected_artifact": expected,
        "max_attempts": 1,
        "allowed_tools": list(tools),
        "verified_input_paths": list(inputs),
        "predecessors": list(predecessors),
        "capacity_bounds": {"max_artifact_bytes": bound, "max_delivery_bytes": bound},
    }


def dependency(key, *, deliveries=None, artifact=True):
    return {
        "node_key": key,
        "condition": "success",
        "read_artifact": artifact,
        "delivery_sources": [key] if deliveries is None else deliveries,
    }


def overflow_wire(*, invalid_kind=True):
    producer = node(
        "producer",
        expected="work/producer.json",
        inputs=["inputs/unicode.json"],
        tools=["file_read", "file_write", "file_revise"],
        kind="json_review",
    )
    consumer = node(
        "consumer",
        predecessors=[dependency("producer")],
        tools=["file_read", "worker_read_completed_delivery"],
        kind="PRIVATE_INVALID_KIND_MARKER" if invalid_kind else "delivery_review",
    )
    second = {**copy.deepcopy(consumer), "key": "second_consumer"}
    return {"allocation_mode": "core_measured", "nodes": [producer, consumer, second]}


def setup(tmp_path):
    store, quest = prepared(
        tmp_path,
        content='Inspect inputs/unicode.json. Task execution limits: {"max_total_tokens":630000,"max_nodes":3,"max_members":3}',
    )
    (tmp_path / "inputs/unicode.json").write_text(
        '{"source":"PRIVATE_SOURCE_MARKER"}', encoding="utf-8"
    )
    inventory = discover_input_inventory(store, "Inspect inputs/unicode.json")
    return store, quest, inventory


def test_invalid_verifier_stays_rejected_while_all_source_overflows_are_measured(tmp_path):
    store, _quest, inventory = setup(tmp_path)
    wire = overflow_wire()
    original = copy.deepcopy(wire)
    before = copy.deepcopy(inventory)
    with pytest.raises(ValidationError):
        DataflowGraphWire.model_validate(wire)
    report = preview_source_capacity(store, json.dumps(wire), inventory)
    assert report["status"] == "available"
    assert report["overflow_nodes"] == ["consumer", "second_consumer"]
    assert len(report["nodes"]) == 3
    consumer = report["nodes"][1]
    assert consumer["future_contributions"] == [
        {
            "kind": "artifact",
            "producer": "producer",
            "path": "work/producer.json",
            "declared_bound": 32768,
            "bytes": 66048,
        },
        {"kind": "delivery", "producer": "producer", "declared_bound": 32768, "bytes": 34944},
    ]
    assert consumer["future_list_framing_bytes"] == 2
    assert consumer["known_source_bytes"] == 2
    assert consumer["future_source_bytes_bound"] == 100994
    assert consumer["max_source_receipt_bytes"] == 102020
    assert "PRIVATE_" not in json.dumps(report)
    assert "success_criteria" not in json.dumps(report)
    assert wire == original and inventory == before
    with pytest.raises(ValidationError):
        DataflowGraphWire.model_validate(wire)


def test_shared_formula_and_existing_diagnostic_shape_are_unchanged(tmp_path):
    from forge_core.capacity_preflight import _receipt, _size

    store, _quest, inventory = setup(tmp_path)
    wire = overflow_wire(invalid_kind=False)
    graph = DataflowGraphWire.model_validate(wire).proposal_data(
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
        verified_paths=[item["path"] for item in inventory],
    )
    receipt = _receipt(store, inventory[0])
    expected = [
        {
            "known_source_bytes": _size([receipt]),
            "future_source_bytes_bound": 0,
            "required_source_receipt_bytes": _size([receipt]),
        },
        {
            "known_source_bytes": 2,
            "future_source_bytes_bound": 100994,
            "required_source_receipt_bytes": 100996,
        },
        {
            "known_source_bytes": 2,
            "future_source_bytes_bound": 100994,
            "required_source_receipt_bytes": 100996,
        },
    ]
    for selected, expected_values in zip(graph["nodes"], expected, strict=True):
        measured = measure_required_sources(store, graph, selected, inventory)
        assert measured.diagnostics() == expected_values
        assert set(measured.diagnostics()) == {
            "known_source_bytes",
            "future_source_bytes_bound",
            "required_source_receipt_bytes",
        }
        assert measured.receipts == ([receipt] if selected["key"] == "producer" else [])


@pytest.mark.parametrize(
    "fault",
    [
        "root_extra",
        "missing_bound",
        "bool_bound",
        "over_bound",
        "unknown_producer",
        "self",
        "forward",
        "missing_tool",
        "unverified_file",
        "unknown_node_field",
    ],
)
def test_invalid_source_projection_is_unavailable_without_repairing_it(tmp_path, fault):
    store, _quest, inventory = setup(tmp_path)
    wire = overflow_wire()
    if fault == "root_extra":
        wire["approved"] = True
    elif fault == "missing_bound":
        wire["nodes"][0].pop("capacity_bounds")
    elif fault == "bool_bound":
        wire["nodes"][0]["capacity_bounds"]["max_delivery_bytes"] = True
    elif fault == "over_bound":
        wire["nodes"][0]["capacity_bounds"]["max_delivery_bytes"] = 32769
    elif fault == "unknown_producer":
        wire["nodes"][1]["predecessors"][0]["node_key"] = "missing"
    elif fault == "self":
        wire["nodes"][1]["predecessors"][0]["node_key"] = "consumer"
    elif fault == "forward":
        wire["nodes"][0]["predecessors"] = [dependency("consumer")]
    elif fault == "missing_tool":
        wire["nodes"][1]["allowed_tools"] = ["file_read"]
    elif fault == "unverified_file":
        wire["nodes"][1]["verified_input_paths"] = ["work/producer.json"]
    else:
        wire["nodes"][1]["grant_permission"] = True
    before = copy.deepcopy(wire)
    report = preview_source_capacity(store, json.dumps(wire), inventory)
    assert report["status"] == "unavailable" and report["reason"]
    assert "nodes" not in report and wire == before
    assert "PRIVATE_" not in json.dumps(report)


@pytest.mark.parametrize(
    "raw",
    ["not JSON", "[]", "{}", "x" * 128001],
    ids=["not-json", "array", "empty-object", "oversized"],
)
def test_invalid_or_oversized_raw_response_never_becomes_a_projection(tmp_path, raw):
    store, _quest, inventory = setup(tmp_path)
    assert preview_source_capacity(store, raw, inventory)["status"] == "unavailable"


def test_preview_does_not_use_provider_tools_or_store_write_transactions(tmp_path, monkeypatch):
    store, _quest, inventory = setup(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("diagnostic preview attempted authority or execution")

    monkeypatch.setattr(store, "unit_of_work", forbidden)
    from forge_core.providers import ProviderGateway
    from forge_core.tool_engine import ToolEngine

    monkeypatch.setattr(ProviderGateway, "generate", forbidden)
    monkeypatch.setattr(ToolEngine, "execute", forbidden)
    before = (tmp_path / "inputs/unicode.json").read_bytes()
    report = preview_source_capacity(store, json.dumps(overflow_wire()), inventory)
    assert report["status"] == "available"
    assert (tmp_path / "inputs/unicode.json").read_bytes() == before


def test_worst_case_details_are_bounded_without_hiding_overflow_nodes(tmp_path):
    store, _quest, _inventory = setup(tmp_path)
    nodes = []
    for index in range(8):
        key = "n" + str(index) + "x" * 98
        previous = [item["key"] for item in nodes]
        nodes.append(
            node(
                key,
                expected="work/" + "长" * 1900 + str(index),
                predecessors=[dependency(previous[-1], deliveries=previous)] if previous else [],
                tools=["file_read", "worker_read_completed_delivery", "file_write"],
                kind="json_review",
            )
        )
    report = preview_source_capacity(
        store,
        json.dumps({"allocation_mode": "core_measured", "nodes": nodes}, ensure_ascii=False),
        [],
    )
    assert report["status"] == "available" and report["truncated"] is True
    assert len(json.dumps(report, ensure_ascii=False).encode()) <= MAX_REPORT_BYTES
    assert len(report["nodes"]) == 8 and report["overflow_nodes"] == [
        item["key"] for item in nodes[1:]
    ]
    assert sum(item["omitted_contributions"] for item in report["nodes"]) > 0
    assert all(item["max_source_receipt_bytes"] > 65536 for item in report["nodes"][1:])


def test_unique_repair_contains_kind_error_and_independent_capacity_errors(tmp_path):
    store, quest, _inventory = setup(tmp_path)
    wire = overflow_wire()
    provider = NativeFramingMock(wire)
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
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
                semantic_protocol_version="v21",
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
            )
        )
    assert len(provider.requests) == 2
    feedback = json.loads(provider.requests[1].messages[-1]["content"])
    assert "success_criteria" in " ".join(feedback["rejected_proposal_error"])
    assert feedback["capacity_diagnostics"]["source_capacity_preview"]["overflow_nodes"] == [
        "consumer",
        "second_consumer",
    ]
    assert "PRIVATE_" not in json.dumps(feedback)
    assert QuestGraphService(store).history(quest.id) == []
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
    for request in provider.requests:
        native = (
            len(json.dumps(provider._payload(request, False), ensure_ascii=False).encode()) + 64
        )
        assert native + request.max_output_tokens <= 65536


def test_first_source_overflow_reports_other_nodes_without_extending_repair(tmp_path):
    store, quest, _inventory = setup(tmp_path)
    provider = NativeFramingMock(overflow_wire(invalid_kind=False))
    with pytest.raises(InvalidGraphProposal):
        asyncio.run(
            QuestGraphService(store).generate(
                quest.id,
                expected_version=0,
                provider=provider,
                provider_id="p",
                model_id="big",
                context_limit=65536,
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
                semantic_protocol_version="v21",
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
            )
        )
    feedback = json.loads(provider.requests[1].messages[-1]["content"])
    assert "Complete derived source capacity exceeds" in " ".join(
        feedback["rejected_proposal_error"]
    )
    assert feedback["capacity_diagnostics"]["source_capacity_preview"]["overflow_nodes"] == [
        "consumer",
        "second_consumer",
    ]
    assert len(provider.requests) == 2 and QuestGraphService(store).history(quest.id) == []


def test_durable_rejection_contains_safe_first_preview_and_never_approves(tmp_path, monkeypatch):
    from dataclasses import replace

    from planner_capacity_fixtures import configure_planner_capacity
    from test_core_measured_planner_v19 import configured_main

    main, quest, _event = configured_main(tmp_path, monkeypatch)
    wire = overflow_wire()
    wire["nodes"][0]["verified_input_paths"] = []
    provider = NativeFramingMock(wire)
    configure_planner_capacity(main, provider)
    provider.config = replace(provider.config, supports_tools=True, supports_json_schema=True)
    assert not asyncio.run(main.process_pending_plan())
    assert len(provider.requests) == 2
    rows = [
        json.loads(row["payload"])
        for row in main.store.list_events(limit=100)
        if row["event_type"] == "quest.plan_rejected"
    ]
    assert len(rows) == 2
    preview = rows[0]["capacity_diagnostics"]["source_capacity_preview"]
    assert preview["overflow_nodes"] == ["consumer", "second_consumer"]
    assert "source_capacity_preview" not in rows[1].get("capacity_diagnostics", {})
    assert "PRIVATE_" not in json.dumps(rows)
    assert main.quest_graph.history(quest.id) == []
