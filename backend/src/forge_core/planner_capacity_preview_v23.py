"""Read-only complete capacity diagnostics for independently valid V23 nodes."""

import copy
import json

from pydantic import ConfigDict, ValidationError, create_model, model_validator

from .budget_policy import SERIAL_CAPACITY_VERSION, validate_serial_policy
from .capacity_preflight import VERSION, node_review_envelope, verify_input_inventory
from .domain import DomainError
from .planner_dataflow import compile_source_dataflow, validate_source_tools
from .planner_dataflow_v23 import DataflowNodeWireV23
from .planner_resource_policy import (
    INTERNAL_OBJECTIVE_INPUT_REFERENCE,
    planned_worker_output_tokens,
)
from .planner_source_preview import MAX_RESPONSE_BYTES
from .providers import ProviderError
from .review_envelope import review_cycle_reserve
from .source_capacity import SOURCE_METADATA_RESERVE_BYTES, measure_required_sources
from .team_composition import (
    DEFAULT_OUTPUT_SCHEMA,
    CompositionLimits,
    RoleSpec,
    TeamComposerEngine,
)
from .token_policy import token_budgets_enforced

MAX_REPORT_BYTES = 8192
NodeWithoutCriteria = create_model(
    "NodeWithoutCriteria",
    __config__=ConfigDict(extra="forbid", strict=True),
    __validators__={"source_tools": model_validator(mode="after")(validate_source_tools)},
    **{
        name: (field.annotation, copy.deepcopy(field))
        for name, field in DataflowNodeWireV23.model_fields.items()
        if name != "success_criteria"
    },
)
NUMBERS = frozenset(
    {
        "known_source_bytes",
        "future_source_bytes_bound",
        "raw_packet_bytes",
        "growth_bound",
        "raw_packet_bound",
        "raw_packet_limit",
        "review_input_bound",
        "correction_input_bound",
        "worker_input_bound",
        "model_context_limit",
        "output_bound",
        "allocated_tokens",
        "native_envelope_fixed_point_iterations",
        "input_acquisition_input_bound",
        "input_acquisition_total_bound",
        "initial_work_tokens",
        "action_completion_calls",
        "action_completion_total_bound",
    }
)


def _unavailable(reason):
    return {
        "version": "planner-capacity-preview-v23",
        "status": "unavailable",
        "reason": reason,
    }


def _aggregate_projection(graph, observations, completed):
    """Apply the original first-pass arithmetic without allocating any resource."""
    measured = {}
    for item in observations:
        candidates = item.get("candidates", [])
        if not candidates:
            return {"status": "unknown", "reason": "not_every_pending_node_measurable"}
        measured[item["node"]] = min(candidates, key=lambda value: value["minimum_node_tokens"])
    serial = graph.get("serial_capacity_policy")
    preserved = {
        node["key"]: node["budget_tokens"] for node in graph["nodes"] if node["key"] in completed
    }
    if serial:
        # Initial fresh graphs have a one-token measurement placeholder, exactly
        # as _size_core_resources. Preserved policies must never be resized.
        shared = (
            serial["shared_reserve_tokens"]
            if completed
            else max(
                1,
                max(
                    item["review_cycle_reserve_tokens"] + (item["policy_cycle_calls"] - 1) * 1024
                    for item in measured.values()
                ),
            )
        )
        budgets = {
            key: max(
                item["initial_work_tokens"] + 1024,
                item["minimum_node_tokens"] + item["policy_cycle_calls"] * 1024 - shared,
            )
            for key, item in measured.items()
        }
    else:
        shared = 0
        budgets = {
            key: item["minimum_node_tokens"] + item["policy_cycle_calls"] * 1024
            for key, item in measured.items()
        }
    total = (
        sum(budgets.values())
        + sum(preserved.values())
        + shared
        + graph["coordination_reserve_tokens"]
    )
    return {
        "status": "measured_first_pass_not_allocation_or_feasibility",
        "shape": "strict_success_chain" if serial else "independent_cycles",
        "node_budget_projection": budgets,
        "preserved_completed_budgets": preserved,
        "shared_reserve_tokens": shared,
        "coordination_reserve_tokens": graph["coordination_reserve_tokens"],
        "required_total_projection": total,
        "original_ceiling": graph["max_total_tokens"],
        "original_cap_headroom": graph["max_total_tokens"] - total,
        **({"token_budgets_enforced": False} if not token_budgets_enforced() else {}),
        "nodes_with_contract_violations": [
            key for key, item in measured.items() if item["violations"]
        ],
        "limit": "Original production first-pass formulas with 1024 framing headroom. Final native resource fixed point, every node contract, actual remaining scopes and normal approvals still must pass. No budgets or semantic choices were changed.",
    }


def _preview_complete_capacity(
    service,
    quest,
    raw_response,
    inventory,
    provider,
    *,
    resource_limits,
    coordination_reserve_tokens,
    completed_nodes=(),
    completed_edges=(),
    parent_graph=None,
    _report_transform=None,
    _hypothesis_read_paths=None,
    _invalid_semantic_keys=(),
    _source_wire_format=None,
):
    """Never compiles a partial plan or assigns a replacement criterion.

    The global dataflow projection has no success_criteria for invalid nodes.
    Only a node passing its entire real V23 semantic wire is passed to the
    original pure measurement function. Other nodes remain explicit unknowns.
    No measured graph, resource allocation, plan, tool result or authority exits.
    """
    if not isinstance(raw_response, str) or len(raw_response.encode()) > MAX_RESPONSE_BYTES:
        return _unavailable("response_outside_projection_bound")
    try:
        raw = json.loads(raw_response)
        if not isinstance(raw, dict) or set(raw) != {"allocation_mode", "nodes"}:
            return _unavailable("invalid_projection_root")
        if raw["allocation_mode"] != "core_measured" or not isinstance(raw["nodes"], list):
            return _unavailable("invalid_projection_root")
        if not 1 <= len(raw["nodes"]) <= 8:
            return _unavailable("invalid_projection_nodes")
        projected = [
            NodeWithoutCriteria.model_validate(
                {k: v for k, v in node.items() if k != "success_criteria"}
            )
            for node in raw["nodes"]
        ]
        selected = compile_source_dataflow(
            projected,
            verified_paths=[item["path"] for item in inventory],
            completed_nodes=completed_nodes,
            completed_edges=completed_edges,
        )
        verify_input_inventory(service.store, inventory)
    except (
        KeyError,
        TypeError,
        ValueError,
        RecursionError,
        ValidationError,
        DomainError,
    ):
        return _unavailable("invalid_noncriterion_projection")
    completed = {node["key"] for node in completed_nodes}
    selected_by_key = {node["key"]: node for node in selected["nodes"]}
    valid, nodes, statuses, allowances = {}, [], {}, {}
    for raw_node, projection in zip(raw["nodes"], projected, strict=True):
        node = {
            **projection.model_dump(exclude={"verified_input_paths", "predecessors"}),
            **selected_by_key[projection.key],
            "budget_tokens": 2049,
        }
        capabilities = node["required_capabilities"]
        allowances[node["key"]] = node["capacity_bounds"].pop("action_evidence_allowance_bytes")
        if len({item["name"] for item in capabilities}) != len(capabilities):
            statuses[node["key"]] = "invalid_semantic_node"
        else:
            node["required_capabilities"] = {item["name"]: item["minimum"] for item in capabilities}
            try:
                if node["key"] in _invalid_semantic_keys:
                    raise ValueError("original_semantic_node_invalid")
                typed = DataflowNodeWireV23.model_validate(raw_node)
                node["success_criteria"] = [
                    item.criterion(typed.expected_artifact) for item in typed.success_criteria
                ]
                valid[node["key"]] = node
            except (ValueError, TypeError, ValidationError):
                statuses[node["key"]] = "invalid_criteria_node_not_measured"
        nodes.append(node)
    graph = {
        "nodes": nodes + list(completed_nodes),
        "edges": selected["edges"],
        **resource_limits,
        "coordination_reserve_tokens": coordination_reserve_tokens,
        "capacity_contract": {
            "version": VERSION,
            "input_inventory": inventory,
            "nodes": {},
            "input_acquisition_policy": "core-declared-source-prefetch-v1",
            "action_evidence_allowances": allowances,
        },
    }
    if _source_wire_format is not None:
        from .source_wire import validate_source_wire_format

        validate_source_wire_format(_source_wire_format)
        graph["capacity_contract"]["source_wire_format"] = _source_wire_format
    if _hypothesis_read_paths is not None:
        from .hypothesis_sources import validate_graph

        graph["capacity_contract"]["hypothesis_read_paths"] = _hypothesis_read_paths
        if completed:
            graph["capacity_contract"]["preserved_completed_keys"] = sorted(completed)
        try:
            validate_graph(graph)
        except DomainError:
            return _unavailable("invalid_hypothesis_scope")
    # Match the unchanged production's initial resource-sizing shape exactly.
    preserved_policy = (parent_graph or {}).get("serial_capacity_policy")
    if parent_graph is None:
        candidate = {
            **graph,
            "serial_capacity_policy": {
                "version": SERIAL_CAPACITY_VERSION,
                "shared_reserve_tokens": 1,
            },
        }
        try:
            if validate_serial_policy(candidate):
                graph = candidate
        except DomainError:
            pass
    elif preserved_policy:
        graph["serial_capacity_policy"] = copy.deepcopy(preserved_policy)
    for node in graph["nodes"]:
        if node["key"] in completed:
            continue
        measured = measure_required_sources(service.store, graph, node, inventory)
        node["capacity_bounds"]["max_source_receipt_bytes"] = (
            measured.required_source_receipt_bytes
            + SOURCE_METADATA_RESERVE_BYTES
            + allowances[node["key"]]
        )
    profiles = [
        model
        for model in service.store.list_models()[:100]
        if model.enabled and not model.quarantine_reason
    ]
    engine, observations = TeamComposerEngine(), []
    for node in nodes:
        key = node["key"]
        if key not in valid:
            observations.append({"node": key, "status": "unavailable", "reason": statuses[key]})
            continue
        response_output = planned_worker_output_tokens(node)
        role = RoleSpec(
            id="research",
            task_node_key=key,
            required_capabilities=node["required_capabilities"],
            tools=node["allowed_tools"],
            input_tokens=1,
            output_tokens=response_output,
            responsibility=node["objective"],
            input_contract=node["required_input_paths"] or [INTERNAL_OBJECTIVE_INPUT_REFERENCE],
            output_schema=DEFAULT_OUTPUT_SCHEMA,
            acceptance_criteria=node["success_criteria"],
        )
        limits = CompositionLimits(
            max_members=1,
            max_concurrency=1,
            max_depth=1,
            max_total_tokens=response_output + 1,
            allowed_tools=node["allowed_tools"],
        )
        candidates, unavailable = [], 0
        with service.store.connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            for model in profiles:
                try:
                    body = engine.compose([role], [model], limits, strategy="greedy")
                    body["members"][0]["input_acquisition_policy"] = (
                        "core-declared-source-prefetch-v1"
                    )
                    body["members"][0]["action_evidence_allowance_bytes"] = allowances[key]
                    if _source_wire_format is not None:
                        body["members"][0]["source_wire_format"] = _source_wire_format
                    selected_provider = (
                        service.capacity_providers.get(model.provider)
                        if service.capacity_providers
                        else provider
                    )
                    if (
                        getattr(
                            getattr(selected_provider, "config", None),
                            "id",
                            model.provider,
                        )
                        != model.provider
                    ):
                        raise DomainError("selected_provider_framing_unavailable")
                    envelope, diagnostic = node_review_envelope(
                        service.store,
                        connection,
                        quest,
                        graph,
                        node,
                        body["members"][0],
                        selected_provider,
                        resource_sizing=True,
                        _diagnostic_only=True,
                    )
                    model_review = any(
                        criterion in {"delivery_review", "json_review:@delivery"}
                        or criterion.startswith("json_review:")
                        for criterion in node["success_criteria"]
                    )
                    reserve = (
                        review_cycle_reserve(envelope)
                        if model_review and not diagnostic["violations"]
                        else (2 * (diagnostic["review_input_bound"] + 1024) if model_review else 0)
                        + envelope["correction_calls"]
                        * (diagnostic["correction_input_bound"] + diagnostic["output_bound"])
                    )
                    candidates.append(
                        {
                            **{
                                name: value for name, value in diagnostic.items() if name in NUMBERS
                            },
                            "profile_id": model.id,
                            "declared_bounds": diagnostic["declared_bounds"],
                            "action_evidence_allowance_bytes": allowances[key],
                            "review_cycle_reserve_tokens": reserve,
                            "minimum_node_tokens": diagnostic["initial_work_tokens"] + reserve,
                            "policy_cycle_calls": 1
                            + diagnostic["action_completion_calls"]
                            + (2 if model_review else 0)
                            + envelope["correction_calls"],
                            "raw_packet_headroom_bytes": 65536 - diagnostic["raw_packet_bound"],
                            "native_review_headroom": model.context_window
                            - diagnostic["review_input_bound"]
                            - (1024 if model_review else 0),
                            "native_correction_headroom": model.context_window
                            - diagnostic["correction_input_bound"]
                            - diagnostic["output_bound"],
                            "violations": diagnostic["violations"],
                        }
                    )
                except (ValueError, TypeError, KeyError, DomainError, ProviderError):
                    unavailable += 1
        observations.append(
            {
                "node": key,
                "status": "available" if candidates else "unavailable",
                "candidates": candidates[:2],
                "omitted_candidates": max(0, len(candidates) - 2),
                "unmeasurable_candidates": unavailable,
            }
        )
    report = {
        "version": "planner-capacity-preview-v23",
        "status": "available",
        "scope": "diagnostic_only_valid_nodes_original_formulas_no_allocation_or_authority",
        "nodes": observations,
        "aggregate_policy_projection": _aggregate_projection(graph, observations, completed),
        "preserved_completed_keys": sorted(completed),
    }
    if _report_transform is not None:
        report = _report_transform(report)
    if len(json.dumps(report, ensure_ascii=False).encode()) > MAX_REPORT_BYTES:
        return _unavailable("complete_summary_outside_diagnostic_bound")
    return report


def preview_complete_capacity(*args, **kwargs):
    try:
        return _preview_complete_capacity(*args, **kwargs)
    except (OSError, KeyError, TypeError, ValueError, RecursionError, DomainError):
        return _unavailable("complete_measurement_unavailable")
