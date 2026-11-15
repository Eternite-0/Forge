"""Quantitative planning guidance and read-only V23 source diagnostics."""

import copy
import json

from pydantic import ConfigDict, create_model, model_validator

from .domain import DomainError
from .planner_dataflow import compile_source_dataflow, validate_source_tools
from .planner_dataflow_v23 import DataflowNodeWireV23
from .planner_source_preview import MAX_REPORT_BYTES, MAX_RESPONSE_BYTES, SOURCE_FIELDS
from .source_capacity import (
    MAX_SOURCE_RECEIPT_BYTES,
    SOURCE_METADATA_RESERVE_BYTES,
    measure_required_sources,
)

SourceNodeV23 = create_model(
    "PlannerSourceNodeV23",
    __config__=ConfigDict(extra="forbid", strict=True),
    __validators__={"source_tools": model_validator(mode="after")(validate_source_tools)},
    **{
        name: (
            DataflowNodeWireV23.model_fields[name].annotation,
            copy.deepcopy(DataflowNodeWireV23.model_fields[name]),
        )
        for name in SOURCE_FIELDS
    },
)


def quantitative_guidance():
    """General policy examples, never a task-specific plan or output default."""
    return {
        "version": "planner-quantitative-guidance-v24",
        "authority": "advice_only_model_selects_all_semantics_and_bounds",
        "source_assignment": (
            "First assign every required inventory source to the real role that assesses it. "
            "Distribute responsibility across the requested specialists; do not give the first "
            "research node all files by default. Preserve complete required sources, findings "
            "and citations. A later role needs original files only for its own independent "
            "assessment; otherwise consume the predecessor's complete findings."
        ),
        "handoffs": (
            "If sequential work satisfies the task, choose one success edge from each node to "
            "the next. The immediate predecessor delivery must carry every still-needed earlier "
            "conclusion and source citation. Read only that delivery when sufficient. Every "
            "extra ancestor delivery adds its entire declared size plus metadata; every extra "
            "transitive edge disables the shared serial reserve. Preserve additional original "
            "inputs or ancestor handoffs when independent assessment actually requires them. "
            "Parallel requirements must retain independent branches and their full-cycle budgets."
        ),
        "byte_equations": {
            "K": "Canonical JSON bytes of the complete selected known-receipt list (empty list=2). Use verified_input_receipt_sizes; list framing also counts.",
            "F": "sum(2*producer_artifact_bound+512 per selected future artifact) + sum(producer_delivery_bound+2176 per selected handoff) + (2 if any handoff else 0)",
            "S": "K+F+1024+action_evidence_allowance_bytes; this is the unchanged total source bound, not a separate action cap",
            "G": "S-K+max_artifact_bytes+max_delivery_bytes",
            "raw_review": "measured_base_raw_packet+G+1024 <= 65536",
            "native_review": "measured_base_native_review+2*G+1024+1024 verdict <= selected model context",
            "native_correction": "measured_worker_input+2*max(0,G-F)+4096+selected response output tokens <= selected model context",
        },
        "sufficient_starting_points_bytes": {
            "condition": "Examples only when the full assigned result fits; model must choose sufficient bounds, never truncate required output to these values.",
            "read_only": "artifact=1, short complete eight-field handoff=1536-2048; multi-source findings/citations/uncertainty commonly need 4096-6144 canonical UTF-8 bytes; action evidence=0 for declared reads and inline analysis only",
            "small_owned_artifact": "Start by estimating actual complete code/report content around 2048-4096 bytes, with a separate complete delivery around 1536-2048; increase if the task requires more.",
            "action_evidence": "Counts new action receipt output beyond declared initial reads, not artifact body or tokens. A simple owned file write receipt commonly fits 1024; a bounded numeric/test result may need 4096 or more. Estimate all planned action results; 32768 is a maximum, not a default.",
        },
        "growth_cost": (
            "One unused byte of artifact, delivery or action allowance adds one raw review byte "
            "and two native review/correction allowance units before repeated review/correction "
            "cycles. Producer output bounds also increase every consumer's F. Tight complete "
            "contracts matter more than assigning fewer budget tokens: Core computes budgets "
            "from your declarations and never shrinks them. A 16384-byte delivery is not a "
            "response-token allowance. Core measures 2048 response tokens for declared outputs up "
            "to 2048 bytes, 4096 through 6144 bytes, and 8192 above; each tier must fit unchanged "
            "model context/output and full review limits."
        ),
        "resource_equations": {
            "independent": "sum(initial_work + complete_review_correction_reserve + policy_cycle_calls*1024) + original coordination reserve",
            "strict_success_chain": "sum(initial_work+1024 baselines) + largest remaining complete-cycle reserve with repeated role framing + original coordination reserve; Core checks exact fixed points and start admission",
            "limits": "Both chosen shape and every raw/native request must fit original caps and current charged/reserved residual. Reserve at least ten percent coordination including Planner/Composer. Shared reserve funds bounded contingency once, not unlimited corrections at every node.",
        },
        "repair": (
            "Use all node measurements and source contributions, not just the first error. "
            "Repair responsibility, necessary handoffs and model-selected sufficient output/action "
            "bounds together. No required source, specialization, report or criterion may be "
            "omitted. Core never rewires, lowers declarations, grants approval, raises caps or "
            "adds retries. Native measurement and ordinary plan/team approval remain decisive."
        ),
    }


def preview_sources_v23(store, raw_response, inventory, *, completed_nodes=(), completed_edges=()):
    """Measure exact V23 source fields even when verifier selections are invalid."""
    unavailable = {"version": "planner-source-preview-v23", "status": "unavailable"}
    if not isinstance(raw_response, str) or len(raw_response.encode()) > MAX_RESPONSE_BYTES:
        return {**unavailable, "reason": "response_outside_projection_bound"}
    try:
        raw = json.loads(raw_response)
        if (
            not isinstance(raw, dict)
            or set(raw) != {"allocation_mode", "nodes"}
            or raw["allocation_mode"] != "core_measured"
            or not isinstance(raw["nodes"], list)
            or not 1 <= len(raw["nodes"]) <= 8
            or any(
                not isinstance(node, dict) or set(node) - DataflowNodeWireV23.model_fields.keys()
                for node in raw["nodes"]
            )
        ):
            raise ValueError("invalid_projection")
        projected = [
            SourceNodeV23.model_validate({name: node[name] for name in SOURCE_FIELDS})
            for node in raw["nodes"]
        ]
        graph = compile_source_dataflow(
            projected,
            verified_paths=[item["path"] for item in inventory],
            completed_nodes=completed_nodes,
            completed_edges=completed_edges,
        )
        from .capacity_preflight import verify_input_inventory

        verify_input_inventory(store, inventory)
        completed = {node["key"] for node in completed_nodes}
        owners = {item["path"]: [] for item in inventory}
        observations = []
        for node in graph["nodes"]:
            for path in node["required_input_paths"]:
                if path in owners:
                    owners[path].append(node["key"])
            if node["key"] in completed:
                continue
            measured = measure_required_sources(store, graph, node, inventory)
            allowance = node["capacity_bounds"]["action_evidence_allowance_bytes"]
            derived = (
                measured.required_source_receipt_bytes + SOURCE_METADATA_RESERVE_BYTES + allowance
            )
            observations.append(
                {
                    "node": node["key"],
                    **measured.diagnostics(),
                    "action_evidence_allowance_bytes": allowance,
                    "metadata_reserve_bytes": SOURCE_METADATA_RESERVE_BYTES,
                    "max_source_receipt_bytes": derived,
                    "source_headroom_bytes": MAX_SOURCE_RECEIPT_BYTES - derived,
                    "future_contributions": list(measured.future_contributions),
                }
            )
        report = {
            "version": "planner-source-preview-v23",
            "status": "available",
            "scope": "diagnostic_only_complete_selected_sources_not_plan_or_authority",
            "nodes": observations,
            "inventory_owners": owners,
            "unselected_inventory_paths": [
                path for path, selected in owners.items() if not selected
            ],
            "inventory_note": "Coverage is descriptive. Model must preserve every task-required source; no automatic assignment, omission or authority is inferred.",
        }
        if len(json.dumps(report, ensure_ascii=False).encode()) > MAX_REPORT_BYTES:
            return {**unavailable, "reason": "complete_summary_outside_diagnostic_bound"}
        return report
    except (OSError, KeyError, TypeError, ValueError, RecursionError, DomainError):
        return {**unavailable, "reason": "invalid_source_projection"}
