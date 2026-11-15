"""Bounded source diagnostics for rejected V21 wires; never an admission path."""

import copy
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator

from .domain import DomainError
from .planner_dataflow import DataflowNodeWire, compile_source_dataflow, validate_source_tools
from .source_capacity import (
    MAX_SOURCE_RECEIPT_BYTES,
    SOURCE_METADATA_RESERVE_BYTES,
    measure_required_sources,
)

VERSION = "planner-source-preview-v1"
MAX_RESPONSE_BYTES = 128000
MAX_REPORT_BYTES = 8192
MAX_LABEL_CHARS = 240
SOURCE_FIELDS = (
    "key",
    "expected_artifact",
    "allowed_tools",
    "verified_input_paths",
    "predecessors",
    "capacity_bounds",
)

# Copy the actual source field contracts instead of weakening a verifier or
# constructing a fake criterion. The full semantic wire still rejects normally.
SourceProjectionNodeWire = create_model(
    "SourceProjectionNodeWire",
    __config__=ConfigDict(extra="forbid", strict=True),
    __validators__={"source_tools": model_validator(mode="after")(validate_source_tools)},
    **{
        name: (
            DataflowNodeWire.model_fields[name].annotation,
            copy.deepcopy(DataflowNodeWire.model_fields[name]),
        )
        for name in SOURCE_FIELDS
    },
)


class SourceProjectionWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    allocation_mode: Literal["core_measured"]
    nodes: list[SourceProjectionNodeWire] = Field(min_length=1, max_length=8)


def _unavailable(reason):
    return {
        "version": VERSION,
        "status": "unavailable",
        "scope": "diagnostic_only_no_plan_or_authority",
        "reason": reason,
    }


def _label(value):
    return value if len(value) <= MAX_LABEL_CHARS else value[: MAX_LABEL_CHARS - 1] + "…"


def preview_source_capacity(
    store, raw_response, inventory, *, completed_nodes=(), completed_edges=()
):
    """Measure independent selected sources even when full verifier fields fail.

    Projection validity establishes no semantic contract or permission. No plan,
    role, verifier, resource allocation, Provider call or Tool Engine is created.
    Complete canonical receipt measurement remains the existing shared formula.
    """
    if not isinstance(raw_response, str) or len(raw_response.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return _unavailable("response_outside_projection_bound")
    try:
        raw = json.loads(raw_response)
        if not isinstance(raw, dict) or set(raw) != {"allocation_mode", "nodes"}:
            return _unavailable("invalid_projection_root")
        if not isinstance(raw["nodes"], list) or not 1 <= len(raw["nodes"]) <= 8:
            return _unavailable("invalid_projection_nodes")
        for node in raw["nodes"]:
            if not isinstance(node, dict) or set(node) - DataflowNodeWire.model_fields.keys():
                return _unavailable("invalid_projection_node_fields")
        selected = SourceProjectionWire.model_validate(
            {
                "allocation_mode": raw["allocation_mode"],
                "nodes": [{name: node[name] for name in SOURCE_FIELDS} for node in raw["nodes"]],
            }
        )
        graph = compile_source_dataflow(
            selected.nodes,
            verified_paths=[item["path"] for item in inventory],
            completed_nodes=completed_nodes,
            completed_edges=completed_edges,
        )
    except (KeyError, TypeError, ValueError, RecursionError, ValidationError, DomainError):
        return _unavailable("invalid_source_projection")

    from .capacity_preflight import verify_input_inventory

    try:
        verify_input_inventory(store, inventory)
        completed = {node["key"] for node in completed_nodes}
        nodes, overflows = [], []
        for node in graph["nodes"]:
            if node["key"] in completed:
                continue
            measured = measure_required_sources(store, graph, node, inventory)
            derived = measured.required_source_receipt_bytes + SOURCE_METADATA_RESERVE_BYTES
            contributions = [
                {
                    key: _label(value) if isinstance(value, str) else value
                    for key, value in item.items()
                }
                for item in measured.future_contributions
            ]
            observation = {
                "node": node["key"],
                **measured.diagnostics(),
                "metadata_reserve_bytes": SOURCE_METADATA_RESERVE_BYTES,
                "max_source_receipt_bytes": derived,
                "source_limit_exceeded": derived > MAX_SOURCE_RECEIPT_BYTES,
                "selected_output_bounds": node["capacity_bounds"],
                "future_list_framing_bytes": measured.future_source_bytes_bound
                - sum(item["bytes"] for item in measured.future_contributions),
                "future_contributions": contributions,
                "omitted_contributions": 0,
            }
            if observation["source_limit_exceeded"]:
                overflows.append(node["key"])
            nodes.append(observation)
    except (OSError, ValueError, TypeError, KeyError, DomainError):
        return _unavailable("source_measurement_unavailable")

    report = {
        "version": VERSION,
        "status": "available",
        "scope": "complete_selected_source_only_not_request_or_task_feasibility",
        "authority": "diagnostic_only_no_plan_or_authority",
        "source_receipt_limit_bytes": MAX_SOURCE_RECEIPT_BYTES,
        "overflow_nodes": overflows,
        "nodes": nodes,
        "preserved_completed_keys": sorted(completed),
        "truncated": False,
    }
    while len(json.dumps(report, ensure_ascii=False).encode("utf-8")) > MAX_REPORT_BYTES:
        candidates = [item for item in nodes if item["future_contributions"]]
        if not candidates:
            return _unavailable("summary_outside_diagnostic_bound")
        largest = max(
            candidates,
            key=lambda item: len(
                json.dumps(item["future_contributions"], ensure_ascii=False).encode("utf-8")
            ),
        )
        largest["future_contributions"].pop()
        largest["omitted_contributions"] += 1
        report["truncated"] = True
    return report
