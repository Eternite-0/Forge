"""Production Composer semantics bound to previously approved Core resources.

This adapter proposes no permissions, changes no plan, and performs no model
selection. TeamCompositionService must still validate the resulting proposal,
remeasure the selected Provider request, and require normal user approval.
Legacy TeamWire and manual proposal callers retain their original allocations.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .budget_policy import validate_serial_policy
from .domain import DomainError
from .hierarchical_plans import node_depth
from .team_composition import TeamCapabilityWire, TeamWire
from .token_policy import token_budgets_enforced


class InvalidTeamProposal(DomainError):
    """Two settled production Composer responses failed deterministic admission."""


def uses_core_composer_resources(graph):
    contract = graph.get("capacity_contract") or {}
    return (
        contract.get("version") == "verified-capacity-v1"
        and (contract.get("resource_allocation") or {}).get("mode") == "core_measured"
        and bool(contract.get("nodes"))
        and all(
            type(item.get("output_bound")) is int and item["output_bound"] > 0
            for item in contract["nodes"].values()
        )
    )


class SemanticTeamRoleWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=100)
    task_node_key: str = Field(min_length=1, max_length=100)
    required_capabilities: list[TeamCapabilityWire] = Field(max_length=32)
    tools: list[str] = Field(max_length=32)
    dynamic: bool
    responsibility: str = Field(min_length=1, max_length=2000)
    input_contract: list[str] = Field(max_length=20)
    output_schema_json: str | None = Field(
        description="Explicit null selects the supplied Core strict eight-field delivery schema."
    )
    acceptance_criteria: list[str] = Field(max_length=20)


class SemanticTeamWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    roles: list[SemanticTeamRoleWire] = Field(min_length=1, max_length=16)
    strategy: Literal["single_model", "greedy", "coverage", "complementarity"]

    def inputs(self, graph, *, operator_max_concurrency):
        """Bind exact approved output/baseline; normal service admission follows.

        Source limits remain ceilings. Steps/tools use bounded Core defaults when
        no source limit exists; duration is divided within the approved total.
        The shared serial reserve appears once in the team limit and never in
        role allocations. Execution limits remain the service's responsibility.
        """
        if not uses_core_composer_resources(graph):
            raise DomainError("Core Composer requires approved measured output bounds")
        if type(operator_max_concurrency) is not int or operator_max_concurrency < 1:
            raise DomainError("Core Composer requires an operator concurrency ceiling")
        nodes = {node["key"]: node for node in graph["nodes"]}
        keys = [role.task_node_key for role in self.roles]
        if len(set(keys)) != len(keys) or set(keys) - set(nodes):
            raise DomainError("Each approved task node requires exactly one team member")
        maximum = (graph.get("execution_constraints") or {}).get("limits", {})
        count = len(self.roles)
        if count > min(16, maximum.get("max_members", 16)):
            raise DomainError("Team max_members exceeds source-bound task limit")
        serial = validate_serial_policy(graph)
        concurrency = (
            1
            if serial
            else min(count, operator_max_concurrency, maximum.get("max_concurrency", count))
        )
        duration = min(
            graph.get("max_duration_seconds") or 120 * count,
            maximum.get("max_duration_seconds", 120 * count),
            3600 * count,
        )
        if type(duration) is not int or duration < count:
            raise DomainError("Approved duration cannot fund every team member")
        per_role_duration, extra = divmod(duration, count)
        duration_by_key = {
            key: per_role_duration + (index < extra) for index, key in enumerate(sorted(keys))
        }
        steps = min(32, maximum.get("max_worker_steps", 16))
        calls = min(64, maximum.get("max_worker_tool_calls", 16))
        roles = []
        observations = graph["capacity_contract"]["nodes"]
        for wire in self.roles:
            node = nodes[wire.task_node_key]
            output = observations.get(wire.task_node_key, {}).get("output_bound")
            baseline = node["budget_tokens"]
            if type(output) is not int or not 0 < output < baseline:
                raise DomainError("Approved measured output cannot fit its node baseline")
            roles.append(
                {
                    **wire.model_dump(),
                    "input_tokens": baseline - output,
                    "output_tokens": output,
                    "max_steps": steps,
                    "max_tool_calls": calls if wire.tools else 0,
                    "max_duration_seconds": duration_by_key[wire.task_node_key],
                    "depth": node_depth(graph, wire.task_node_key),
                }
            )
        allocation = sum(nodes[key]["budget_tokens"] for key in keys) + (
            serial["shared_reserve_tokens"] if serial else 0
        )
        if token_budgets_enforced() and allocation > min(
            graph["max_total_tokens"] - (graph.get("coordination_reserve_tokens") or 0),
            maximum.get("max_total_tokens", graph["max_total_tokens"]),
        ):
            raise DomainError("Core team resources exceed the original approved ceiling")
        tools = sorted({tool for role in self.roles for tool in role.tools})
        limits = {
            "max_members": count,
            "max_depth": max(role["depth"] for role in roles),
            "max_concurrency": concurrency,
            "max_total_tokens": allocation,
            "max_total_tool_calls": sum(role["max_tool_calls"] for role in roles),
            "max_cost_microunits": graph.get("max_cost_microunits"),
            "currency": graph.get("cost_currency"),
            "allowed_tools": tools,
        }
        # The unchanged legacy parser supplies all established role validators,
        # including explicit-null strict schema selection for dynamic roles.
        return TeamWire.model_validate(
            {"roles": roles, "limits": limits, "strategy": self.strategy}
        ).inputs()


_DIAGNOSTIC_FIELDS = frozenset(
    {
        "roles",
        "id",
        "task_node_key",
        "required_capabilities",
        "name",
        "minimum",
        "tools",
        "dynamic",
        "responsibility",
        "input_contract",
        "output_schema_json",
        "acceptance_criteria",
        "strategy",
        "limits",
        "input_tokens",
        "output_tokens",
        "max_steps",
        "max_tool_calls",
        "max_duration_seconds",
        "depth",
        "max_members",
        "max_depth",
        "max_concurrency",
        "max_total_tokens",
        "max_total_tool_calls",
        "max_cost_microunits",
        "currency",
        "allowed_tools",
    }
)
_CAPACITY_NUMBERS = frozenset(
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
        "minimum_node_tokens",
        "review_correction_reserve",
        "initial_work_tokens",
        "baseline_tokens",
    }
)

# Match only fixed implementation strings. Never persist exception text, role
# names, model output, raw schemas or arbitrary provider errors.
_REJECTION_REASONS = {
    "Each approved task node requires exactly one team member": "task_node_coverage_mismatch",
    "Invalid role count or duplicate roles": "role_count_or_duplicate_ids",
    "Duplicate capability": "duplicate_capability",
    "Role exceeds approved task tool scope": "role_tool_scope_exceeded",
    "Role weakens approved task acceptance criteria": "role_criteria_weakened",
    "Role weakens approved task capability requirements": "role_capability_minimum_weakened",
    "Team tool scope must equal its member scopes": "team_tool_scope_mismatch",
    "Team token budget exceeds plan": "team_plan_token_limit_exceeded",
    "Team token budget exceeds task budget": "role_node_token_limit_exceeded",
    "Team duration budget exceeds plan": "team_plan_duration_exceeded",
    "Team concurrency exceeds operator limit": "operator_concurrency_exceeded",
    "Team max_members exceeds source-bound task limit": "source_member_limit_exceeded",
    "Worker steps/tools exceed source-bound task limit": "source_worker_action_limit_exceeded",
    "Team duration exceeds source-bound task limit": "source_duration_limit_exceeded",
    "Approved duration cannot fund every team member": "approved_duration_insufficient",
    "Approved measured output cannot fit its node baseline": "approved_output_baseline_invalid",
    "Core team resources exceed the original approved ceiling": "approved_team_ceiling_exceeded",
    "Serial team allocation must equal each approved baseline": "serial_baseline_mismatch",
    "Serial team ceiling must include its baselines and shared reserve": "serial_reserve_missing",
    "Token budget exceeded": "composition_token_limit_exceeded",
    "Tool call budget exceeded": "composition_tool_limit_exceeded",
    "No team satisfies the combined budget": "no_team_fits_combined_budget",
    "Invalid tool scope": "invalid_role_tool_scope",
    "Invalid capability requirements": "invalid_capability_requirements",
    "Invalid role contract": "role_contract_item_blank_or_too_long",
    "Dynamic role requires a complete contract": "dynamic_role_contract_incomplete",
    "Output schema exceeds size limit": "output_schema_size_exceeded",
    "Invalid output schema": "invalid_output_schema",
    "Output object must be bounded and closed": "output_schema_requires_closed_bounds",
    "Output arrays require a bounded maxItems": "output_array_bound_missing",
    "Unsupported output schema keyword": "unsupported_output_schema_keyword",
    "Unsupported output schema type": "unsupported_output_schema_type",
    "Quest version conflict": "quest_version_conflict",
    "Composer event lease expired": "composer_lease_expired",
    "Composer event lease is unavailable": "composer_lease_unavailable",
    "Team plus coordination reserve exceeds current residual budget": "team_residual_budget_insufficient",
    "Approved team would exceed current residual budget": "approved_team_residual_budget_insufficient",
    "Serial capacity team requires concurrency one and token-only limits": "serial_team_limits_invalid",
    "Team max_depth exceeds source-bound task limit": "source_depth_limit_exceeded",
    "Team max_concurrency exceeds source-bound task limit": "source_concurrency_limit_exceeded",
    "Team max_total_tokens exceeds source-bound task limit": "source_token_limit_exceeded",
}


def _safe_rejection_reason(exc):
    message = str(exc)
    if message in _REJECTION_REASONS:
        return _REJECTION_REASONS[message]
    if message.startswith("No eligible model for at least one role: "):
        return "no_eligible_model_for_role"
    if message.startswith("Unknown role requires a dynamic proposal; preset IDs are "):
        return "unknown_role_requires_dynamic_contract"
    return "unclassified_rejection"


def _safe_role_validation(errors):
    """Identify bounded known validator fields, without retaining their values."""
    result = []
    for error in errors[:8]:
        code = _safe_rejection_reason(error.get("ctx", {}).get("error", ""))
        value = error.get("input")
        if not isinstance(value, dict):
            continue
        if code == "role_contract_item_blank_or_too_long":
            for field in ("input_contract", "acceptance_criteria"):
                items = value.get(field)
                if not isinstance(items, list):
                    continue
                for index, item in enumerate(items[:20]):
                    if isinstance(item, str) and (not item.strip() or len(item) > 1000):
                        result.append({"field": field, "index": index, "reason": code})
        elif code == "unknown_role_requires_dynamic_contract":
            result.append({"field": "id", "reason": code})
        elif code == "dynamic_role_contract_incomplete":
            for field in (
                "responsibility",
                "input_contract",
                "output_schema",
                "acceptance_criteria",
            ):
                if not value.get(field):
                    result.append({"field": field, "reason": code})
    return result[:8]


def safe_team_rejection_diagnostics(exc=None):
    """No model text, input values, exception messages, or raw schemas are stored."""
    result = {"kind": "incomplete_team_proposal" if exc is None else "invalid_team_proposal"}
    if exc is not None:
        result["reason"] = _safe_rejection_reason(exc)
    errors = getattr(exc, "errors", None)
    if callable(errors):
        validation_errors = errors()[:8]
        known_reasons = {
            _safe_rejection_reason(error.get("ctx", {}).get("error", ""))
            for error in validation_errors
        } - {"unclassified_rejection"}
        if known_reasons:
            result["validation_reasons"] = sorted(known_reasons)
        role_validation = _safe_role_validation(validation_errors)
        if role_validation:
            result["role_validation"] = role_validation
        result["fields"] = [
            [
                part
                if type(part) is int and 0 <= part <= 127
                else part
                if isinstance(part, str) and part in _DIAGNOSTIC_FIELDS
                else "unrecognized_field"
                for part in error.get("loc", ())[:8]
            ]
            for error in validation_errors
        ]
    capacity = getattr(exc, "capacity_diagnostics", None)
    if isinstance(capacity, dict):
        result["capacity"] = {
            key: value
            for key, value in capacity.items()
            if key in _CAPACITY_NUMBERS and type(value) is int and 0 <= value <= 1000000000
        }
    return result
