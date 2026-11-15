"""Offline V23-wire guidance and diagnostics; no actual-model claims."""

import asyncio
import copy
import json

import pytest
from test_core_measured_serial_v17 import NativeFramingMock
from test_planner_protocol_v23 import graph, node, preview
from test_planner_router_input_v18 import Captured, NativeCapture, route_original_scenario
from test_planner_source_preview_v22 import dependency, setup

from forge_core.planner_guidance_v24 import preview_sources_v23
from forge_core.quest_graph import QuestGraphService


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize("repair", [False, True])
def test_actual_router_shapes_keep_full_sources_and_guidance_within_original_limits(
    tmp_path, level, repair
):
    store, quest, original, _observed = route_original_scenario(tmp_path, level)
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
                semantic_protocol_version="v23",
            )
        )
    request = provider.requests[0]
    payload = request.messages[1]["content"]
    data = json.loads(payload)
    assert len(payload.encode()) <= 24000
    assert provider.native_sizes[0] + request.max_output_tokens <= 65536
    assert all(
        size + item.max_output_tokens <= 65536
        for size, item in zip(provider.native_sizes, provider.requests, strict=True)
    )
    assert "action_evidence_allowance_bytes" in request.messages[0]["content"]
    assert data["original_request"]["content"] == original
    assert data["verified_input_inventory"] and data["verified_input_receipt_sizes"]
    assert data["semantic_protocol_version"] == "v23"
    guidance = data["quantitative_planning_guidance"]
    assert guidance["authority"] == "advice_only_model_selects_all_semantics_and_bounds"
    assert "1536-2048" in guidance["sufficient_starting_points_bytes"]["read_only"]
    assert "action evidence=0" in guidance["sufficient_starting_points_bytes"]["read_only"]
    assert set(guidance["byte_equations"]) >= {"K", "F", "S", "G", "native_correction"}
    assert QuestGraphService(store).history(quest["id"]) == []


def test_v23_source_preview_measures_action_allowance_and_every_handoff_without_mutation(tmp_path):
    store, quest, inventory = setup(tmp_path)
    selected = graph(
        node(
            "producer",
            expected="work/code.py",
            inputs=["inputs/unicode.json"],
            tools=["file_read", "file_write"],
            kind="PRIVATE_INVALID_VERIFIER",
            bound=2048,
            allowance=1024,
        ),
        node(
            "consumer",
            predecessors=[dependency("producer")],
            tools=["file_read", "worker_read_completed_delivery"],
            bound=1536,
            allowance=4096,
        ),
    )
    before = copy.deepcopy(selected)
    report = preview_sources_v23(store, json.dumps(selected), inventory)
    assert report["status"] == "available", report
    first, second = report["nodes"]
    assert first["action_evidence_allowance_bytes"] == 1024
    assert second["future_contributions"] == [
        {
            "kind": "artifact",
            "producer": "producer",
            "path": "work/code.py",
            "declared_bound": 2048,
            "bytes": 4608,
        },
        {"kind": "delivery", "producer": "producer", "declared_bound": 2048, "bytes": 4224},
    ]
    assert second["max_source_receipt_bytes"] == 2 + 4608 + 4224 + 2 + 1024 + 4096
    assert report["inventory_owners"] == {"inputs/unicode.json": ["producer"]}
    assert report["unselected_inventory_paths"] == []
    assert "PRIVATE_" not in json.dumps(report)
    assert selected == before and QuestGraphService(store).history(quest.id) == []


def test_complete_preview_explains_all_cycles_and_extra_edge_loss_of_serial_pool(tmp_path):
    store, quest, _inventory = setup(tmp_path)
    selected = graph(
        node("first", inputs=["inputs/unicode.json"], tools=["file_read"], bound=1536),
        node(
            "second",
            predecessors=[dependency("first", artifact=False)],
            tools=["worker_read_completed_delivery"],
            bound=1536,
        ),
        node(
            "third",
            predecessors=[dependency("second", artifact=False)],
            tools=["worker_read_completed_delivery"],
            bound=1536,
        ),
    )
    original = copy.deepcopy(selected)
    serial = preview(store, quest, selected, NativeFramingMock(selected))
    assert serial["status"] == "available", serial
    aggregate = serial["aggregate_policy_projection"]
    assert aggregate["shape"] == "strict_success_chain"
    assert aggregate["shared_reserve_tokens"] > 0
    assert not aggregate["nodes_with_contract_violations"]
    for observed in serial["nodes"]:
        candidate = observed["candidates"][0]
        assert (
            candidate["minimum_node_tokens"]
            == candidate["initial_work_tokens"] + candidate["review_cycle_reserve_tokens"]
        )
        assert candidate["policy_cycle_calls"] == 4
        assert candidate["native_correction_headroom"] >= 0
    selected["nodes"][2]["predecessors"].append(dependency("first", artifact=False, deliveries=[]))
    independent = preview(store, quest, selected, NativeFramingMock(selected))
    other = independent["aggregate_policy_projection"]
    assert other["shape"] == "independent_cycles"
    assert other["shared_reserve_tokens"] == 0
    assert other["required_total_projection"] > aggregate["required_total_projection"]
    assert original["nodes"][2]["predecessors"] != selected["nodes"][2]["predecessors"]
    assert QuestGraphService(store).history(quest.id) == []
