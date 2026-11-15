"""Bounded complete V25 diagnostics; explicit profile decode never alters a choice."""

import copy
import json

from pydantic import ConfigDict, create_model

from .domain import DomainError
from .planner_capacity_preview_v23 import _preview_complete_capacity
from .planner_dataflow_v25 import DataflowNodeWireV25
from .planner_guidance_v24 import preview_sources_v23
from .planner_source_preview import MAX_RESPONSE_BYTES

ProjectionNode = create_model(
    "PlannerV25ProfileSourceProjection",
    __config__=ConfigDict(extra="forbid", strict=True),
    **{
        name: (field.annotation, copy.deepcopy(field))
        for name, field in DataflowNodeWireV25.model_fields.items()
        if name != "success_criteria"
    },
)

NODE_COLUMNS = [
    "profile_index",
    "artifact_bytes",
    "delivery_bytes",
    "action_evidence_bytes",
    "known_source_bytes",
    "future_source_bytes",
    "source_bound_bytes",
    "raw_packet_bound",
    "raw_headroom",
    "review_input",
    "review_headroom",
    "correction_input",
    "correction_headroom",
    "initial_work_tokens",
    "review_cycle_reserve_tokens",
    "minimum_node_tokens",
    "policy_cycle_calls",
    "violation_indices",
]


def decoded_response(raw_response):
    """Decode valid profile/source fields while retaining invalid original criteria."""
    if not isinstance(raw_response, str) or len(raw_response.encode()) > MAX_RESPONSE_BYTES:
        raise ValueError("response_outside_projection_bound")
    raw = json.loads(raw_response)
    if (
        not isinstance(raw, dict)
        or set(raw) != {"allocation_mode", "nodes"}
        or raw["allocation_mode"] != "core_measured"
        or not isinstance(raw["nodes"], list)
        or not 1 <= len(raw["nodes"]) <= 8
    ):
        raise ValueError("invalid_profile_projection")
    nodes = []
    for node in raw["nodes"]:
        if not isinstance(node, dict) or set(node) - DataflowNodeWireV25.model_fields.keys():
            raise ValueError("invalid_profile_node_fields")
        projected = ProjectionNode.model_validate(
            {key: value for key, value in node.items() if key != "success_criteria"}
        )
        if projected.output_capacity.profile != "custom":
            inline = projected.output_capacity.profile.startswith("inline_")
            if inline != (projected.expected_artifact is None):
                raise ValueError("capacity_profile_must_match_explicit_artifact_contract")
        decoded = projected.model_dump(exclude={"output_capacity", "action_evidence"})
        decoded["success_criteria"] = node.get("success_criteria")
        decoded["capacity_bounds"] = {
            **projected.output_capacity.bounds(),
            "action_evidence_allowance_bytes": projected.action_evidence.allowance(),
        }
        nodes.append(decoded)
    return json.dumps({"allocation_mode": "core_measured", "nodes": nodes}, ensure_ascii=False)


def compact_report(report):
    """All nodes remain explicit; two candidate numeric rows are bounded per node."""
    nodes = []
    profiles, violation_codes = [], []
    for node in report["nodes"]:
        if node["status"] != "available":
            nodes.append(
                {
                    "node": node["node"],
                    "status": node["status"],
                    "reason": node.get("reason", "no_measurable_candidate"),
                    "omitted_candidates": node.get("omitted_candidates", 0),
                    "unmeasurable_candidates": node.get("unmeasurable_candidates", 0),
                }
            )
            continue
        rows = []
        for item in node["candidates"]:
            bounds = item["declared_bounds"]
            if item["profile_id"] not in profiles:
                profiles.append(item["profile_id"])
            for code in item["violations"]:
                if code not in violation_codes:
                    violation_codes.append(code)
            rows.append(
                [
                    profiles.index(item["profile_id"]),
                    bounds["max_artifact_bytes"],
                    bounds["max_delivery_bytes"],
                    item["action_evidence_allowance_bytes"],
                    item["known_source_bytes"],
                    item["future_source_bytes_bound"],
                    bounds["max_source_receipt_bytes"],
                    item["raw_packet_bound"],
                    item["raw_packet_headroom_bytes"],
                    item["review_input_bound"],
                    item["native_review_headroom"],
                    item["correction_input_bound"],
                    item["native_correction_headroom"],
                    item["initial_work_tokens"],
                    item["review_cycle_reserve_tokens"],
                    item["minimum_node_tokens"],
                    item["policy_cycle_calls"],
                    [violation_codes.index(code) for code in item["violations"]],
                ]
            )
        nodes.append(
            {
                "node": node["node"],
                "status": "available",
                "rows": rows,
                "omitted_candidates": node["omitted_candidates"],
                "unmeasurable_candidates": node["unmeasurable_candidates"],
            }
        )
    return {
        "version": "planner-capacity-preview-v25",
        "status": "available",
        "scope": "diagnostic_only_original_profiles_and_formulas_no_allocation_or_authority",
        "columns": NODE_COLUMNS,
        "profiles": profiles,
        "violation_codes": violation_codes,
        "nodes": nodes,
        "aggregate_policy_projection": report["aggregate_policy_projection"],
        "preserved_completed_keys": report["preserved_completed_keys"],
    }


def preview_complete_capacity_v25(*args, **kwargs):
    try:
        service, quest, raw, inventory, provider = args
        return _preview_complete_capacity(
            service,
            quest,
            decoded_response(raw),
            inventory,
            provider,
            _report_transform=compact_report,
            **kwargs,
        )
    except (OSError, KeyError, TypeError, ValueError, RecursionError, DomainError):
        return {
            "version": "planner-capacity-preview-v25",
            "status": "unavailable",
            "reason": "invalid_profile_or_complete_measurement",
        }


def preview_sources_v25(store, raw_response, inventory, **kwargs):
    try:
        return preview_sources_v23(store, decoded_response(raw_response), inventory, **kwargs)
    except (OSError, KeyError, TypeError, ValueError, RecursionError, DomainError):
        return {
            "version": "planner-source-preview-v25",
            "status": "unavailable",
            "reason": "invalid_profile_source_projection",
        }
