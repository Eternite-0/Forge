"""Semantic diagnostics are observations, never automatic graph repairs."""

import copy
import json

import pytest

from forge_core.planner_diagnostics import (
    MAX_DIAGNOSTIC_BYTES,
    MAX_INVALID_PATHS,
    MAX_PATH_CHARS,
    semantic_graph_diagnostics,
)


def node(key, *, output=None, inputs=()):
    return {"key": key, "expected_artifact": output, "required_input_paths": list(inputs)}


def edge(source, target, condition="success"):
    return {"source": source, "target": target, "condition": condition}


def test_self_output_and_future_input_identify_actual_producers_without_mutation():
    data = {
        "nodes": [
            node(
                "write", output="work/report.json", inputs=["work/report.json", "work/later.json"]
            ),
            node("later", output="work/later.json"),
        ],
        "edges": [edge("write", "later")],
    }
    before = copy.deepcopy(data)
    result = semantic_graph_diagnostics(data)
    assert data == before
    assert result["invalid_input_paths"] == [
        {
            "node": "write",
            "path": "work/report.json",
            "self_output": True,
            "actual_producers": ["write"],
            "missing_direct_success_edge": True,
        },
        {
            "node": "write",
            "path": "work/later.json",
            "self_output": False,
            "actual_producers": ["later"],
            "missing_direct_success_edge": True,
        },
    ]
    assert result["cycle_witness"] == []
    assert result["truncated"] is False


@pytest.mark.parametrize("condition", ["success", "always"])
def test_verified_or_existing_direct_predecessor_input_is_not_misreported(condition):
    data = {
        "nodes": [
            node("a", output="work/a.json"),
            node("b", inputs=["source.json", "work/a.json"]),
        ],
        "edges": [edge("a", "b", condition)],
    }
    result = semantic_graph_diagnostics(data, verified_paths=["source.json"])
    assert result["invalid_input_paths"] == []
    assert result["cycle_witness"] == []
    assert result["truncated"] is False


def test_failure_edge_and_transitive_ancestor_do_not_prove_direct_input_visibility():
    data = {
        "nodes": [node("a", output="work/a.json"), node("b"), node("c", inputs=["work/a.json"])],
        "edges": [edge("a", "b"), edge("b", "c"), edge("a", "c", "failure")],
    }
    result = semantic_graph_diagnostics(data)
    assert result["invalid_input_paths"][0]["actual_producers"] == ["a"]
    assert result["invalid_input_paths"][0]["missing_direct_success_edge"] is True


def test_ambiguous_producers_are_reported_even_with_one_direct_success_edge():
    data = {
        "nodes": [
            node("a", output="same.json"),
            node("b", output="same.json"),
            node("c", inputs=["same.json"]),
        ],
        "edges": [edge("a", "c")],
    }
    row = semantic_graph_diagnostics(data)["invalid_input_paths"][0]
    assert row["actual_producers"] == ["a", "b"]
    assert row["missing_direct_success_edge"] is False


def test_cycle_witness_is_an_actual_closed_edge_sequence_including_conditions():
    edges = [edge("root", "a"), edge("a", "b", "failure"), edge("b", "c", "always"), edge("c", "a")]
    data = {"nodes": [node(key) for key in ("root", "a", "b", "c")], "edges": edges}
    before = copy.deepcopy(data)
    result = semantic_graph_diagnostics(data)
    witness = result["cycle_witness"]
    assert data == before
    assert witness == edges[1:]
    assert all(
        a["target"] == b["source"] for a, b in zip(witness, witness[1:] + witness[:1], strict=True)
    )
    assert all(item in edges for item in witness)


def test_self_cycle_is_a_single_observed_edge():
    loop = edge("a", "a")
    result = semantic_graph_diagnostics(
        {"nodes": [node("a", output="own.json", inputs=["own.json"])], "edges": [loop]}
    )
    assert result["cycle_witness"] == [loop]
    assert result["invalid_input_paths"][0]["self_output"] is True


def test_unknown_missing_path_does_not_invent_a_producer_or_edge():
    result = semantic_graph_diagnostics(
        {"nodes": [node("a", inputs=["missing.json"])], "edges": []}
    )
    row = result["invalid_input_paths"][0]
    assert row["actual_producers"] == []
    assert row["missing_direct_success_edge"] is True


def test_output_is_bounded_and_source_content_never_enters_diagnostics():
    secret_source = "PRIVATE_SOURCE_BODY_" * 1000
    nodes = [
        {
            **node(f"n{i}", inputs=[f"work/{j}/" + "长" * 1000 for j in range(50)]),
            "objective": secret_source,
            "source": secret_source,
        }
        for i in range(10)
    ]
    data = {
        "nodes": nodes,
        "edges": [],
        "original_request": secret_source,
        "source_text": secret_source,
    }
    before = copy.deepcopy(data)
    result = semantic_graph_diagnostics(data, verified_paths=["source.json"] * 50)
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    assert data == before
    assert len(encoded) <= MAX_DIAGNOSTIC_BYTES
    assert b"PRIVATE_SOURCE_BODY" not in encoded
    assert result["truncated"] is True
    assert result["omitted"]["nodes"] == 2
    assert result["omitted"]["input_paths"] == 8 * 18
    assert result["omitted"]["input_diagnostics"] > 0
    assert len(result["invalid_input_paths"]) <= MAX_INVALID_PATHS
    assert all(len(row["path"]) <= MAX_PATH_CHARS for row in result["invalid_input_paths"])


def test_maximum_cycle_witness_and_edge_scan_are_bounded():
    keys = [f"n{i}" + "x" * 98 for i in range(8)]
    cycle = [edge(key, keys[(i + 1) % 8]) for i, key in enumerate(keys)]
    result = semantic_graph_diagnostics(
        {"nodes": [node(key) for key in keys], "edges": cycle + [edge(keys[0], keys[1])] * 50}
    )
    assert result["cycle_witness"] == cycle
    assert result["omitted"]["edges"] == 42
    assert result["truncated"] is True
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= MAX_DIAGNOSTIC_BYTES


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"nodes": "source text", "edges": []},
        {"nodes": [None, {"key": []}], "edges": [None]},
    ],
)
def test_malformed_raw_semantics_remain_a_bounded_observation(data):
    result = semantic_graph_diagnostics(data)
    assert result["truncated"] is True
    assert len(json.dumps(result).encode()) <= MAX_DIAGNOSTIC_BYTES
