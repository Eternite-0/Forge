"""Explicit model-selected capacity profiles; exact decoding to the V23 contract."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .graph_domain import CapabilityWire, _strict_wire_schema
from .planner_dataflow import DataflowDependencyWire
from .planner_dataflow_v23 import DataflowGraphWireV23, DataflowNodeWireV23, DataflowVerifierWireV23

OUTPUT_PROFILES = {
    "inline_1536": (1, 1536),
    "inline_2048": (1, 2048),
    "inline_6144": (1, 6144),
    "artifact_2048_delivery_1536": (2048, 1536),
    "artifact_3072_delivery_1536": (3072, 1536),
}
ACTION_PROFILES = {
    "declared_sources_only_0": 0,
    "small_receipts_1024": 1024,
    "bounded_test_receipts_4096": 4096,
}


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=_strict_wire_schema)


class OutputProfile(ProfileModel):
    profile: Literal[
        "inline_1536",
        "inline_2048",
        "inline_6144",
        "artifact_2048_delivery_1536",
        "artifact_3072_delivery_1536",
    ] = Field(
        description="Canonical UTF-8 bytes for the complete eight-field delivery. inline_1536/2048 suit short handoffs; multi-source findings, citations and uncertainty commonly need inline_6144 (4-6 KiB). Inline creates no artifact; choose sufficient custom bounds for other complete results. No default."
    )

    def bounds(self):
        artifact, delivery = OUTPUT_PROFILES[self.profile]
        return {"max_artifact_bytes": artifact, "max_delivery_bytes": delivery}


class CustomOutputProfile(ProfileModel):
    profile: Literal["custom"]
    max_artifact_bytes: int = Field(ge=1, le=32768)
    max_delivery_bytes: int = Field(ge=1, le=32768)

    def bounds(self):
        return self.model_dump(exclude={"profile"})


class ActionProfile(ProfileModel):
    profile: Literal[
        "declared_sources_only_0", "small_receipts_1024", "bounded_test_receipts_4096"
    ] = Field(
        description="New receipt output beyond complete declared initial sources: 0 for inline declared-source analysis, 1024 for small receipts, 4096 for bounded numeric/test receipts, or sufficient custom. No tool authority or default."
    )

    def allowance(self):
        return ACTION_PROFILES[self.profile]


class CustomActionProfile(ProfileModel):
    profile: Literal["custom"]
    action_evidence_allowance_bytes: int = Field(ge=0, le=32768)

    def allowance(self):
        return self.action_evidence_allowance_bytes


OutputCapacity = Annotated[OutputProfile | CustomOutputProfile, Field(discriminator="profile")]
ActionEvidence = Annotated[ActionProfile | CustomActionProfile, Field(discriminator="profile")]


class DataflowNodeWireV25(ProfileModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    objective: str = Field(min_length=1, max_length=2000)
    success_criteria: list[DataflowVerifierWireV23] = Field(min_length=1, max_length=5)
    required_capabilities: list[CapabilityWire]
    expected_artifact: str | None = Field(max_length=2000)
    max_attempts: int = Field(ge=1, le=10)
    allowed_tools: list[str] = Field(max_length=32)
    verified_input_paths: list[str] = Field(max_length=32)
    predecessors: list[DataflowDependencyWire] = Field(max_length=8)
    output_capacity: OutputCapacity
    action_evidence: ActionEvidence

    def v23_data(self):
        value = self.model_dump(exclude={"output_capacity", "action_evidence"})
        value["capacity_bounds"] = {
            **self.output_capacity.bounds(),
            "action_evidence_allowance_bytes": self.action_evidence.allowance(),
        }
        return value

    @model_validator(mode="after")
    def exact_semantics(self):
        if isinstance(self.output_capacity, OutputProfile):
            inline = self.output_capacity.profile.startswith("inline_")
            if inline != (self.expected_artifact is None):
                raise ValueError("capacity_profile_must_match_explicit_artifact_contract")
        DataflowNodeWireV23.model_validate(self.v23_data())
        return self


class DataflowGraphWireV25(ProfileModel):
    allocation_mode: Literal["core_measured"]
    nodes: list[DataflowNodeWireV25] = Field(min_length=1, max_length=8)

    def decoded(self):
        return DataflowGraphWireV23.model_validate(
            {
                "allocation_mode": self.allocation_mode,
                "nodes": [node.v23_data() for node in self.nodes],
            }
        )

    def proposal_data(self, **kwargs):
        graph = self.decoded().proposal_data(**kwargs)
        graph["capacity_contract"]["planner_capacity_choices"] = {
            node.key: {
                "output_capacity": node.output_capacity.model_dump(),
                "action_evidence": node.action_evidence.model_dump(),
            }
            for node in self.nodes
        }
        return graph


class CapacityChoice(ProfileModel):
    output_capacity: OutputCapacity
    action_evidence: ActionEvidence


def validate_capacity_choices(graph):
    """Provenance must match effective bounds; it grants no additional authority."""
    contract = graph.get("capacity_contract") or {}
    if contract.get("planner_capacity_protocol") == "v26":
        from .planner_dataflow_v26 import validate_capacity_choices_v26

        return validate_capacity_choices_v26(graph)
    choices = contract.get("planner_capacity_choices")
    if choices is None:
        return
    completed = set(contract.get("preserved_completed_keys") or [])
    pending = {node["key"]: node for node in graph["nodes"] if node["key"] not in completed}
    if not isinstance(choices, dict) or set(choices) != set(pending):
        raise ValueError("planner_capacity_choices_require_exact_pending_nodes")
    allowances = contract.get("action_evidence_allowances")
    if not isinstance(allowances, dict):
        raise TypeError("planner_capacity_choices_require_original_allowances")
    for key, selected in choices.items():
        choice = CapacityChoice.model_validate(selected)
        bounds = pending[key].get("capacity_bounds") or {}
        if isinstance(choice.output_capacity, OutputProfile):
            inline = choice.output_capacity.profile.startswith("inline_")
            if inline != (pending[key].get("expected_artifact") is None):
                raise ValueError("capacity_profile_must_match_explicit_artifact_contract")
        if (
            any(
                bounds.get(field) != value
                for field, value in choice.output_capacity.bounds().items()
            )
            or allowances.get(key) != choice.action_evidence.allowance()
        ):
            raise ValueError("planner_capacity_choice_differs_from_effective_bounds")
