"""V21 semantic wire: one explicit declaration per scheduled/data dependency."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .graph_domain import (
    CapabilityWire,
    OutputCapacityBounds,
    _strict_wire_schema,
    success_ancestors,
)


class DataflowVerifierWire(BaseModel):
    """Artifact verifiers target only the node's one explicitly selected output."""

    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)
    kind: Literal[
        "delivery_review",
        "json_review:@delivery",
        "json_review",
        "json_valid",
        "semantic_numeric:scheduler-v1",
    ]

    def criterion(self, expected_artifact):
        if self.kind in {"delivery_review", "json_review:@delivery"}:
            return self.kind
        if expected_artifact is None:
            raise ValueError("artifact_verifier_requires_explicit_expected_artifact")
        return self.kind + ":" + expected_artifact


class DataflowDependencyWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)
    node_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    condition: Literal["success", "failure", "always"]
    read_artifact: bool
    delivery_sources: list[str] = Field(max_length=8)

    @model_validator(mode="after")
    def complete_output_reads_require_success(self):
        if (self.read_artifact or self.delivery_sources) and self.condition != "success":
            raise ValueError("dataflow_output_reads_require_success_dependency")
        if len(self.delivery_sources) != len(set(self.delivery_sources)):
            raise ValueError("duplicate_dataflow_delivery_source")
        return self


class DataflowNodeWire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)
    key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    objective: str = Field(min_length=1, max_length=2000)
    success_criteria: list[DataflowVerifierWire] = Field(min_length=1, max_length=5)
    required_capabilities: list[CapabilityWire]
    expected_artifact: str | None = Field(max_length=2000)
    max_attempts: int = Field(ge=1, le=10)
    allowed_tools: list[str] = Field(max_length=32)
    verified_input_paths: list[str] = Field(max_length=32)
    predecessors: list[DataflowDependencyWire] = Field(max_length=8)
    capacity_bounds: OutputCapacityBounds

    @model_validator(mode="after")
    def explicit_contract_tools(self):
        validate_source_tools(self)
        artifact_criteria = [
            item
            for item in self.success_criteria
            if item.kind not in {"delivery_review", "json_review:@delivery"}
        ]
        if artifact_criteria and self.expected_artifact is None:
            raise ValueError("artifact_verifier_requires_explicit_expected_artifact")
        if artifact_criteria and "file_write" not in self.allowed_tools:
            raise ValueError("artifact_verifier_requires_file_write_on_same_node")
        return self


class DataflowGraphWire(BaseModel):
    """The model supplies a topological order; Core never sorts or repairs it."""

    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)
    allocation_mode: Literal["core_measured"]
    nodes: list[DataflowNodeWire] = Field(min_length=1, max_length=8)

    def proposal_data(
        self,
        *,
        resource_limits,
        coordination_reserve_tokens,
        verified_paths,
        completed_nodes=(),
        completed_edges=(),
    ):
        selected = compile_source_dataflow(
            self.nodes,
            verified_paths=verified_paths,
            completed_nodes=completed_nodes,
            completed_edges=completed_edges,
        )
        sources = {node["key"]: node for node in selected["nodes"]}
        nodes = []
        for wire_node in self.nodes:
            node = wire_node.model_dump(exclude={"verified_input_paths", "predecessors"})
            capabilities = node["required_capabilities"]
            if len({item["name"] for item in capabilities}) != len(capabilities):
                raise ValueError("duplicate_dataflow_capability")
            node["required_capabilities"] = {item["name"]: item["minimum"] for item in capabilities}
            node["success_criteria"] = [
                item.criterion(wire_node.expected_artifact) for item in wire_node.success_criteria
            ]
            node.update(sources[wire_node.key])
            node["budget_tokens"] = 2049
            nodes.append(node)
        nodes.extend(completed_nodes)
        edges = selected["edges"]
        return {
            "nodes": nodes,
            "edges": edges,
            **resource_limits,
            "coordination_reserve_tokens": coordination_reserve_tokens,
        }


def validate_source_tools(self):
    from .tool_domain import CONTRACTS

    known_tools = {item.name for item in CONTRACTS if item.risk in {"R0", "R1"}}
    if (
        len(self.allowed_tools) != len(set(self.allowed_tools))
        or set(self.allowed_tools) - known_tools
    ):
        raise ValueError("unknown_or_duplicate_dataflow_tool")
    if len(self.verified_input_paths) != len(set(self.verified_input_paths)):
        raise ValueError("duplicate_verified_input_path")
    if self.verified_input_paths and "file_read" not in self.allowed_tools:
        raise ValueError("verified_inputs_require_file_read")
    if (
        any(item.read_artifact for item in self.predecessors)
        and "file_read" not in self.allowed_tools
    ):
        raise ValueError("predecessor_artifacts_require_file_read")
    if (
        any(item.delivery_sources for item in self.predecessors)
        and "worker_read_completed_delivery" not in self.allowed_tools
    ):
        raise ValueError("completed_deliveries_require_explicit_read_tool")
    return self


def compile_source_dataflow(wire_nodes, *, verified_paths, completed_nodes=(), completed_edges=()):
    verified = set(verified_paths)
    if any(set(node.verified_input_paths) - verified for node in wire_nodes):
        from .capacity_preflight import CapacityPreflightError
        from .planner_diagnostics import semantic_graph_diagnostics

        report = semantic_graph_diagnostics(
            {
                "nodes": [
                    {"key": node.key, "required_input_paths": node.verified_input_paths}
                    for node in wire_nodes
                ],
                "edges": [],
            },
            verified_paths=list(verified_paths),
        )
        report["scope"] = "v21_verified_inventory_selection"
        for item in report["invalid_input_paths"]:
            for key in ("self_output", "actual_producers", "missing_direct_success_edge"):
                item.pop(key, None)
            item["source_field"] = "verified_input_paths"
        raise CapacityPreflightError(
            "V21 verified_input_paths must select verified inventory files only",
            {"semantic_diagnostics": report},
        )
    completed = {node["key"]: node for node in completed_nodes}
    keys = [node.key for node in wire_nodes]
    if (
        len(completed) != len(completed_nodes)
        or len(keys) != len(set(keys))
        or set(keys) & completed.keys()
    ):
        raise ValueError("duplicate_dataflow_node")
    if len(keys) + len(completed) > 8:
        raise ValueError("dataflow_node_limit_exceeded")
    if any(
        edge["source"] not in completed or edge["target"] not in completed
        for edge in completed_edges
    ):
        raise ValueError("completed_edges_must_preserve_only_completed_contracts")
    known = dict(completed)
    nodes, edges = [], [dict(edge) for edge in completed_edges]
    for wire_node in wire_nodes:
        node = wire_node.model_dump(
            include={"key", "expected_artifact", "allowed_tools", "capacity_bounds"}
        )
        paths, deliveries, parents = list(wire_node.verified_input_paths), [], set()
        for dependency in wire_node.predecessors:
            key = dependency.node_key
            if key == wire_node.key:
                raise ValueError("dataflow_self_dependency")
            if key not in known:
                raise ValueError("dataflow_producer_must_be_completed_or_earlier_in_model_order")
            if key in parents:
                raise ValueError("duplicate_dataflow_predecessor")
            parents.add(key)
            edges.append(
                {
                    "source": key,
                    "target": wire_node.key,
                    "condition": dependency.condition,
                }
            )
            if dependency.read_artifact:
                artifact = known[key].get("expected_artifact")
                if artifact is None:
                    raise ValueError("dataflow_producer_has_no_expected_artifact")
                paths.append(artifact)
            eligible_deliveries = success_ancestors(edges, key) | {key}
            if set(dependency.delivery_sources) - eligible_deliveries:
                raise ValueError("dataflow_delivery_source_must_be_success_ancestor_of_dependency")
            deliveries.extend(dependency.delivery_sources)
        if len(paths) != len(set(paths)) or len(deliveries) != len(set(deliveries)):
            raise ValueError("duplicate_dataflow_source_selection")
        if len(paths) > 32 or len(deliveries) > 8 or len(edges) > 16:
            raise ValueError("dataflow_source_or_edge_limit_exceeded")
        node["required_input_paths"] = paths
        node["required_predecessor_deliveries"] = deliveries
        nodes.append(node)
        known[wire_node.key] = node
    nodes.extend(completed_nodes)
    return {"nodes": nodes, "edges": edges}
