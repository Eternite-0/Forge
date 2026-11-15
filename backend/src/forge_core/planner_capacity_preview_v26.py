"""V26 diagnostics preserve exact hypotheses and original semantic validity."""

import copy
import json

from pydantic import ConfigDict, create_model

from .domain import DomainError
from .hypothesis_sources import validate_paths
from .planner_capacity_preview_v23 import _preview_complete_capacity
from .planner_capacity_preview_v25 import compact_report, decoded_response
from .planner_dataflow_v26 import DataflowNodeWireV26
from .planner_guidance_v24 import preview_sources_v23
from .planner_source_preview import MAX_RESPONSE_BYTES

ProjectionNodeV26 = create_model(
    "PlannerV26SourceProjection",
    __config__=ConfigDict(extra="forbid", strict=True),
    **{
        name: (field.annotation, copy.deepcopy(field))
        for name, field in DataflowNodeWireV26.model_fields.items()
        if name != "success_criteria"
    },
)


def projected_response(raw_response):
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
        raise ValueError("invalid_v26_projection")
    nodes, hypotheses, invalid = [], {}, set()
    for value in raw["nodes"]:
        if not isinstance(value, dict) or set(value) - DataflowNodeWireV26.model_fields.keys():
            raise ValueError("invalid_v26_node_fields")
        projected = ProjectionNodeV26.model_validate(
            {key: item for key, item in value.items() if key != "success_criteria"}
        )
        validate_paths(projected.hypothesis_read_paths)
        if projected.key in hypotheses:
            raise ValueError("duplicate_v26_projection_node")
        hypotheses[projected.key] = list(projected.hypothesis_read_paths)
        try:
            DataflowNodeWireV26.model_validate(value)
        except (ValueError, TypeError, DomainError):
            invalid.add(projected.key)
        node = projected.model_dump(
            exclude={"hypothesis_read_paths", "output_capacity", "action_evidence"}
        )
        node["success_criteria"] = value.get("success_criteria")
        node["output_capacity"] = {
            "profile": "custom",
            **projected.output_capacity.bounds(),
        }
        node["action_evidence"] = {
            "profile": "custom",
            "action_evidence_allowance_bytes": projected.action_evidence.allowance(),
        }
        nodes.append(node)
    # Reuse the unchanged exact capacity decoder; original V26 validity is
    # independently retained so a path-bearing V23 criterion cannot become valid.
    decoded = decoded_response(json.dumps({"allocation_mode": "core_measured", "nodes": nodes}))
    return decoded, hypotheses, invalid


def preview_complete_capacity_v26(*args, **kwargs):
    try:
        service, quest, raw, inventory, provider = args
        decoded, hypotheses, invalid = projected_response(raw)
        report = _preview_complete_capacity(
            service,
            quest,
            decoded,
            inventory,
            provider,
            _report_transform=compact_report,
            _hypothesis_read_paths=hypotheses,
            _invalid_semantic_keys=invalid,
            **kwargs,
        )
        report["version"] = (
            "planner-capacity-preview-v28"
            if kwargs.get("_source_wire_format") is not None
            else "planner-capacity-preview-v26"
        )
        return report
    except (OSError, KeyError, TypeError, ValueError, RecursionError, DomainError):
        return {
            "version": "planner-capacity-preview-v26",
            "status": "unavailable",
            "reason": "invalid_v26_profile_or_complete_measurement",
        }


def preview_sources_v26(store, raw_response, inventory, **kwargs):
    try:
        decoded, _hypotheses, _invalid = projected_response(raw_response)
        report = preview_sources_v23(store, decoded, inventory, **kwargs)
        report["version"] = "planner-source-preview-v26"
        return report
    except (OSError, KeyError, TypeError, ValueError, RecursionError, DomainError):
        return {
            "version": "planner-source-preview-v26",
            "status": "unavailable",
            "reason": "invalid_v26_source_projection",
        }
