from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .token_policy import token_budgets_enforced


def success_ancestors(edges, node_key):
    """Bounded reverse reachability through success edges, excluding self."""
    parents = {}
    for edge in edges:
        if edge["condition"] == "success":
            parents.setdefault(edge["target"], set()).add(edge["source"])
    seen, pending = set(), list(parents.get(node_key, ()))
    while pending:
        key = pending.pop()
        if key != node_key and key not in seen:
            seen.add(key)
            pending.extend(parents.get(key, ()))
    return seen


class NodeCapacityBounds(BaseModel):
    """Explicit opt-in bounds; unknown future artifact sizes are never zero."""

    model_config = ConfigDict(extra="forbid", strict=True)
    max_source_receipt_bytes: int = Field(ge=1, le=65536)
    max_artifact_bytes: int = Field(ge=1, le=32768)
    max_delivery_bytes: int = Field(ge=1, le=32768)


class SerialCapacityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal["serial-capacity-v1"]
    shared_reserve_tokens: int = Field(ge=1, le=1000000)


class TaskNodeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    objective: str = Field(min_length=1, max_length=2000)
    success_criteria: list[str] = Field(min_length=1, max_length=20)
    budget_tokens: int = Field(ge=1, le=1000000)
    required_capabilities: dict[str, float] = Field(default_factory=dict, max_length=32)
    expected_artifact: str | None = Field(default=None, max_length=2000)
    max_attempts: int = Field(default=1, ge=1, le=10)
    allowed_tools: list[str] = Field(default_factory=list, max_length=32)
    required_input_paths: list[str] | None = Field(default=None, max_length=32)
    required_predecessor_deliveries: list[str] | None = Field(default=None, max_length=8)
    capacity_bounds: NodeCapacityBounds | None = None

    @model_validator(mode="after")
    def criteria_valid(self):
        if any(not item.strip() or len(item) > 1000 for item in self.success_criteria):
            raise ValueError("Invalid success criterion")
        if any(
            not key.strip() or not 0 <= value <= 100
            for key, value in self.required_capabilities.items()
        ):
            raise ValueError("Invalid required capability")
        from .tool_domain import CONTRACTS

        known = {contract.name for contract in CONTRACTS}
        if len(set(self.allowed_tools)) != len(self.allowed_tools) or any(
            name not in known for name in self.allowed_tools
        ):
            raise ValueError("Unknown or duplicate tool")
        deliveries = self.required_predecessor_deliveries or []
        import re

        if len(deliveries) != len(set(deliveries)) or any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key) for key in deliveries
        ):
            raise ValueError("Invalid or duplicate completed-delivery source")
        if deliveries and "worker_read_completed_delivery" not in self.allowed_tools:
            raise ValueError("Completed deliveries require the explicit Core read tool")
        return self


class TaskEdgeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    condition: Literal["success", "failure", "always"]


class GraphProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nodes: list[TaskNodeProposal] = Field(min_length=1, max_length=128)
    edges: list[TaskEdgeProposal] = Field(max_length=512)
    max_total_tokens: int = Field(default=1000000, ge=1, le=1000000)
    max_cost_microunits: int | None = Field(default=None, ge=0)
    cost_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    max_duration_seconds: int | None = Field(default=None, ge=1)
    execution_constraints: dict | None = None
    capacity_contract: dict | None = None
    coordination_reserve_tokens: int | None = Field(default=None, ge=1, le=1000000)
    revision_lineage: dict | None = None
    hierarchical_expansions: list[dict] | None = Field(default=None, max_length=8)
    serial_capacity_policy: SerialCapacityPolicy | None = None

    @model_validator(mode="after")
    def graph_valid(self):
        if (self.max_cost_microunits is None) != (self.cost_currency is None):
            raise ValueError("A cost limit requires an explicit currency")
        keys = [node.key for node in self.nodes]
        if len(keys) != len(set(keys)) or (
            token_budgets_enforced()
            and sum(node.budget_tokens for node in self.nodes) > self.max_total_tokens
        ):
            raise ValueError("Duplicate node or total budget exceeded")
        incoming = dict.fromkeys(keys, 0)
        outgoing = {key: [] for key in keys}
        edges = set()
        for edge in self.edges:
            pair = (edge.source, edge.target)
            if edge.source not in incoming or edge.target not in incoming or pair in edges:
                raise ValueError("Invalid graph edge")
            edges.add(pair)
            incoming[edge.target] += 1
            outgoing[edge.source].append(edge.target)
        ready = [key for key, count in incoming.items() if count == 0]
        count = 0
        while ready:
            node = ready.pop()
            count += 1
            for child in outgoing[node]:
                incoming[child] -= 1
                if incoming[child] == 0:
                    ready.append(child)
        if count != len(keys):
            raise ValueError("Graph contains a cycle")
        for node in self.nodes:
            parents = success_ancestors([edge.model_dump() for edge in self.edges], node.key)
            if set(node.required_predecessor_deliveries or []) - parents:
                raise ValueError("Completed delivery must be an approved success ancestor")
        if self.serial_capacity_policy is not None:
            from .budget_policy import validate_serial_policy

            validate_serial_policy(self.model_dump())
        return self


class CapabilityWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    minimum: float = Field(ge=0, le=100, allow_inf_nan=False)


def _strict_wire_schema(schema):
    """Require nullable wire properties while retaining legacy parsing defaults."""
    properties = schema.get("properties", {})
    schema["required"] = list(properties)
    for value in properties.values():
        value.pop("default", None)


class NodeWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)
    key: str
    objective: str
    success_criteria: list[str]
    budget_tokens: int
    required_capabilities: list[CapabilityWire]
    expected_artifact: str | None
    max_attempts: int
    allowed_tools: list[str]
    required_input_paths: list[str] | None = None
    required_predecessor_deliveries: list[str] | None = None
    capacity_bounds: NodeCapacityBounds | None = None


class GraphWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)
    nodes: list[NodeWire]
    edges: list[TaskEdgeProposal]
    max_total_tokens: int
    max_cost_microunits: int | None
    cost_currency: str | None
    max_duration_seconds: int | None
    coordination_reserve_tokens: int | None = None
    serial_capacity_policy: SerialCapacityPolicy | None = None

    def proposal_data(self, *, completed_nodes=()):
        data = self.model_dump()
        for node in data["nodes"]:
            capabilities = node["required_capabilities"]
            if len({item["name"] for item in capabilities}) != len(capabilities):
                raise ValueError("Duplicate capability")
            node["required_capabilities"] = {item["name"]: item["minimum"] for item in capabilities}
        data["nodes"].extend(completed_nodes)
        return data

    def proposal(self, *, completed_nodes=()):
        data = self.proposal_data(completed_nodes=completed_nodes)
        if data.pop("allocation_mode", "model_declared") != "model_declared":
            raise ValueError("Core measured allocation requires native capacity measurement")
        return GraphProposal.model_validate(data)


class CapacityNodeWire(NodeWire):
    required_input_paths: list[str]
    capacity_bounds: NodeCapacityBounds


class CapacityGraphWire(GraphWire):
    nodes: list[CapacityNodeWire]
    coordination_reserve_tokens: int
    allocation_mode: Literal["model_declared", "core_measured_serial"] = "model_declared"


class OutputCapacityBounds(BaseModel):
    """Model-selected complete output bounds; Core measures required input receipts."""

    model_config = ConfigDict(extra="forbid", strict=True)
    max_artifact_bytes: int = Field(ge=1, le=32768)
    max_delivery_bytes: int = Field(ge=1, le=32768)


class CoreVerifierWire(BaseModel):
    """Typed selection of one existing executable verifier; no prose criteria."""

    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)
    kind: Literal[
        "delivery_review",
        "json_review:@delivery",
        "json_review",
        "json_valid",
        "semantic_numeric:scheduler-v1",
    ]
    artifact_path: str | None = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def path_matches_kind(self):
        inline = self.kind in {"delivery_review", "json_review:@delivery"}
        if inline and self.artifact_path is not None:
            raise ValueError("inline_verifier_artifact_path_must_be_null")
        if not inline and self.artifact_path is None:
            raise ValueError("artifact_verifier_requires_artifact_path")
        return self

    def criterion(self):
        return self.kind if self.artifact_path is None else self.kind + ":" + self.artifact_path


class CoreMeasuredNodeWire(BaseModel):
    """Autonomous semantic contract; resource allocations are Core-owned."""

    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)
    key: str
    objective: str
    success_criteria: list[CoreVerifierWire] = Field(min_length=1, max_length=5)
    required_capabilities: list[CapabilityWire]
    expected_artifact: str | None
    max_attempts: int
    allowed_tools: list[str]
    required_input_paths: list[str]
    required_predecessor_deliveries: list[str]
    capacity_bounds: OutputCapacityBounds

    @model_validator(mode="after")
    def artifact_verifiers_match_node(self):
        for criterion in self.success_criteria:
            if (
                criterion.artifact_path is not None
                and criterion.artifact_path != self.expected_artifact
            ):
                raise ValueError("artifact_verifier_path_must_match_expected_artifact")
        return self


class CoreMeasuredGraphWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)
    allocation_mode: Literal["core_measured"]
    nodes: list[CoreMeasuredNodeWire] = Field(min_length=1, max_length=8)
    edges: list[TaskEdgeProposal] = Field(max_length=16)

    def proposal_data(self, *, resource_limits, coordination_reserve_tokens, completed_nodes=()):
        data = self.model_dump()
        data.pop("allocation_mode")
        for wire_node, node in zip(self.nodes, data["nodes"], strict=True):
            capabilities = node["required_capabilities"]
            if len({item["name"] for item in capabilities}) != len(capabilities):
                raise ValueError("Duplicate capability")
            node["required_capabilities"] = {item["name"]: item["minimum"] for item in capabilities}
            node["success_criteria"] = [
                criterion.criterion() for criterion in wire_node.success_criteria
            ]
            node["budget_tokens"] = 2049
        data["nodes"].extend(completed_nodes)
        data.update(resource_limits)
        data["coordination_reserve_tokens"] = coordination_reserve_tokens
        return data


def graph_contract_dump(proposal):
    """Keep the pre-v16 canonical shape for manual/legacy plans without opt-in."""
    data = proposal.model_dump()
    for key in (
        "execution_constraints",
        "capacity_contract",
        "coordination_reserve_tokens",
        "revision_lineage",
        "hierarchical_expansions",
        "serial_capacity_policy",
    ):
        if data.get(key) is None:
            data.pop(key, None)
    for node in data["nodes"]:
        for key in ("required_input_paths", "capacity_bounds", "required_predecessor_deliveries"):
            if node.get(key) is None:
                node.pop(key, None)
    return data
