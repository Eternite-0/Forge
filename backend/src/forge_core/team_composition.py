"""Bounded, explainable team proposals; never starts a Worker or grants a tool."""

import hashlib
import json
import math
import time
from copy import deepcopy
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .domain import DomainError, ForgeEvent, ModelProfile, new_id, now_iso
from .token_policy import token_budgets_enforced
from .worker_schema import DELIVERY_OUTPUT_SCHEMA, STRICT_DELIVERY_OUTPUT_SCHEMA


class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")
    id: str = Field(min_length=1, max_length=100)
    task_node_key: str | None = Field(default=None, min_length=1, max_length=100)
    required_capabilities: dict[str, float] = Field(default_factory=dict, max_length=32)
    tools: list[str] = Field(default_factory=list, max_length=32)
    input_tokens: int = Field(ge=0, le=1000000)
    output_tokens: int = Field(gt=0, le=1000000)
    max_steps: int = Field(default=8, ge=1, le=32)
    max_tool_calls: int = Field(default=8, ge=0, le=64)
    max_duration_seconds: int = Field(default=60, ge=1, le=3600)
    depth: int = Field(default=0, ge=0, le=8)
    dynamic: bool = False
    responsibility: str | None = Field(default=None, min_length=1, max_length=2000)
    input_contract: list[str] = Field(default_factory=list, max_length=20)
    output_schema: dict | None = None
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_requirements(self):
        if any(
            not name or not math.isfinite(value) or not 0 <= value <= 100
            for name, value in self.required_capabilities.items()
        ):
            raise ValueError("Invalid capability requirements")
        if any(not tool for tool in self.tools) or len(set(self.tools)) != len(self.tools):
            raise ValueError("Invalid tool scope")
        if self.id not in PRESET_ROLES and not self.dynamic:
            raise ValueError(
                "Unknown role requires a dynamic proposal; preset IDs are "
                + ", ".join(PRESET_ROLES)
            )
        if any(
            not text.strip() or len(text) > 1000
            for text in [*self.input_contract, *self.acceptance_criteria]
        ):
            raise ValueError("Invalid role contract")
        if self.dynamic and (
            not self.responsibility
            or not self.responsibility.strip()
            or not self.input_contract
            or not self.output_schema
            or not self.acceptance_criteria
        ):
            raise ValueError("Dynamic role requires a complete contract")
        if self.output_schema is not None:
            _validate_output_schema(self.output_schema)
        return self


class CompositionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")
    max_members: int = Field(ge=1, le=16)
    max_depth: int = Field(ge=0, le=8)
    max_concurrency: int = Field(ge=1, le=16)
    max_total_tokens: int = Field(gt=0, le=16000000)
    max_total_tool_calls: int = Field(default=64, ge=0, le=1024)
    max_cost_microunits: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    allowed_tools: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_limits(self):
        if self.max_concurrency > self.max_members:
            raise ValueError("Concurrency exceeds member limit")
        if self.max_cost_microunits is not None and self.currency is None:
            raise ValueError("A monetary cap requires an explicit currency")
        if any(not tool for tool in self.allowed_tools) or len(set(self.allowed_tools)) != len(
            self.allowed_tools
        ):
            raise ValueError("Invalid allowed tools")
        return self


PRESET_ROLES = {
    "research": {"research": 70},
    "math": {"mathematics": 70},
    "planner": {"planning": 70},
    "architect": {"architecture": 70},
    "coding": {"coding": 70},
    "design": {"design": 70},
    "experiment": {"coding": 60, "mathematics": 60},
    "reviewer": {"instruction_following": 70},
}

PRESET_RESPONSIBILITIES = {
    "research": "Gather and synthesize relevant evidence.",
    "math": "Develop and check quantitative reasoning.",
    "planner": "Break down objectives and identify dependencies.",
    "architect": "Design system structure and interfaces.",
    "coding": "Implement and inspect the assigned change.",
    "design": "Develop visual and interaction concepts.",
    "experiment": "Design and evaluate reproducible experiments.",
    "reviewer": "Independently examine results and evidence.",
}
# Phase 2 delivery contract (Summary/Findings/Decisions/Artifacts/Evidence/
# Unresolved/Confidence/Next actions). Kept in one place so the Worker prompt,
# the default role contract and the reviewer all describe the same structure.
DEFAULT_OUTPUT_SCHEMA = DELIVERY_OUTPUT_SCHEMA


def _validate_output_schema(schema):
    if len(json.dumps(schema, allow_nan=False).encode("utf-8")) > 8192:
        raise ValueError("Output schema exceeds size limit")

    def visit(node, depth):
        if not isinstance(node, dict) or depth > 8:
            raise ValueError("Invalid output schema")
        kind = node.get("type")
        if kind == "object":
            if set(node) - {"type", "properties", "required", "additionalProperties"}:
                raise ValueError("Unsupported output schema keyword")
            properties = node.get("properties")
            if (
                not isinstance(properties, dict)
                or len(properties) > 32
                or node.get("additionalProperties") is not False
            ):
                raise ValueError("Output object must be bounded and closed")
            required = node.get("required")
            if (
                not isinstance(required, list)
                or len(set(required)) != len(required)
                or not set(required) <= set(properties)
                or not required
            ):
                raise ValueError("Required output properties must be a subset of the schema")
            # An optional property is only allowed with an explicit bounded default, so a
            # missing value is never ambiguous and never invented by the runtime.
            for name, child in properties.items():
                if name not in set(required) and "default" not in child:
                    raise ValueError("Optional output property requires an explicit default")
            for child in properties.values():
                visit(child, depth + 1)
        elif kind == "array":
            if (
                set(node) - {"type", "items", "maxItems", "default"}
                or type(node.get("maxItems")) is not int
                or not 0 <= node["maxItems"] <= 100
            ):
                raise ValueError("Output arrays require a bounded maxItems")
            visit(node.get("items"), depth + 1)
        elif kind in {"string", "number", "integer", "boolean", "null"}:
            allowed = {"type", "default"}
            if kind in {"number", "integer"}:
                allowed |= {"minimum", "maximum"}
                for bound in ("minimum", "maximum"):
                    if bound in node and (
                        type(node[bound]) not in (int, float) or not math.isfinite(node[bound])
                    ):
                        raise ValueError("Output numeric bounds must be finite numbers")
                if "minimum" in node and "maximum" in node and node["minimum"] > node["maximum"]:
                    raise ValueError("Output numeric bounds are inverted")
            if set(node) - allowed or "type" not in node:
                raise ValueError("Unsupported scalar schema keyword")
        else:
            raise ValueError("Unsupported output schema type")

    if schema.get("type") != "object":
        raise ValueError("Output schema root must be an object")
    visit(schema, 0)


class TeamCapabilityWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    minimum: float


class TeamRoleWire(BaseModel):
    # Strict Provider schemas require nullable fields to be required. The local
    # parser still accepts omission for compatibility with existing responses.
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra=lambda schema: schema.update(required=list(schema["properties"])),
    )
    id: str
    task_node_key: str
    required_capabilities: list[TeamCapabilityWire]
    tools: list[str]
    input_tokens: int
    output_tokens: int
    max_steps: int
    max_tool_calls: int
    max_duration_seconds: int
    depth: int
    dynamic: bool
    responsibility: str
    input_contract: list[str]
    # Explicit null is a model-selected contract, including for dynamic roles.
    # Local omission retains preset compatibility; it is not a dynamic contract.
    output_schema_json: str | None = Field(
        default=None,
        description=(
            "Explicit null selects the supplied Core schema requiring all eight delivery fields "
            "and confidence between 0 and 1 for this role, "
            "including a dynamic role. A dynamic role must still provide nonempty responsibility, "
            "input_contract and acceptance_criteria. A string must contain a valid bounded JSON schema."
        ),
    )
    acceptance_criteria: list[str]


class TeamLimitsWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_members: int
    max_depth: int
    max_concurrency: int
    max_total_tokens: int
    max_total_tool_calls: int
    max_cost_microunits: int | None
    currency: str | None
    allowed_tools: list[str]


class TeamWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    roles: list[TeamRoleWire]
    limits: TeamLimitsWire
    strategy: Literal["single_model", "greedy", "coverage", "complementarity"]

    def inputs(self):
        roles = []
        for wire in self.roles:
            data = wire.model_dump()
            capabilities = data.pop("required_capabilities")
            if len({item["name"] for item in capabilities}) != len(capabilities):
                raise ValueError("Duplicate capability")
            data["required_capabilities"] = {item["name"]: item["minimum"] for item in capabilities}
            raw_schema = data.pop("output_schema_json")
            if raw_schema is None and "output_schema_json" in wire.model_fields_set:
                data["output_schema"] = deepcopy(STRICT_DELIVERY_OUTPUT_SCHEMA)
            elif raw_schema is None or not raw_schema.strip():
                data["output_schema"] = None
            else:
                if len(raw_schema.encode("utf-8")) > 8192:
                    raise ValueError("Output schema exceeds size limit")
                data["output_schema"] = json.loads(raw_schema)
            roles.append(RoleSpec.model_validate(data))
        return (
            roles,
            CompositionLimits.model_validate(self.limits.model_dump()),
            self.strategy,
        )


def _cost(profile, role, currency):
    if (
        profile.input_cost_per_million is None
        or profile.output_cost_per_million is None
        or profile.currency is None
        or (currency is not None and profile.currency != currency)
    ):
        return None
    amount = (
        Decimal(str(profile.input_cost_per_million)) * role.input_tokens
        + Decimal(str(profile.output_cost_per_million)) * role.output_tokens
    )
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


class TeamComposerEngine:
    """Deterministic baselines; beam width bounds combinatorial exploration."""

    def __init__(self, *, confidence_weight=5.0, coordination_weight=1.0, online_weight=1.0):
        if any(
            not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 10
            for value in (confidence_weight, coordination_weight, online_weight)
        ):
            raise ValueError("Invalid scoring weight")
        self.confidence_weight = float(confidence_weight)
        self.coordination_weight = float(coordination_weight)
        self.online_weight = float(online_weight)

    @staticmethod
    def _rejection(role, model, limits):
        if not model.enabled or model.quarantine_reason or not model.model_id:
            return "disabled_quarantined_or_missing_identity"
        if role.depth > limits.max_depth or not set(role.tools).issubset(limits.allowed_tools):
            return "role_scope_or_depth"
        if (
            model.context_window is None
            or model.max_output_tokens is None
            # estimated_tokens is the cumulative Worker/review allowance. Each
            # request independently fits the model window in WorkerManager.
            or model.context_window <= role.output_tokens
            or model.max_output_tokens < role.output_tokens
        ):
            return "unknown_or_insufficient_context"
        if role.tools and (
            model.tool_calling_support is None or not model.tool_calling_support.supported
        ):
            return "unknown_or_unsupported_tools"
        measures = model.capability_measurements
        if any(
            name not in measures or measures[name].value < threshold
            for name, threshold in role.required_capabilities.items()
        ):
            return "unknown_or_insufficient_capability"
        if limits.max_cost_microunits is not None and _cost(model, role, limits.currency) is None:
            return "unknown_price_or_currency_mismatch"
        return None

    def _candidate(self, role, model, limits, online=None):
        if online is not None and (
            online.get("execution_mode") != "live"
            or online.get("source") != "core_execution_observation"
            or online.get("provider_id") != model.provider
            or online.get("model_id") != model.model_id
        ):
            raise ValueError("Online performance provenance does not match model")
        measures = model.capability_measurements
        cost = _cost(model, role, limits.currency)
        fit = sum(measures[name].value for name in role.required_capabilities) / max(
            1, len(role.required_capabilities)
        )
        reliability_measure = model.performance_measurements.get("reliability")
        speed_measure = model.performance_measurements.get("tokens_per_second")
        reliability = (
            reliability_measure.value if reliability_measure is not None else model.reliability
        )
        speed = speed_measure.value if speed_measure is not None else model.tokens_per_second
        # Scores guide ordering only; unknown fields remain unknown in outputs.
        utility = (
            fit
            + (10 * reliability if reliability is not None else 0)
            + (min(speed, 100) / 20 if speed is not None else 0)
        )
        confidences = [
            measures[name].confidence
            for name in role.required_capabilities
            if measures[name].confidence is not None
        ]
        if confidences:
            utility += self.confidence_weight * sum(confidences) / len(confidences)
        if cost is not None:
            utility -= min(cost / 1000000, 10)
        online_adjustments = {}
        for name, weight in {
            "transport_success_rate": 5,
            "task_success_rate": 10,
            "tool_call_success_rate": 3 if role.tools else 0,
            "schema_compliance": 3,
            "review_pass_rate": 5,
        }.items():
            measurement = (online or {}).get("measurements", {}).get(name)
            # Five is an admission floor, not a statistical sufficiency claim.
            # A single successful request cannot become an ability score.
            if measurement and measurement["n_samples"] >= 5:
                online_adjustments[name] = weight * (measurement["confidence"]["lower"] - 0.5)
        latency = (online or {}).get("measurements", {}).get("p95_latency_seconds")
        if latency and latency["n_samples"] >= 5:
            online_adjustments["latency_time_budget"] = -2 * min(
                latency["value"] / role.max_duration_seconds, 1
            )
        utility += self.online_weight * sum(online_adjustments.values())
        return {
            "role_id": role.id,
            "task_node_key": role.task_node_key,
            "responsibility": role.responsibility or PRESET_RESPONSIBILITIES.get(role.id),
            "input_contract": role.input_contract,
            "output_schema": role.output_schema,
            "acceptance_criteria": role.acceptance_criteria,
            "profile_id": model.id,
            "provider_id": model.provider,
            "model_id": model.model_id,
            "tool_calling_support": model.tool_calling_support.model_dump()
            if model.tool_calling_support
            else None,
            "capability_sources": {
                **{name: measures[name].source for name in role.required_capabilities},
                **(
                    {"tools": model.tool_calling_support.source}
                    if model.tool_calling_support and model.tool_calling_support.supported
                    else {}
                ),
            },
            "capabilities": {name: measurement.value for name, measurement in measures.items()},
            "capability_confidence": {
                name: measures[name].confidence for name in role.required_capabilities
            },
            "tool_scope": sorted(role.tools),
            "depth": role.depth,
            "dynamic": role.dynamic,
            "estimated_tokens": role.input_tokens + role.output_tokens,
            "output_tokens": role.output_tokens,
            "model_max_output_tokens": model.max_output_tokens,
            "model_context_window": model.context_window,
            "max_steps": role.max_steps,
            "max_tool_calls": role.max_tool_calls,
            "max_duration_seconds": role.max_duration_seconds,
            "estimated_cost_microunits": cost,
            "currency": model.currency if cost is not None else None,
            "reliability": reliability,
            "tokens_per_second": speed,
            "performance_sources": {
                "reliability": reliability_measure.source if reliability_measure else None,
                "tokens_per_second": speed_measure.source if speed_measure else None,
            },
            "online_performance": online,
            "online_utility_adjustments": online_adjustments,
            "utility": utility,
            "reasons": [
                "hard requirements satisfied",
                "unknown metrics remain unknown",
            ],
        }

    def compose(
        self,
        roles,
        profiles,
        limits,
        *,
        strategy: Literal["single_model", "greedy", "coverage", "complementarity"],
        online_snapshots=None,
    ):
        roles = [RoleSpec.model_validate(item) for item in roles]
        profiles = [
            model if model.quarantine_reason else ModelProfile.model_validate(model.model_dump())
            for model in profiles
        ]
        limits = CompositionLimits.model_validate(limits)
        if strategy not in {"single_model", "greedy", "coverage", "complementarity"}:
            raise ValueError("Unknown composition strategy")
        if (
            not roles
            or len(roles) > limits.max_members
            or len({role.id for role in roles}) != len(roles)
        ):
            raise ValueError("Invalid role count or duplicate roles")
        if (
            token_budgets_enforced()
            and sum(role.input_tokens + role.output_tokens for role in roles)
            > limits.max_total_tokens
        ):
            raise ValueError("Token budget exceeded")
        if sum(role.max_tool_calls for role in roles) > limits.max_total_tool_calls:
            raise ValueError("Tool call budget exceeded")
        if len(profiles) > 100 or len({model.id for model in profiles}) != len(profiles):
            raise ValueError("Duplicate model profile IDs")
        candidates = []
        audit = {}
        for role in roles:
            eligible, rejected = [], []
            for model in profiles:
                reason = self._rejection(role, model, limits)
                if reason:
                    rejected.append({"profile_id": model.id, "reason": reason})
                else:
                    eligible.append(
                        self._candidate(role, model, limits, (online_snapshots or {}).get(model.id))
                    )
            candidates.append(eligible)
            ranked = sorted(eligible, key=lambda item: (-item["utility"], item["profile_id"]))
            audit[role.id] = {
                "eligible": [
                    {"profile_id": item["profile_id"], "utility": item["utility"]}
                    for item in ranked[:10]
                ],
                "rejected": sorted(rejected, key=lambda item: str(item["profile_id"]))[:10],
                "eligible_omitted": max(0, len(eligible) - 10),
                "rejected_omitted": max(0, len(rejected) - 10),
            }
        if any(not entries for entries in candidates):
            reasons = [
                f"role[{index}]:"
                + ",".join(sorted({item["reason"] for item in audit[role.id]["rejected"]}))
                for index, (role, entries) in enumerate(zip(roles, candidates, strict=True))
                if not entries
            ]
            raise ValueError(
                "No eligible model for at least one role: " + "; ".join(reasons)[:1000]
            )

        def score(members):
            utility = sum(member["utility"] for member in members)
            required = {name for role in roles for name in role.required_capabilities}
            coverage = {
                name for member in members for name in member["capabilities"] if name in required
            }
            best = {
                name: max(member["capabilities"].get(name, 0) for member in members)
                for name in coverage
            }
            profile_handoffs = max(0, len({member["profile_id"] for member in members}) - 1)
            provider_handoffs = max(0, len({member["provider_id"] for member in members}) - 1)
            if strategy == "coverage":
                return sum(best.values()) + utility * 0.01
            if strategy == "complementarity":
                return (
                    utility
                    + sum(best.values()) * 0.25
                    - self.coordination_weight * (profile_handoffs * 5 + provider_handoffs * 3)
                )
            return utility

        beam = [[]]
        for entries in candidates:
            options = []
            for partial in beam:
                for entry in entries:
                    if (
                        strategy == "single_model"
                        and partial
                        and entry["profile_id"] != partial[0]["profile_id"]
                    ):
                        continue
                    members = [*partial, entry]
                    if (
                        limits.max_cost_microunits is not None
                        and sum(m["estimated_cost_microunits"] for m in members)
                        > limits.max_cost_microunits
                    ):
                        continue
                    options.append(members)
            if not options:
                raise ValueError("No team satisfies the combined budget")
            options.sort(
                key=lambda group: (
                    -score(group),
                    tuple(member["profile_id"] for member in group),
                )
            )
            beam = options[: 1 if strategy == "greedy" else 64]
        chosen = beam[0]
        for member in chosen:
            audit[member["role_id"]]["selected"] = member["profile_id"]
            audit[member["role_id"]]["selection_reason"] = (
                f"selected by bounded {strategy} search under hard constraints"
            )
        costs = [member["estimated_cost_microunits"] for member in chosen]
        currencies = {member["currency"] for member in chosen if member["currency"] is not None}
        known_cost = (
            None if any(value is None for value in costs) or len(currencies) != 1 else sum(costs)
        )
        return {
            "strategy": strategy,
            "scoring_weights": {
                "confidence": self.confidence_weight,
                "coordination": self.coordination_weight,
                "online_performance": self.online_weight,
            },
            "members": chosen,
            "limits": limits.model_dump(),
            "estimated_tokens": sum(member["estimated_tokens"] for member in chosen),
            "estimated_cost_microunits": known_cost,
            "currency": next(iter(currencies)) if known_cost is not None else None,
            "score": score(chosen),
            "candidate_audit": audit,
            "candidate_counts": {
                role.id: len(entries) for role, entries in zip(roles, candidates, strict=True)
            },
        }


def _canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class TeamCompositionService:
    """Persist a plan-bound proposal; user approval never grants tool permissions."""

    def __init__(
        self,
        store,
        *,
        engine=None,
        operator_max_concurrency=None,
        capacity_providers=None,
        budget_ledger=None,
        composer_capacity_measurer=None,
    ):
        if operator_max_concurrency is not None and (
            type(operator_max_concurrency) is not int or operator_max_concurrency < 1
        ):
            raise ValueError("Operator max concurrency must be a positive integer")
        self.store = store
        self.engine = engine or TeamComposerEngine()
        self.operator_max_concurrency = operator_max_concurrency
        self.capacity_providers = capacity_providers
        self.budget_ledger = budget_ledger
        self.composer_capacity_measurer = composer_capacity_measurer

    @staticmethod
    def _check_task_limits(connection, quest_id, graph, limits, roles):
        from .constraints import validate_execution_constraints

        binding = graph.get("execution_constraints")
        if binding is None:
            return
        maximum = validate_execution_constraints(connection, quest_id, binding)
        for field in (
            "max_members",
            "max_concurrency",
            "max_depth",
            "max_total_tokens",
        ):
            if field == "max_total_tokens" and not token_budgets_enforced():
                continue
            if getattr(limits, field) > maximum.get(field, 16000000):
                raise DomainError("Team " + field + " exceeds source-bound task limit")
        if any(
            role.max_steps > maximum.get("max_worker_steps", 32)
            or role.max_tool_calls > maximum.get("max_worker_tool_calls", 64)
            for role in roles
        ):
            raise DomainError("Worker steps/tools exceed source-bound task limit")
        if sum(role.max_duration_seconds for role in roles) > maximum.get(
            "max_duration_seconds", 1000000000
        ):
            raise DomainError("Team duration exceeds source-bound task limit")

    def _check_operator_concurrency(self, limits):
        if (
            self.operator_max_concurrency is not None
            and limits.max_concurrency > self.operator_max_concurrency
        ):
            raise DomainError("Team concurrency exceeds operator limit")

    @staticmethod
    def _current_plan(
        connection,
        quest_id,
        plan_revision,
        plan_digest,
        *,
        allow_running=False,
        historical=False,
        lineage_evidence=False,
    ):
        quest = connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
        plan = connection.execute(
            "SELECT * FROM quest_plans WHERE quest_id=? AND revision=?",
            (quest_id, plan_revision),
        ).fetchone()
        if (
            quest is None
            or plan is None
            or (quest["plan_revision"] != plan_revision and not lineage_evidence)
            or quest["status"]
            not in (
                (
                    {"approved", "queued", "running", "completed", "reviewing"}
                    if lineage_evidence
                    else {"approved", "queued", "running", "completed"}
                )
                if historical
                else (
                    {"approved", "queued", "running"} if allow_running else {"approved", "queued"}
                )
            )
            or (
                plan["status"] != "approved"
                and not (lineage_evidence and plan["status"] == "superseded")
            )
            or plan["digest"] != plan_digest
        ):
            raise DomainError("Approved current plan required")
        return quest, plan

    def propose(
        self,
        quest_id,
        roles,
        limits,
        *,
        strategy,
        expected_version,
        source_event_id=None,
        source_event_token=None,
        reassignment_run_id=None,
        reassignment_reason=None,
        composition_cycle_id=None,
    ):
        limits = CompositionLimits.model_validate(limits)
        self._check_operator_concurrency(limits)
        roles = [RoleSpec.model_validate(role) for role in roles]
        if (source_event_id is None) != (source_event_token is None):
            raise DomainError("A composer event requires its lease token")
        with self.store.unit_of_work() as work:
            quest = work.get_quest(quest_id)
            if quest is None:
                raise KeyError(quest_id)
            if quest["version"] != expected_version:
                raise DomainError("Quest version conflict")
            plan = work.connection.execute(
                "SELECT * FROM quest_plans WHERE quest_id=? AND revision=?",
                (quest_id, quest["plan_revision"]),
            ).fetchone()
            if plan is None:
                raise DomainError("Approved current plan required")
            self._current_plan(
                work.connection,
                quest_id,
                plan["revision"],
                plan["digest"],
                allow_running=True,
            )
            if reassignment_run_id is not None:
                self._reassignment_target(work.connection, quest_id, reassignment_run_id)
                if (
                    not isinstance(reassignment_reason, str)
                    or not reassignment_reason.strip()
                    or len(reassignment_reason) > 2000
                ):
                    raise DomainError("A bounded reassignment reason is required")
            if source_event_id is not None:
                notice = work.connection.execute(
                    """SELECT * FROM event_inbox WHERE consumer_id='composer' AND event_id=?
                    AND event_type IN ('quest.plan_approved','worker.help_requested') AND quest_id=? AND processed_at IS NULL
                    AND notice_dead_at IS NULL AND notice_token=? AND notice_next_attempt>?""",
                    (source_event_id, quest_id, source_event_token, time.time()),
                ).fetchone()
                if notice is None:
                    raise DomainError("Composer event lease is unavailable")
                payload = json.loads(notice["payload"])
                if notice["event_type"] == "worker.help_requested":
                    if reassignment_run_id is None or payload.get("run_id") != reassignment_run_id:
                        raise DomainError("Help event refers to another Worker")
                    source_run = work.connection.execute(
                        "SELECT * FROM worker_runs WHERE id=? AND quest_id=? AND plan_id=?",
                        (reassignment_run_id, quest_id, plan["id"]),
                    ).fetchone()
                    original_event = work.connection.execute(
                        "SELECT worker_id FROM events WHERE id=?", (source_event_id,)
                    ).fetchone()
                    if (
                        source_run is None
                        or original_event is None
                        or source_run["worker_id"] != original_event["worker_id"]
                        or source_run["plan_revision"] != plan["revision"]
                        or source_run["plan_digest"] != plan["digest"]
                    ):
                        raise DomainError("Help event Worker binding is invalid")
                elif (
                    payload.get("plan_revision") != plan["revision"]
                    or payload.get("digest") != plan["digest"]
                ):
                    raise DomainError("Composer event refers to another plan")

            def acknowledge():
                if source_event_id is not None:
                    cursor = work.connection.execute(
                        """UPDATE event_inbox SET processed_at=?,notice_token=NULL WHERE consumer_id='composer'
                        AND event_id=? AND processed_at IS NULL AND notice_token=? AND notice_next_attempt>?""",
                        (now_iso(), source_event_id, source_event_token, time.time()),
                    )
                    if cursor.rowcount != 1:
                        raise DomainError("Composer event lease expired")

            graph = json.loads(plan["graph_json"])
            if (
                source_event_id is not None
                and notice["event_type"] == "worker.help_requested"
                and (graph.get("capacity_contract") or {}).get("composition_capacity_policy")
                is not None
                and composition_cycle_id is None
            ):
                raise DomainError("Bounded Worker help requires its measured composition cycle")
            if _digest(graph) != plan["digest"]:
                raise DomainError("Approved plan digest does not match its content")
            self._check_task_limits(work.connection, quest_id, graph, limits, roles)
            if token_budgets_enforced() and limits.max_total_tokens > graph["max_total_tokens"]:
                raise DomainError("Team token budget exceeds plan")
            plan_duration = graph.get("max_duration_seconds")
            if (
                plan_duration is not None
                and sum(role.max_duration_seconds for role in roles) > plan_duration
            ):
                raise DomainError("Team duration budget exceeds plan")
            plan_cap = graph.get("max_cost_microunits")
            if plan_cap is not None:
                plan_currency = graph.get("cost_currency")
                if (
                    not plan_currency
                    or limits.currency != plan_currency
                    or limits.max_cost_microunits is None
                    or limits.max_cost_microunits > plan_cap
                ):
                    raise DomainError("Team monetary budget must fit the plan currency and cap")
            nodes = {node["key"]: node for node in graph["nodes"]}
            from .budget_policy import node_execution_limit, validate_serial_policy

            serial_policy = validate_serial_policy(graph)
            if serial_policy and (
                limits.max_concurrency != 1
                or limits.max_cost_microunits is not None
                or limits.currency is not None
            ):
                raise DomainError(
                    "Serial capacity team requires concurrency one and token-only limits"
                )
            role_nodes = [role.task_node_key for role in roles]
            required_nodes = set(nodes)
            if reassignment_run_id is not None or graph.get("revision_lineage"):
                required_nodes -= {
                    row["node_key"]
                    for row in work.connection.execute(
                        "SELECT node_key FROM task_nodes WHERE plan_id=? AND status='succeeded'",
                        (plan["id"],),
                    )
                }
            if (
                len(role_nodes) != len(set(role_nodes))
                or not required_nodes.issubset(role_nodes)
                or not set(role_nodes).issubset(nodes)
                or (graph.get("revision_lineage") and set(role_nodes) != required_nodes)
            ):
                raise DomainError("Each approved task node requires exactly one team member")
            used = {}
            if set(limits.allowed_tools) != {tool for role in roles for tool in role.tools}:
                raise DomainError("Team tool scope must equal its member scopes")
            for index, role in enumerate(roles):
                node = nodes.get(role.task_node_key)
                if graph.get("hierarchical_expansions"):
                    from .hierarchical_plans import node_depth

                    if role.depth != node_depth(graph, role.task_node_key):
                        raise DomainError(
                            "Role depth must match its approved hierarchical child contract"
                        )
                if node is None or not set(role.tools).issubset(node.get("allowed_tools", [])):
                    raise DomainError("Role exceeds approved task tool scope")
                if role.acceptance_criteria and not set(node["success_criteria"]).issubset(
                    role.acceptance_criteria
                ):
                    raise DomainError("Role weakens approved task acceptance criteria")
                roles[index] = role.model_copy(
                    update={
                        "responsibility": role.responsibility
                        or PRESET_RESPONSIBILITIES.get(role.id),
                        "input_contract": role.input_contract or [node["objective"]],
                        "output_schema": role.output_schema or DEFAULT_OUTPUT_SCHEMA,
                        "acceptance_criteria": role.acceptance_criteria or node["success_criteria"],
                    }
                )
                if any(
                    role.required_capabilities.get(name, -1) < minimum
                    for name, minimum in node.get("required_capabilities", {}).items()
                ):
                    raise DomainError("Role weakens approved task capability requirements")
                used[role.task_node_key] = (
                    used.get(role.task_node_key, 0) + role.input_tokens + role.output_tokens
                )
                if token_budgets_enforced() and used[role.task_node_key] > node["budget_tokens"]:
                    raise DomainError("Team token budget exceeds task budget")
                if serial_policy and used[role.task_node_key] != node["budget_tokens"]:
                    raise DomainError("Serial team allocation must equal each approved baseline")
            if (
                token_budgets_enforced()
                and serial_policy
                and (
                    sum(used.values()) + serial_policy["shared_reserve_tokens"]
                    > limits.max_total_tokens
                )
            ):
                raise DomainError(
                    "Serial team ceiling must include its baselines and shared reserve"
                )
            rows = work.connection.execute(
                "SELECT id,profile FROM models ORDER BY id LIMIT 101"
            ).fetchall()
            if len(rows) > 100:
                raise DomainError("Too many model profiles for bounded composition")
            from .model_intelligence import decode_profile
            from .model_performance import performance_snapshot

            profiles = [decode_profile(row["profile"], row["id"]) for row in rows]
            snapshots = {
                model.id: performance_snapshot(work.connection, model.provider, model.model_id)
                for model in profiles
                if model.enabled and model.model_id and not model.quarantine_reason
            }
            try:
                body = self.engine.compose(
                    roles,
                    profiles,
                    limits,
                    strategy=strategy,
                    online_snapshots=snapshots,
                )
            except ValueError as exc:
                raise DomainError(str(exc)) from exc
            selected = {member["profile_id"] for member in body["members"]}
            for member in body["members"]:
                sources = nodes[member["task_node_key"]].get("required_predecessor_deliveries")
                if sources is not None:
                    member["required_predecessor_deliveries"] = sources
                if serial_policy:
                    member["serial_capacity_policy"] = serial_policy
                    member["execution_token_limit"] = node_execution_limit(
                        graph, nodes[member["task_node_key"]]
                    )
            body.update(
                {
                    "quest_id": quest_id,
                    "plan_revision": plan["revision"],
                    "plan_digest": plan["digest"],
                    "roles": [role.model_dump() for role in roles],
                    "profile_hashes": {
                        row["id"]: hashlib.sha256(row["profile"].encode("utf-8")).hexdigest()
                        for row in rows
                        if row["id"] in selected
                    },
                }
            )
            if graph.get("execution_constraints"):
                body["execution_constraints"] = graph["execution_constraints"]
            if graph.get("capacity_contract"):
                from .capacity_preflight import (
                    coordination_budget_snapshot,
                    node_review_envelope,
                    validate_plan_contract,
                )
                from .composition_capacity import provider_for_graph

                validate_plan_contract(
                    self.store,
                    work.connection,
                    quest_id,
                    graph,
                    composer_capacity_measurer=self.composer_capacity_measurer,
                    composer_provider=provider_for_graph(self.capacity_providers, graph),
                )
                if self.capacity_providers is None:
                    raise DomainError(
                        "New capacity contract requires actual selected Provider framing"
                    )
                body["capacity_contract_version"] = graph["capacity_contract"]["version"]
                input_policy = graph["capacity_contract"].get("input_acquisition_policy")
                if input_policy:
                    body["input_acquisition_policy"] = input_policy
                if "source_wire_format" in graph["capacity_contract"]:
                    body["source_wire_format"] = graph["capacity_contract"]["source_wire_format"]
                if input_policy == "core-declared-source-prefetch-v1":
                    body["action_evidence_allowances"] = graph["capacity_contract"][
                        "action_evidence_allowances"
                    ]
                if "hypothesis_read_paths" in graph["capacity_contract"]:
                    body["hypothesis_read_paths"] = graph["capacity_contract"][
                        "hypothesis_read_paths"
                    ]
                inventory = graph["capacity_contract"]["input_inventory"]
                for member in body["members"]:
                    if input_policy:
                        member["input_acquisition_policy"] = input_policy
                    if "source_wire_format" in graph["capacity_contract"]:
                        member["source_wire_format"] = graph["capacity_contract"][
                            "source_wire_format"
                        ]
                    if input_policy == "core-declared-source-prefetch-v1":
                        member["action_evidence_allowance_bytes"] = graph["capacity_contract"][
                            "action_evidence_allowances"
                        ][member["task_node_key"]]
                    node = nodes[member["task_node_key"]]
                    if "hypothesis_read_paths" in graph["capacity_contract"]:
                        member["hypothesis_read_paths"] = graph["capacity_contract"][
                            "hypothesis_read_paths"
                        ][member["task_node_key"]]
                    try:
                        selected_provider = self.capacity_providers.get(member["provider_id"])
                    except ValueError as exc:
                        raise DomainError("Selected capacity Provider unavailable") from exc
                    envelope, observation = node_review_envelope(
                        self.store,
                        work.connection,
                        quest,
                        graph,
                        node,
                        member,
                        selected_provider,
                    )
                    member.update(
                        capacity_preflight=observation,
                        required_input_paths=node["required_input_paths"],
                        verified_input_inventory=[
                            item
                            for item in inventory
                            if item["path"] in node["required_input_paths"]
                        ],
                    )
                    member[
                        "review_envelope"
                        if envelope["version"] == "review-envelope-v1"
                        else "worker_capacity"
                    ] = envelope
                snapshot = coordination_budget_snapshot(
                    work.connection,
                    self.store,
                    quest_id,
                    graph,
                    self.budget_ledger,
                )
                allocation = sum(member["estimated_tokens"] for member in body["members"])
                if token_budgets_enforced() and (
                    allocation + (serial_policy["shared_reserve_tokens"] if serial_policy else 0)
                    > snapshot["max_worker_allocations_now"]
                ):
                    raise DomainError(
                        "Team plus coordination reserve exceeds current residual budget"
                    )
                body["capacity_budget_snapshot"] = snapshot
            if composition_cycle_id is not None:
                if reassignment_run_id is None or source_event_id is None:
                    raise DomainError("A composition cycle requires its exact reassignment event")
                from .composition_cycles import validate_cycle

                cycle, _, _ = validate_cycle(
                    self.store,
                    work.connection,
                    quest,
                    plan,
                    source_event_id,
                    provider_for_graph(self.capacity_providers, graph),
                    self.composer_capacity_measurer,
                    cycle_id=composition_cycle_id,
                )
                if cycle["binding"]["run_id"] != reassignment_run_id:
                    raise DomainError("Composition cycle refers to another reassignment target")
                body["composition_cycle_id"] = composition_cycle_id
                from .composition_cycles import cycle_state

                cycle_status = cycle_state(work.connection, cycle)
                if (
                    cycle_status["unresolved_composer_calls"]
                    or not 1 <= cycle_status["settled_composer_calls"] <= 2
                ):
                    raise DomainError(
                        "Reassignment composition must settle before its team proposal"
                    )
            if reassignment_run_id is not None:
                body["reassignment"] = {
                    "run_id": reassignment_run_id,
                    "reason": reassignment_reason,
                }
            proposal_digest = _digest(body)
            existing = work.connection.execute(
                "SELECT * FROM team_proposals WHERE quest_id=? AND proposal_digest=?",
                (quest_id, proposal_digest),
            ).fetchone()
            if existing is not None:
                if existing["status"] in {"proposed", "approved"}:
                    acknowledge()
                    return {
                        **body,
                        "id": existing["id"],
                        "proposal_digest": proposal_digest,
                        "status": existing["status"],
                    }
                raise DomainError("Revoked or superseded proposal cannot be reused")
            work.connection.execute(
                "UPDATE team_proposals SET status='superseded' WHERE quest_id=? AND status='proposed'",
                (quest_id,),
            )
            identifier = new_id("team")
            work.connection.execute(
                "INSERT INTO team_proposals VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    quest_id,
                    plan["revision"],
                    plan["digest"],
                    proposal_digest,
                    "proposed",
                    _canonical(body),
                    now_iso(),
                    None,
                    None,
                ),
            )
            work.append_event(
                ForgeEvent(
                    event_type="team.proposed",
                    quest_id=quest_id,
                    payload={"team_id": identifier, "proposal_digest": proposal_digest},
                )
            )
            acknowledge()
            return {
                **body,
                "id": identifier,
                "proposal_digest": proposal_digest,
                "status": "proposed",
            }

    def _checked(
        self,
        connection,
        quest_id,
        plan_revision,
        proposal_digest,
        *,
        status,
        allow_running=False,
        validate_profiles=True,
        historical=False,
        lineage_evidence=False,
    ):
        row = connection.execute(
            "SELECT * FROM team_proposals WHERE quest_id=? AND plan_revision=? AND proposal_digest=?",
            (quest_id, plan_revision, proposal_digest),
        ).fetchone()
        if row is None or row["status"] != status:
            raise DomainError("Team approval unavailable")
        quest, plan = self._current_plan(
            connection,
            quest_id,
            plan_revision,
            row["plan_digest"],
            allow_running=allow_running or status == "approved",
            historical=historical,
            lineage_evidence=lineage_evidence,
        )
        try:
            if _digest(json.loads(plan["graph_json"])) != row["plan_digest"]:
                raise ValueError
            body = json.loads(row["body_json"])
            if (
                _digest(body) != proposal_digest
                or body["quest_id"] != quest_id
                or body["plan_revision"] != plan_revision
                or body["plan_digest"] != row["plan_digest"]
            ):
                raise ValueError
            from .source_wire import approved_source_wire_format

            graph = json.loads(plan["graph_json"])
            approved_source_wire_format(graph, body)
            for member in body["members"]:
                approved_source_wire_format(graph, member)
            for model_id, expected in body["profile_hashes"].items() if validate_profiles else []:
                model = connection.execute(
                    "SELECT profile FROM models WHERE id=?", (model_id,)
                ).fetchone()
                if (
                    model is None
                    or hashlib.sha256(model["profile"].encode("utf-8")).hexdigest() != expected
                ):
                    raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise DomainError("Team proposal is stale or invalid") from exc
        return quest, row, body

    def approve(self, quest_id, plan_revision, proposal_digest, *, expected_version, actor):
        if actor != "local_user":
            raise DomainError("Only the authenticated user may approve a team")
        with self.store.unit_of_work() as work:
            quest, row, body = self._checked(
                work.connection,
                quest_id,
                plan_revision,
                proposal_digest,
                status="proposed",
                allow_running=True,
            )
            if quest["version"] != expected_version:
                raise DomainError("Quest version conflict")
            self._check_operator_concurrency(CompositionLimits.model_validate(body["limits"]))
            graph = json.loads(
                work.connection.execute(
                    "SELECT graph_json FROM quest_plans WHERE quest_id=? AND revision=?",
                    (quest_id, plan_revision),
                ).fetchone()[0]
            )
            self._check_task_limits(
                work.connection,
                quest_id,
                graph,
                CompositionLimits.model_validate(body["limits"]),
                [RoleSpec.model_validate(role) for role in body["roles"]],
            )
            from .budget_policy import node_execution_limit, validate_serial_policy

            serial_policy = validate_serial_policy(graph)
            if serial_policy:
                if (
                    body["limits"]["max_concurrency"] != 1
                    or body["limits"].get("max_cost_microunits") is not None
                    or body["limits"].get("currency") is not None
                ):
                    raise DomainError(
                        "Serial capacity approval requires concurrency one and token-only limits"
                    )
                for member in body["members"]:
                    node = next(
                        item for item in graph["nodes"] if item["key"] == member["task_node_key"]
                    )
                    if member.get("serial_capacity_policy") != serial_policy or member.get(
                        "execution_token_limit"
                    ) != node_execution_limit(graph, node):
                        raise DomainError("Serial approved member policy changed")
            if graph.get("capacity_contract"):
                from .capacity_preflight import (
                    coordination_budget_snapshot,
                    node_review_envelope,
                    validate_plan_contract,
                )
                from .composition_capacity import provider_for_graph

                validate_plan_contract(
                    self.store,
                    work.connection,
                    quest_id,
                    graph,
                    composer_capacity_measurer=self.composer_capacity_measurer,
                    composer_provider=provider_for_graph(self.capacity_providers, graph),
                )
                if self.capacity_providers is None:
                    raise DomainError("Capacity approval requires actual selected Provider framing")
                nodes = {node["key"]: node for node in graph["nodes"]}
                input_policy = graph["capacity_contract"].get("input_acquisition_policy")
                if body.get("input_acquisition_policy") != input_policy:
                    raise DomainError("Team input acquisition protocol differs from approved plan")
                from .source_wire import approved_source_wire_format

                approved_source_wire_format(graph, body)
                if body.get("hypothesis_read_paths") != graph["capacity_contract"].get(
                    "hypothesis_read_paths"
                ):
                    raise DomainError("Team hypothesis paths differ from the approved plan")
                if input_policy == "core-declared-source-prefetch-v1" and body.get(
                    "action_evidence_allowances"
                ) != graph["capacity_contract"].get("action_evidence_allowances"):
                    raise DomainError("Team action evidence sizing differs from approved plan")
                for member in body["members"]:
                    from .hypothesis_sources import validate_member as validate_hypothesis_member

                    validate_hypothesis_member(graph, member)
                    approved_source_wire_format(graph, member)
                    if input_policy == "core-declared-source-prefetch-v1" and (
                        type(member.get("action_evidence_allowance_bytes")) is not int
                        or member.get("action_evidence_allowance_bytes")
                        != graph["capacity_contract"]["action_evidence_allowances"].get(
                            member["task_node_key"]
                        )
                    ):
                        raise DomainError(
                            "Member action evidence sizing differs from approved plan"
                        )
                    if member.get("input_acquisition_policy") != input_policy:
                        raise DomainError(
                            "Member input acquisition protocol differs from approved plan"
                        )
                    try:
                        selected_provider = self.capacity_providers.get(member["provider_id"])
                    except ValueError as exc:
                        raise DomainError("Selected capacity Provider unavailable") from exc
                    envelope, _ = node_review_envelope(
                        self.store,
                        work.connection,
                        quest,
                        graph,
                        nodes[member["task_node_key"]],
                        member,
                        selected_provider,
                    )
                    if envelope != member.get("review_envelope", member.get("worker_capacity")):
                        raise DomainError(
                            "Selected Provider capacity changed; propose a new team for approval"
                        )
                snapshot = coordination_budget_snapshot(
                    work.connection,
                    self.store,
                    quest_id,
                    graph,
                    self.budget_ledger,
                )
                if token_budgets_enforced() and (
                    sum(member["estimated_tokens"] for member in body["members"])
                    + (serial_policy["shared_reserve_tokens"] if serial_policy else 0)
                    > snapshot["max_worker_allocations_now"]
                ):
                    raise DomainError("Approved team would exceed current residual budget")
            cursor = work.connection.execute(
                "UPDATE quests SET version=version+1,updated_at=? WHERE id=? AND version=?",
                (now_iso(), quest_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise DomainError("Quest version conflict")
            reassignment = body.get("reassignment")
            if reassignment:
                if body.get("composition_cycle_id") is not None:
                    from .composition_cycles import validate_proposal_cycle

                    validate_proposal_cycle(
                        self.store,
                        work.connection,
                        quest,
                        work.connection.execute(
                            "SELECT * FROM quest_plans WHERE quest_id=? AND revision=?",
                            (quest_id, plan_revision),
                        ).fetchone(),
                        body,
                        provider_for_graph(self.capacity_providers, graph),
                        self.composer_capacity_measurer,
                    )
                target = self._reassignment_target(
                    work.connection, quest_id, reassignment["run_id"]
                )
                work.connection.execute(
                    "UPDATE worker_runs SET state='cancelled',lease_token=NULL,lease_until=NULL WHERE id=?",
                    (target["id"],),
                )
                work.connection.execute(
                    "UPDATE workers SET status='cancelled' WHERE id=?",
                    (target["worker_id"],),
                )
                work.connection.execute(
                    "UPDATE task_nodes SET worker_id=NULL,status='proposed' WHERE plan_id=? AND node_key=? AND worker_id=? AND status<>'succeeded'",
                    (target["plan_id"], target["node_key"], target["worker_id"]),
                )
                work.append_event(
                    ForgeEvent(
                        event_type="team.reassigned",
                        quest_id=quest_id,
                        payload={
                            "old_run_id": target["id"],
                            "team_id": row["id"],
                            "reason": reassignment["reason"],
                        },
                    )
                )
            active = work.connection.execute(
                """SELECT 1 FROM worker_runs WHERE quest_id=? AND team_digest<>?
                AND state IN ('queued','running','paused','blocked','review_pending') LIMIT 1""",
                (quest_id, proposal_digest),
            ).fetchone()
            if active:
                raise DomainError("Existing team has unsettled Worker runs")
            work.connection.execute(
                "UPDATE team_proposals SET status='superseded' WHERE quest_id=? AND status='approved'",
                (quest_id,),
            )
            work.connection.execute(
                "UPDATE team_proposals SET status='approved',approved_by=?,approved_at=? WHERE id=? AND status='proposed'",
                (actor, now_iso(), row["id"]),
            )
            work.append_event(
                ForgeEvent(
                    event_type="team.approved",
                    quest_id=quest_id,
                    payload={"team_id": row["id"], "proposal_digest": proposal_digest},
                )
            )
            return {
                **body,
                "id": row["id"],
                "proposal_digest": proposal_digest,
                "status": "approved",
            }

    def _reassignment_target(self, connection, quest_id, run_id):
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE quest_id=? AND event_type='team.reassigned'",
            (quest_id,),
        ).fetchone()[0]
        if count >= 3:
            raise DomainError("Quest reassignment limit reached")
        run = connection.execute(
            "SELECT * FROM worker_runs WHERE id=? AND quest_id=?", (run_id, quest_id)
        ).fetchone()
        if run is None or run["state"] not in {"blocked", "failed", "cancelled"}:
            raise DomainError("Only settled blocked or failed Workers may be reassigned")
        if (
            connection.execute(
                "SELECT 1 FROM action_intents WHERE run_id=? AND state IN ('prepared','executing','uncertain') LIMIT 1",
                (run_id,),
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM worker_tool_calls WHERE run_id=? AND state IN ('running','uncertain') LIMIT 1",
                (run_id,),
            ).fetchone()
            or (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_core_source_reads'"
                ).fetchone()
                and connection.execute(
                    "SELECT 1 FROM worker_core_source_reads WHERE run_id=? "
                    "AND state IN ('pending','running','uncertain','failed') LIMIT 1",
                    (run_id,),
                ).fetchone()
            )
        ):
            raise DomainError("Worker actions must be settled before reassignment")
        node = connection.execute(
            "SELECT status,worker_id FROM task_nodes WHERE plan_id=? AND node_key=?",
            (run["plan_id"], run["node_key"]),
        ).fetchone()
        if node is None or node["status"] == "succeeded" or node["worker_id"] != run["worker_id"]:
            raise DomainError("Reassignment target no longer owns unfinished work")
        return run

    def propose_reassignment(
        self,
        quest_id,
        run_id,
        reason,
        roles,
        limits,
        *,
        strategy,
        expected_version,
        source_event_id=None,
        source_event_token=None,
        composition_cycle_id=None,
    ):
        """Request a separately approved, bounded replacement; no budget is refunded."""
        return self.propose(
            quest_id,
            roles,
            limits,
            strategy=strategy,
            expected_version=expected_version,
            reassignment_run_id=run_id,
            reassignment_reason=reason,
            composition_cycle_id=composition_cycle_id,
            source_event_id=source_event_id,
            source_event_token=source_event_token,
        )

    def revoke(self, quest_id, proposal_digest, *, actor):
        if actor != "local_user":
            raise DomainError("Only the authenticated user may revoke a team")
        with self.store.unit_of_work() as work:
            row = work.connection.execute(
                "SELECT id,status FROM team_proposals WHERE quest_id=? AND proposal_digest=? AND status IN ('proposed','approved')",
                (quest_id, proposal_digest),
            ).fetchone()
            if row is None:
                raise DomainError("Team proposal cannot be revoked")
            if (
                row["status"] == "approved"
                and work.connection.execute(
                    """SELECT 1 FROM worker_runs WHERE quest_id=? AND team_digest=?
                AND state IN ('queued','running','paused','blocked','review_pending') LIMIT 1""",
                    (quest_id, proposal_digest),
                ).fetchone()
            ):
                raise DomainError("Team has unsettled Worker runs")
            work.connection.execute(
                "UPDATE team_proposals SET status='revoked' WHERE id=?", (row["id"],)
            )
            work.append_event(
                ForgeEvent(
                    event_type="team.revoked",
                    quest_id=quest_id,
                    payload={"team_id": row["id"]},
                )
            )

    def get_approved(self, quest_id, plan_revision, proposal_digest, *, connection):
        if not connection.in_transaction:
            raise DomainError("Team validation requires caller-owned write transaction")
        _, row, body = self._checked(
            connection, quest_id, plan_revision, proposal_digest, status="approved"
        )
        return {
            **body,
            "id": row["id"],
            "proposal_digest": proposal_digest,
            "status": "approved",
        }

    def get_historical_approved(
        self, quest_id, plan_revision, proposal_digest, *, connection, run_id
    ):
        """Validate past approval for evidence only; never authorizes new execution."""
        if not connection.in_transaction:
            raise DomainError("Team validation requires caller-owned write transaction")
        record = connection.execute(
            "SELECT status,approved_by,approved_at FROM team_proposals WHERE quest_id=? AND proposal_digest=?",
            (quest_id, proposal_digest),
        ).fetchone()
        if (
            record is None
            or record["status"] not in {"approved", "superseded"}
            or record["approved_by"] != "local_user"
            or not record["approved_at"]
        ):
            raise DomainError("Historical team approval unavailable")
        run = connection.execute(
            "SELECT * FROM worker_runs WHERE id=? AND quest_id=? AND plan_revision=? AND team_digest=?",
            (run_id, quest_id, plan_revision, proposal_digest),
        ).fetchone()
        if run is None or run["created_at"] < record["approved_at"]:
            raise DomainError("Worker predates its team approval")
        quest = connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
        lineage_evidence = quest["plan_revision"] != plan_revision
        if lineage_evidence:
            from .plan_lineage import validated_lineage_source

            current = connection.execute(
                "SELECT * FROM quest_plans WHERE quest_id=? AND revision=?",
                (quest_id, quest["plan_revision"]),
            ).fetchone()
            if (
                current is None
                or validated_lineage_source(connection, quest, current, run["node_key"])[
                    "source_run"
                ]["id"]
                != run_id
            ):
                raise DomainError("Historical Worker is not preserved by current approved lineage")
        if record["status"] == "superseded":
            successor = connection.execute(
                "SELECT MIN(approved_at) FROM team_proposals WHERE quest_id=? AND approved_at>? AND (plan_revision=? OR ?)",
                (quest_id, record["approved_at"], plan_revision, int(lineage_evidence)),
            ).fetchone()[0]
            if successor is None or run["created_at"] >= successor:
                raise DomainError("Worker was created after team supersession")
        _, row, body = self._checked(
            connection,
            quest_id,
            plan_revision,
            proposal_digest,
            status=record["status"],
            allow_running=True,
            validate_profiles=False,
            historical=True,
            lineage_evidence=lineage_evidence,
        )
        return {
            **body,
            "id": row["id"],
            "proposal_digest": proposal_digest,
            "status": row["status"],
        }
