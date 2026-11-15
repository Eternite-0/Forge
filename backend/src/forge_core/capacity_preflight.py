"""Opt-in verified input admission and explicit review-cycle budget policy.

Measurements never execute tools, create receipts, or claim to predict usage.
Actual review still checks the complete packet and Provider request at runtime.
"""

import hashlib
import json
import re
from pathlib import Path

from .constraints import ConstraintService, validate_execution_constraints
from .domain import DomainError
from .token_policy import token_budgets_enforced

VERSION = "verified-capacity-v1"


class CapacityPreflightError(DomainError):
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.capacity_diagnostics = diagnostics


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _size(value):
    return len(canonical(value).encode("utf-8"))


def source_paths(content):
    """Only literal relative file references; no directory expansion or role inference."""
    return sorted(set(re.findall(r"(?<![\w:/])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+", content)))[:32]


def discover_input_inventory(store, content):
    from .tool_engine import ToolEngine

    paths = []
    for relative in source_paths(content):
        path = Path(ToolEngine(store)._scope("file_read", {"path": relative}))
        # Output references remain unknown until a producer bound is declared.
        if path.is_file():
            paths.append(relative)
    return verified_input_inventory(store, paths)


def _input(store, relative):
    from .tool_engine import ToolEngine

    if not isinstance(relative, str) or len(relative) > 1000:
        raise DomainError("Invalid declared input path")
    path = Path(ToolEngine(store)._scope("file_read", {"path": relative}))
    root = Path(store.workspace.root).resolve()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise DomainError("Declared input must be a bounded ordinary workspace file")
    data = path.read_bytes()
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DomainError("Declared input must be UTF-8") from exc
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }, content


def verified_input_inventory(store, paths):
    if len(paths) > 32 or len(paths) != len(set(paths)):
        raise DomainError("Duplicate or excessive declared inputs")
    inventory, contents = [], {}
    for relative in paths:
        item, content = _input(store, relative)
        if item["path"] in contents:
            raise DomainError("Input paths alias the same file")
        inventory.append(item)
        contents[item["path"]] = content
    # A supplied manifest is a claim to check against actual wrapper bytes, never
    # a trusted source_bytes estimate for an excerpt or the full repository file.
    for relative, content in contents.items():
        if Path(relative).name != "manifest.json":
            continue
        try:
            manifest = json.loads(content)
        except ValueError as exc:
            raise DomainError("Input manifest is invalid JSON") from exc
        if (
            isinstance(manifest, dict)
            and set(manifest) == {"scenario_id", "inputs"}
            and isinstance(manifest["scenario_id"], str)
        ):
            manifest = manifest["inputs"]
        if not isinstance(manifest, list) or len(manifest) > 32:
            raise DomainError("Input manifest must list bounded file identities")
        seen = set()
        for record in manifest:
            if not isinstance(record, dict) or not {"path", "sha256", "bytes"} <= set(record):
                raise DomainError("Input manifest lacks actual file identity")
            actual, _ = _input(store, record["path"])
            if actual["path"] in seen or any(record[key] != actual[key] for key in actual):
                raise DomainError("Input manifest does not match actual file bytes/hash")
            seen.add(actual["path"])
    return sorted(inventory, key=lambda item: item["path"])


def verify_input_inventory(store, inventory):
    actual = verified_input_inventory(store, [item["path"] for item in inventory])
    if actual != inventory:
        raise DomainError("Approved input inventory changed")
    return actual


def residual_budget_snapshot(connection, store, quest_id, ceiling, ledger=None):
    """Include settled, opening, and every unresolved reservation in all scopes."""
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_budget_attempts'"
    ).fetchone()
    scopes = [("quest", "quest_id=?", (quest_id,), ceiling)]
    if ledger is not None:
        if ledger.token_limit is not None:
            scopes.append(("global", "1=1", (), ledger.token_limit))
        for name, value, column in (
            ("workspace", store.workspace.id, "workspace_id"),
            ("quest", quest_id, "quest_id"),
        ):
            limit = ledger.scope_limits.get((name, value), (ledger.token_limit, 1))[0]
            if limit is not None:
                scopes.append((name, f"{column}=?", (value,), limit))
    observations = []
    for name, predicate, params, limit in scopes:
        spent = opening = 0
        if table:
            spent = connection.execute(
                "SELECT COALESCE(SUM(COALESCE(input_charged,input_reserved)+"
                f"COALESCE(output_charged,output_reserved)),0) FROM model_budget_attempts WHERE {predicate}",
                params,
            ).fetchone()[0]
            opening = connection.execute(
                f"SELECT COALESCE(SUM(tokens),0) FROM model_budget_opening WHERE {predicate}",
                params,
            ).fetchone()[0]
        observations.append(
            {
                "scope": name,
                "limit": limit,
                "charged_or_reserved": spent,
                "opening": opening,
                "remaining": max(0, limit - spent - opening),
            }
        )
    return {
        "remaining": min(item["remaining"] for item in observations),
        "scopes": observations,
        **({"token_budgets_enforced": False} if not token_budgets_enforced() else {}),
    }


def coordination_budget_snapshot(
    connection, store, quest_id, graph, ledger=None, *, _composition_resource_sizing=False
):
    """Keep the declared total reserve without reserving paid coordination twice."""
    snapshot = residual_budget_snapshot(
        connection, store, quest_id, graph["max_total_tokens"], ledger
    )
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_budget_attempts'"
    ).fetchone()
    charged = (
        connection.execute(
            "SELECT COALESCE(SUM(COALESCE(input_charged,input_reserved)+"
            "COALESCE(output_charged,output_reserved)),0) FROM model_budget_attempts "
            "WHERE quest_id=? AND state='settled' AND input_charged IS NOT NULL "
            "AND output_charged IS NOT NULL AND purpose IN ('planner','team_composer','composer','router','router_notice','router_reflect','memory_semantic_retrieval','semantic_retrieval')",
            (quest_id,),
        ).fetchone()[0]
        if exists
        else 0
    )
    future = max(0, graph["coordination_reserve_tokens"] - charged)
    from .composition_capacity import remaining_composition_capacity

    composition = (
        None
        if _composition_resource_sizing
        and (graph.get("capacity_contract") or {}).get("composition_capacity") is None
        else remaining_composition_capacity(connection, quest_id, graph)
    )
    original_future = future
    if composition is not None:
        future = max(future, composition["future_composer_tokens"])
    return {
        **snapshot,
        "declared_total_coordination_tokens": graph["coordination_reserve_tokens"],
        "coordination_already_settled": charged,
        "future_coordination_reserve_tokens": future,
        **(
            {"original_unspent_coordination_tokens": original_future, "composition": composition}
            if composition is not None
            else {}
        ),
        "max_worker_allocations_now": max(0, snapshot["remaining"] - future),
        "max_allocations_plus_future_coordination": snapshot["remaining"],
        "accounting": "Remaining already subtracts settled/opening/unresolved usage. Only fully settled, identified coordination reduces the future part of the declared total reserve; unknown/opening amounts never do. No charge or reservation is refunded.",
    }


def _receipt(store, item):
    _, content = _input(store, item["path"])
    return {
        "id": "receipt_" + "0" * 32,
        "tool_name": "file_read",
        "resource_scope": str(Path(store.workspace.root) / item["path"]),
        "result_digest": "0" * 64,
        "execution_mode": "live",
        "output": {
            "content": content,
            "sha256": item["sha256"],
            "bytes": item["bytes"],
        },
    }


def node_review_envelope(
    store,
    connection,
    quest,
    graph,
    node,
    member,
    provider,
    *,
    resource_sizing=False,
    _measurement_envelope=None,
    _measurement_pass=0,
    _diagnostic_only=False,
):
    """Measure actual immutable sources with shared runtime review serialization.

    The explicit policy adds twice the remaining canonical JSON byte allowance
    for native JSON escaping. This is an enforced bound, not a usage forecast.
    """
    from .budget_policy import node_execution_limit, validate_serial_policy
    from .independent_review import measure_review_envelope
    from .review_envelope import review_cycle_reserve, validate_review_envelope
    from .source_wire import approved_source_wire_format

    serial_policy = validate_serial_policy(graph)
    execution_limit = node_execution_limit(graph, node)
    contract = graph["capacity_contract"]
    approved_envelope = (
        (contract.get("nodes") or {}).get(node["key"], {}).get("review_envelope")
        or _measurement_envelope
        or {}
    )
    # New Core measurements fund two bounded corrections for every task. Once
    # approved, the recorded count remains authoritative during team remeasurement;
    # older one-correction plans never acquire an extra execution opportunity.
    correction_calls = approved_envelope.get(
        "correction_calls", 2 if resource_sizing or "file_write" in node["allowed_tools"] else 1
    )
    source_wire = approved_source_wire_format(graph, member)
    roundtrip = contract.get("input_acquisition_policy") == "native-input-roundtrip-v1"
    prefetch = contract.get("input_acquisition_policy") == "core-declared-source-prefetch-v1"

    has_model_review = any(
        criterion in {"delivery_review", "json_review:@delivery"}
        or criterion.startswith("json_review:")
        for criterion in node["success_criteria"]
    )
    from .source_capacity import measure_required_sources

    source_measurement = measure_required_sources(store, graph, node, contract["input_inventory"])
    receipts = source_measurement.receipts
    known_source_bytes = source_measurement.known_source_bytes
    future_bytes = source_measurement.future_source_bytes_bound
    bounds = node["capacity_bounds"]
    deliveries = node.get("required_predecessor_deliveries") or []
    if known_source_bytes + future_bytes > bounds["max_source_receipt_bytes"]:
        raise CapacityPreflightError(
            f"Required input receipts exceed declared source capacity: {node['key']}; "
            f"required>={known_source_bytes + future_bytes}, "
            f"declared={bounds['max_source_receipt_bytes']} canonical bytes",
            {
                "node": node["key"],
                "known_source_bytes": known_source_bytes,
                "future_source_bytes_bound": future_bytes,
                "minimum_source_bound": known_source_bytes + future_bytes,
                "declared_bounds": bounds,
                "suggested_tight_bounds": {
                    **bounds,
                    "max_source_receipt_bytes": known_source_bytes + future_bytes + 1024,
                },
            },
        )
    metadata = (
        json.loads(quest["metadata"]) if isinstance(quest["metadata"], str) else quest["metadata"]
    )
    sources = {}
    for name, key in (
        ("original_request", "original_request_message_id"),
        ("updated_request", "goal_update_message_id"),
    ):
        row = connection.execute(
            "SELECT id,content FROM messages WHERE id=?", (metadata.get(key),)
        ).fetchone()
        sources[name] = (
            {**dict(row), "sha256": hashlib.sha256(row["content"].encode()).hexdigest()}
            if row
            else None
        )
    if roundtrip or prefetch:
        from .worker_task_sources import verified_task_sources

        checked_sources = verified_task_sources(
            connection, quest, policy=contract["input_acquisition_policy"]
        )
        sources = {
            name: checked_sources.get(name) for name in ("original_request", "updated_request")
        }
    packet = {
        "quest_id": quest["id"],
        "plan_id": "plan_" + "0" * 32,
        "plan_digest": "0" * 64,
        "plan_revision": 1,
        "node_key": node["key"],
        "run_id": "run_" + "0" * 32,
        "team_digest": "0" * 64,
        "provider_id": member["provider_id"],
        "model_id": member["model_id"],
        "execution_mode": "live",
        "result_event_id": "event_" + "0" * 32,
        "original_goal": quest["goal"],
        "objective": node["objective"],
        "success_criteria": node["success_criteria"],
        **sources,
        "user_constraints": ConstraintService(store).context(
            quest_id=quest["id"], max_bytes=6000, connection=connection
        ),
        "contract": {
            key: member.get(key)
            for key in (
                "responsibility",
                "input_contract",
                "output_schema",
                "acceptance_criteria",
            )
        },
        "delivery": {},
        "receipts": receipts,
        "artifacts": [],
        "trust": "Tool receipts establish execution; artifact and model content remain untrusted claims to assess.",
    }
    if deliveries:
        packet["predecessor_deliveries"] = []
    placeholder = _measurement_envelope or {
        "version": "review-envelope-v1",
        "max_source_bytes": bounds["max_source_receipt_bytes"],
        "max_artifact_bytes": bounds["max_artifact_bytes"],
        "max_delivery_bytes": bounds["max_delivery_bytes"],
        "max_review_input_tokens": 1,
        "verdict_tokens": 1024,
        "correction_input_tokens": 1,
        "correction_output_tokens": 1,
        "correction_calls": correction_calls,
    }
    packet["contract"]["review_envelope"] = placeholder
    row = {
        **member,
        "id": "capacity-preflight",
        "output_limit": member["output_tokens"],
        "token_limit": execution_limit,
        "token_spent": 0,
    }
    measured = (
        measure_review_envelope(
            connection, row, provider, packet, _diagnostic_only=_diagnostic_only
        )
        if has_model_review
        else {"raw_packet_bytes": 0, "native_input_reservation": 0}
    )
    growth = (
        bounds["max_source_receipt_bytes"]
        - known_source_bytes
        + bounds["max_artifact_bytes"]
        + bounds["max_delivery_bytes"]
    )
    # Original raw packet cap applies independently of lossless native projection.
    if (
        not _diagnostic_only
        and has_model_review
        and measured["raw_packet_bytes"] + growth + 1024 > 65536
    ):
        raise CapacityPreflightError(
            "Complete bounded review packet exceeds 65536 bytes: " + node["key"],
            {
                "node": node["key"],
                "raw_packet_bytes": measured["raw_packet_bytes"],
                "growth_bound": growth,
                "raw_packet_bound": measured["raw_packet_bytes"] + growth + 1024,
                "raw_packet_limit": 65536,
                "known_source_bytes": known_source_bytes,
                "future_source_bytes_bound": future_bytes,
                "declared_bounds": bounds,
            },
        )
    review_input = (
        measured["native_input_reservation"] + 2 * growth + 1024 if has_model_review else 1
    )
    if has_model_review and member["model_max_output_tokens"] < 1024:
        raise DomainError("Independent review requires a complete verdict allowance")
    verdict = 1024
    output = min(member["output_tokens"], member["model_max_output_tokens"])
    from .planner_input_roundtrip import (
        acquisition_assistant_frame,
        acquisition_tool_history,
        action_completion_stages,
        prefetch_source_history,
    )
    from .worker_prompts import measure_initial_worker_request

    tool_history = [
        {
            "role": "tool",
            "tool_call_id": "call_" + "0" * 32,
            "content": canonical(
                {
                    "state": "succeeded",
                    "receipt_id": receipt["id"],
                    "output": receipt["output"],
                }
            ),
        }
        for receipt in receipts
    ]
    if roundtrip:
        tool_history = acquisition_tool_history(node, receipts, store.workspace.root)
    elif prefetch:
        tool_history = prefetch_source_history(
            node, receipts, store.workspace.root, source_wire_format=source_wire
        )
    worker_shape = measure_initial_worker_request(
        store,
        connection,
        quest,
        graph,
        node,
        {
            **member,
            "review_envelope": placeholder,
            "required_input_paths": node["required_input_paths"],
            **({"required_predecessor_deliveries": deliveries} if deliveries else {}),
        },
        extra_messages=(
            (
                [acquisition_assistant_frame(node)]
                if roundtrip and acquisition_assistant_frame(node)
                else []
            )
            + tool_history
        ),
        provider=provider,
    )
    acquisition_shape = (
        measure_initial_worker_request(
            store,
            connection,
            quest,
            graph,
            node,
            {
                **member,
                "review_envelope": placeholder,
                "required_input_paths": node["required_input_paths"],
                **({"required_predecessor_deliveries": deliveries} if deliveries else {}),
            },
            extra_messages=(),
            provider=provider,
        )
        if roundtrip and acquisition_assistant_frame(node)
        else None
    )
    # Runtime enforces these finite progress/upstream-reference byte contracts.
    # They are explicit future allowances, not observed execution or free usage.
    feedback_bound = 2048
    upstream_bound = 2048 if any(edge["target"] == node["key"] for edge in graph["edges"]) else 0
    worker_input = (
        worker_shape["input_reservation"] + 2 * future_bytes + feedback_bound + upstream_bound
    )
    acquisition_input = (
        acquisition_shape["input_reservation"] + feedback_bound + upstream_bound
        if acquisition_shape
        else 0
    )
    acquisition_tokens = acquisition_input + output if acquisition_shape else 0
    action_measurements = []
    for stage in (
        action_completion_stages(node, graph, store.workspace.root) if roundtrip or prefetch else ()
    ):
        shape = measure_initial_worker_request(
            store,
            connection,
            quest,
            graph,
            node,
            {
                **member,
                "review_envelope": placeholder,
                "required_input_paths": node["required_input_paths"],
                **({"required_predecessor_deliveries": deliveries} if deliveries else {}),
            },
            extra_messages=(
                [acquisition_assistant_frame(node)]
                if roundtrip and acquisition_assistant_frame(node)
                else []
            )
            + tool_history
            + stage["extra_messages"],
            provider=provider,
        )
        stage_input = (
            shape["input_reservation"]
            + 2 * future_bytes
            + feedback_bound
            + upstream_bound
            + stage["future_body_and_receipt_growth"]
        )
        action_measurements.append(
            {
                "after_tool": stage["after_tool"],
                "request_measurement": shape,
                "input_bound": stage_input,
                "output_bound": output,
                "total_bound": stage_input + output,
            }
        )
    action_tokens = sum(stage["total_bound"] for stage in action_measurements)
    initial_work = acquisition_tokens + worker_input + output + action_tokens
    # Future source readback is already present in worker_input. Keep it once
    # in correction history, just as known source receipts measured above.
    correction_growth = max(0, growth - future_bytes)
    correction_input = worker_input + 2 * correction_growth + 4096
    envelope = {
        "version": "review-envelope-v1",
        "max_source_bytes": bounds["max_source_receipt_bytes"],
        "max_artifact_bytes": bounds["max_artifact_bytes"],
        "max_delivery_bytes": bounds["max_delivery_bytes"],
        "max_review_input_tokens": review_input,
        "verdict_tokens": verdict,
        "correction_input_tokens": correction_input,
        "correction_output_tokens": output,
        "correction_calls": correction_calls,
    }
    diagnostics = {
        "node": node["key"],
        "known_source_bytes": known_source_bytes,
        "future_source_bytes_bound": future_bytes,
        "declared_bounds": bounds,
        "raw_packet_bytes": measured["raw_packet_bytes"],
        "growth_bound": growth,
        "raw_packet_bound": measured["raw_packet_bytes"] + growth + 1024 if has_model_review else 0,
        "raw_packet_limit": 65536,
        "review_input_bound": review_input if has_model_review else 0,
        "correction_input_bound": correction_input,
        "worker_input_bound": worker_input,
        "model_context_limit": member["model_context_window"],
        "output_bound": output,
        "allocated_tokens": execution_limit
        if serial_policy
        else min(node["budget_tokens"], member["estimated_tokens"]),
        "input_acquisition_input_bound": acquisition_input,
        "input_acquisition_total_bound": acquisition_tokens,
        "initial_work_tokens": initial_work,
        "action_completion_calls": len(action_measurements),
        "action_completion_total_bound": action_tokens,
        "suggested_tight_bounds": {
            "max_source_receipt_bytes": bounds["max_source_receipt_bytes"],
            "max_artifact_bytes": bounds["max_artifact_bytes"],
            "max_delivery_bytes": bounds["max_delivery_bytes"],
        },
        "native_context_excess_bytes": {
            "review": max(0, review_input + verdict - member["model_context_window"])
            if has_model_review
            else 0,
            "correction": max(0, correction_input + output - member["model_context_window"]),
        },
        "suggestion_note": (
            "These are the current complete declared bounds, not fit-certified replacements. "
            "Changing cumulative token estimates cannot fix a single-request context excess. "
            "Do not raise output bounds or discard planned action evidence by default. "
            "Reassign necessary source responsibility, remove only redundant handoffs and "
            "use concise complete contracts; lower a bound only if all assigned content fits. "
            "Preserve every required source, specialization, artifact and criterion; Core remeasures."
        ),
    }
    if len(graph["nodes"]) >= 2:
        diagnostics["serial_capacity_option"] = {
            "suggested_policy": {
                "version": "serial-capacity-v1",
                "shared_reserve_tokens": (2 * (review_input + verdict) if has_model_review else 0)
                + envelope["correction_calls"] * (correction_input + output)
                + 4096,
            },
            "current_node_initial_baseline_suggestion": initial_work + 4096,
            "required_changes": "If independent worst-case cycle allocations cannot fit, return an explicit serial_capacity_policy object instead of null. Rewire to exactly one success chain (no transitive/extra edges), require only the immediate predecessor delivery and carry all necessary earlier conclusions/source citations forward in that complete delivery. Preserve full role specializations, criteria and required artifacts. Avoid repeating already-carried sources at every downstream node unless needed for independent assessment. Set every node baseline to fund its complete initial input/output, one pool to fund the largest complete contingency, and baseline sum+pool+coordination <= unchanged task ceiling. This is an option requiring fresh plan/team approval, not permission to omit requirements.",
        }
    diagnostic_violations = []
    if has_model_review:
        try:
            envelope = validate_review_envelope(envelope)
        except DomainError as exc:
            if not _diagnostic_only:
                raise CapacityPreflightError(
                    "Complete review/correction bounds exceed original limits: " + node["key"],
                    diagnostics,
                ) from exc
            diagnostic_violations.append("review_envelope_bounds_invalid")
    if (
        max(
            review_input + verdict if has_model_review else 0,
            correction_input + output,
            acquisition_tokens,
            *[stage["total_bound"] for stage in action_measurements],
        )
        > member["model_context_window"]
    ):
        if not _diagnostic_only:
            raise CapacityPreflightError(
                "Complete review/correction exceeds selected model context: " + node["key"],
                diagnostics,
            )
        diagnostic_violations.append("selected_model_context_exceeded")
    # Envelope numbers themselves are part of both native requests. Remeasure
    # the exact final numbers rather than treating one-digit placeholders as
    # actual framing; the existing future-growth policy remains unchanged.
    if envelope != placeholder:
        if _measurement_pass >= 7:
            raise CapacityPreflightError(
                "Native envelope measurement did not converge within eight passes: " + node["key"],
                {**diagnostics, "measurement_iterations": 8},
            )
        return node_review_envelope(
            store,
            connection,
            quest,
            graph,
            node,
            member,
            provider,
            resource_sizing=resource_sizing,
            _measurement_envelope=envelope,
            _measurement_pass=_measurement_pass + 1,
            _diagnostic_only=_diagnostic_only,
        )
    if _diagnostic_only:
        if has_model_review and measured["raw_packet_bytes"] + growth + 1024 > 65536:
            diagnostic_violations.append("raw_review_packet_limit_exceeded")
        return envelope, {
            **diagnostics,
            "violations": diagnostic_violations,
            "native_envelope_fixed_point_iterations": _measurement_pass + 1,
            "scope": "diagnostic_only_no_allocation_or_authority",
        }
    reserve = (
        review_cycle_reserve(envelope)
        if has_model_review
        else envelope["correction_calls"] * (correction_input + output)
    )
    if not has_model_review:
        envelope = {
            key: value
            for key, value in envelope.items()
            if key not in {"max_review_input_tokens", "verdict_tokens"}
        }
        envelope["version"] = "worker-capacity-v1"
    allocated = (
        execution_limit if serial_policy else min(node["budget_tokens"], member["estimated_tokens"])
    )
    if (
        token_budgets_enforced()
        and serial_policy
        and not resource_sizing
        and initial_work > min(node["budget_tokens"], member["estimated_tokens"])
    ):
        diagnostics.update(
            initial_work_tokens=initial_work,
            baseline_tokens=min(node["budget_tokens"], member["estimated_tokens"]),
        )
        raise CapacityPreflightError(
            "Serial baseline cannot fund complete initial work: " + node["key"],
            diagnostics,
        )
    if token_budgets_enforced() and not resource_sizing and initial_work + reserve > allocated:
        diagnostics.update(
            minimum_node_tokens=initial_work + reserve,
            review_correction_reserve=reserve,
        )
        raise CapacityPreflightError(
            f"Node lacks complete Worker/review/correction allowance: {node['key']}; "
            f"minimum={initial_work + reserve}, "
            f"allocated={allocated}; "
            f"worker={worker_input + output}, review/correction={reserve}",
            diagnostics,
        )
    return envelope, {
        "known_receipt_bytes": known_source_bytes,
        "future_input_bytes_bound": future_bytes,
        "worker_input_policy": worker_input,
        "review_cycle_reserve": reserve,
        "initial_worker_request_measurement": worker_shape,
        "native_envelope_fixed_point_iterations": _measurement_pass + 1,
        "feedback_bytes_policy": feedback_bound,
        "upstream_reference_bytes_policy": upstream_bound,
        "minimum_node_tokens": initial_work + reserve,
        "initial_work_tokens": initial_work,
        "initial_model_calls": 1 + bool(acquisition_shape) + len(action_measurements),
        "action_completion_stages": action_measurements,
        "action_completion_total_bound": action_tokens,
        "input_acquisition": {
            "required": acquisition_shape is not None,
            "request_measurement": acquisition_shape,
            "input_bound": acquisition_input,
            "output_bound": output if acquisition_shape else 0,
            "total_bound": acquisition_tokens,
            "policy": "One synthetic declared-input read batch followed by complete source-bearing analysis; not executed calls, receipts or guaranteed model behavior. Additional exploration is charged under unchanged runtime limits.",
        },
        "output_bound": output,
        **({"execution_token_limit": execution_limit} if serial_policy else {}),
        "criterion_mode": "independent_model_review" if has_model_review else "fixed_verification",
        "measurement": "synthetic_policy_envelope_not_execution_or_usage_prediction",
        "minimum_node_tokens_meaning": "Admission requirement under this chosen worst-case two-review/correction policy; not a necessary lower bound on task usage or model tokens.",
    }


def validate_plan_contract(
    store,
    connection,
    quest_id,
    graph,
    *,
    preserved_completed_keys=(),
    composer_capacity_measurer=None,
    composer_provider=None,
    _composition_resource_sizing=False,
):
    from .budget_policy import validate_serial_policy
    from .composition_capacity import validate_binding as validate_composition_binding
    from .graph_domain import success_ancestors
    from .hypothesis_sources import validate_graph as validate_hypothesis_sources
    from .hypothesis_sources import validate_quest_hypotheses
    from .planner_dataflow_v25 import validate_capacity_choices

    current_quest = connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
    if current_quest is None:
        raise DomainError("Quest missing for capacity validation")
    validate_composition_binding(
        store,
        connection,
        current_quest,
        graph,
        composer_provider,
        composer_capacity_measurer,
        resource_sizing=_composition_resource_sizing,
    )

    validate_capacity_choices(graph)
    validate_hypothesis_sources(graph)
    validate_quest_hypotheses(connection, quest_id, graph)

    serial_policy = validate_serial_policy(graph)
    binding = graph.get("execution_constraints")
    limits = validate_execution_constraints(connection, quest_id, binding)
    if len(graph["nodes"]) > limits.get("max_nodes", 128) or (
        token_budgets_enforced()
        and graph["max_total_tokens"] > limits.get("max_total_tokens", 1000000)
    ):
        raise DomainError("Plan exceeds source-bound task execution limits")
    if graph.get("max_duration_seconds") and graph["max_duration_seconds"] > limits.get(
        "max_duration_seconds", 1000000000
    ):
        raise DomainError("Plan duration exceeds source-bound task limit")
    contract = graph.get("capacity_contract")
    if contract is None:
        if serial_policy:
            raise DomainError("Serial capacity requires the verified per-node capacity contract")
        return
    if contract.get("version") != VERSION:
        raise DomainError("Unknown plan capacity contract")
    from .source_wire import approved_source_wire_format

    approved_source_wire_format(graph, contract)
    if "input_acquisition_policy" in contract and contract["input_acquisition_policy"] not in {
        "native-input-roundtrip-v1",
        "core-declared-source-prefetch-v1",
    }:
        raise DomainError("Unknown plan input acquisition protocol")
    if contract.get("input_acquisition_policy") == "core-declared-source-prefetch-v1":
        allowances = contract.get("action_evidence_allowances")
        pending = {node["key"] for node in graph["nodes"]} - set(
            contract.get("preserved_completed_keys", [])
        )
        if (
            not isinstance(allowances, dict)
            or set(allowances) != pending
            or any(
                type(value) is not int or not 0 <= value <= 32768 for value in allowances.values()
            )
        ):
            raise DomainError("V23 requires exact pending-node action evidence allowances")
    if not isinstance(contract.get("input_inventory"), list) or not isinstance(
        contract.get("nodes"), dict
    ):
        raise DomainError("Capacity contract requires verified inventory and per-node envelopes")
    verify_input_inventory(store, contract["input_inventory"])
    known = {item["path"] for item in contract["input_inventory"]}
    preserved = set(contract.get("preserved_completed_keys", []))
    proved_completed = {
        item["node_key"] for item in graph.get("revision_lineage", {}).get("completed_nodes", [])
    }
    if preserved - proved_completed - set(preserved_completed_keys):
        raise DomainError("Preserved capacity nodes require verified completed execution lineage")
    if preserved and not preserved.issubset({node["key"] for node in graph["nodes"]}):
        raise DomainError("Unknown preserved completed capacity node")
    for node in graph["nodes"]:
        if node["key"] in preserved:
            continue
        if node.get("required_input_paths") is None or not node.get("capacity_bounds"):
            raise DomainError("New capacity contract requires explicit node paths and byte bounds")
        if contract.get("input_acquisition_policy") == "core-declared-source-prefetch-v1":
            from .source_capacity import (
                SOURCE_METADATA_RESERVE_BYTES,
                measure_required_sources,
            )

            sources = measure_required_sources(store, graph, node, contract["input_inventory"])
            expected_source_bound = (
                sources.required_source_receipt_bytes
                + SOURCE_METADATA_RESERVE_BYTES
                + allowances[node["key"]]
            )
            if node["capacity_bounds"]["max_source_receipt_bytes"] != expected_source_bound:
                raise DomainError("V23 action evidence sizing differs from complete source bound")
        parents = {edge["source"] for edge in graph["edges"] if edge["target"] == node["key"]}
        delivery_parents = success_ancestors(graph["edges"], node["key"])
        deliveries = node.get("required_predecessor_deliveries") or []
        if deliveries and (
            len(deliveries) > 8
            or len(deliveries) != len(set(deliveries))
            or set(deliveries) - delivery_parents
            or "worker_read_completed_delivery" not in node["allowed_tools"]
        ):
            raise DomainError("Completed delivery requires declared approved success ancestors")
        future = {
            other.get("expected_artifact") for other in graph["nodes"] if other["key"] in parents
        }
        if set(node["required_input_paths"]) - known - future:
            raise DomainError("Required input has no verified source or bounded predecessor")
    reserve = graph.get("coordination_reserve_tokens")
    if token_budgets_enforced() and (
        not reserve
        or sum(node["budget_tokens"] for node in graph["nodes"])
        + reserve
        + (serial_policy["shared_reserve_tokens"] if serial_policy else 0)
        > graph["max_total_tokens"]
    ):
        raise DomainError("Plan must retain its explicit coordination reserve")
