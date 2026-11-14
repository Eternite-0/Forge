from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskNodeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    objective: str = Field(min_length=1, max_length=2000)
    success_criteria: list[str] = Field(min_length=1, max_length=20)
    budget_tokens: int = Field(ge=1, le=100000)
    required_capabilities: dict[str, float] = Field(default_factory=dict, max_length=32)
    expected_artifact: str | None = Field(default=None, max_length=2000)
    max_attempts: int = Field(default=1, ge=1, le=10)
    allowed_tools: list[str] = Field(default_factory=list, max_length=32)

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

    @model_validator(mode="after")
    def graph_valid(self):
        if (self.max_cost_microunits is None) != (self.cost_currency is None):
            raise ValueError("A cost limit requires an explicit currency")
        keys = [node.key for node in self.nodes]
        if (
            len(keys) != len(set(keys))
            or sum(node.budget_tokens for node in self.nodes) > self.max_total_tokens
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
        return self


class CapabilityWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    minimum: float = Field(ge=0, le=100, allow_inf_nan=False)


class NodeWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str
    objective: str
    success_criteria: list[str]
    budget_tokens: int
    required_capabilities: list[CapabilityWire]
    expected_artifact: str | None
    max_attempts: int
    allowed_tools: list[str]


class GraphWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nodes: list[NodeWire]
    edges: list[TaskEdgeProposal]
    max_total_tokens: int
    max_cost_microunits: int | None
    cost_currency: str | None
    max_duration_seconds: int | None

    def proposal(self):
        data = self.model_dump()
        for node in data["nodes"]:
            capabilities = node["required_capabilities"]
            if len({item["name"] for item in capabilities}) != len(capabilities):
                raise ValueError("Duplicate capability")
            node["required_capabilities"] = {item["name"]: item["minimum"] for item in capabilities}
        return GraphProposal.model_validate(data)
