"""Core derives source bytes with the same complete packet formula as preflight."""

import copy
import json
from itertools import pairwise

import pytest

from forge_core.capacity_preflight import CapacityPreflightError, verified_input_inventory
from forge_core.domain import DomainError
from forge_core.predecessor_delivery import HANDOFF_METADATA_BYTES
from forge_core.settings import Workspace
from forge_core.source_capacity import (
    SOURCE_METADATA_RESERVE_BYTES,
    bind_source_receipt_bounds,
    measure_required_sources,
    required_source_receipt_bytes,
)
from forge_core.store import Store


def prepared(tmp_path, count=4):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    paths = []
    for number in range(count):
        path = f"input-{number}.txt"
        (tmp_path / path).write_text(
            f'中文 source {number}: "quote" \\path\nnext', encoding="utf-8"
        )
        paths.append(path)
    return store, paths, verified_input_inventory(store, paths)


def node(key, inputs=(), deliveries=(), artifact=None, artifact_bytes=1, delivery_bytes=3072):
    return {
        "key": key,
        "objective": f"Preserve all assigned requirements: {key}",
        "required_input_paths": list(inputs),
        "required_predecessor_deliveries": list(deliveries),
        "expected_artifact": artifact,
        "capacity_bounds": {
            "max_artifact_bytes": artifact_bytes,
            "max_delivery_bytes": delivery_bytes,
        },
    }


def graph(*nodes):
    return {
        "nodes": list(nodes),
        "edges": [
            {"source": a["key"], "target": b["key"], "condition": "success"}
            for a, b in pairwise(nodes)
        ],
        "max_total_tokens": 360000,
        "coordination_reserve_tokens": 36000,
    }


def test_canonical_list_framing_adds_five_bytes_to_four_complete_receipts(tmp_path):
    store, paths, inventory = prepared(tmp_path)
    data = graph(node("reader", paths))
    measured = measure_required_sources(store, data, data["nodes"][0], inventory)
    individually_summed = sum(
        len(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        for receipt in measured.receipts
    )
    assert measured.known_source_bytes == individually_summed + 5
    assert measured.future_source_bytes_bound == 0
    assert required_source_receipt_bytes(store, data, data["nodes"][0], inventory) == (
        individually_summed + 5
    )
    observation = bind_source_receipt_bounds(store, data, inventory)
    assert data["nodes"][0]["capacity_bounds"]["max_source_receipt_bytes"] == (
        individually_summed + 5 + SOURCE_METADATA_RESERVE_BYTES
    )
    assert "中文 source" not in json.dumps(observation, ensure_ascii=False)
    assert "中文 source" not in repr(measured)


def test_full_artifact_and_delivery_are_separate_explicit_sources_without_deduplication(tmp_path):
    store, _paths, inventory = prepared(tmp_path, count=0)
    producer = node("producer", artifact="work/full.json", artifact_bytes=3000, delivery_bytes=2000)
    consumer = node("consumer", ["work/full.json"], ["producer"])
    data = graph(producer, consumer)
    measured = measure_required_sources(store, data, consumer, inventory)
    assert measured.known_source_bytes == 2
    assert measured.future_source_bytes_bound == (
        2 * 3000 + 512 + 2 + 2000 + HANDOFF_METADATA_BYTES + 128
    )
    before = copy.deepcopy(data)
    bind_source_receipt_bounds(store, data, inventory)
    for actual, original in zip(data["nodes"], before["nodes"], strict=True):
        source = actual["capacity_bounds"]["max_source_receipt_bytes"]
        restored = copy.deepcopy(actual)
        restored["capacity_bounds"].pop("max_source_receipt_bytes")
        assert restored == original
        assert source > 0
    assert data["edges"] == before["edges"]
    assert data["max_total_tokens"] == 360000
    assert data["coordination_reserve_tokens"] == 36000


def test_no_source_still_preserves_the_empty_canonical_list_and_metadata_policy(tmp_path):
    store, _paths, inventory = prepared(tmp_path, count=0)
    data = graph(node("inline"))
    result = bind_source_receipt_bounds(store, data, inventory)
    assert result["nodes"]["inline"]["required_source_receipt_bytes"] == 2
    assert data["nodes"][0]["capacity_bounds"]["max_source_receipt_bytes"] == 1026


def test_source_overflow_is_atomic_and_never_clamped_or_shrunk(tmp_path):
    store, _paths, inventory = prepared(tmp_path, count=0)
    producer = node("producer", artifact="work/full.json", artifact_bytes=32768)
    data = graph(producer, node("consumer", ["work/full.json"]))
    before = copy.deepcopy(data)
    with pytest.raises(CapacityPreflightError, match="exceeds 65536") as caught:
        bind_source_receipt_bounds(store, data, inventory)
    assert data == before
    assert caught.value.capacity_diagnostics["max_source_receipt_bytes"] > 65536
    assert caught.value.capacity_diagnostics["source_receipt_limit_bytes"] == 65536


def test_existing_pending_source_bound_cannot_be_overwritten_including_on_later_error(tmp_path):
    store, _paths, inventory = prepared(tmp_path, count=0)
    data = graph(node("new"), node("legacy"))
    data["nodes"][1]["capacity_bounds"]["max_source_receipt_bytes"] = 50000
    before = copy.deepcopy(data)
    with pytest.raises(DomainError, match="omitted pending source bounds"):
        bind_source_receipt_bounds(store, data, inventory)
    assert data == before


def test_completed_source_and_output_contracts_remain_exact(tmp_path):
    store, _paths, inventory = prepared(tmp_path, count=0)
    complete = node("completed", artifact="work/prior.json", artifact_bytes=5000)
    complete["capacity_bounds"]["max_source_receipt_bytes"] = 45000
    complete["budget_tokens"] = 123456
    data = graph(complete, node("pending", ["work/prior.json"]))
    before = copy.deepcopy(complete)
    result = bind_source_receipt_bounds(store, data, inventory, completed_keys=["completed"])
    assert complete == before
    assert result["preserved_completed_keys"] == ["completed"]
    assert set(result["nodes"]) == {"pending"}
    assert data["nodes"][1]["capacity_bounds"]["max_source_receipt_bytes"] == 2 + 10512 + 1024


def test_mutated_inventory_fails_before_any_binding(tmp_path):
    store, paths, inventory = prepared(tmp_path, count=1)
    data = graph(node("reader", paths))
    before = copy.deepcopy(data)
    (tmp_path / paths[0]).write_text("changed source", encoding="utf-8")
    with pytest.raises(DomainError, match="Approved input inventory changed"):
        bind_source_receipt_bounds(store, data, inventory)
    assert data == before


def test_repeated_source_for_different_nodes_is_measured_in_full_for_each(tmp_path):
    store, paths, inventory = prepared(tmp_path, count=1)
    data = graph(node("first", paths), node("second", paths))
    result = bind_source_receipt_bounds(store, data, inventory)
    first, second = (result["nodes"][key] for key in ("first", "second"))
    assert first == second
    assert first["known_source_bytes"] > (tmp_path / paths[0]).stat().st_size
    assert data["nodes"][0]["required_input_paths"] == paths
    assert data["nodes"][1]["required_input_paths"] == paths


def test_inventory_source_precedes_future_artifact_classification_exactly_as_before(tmp_path):
    store, paths, inventory = prepared(tmp_path, count=1)
    data = graph(node("producer", artifact=paths[0]), node("consumer", paths))
    measured = measure_required_sources(store, data, data["nodes"][1], inventory)
    assert len(measured.receipts) == 1
    assert measured.future_source_bytes_bound == 0


@pytest.mark.parametrize("kind", ["unknown", "ambiguous", "missing_delivery"])
def test_unknown_future_sources_reject_without_partial_binding(tmp_path, kind):
    store, _paths, inventory = prepared(tmp_path, count=0)
    first = node("first", artifact="work/shared.json")
    if kind == "unknown":
        data = graph(first, node("consumer", ["work/unknown.json"]))
    elif kind == "ambiguous":
        data = graph(
            first,
            node("second", artifact="work/shared.json"),
            node("consumer", ["work/shared.json"]),
        )
    else:
        data = graph(first, node("consumer", deliveries=["missing"]))
    before = copy.deepcopy(data)
    with pytest.raises(DomainError):
        bind_source_receipt_bounds(store, data, inventory)
    assert data == before


def test_measurement_creates_no_runtime_evidence_or_model_attempts(tmp_path):
    store, paths, inventory = prepared(tmp_path, count=1)
    with store.connection() as connection:
        before = connection.iterdump()
        snapshot_before = list(before)
    data = graph(node("reader", paths))
    bind_source_receipt_bounds(store, data, inventory)
    with store.connection() as connection:
        assert list(connection.iterdump()) == snapshot_before
        assert connection.total_changes == 0
