"""V26 explicit compact capacities, single verifier encoding and hypothesis scope."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .hypothesis_sources import validate_graph, validate_paths
from .planner_dataflow import DataflowVerifierWire
from .planner_dataflow_v25 import (
    ACTION_PROFILES,
    OUTPUT_PROFILES,
    ActionProfile,
    CapacityChoice,
    CustomActionProfile,
    CustomOutputProfile,
    DataflowGraphWireV25,
    DataflowNodeWireV25,
    OutputProfile,
    ProfileModel,
)

OUTPUT_PROFILES_V26 = {
    **OUTPUT_PROFILES,
    "artifact_128_delivery_1024": (128, 1024),
    "artifact_512_delivery_1024": (512, 1024),
}
ACTION_PROFILES_V26 = {
    **ACTION_PROFILES,
    "bounded_test_receipts_2048": 2048,
    "bounded_test_receipts_3072": 3072,
}


class OutputProfileV26(OutputProfile):
    profile: Literal[
        "inline_1536",
        "inline_2048",
        "inline_6144",
        "artifact_2048_delivery_1536",
        "artifact_3072_delivery_1536",
        "artifact_128_delivery_1024",
        "artifact_512_delivery_1024",
    ] = Field(
        description="Complete canonical UTF-8 bytes, including all eight delivery fields. Multi-source analysis with findings/citations/uncertainty commonly needs inline_6144 (4-6 KiB); inline_1536/2048 suit short handoffs. Tiny artifacts may fit 128/512; otherwise select sufficient custom artifact and delivery bounds."
    )

    def bounds(self):
        artifact, delivery = OUTPUT_PROFILES_V26[self.profile]
        return {"max_artifact_bytes": artifact, "max_delivery_bytes": delivery}


class ActionProfileV26(ActionProfile):
    profile: Literal[
        "declared_sources_only_0",
        "small_receipts_1024",
        "bounded_test_receipts_2048",
        "bounded_test_receipts_3072",
        "bounded_test_receipts_4096",
    ] = Field(
        description="Explicit bound for all new complete tool results, including required directory_list, tests and hypothesis file_read. Zero covers declared prefetched sources only. No authority or default."
    )

    def allowance(self):
        return ACTION_PROFILES_V26[self.profile]


OutputCapacityV26 = Annotated[
    OutputProfileV26 | CustomOutputProfile, Field(discriminator="profile")
]
ActionEvidenceV26 = Annotated[
    ActionProfileV26 | CustomActionProfile, Field(discriminator="profile")
]


class DataflowNodeWireV26(DataflowNodeWireV25):
    # The path occurs once, in expected_artifact. No path-bearing verifier wire.
    success_criteria: list[DataflowVerifierWire] = Field(min_length=1, max_length=5)
    output_capacity: OutputCapacityV26
    action_evidence: ActionEvidenceV26
    hypothesis_read_paths: list[str] = Field(
        max_length=32,
        description="Exact relative workspace paths explicitly recovered in the current goal; [] otherwise. Unverified hypotheses only, never verified inventory or future artifacts. Positive action allowance and explicit file_read required. Core never prefetches these paths.",
    )

    def v23_data(self):
        value = super().v23_data()
        value.pop("hypothesis_read_paths")
        return value

    def v25_data(self):
        value = self.model_dump(exclude={"hypothesis_read_paths"})
        # Exact lookup of the selected explicit profile; no bounds are inferred.
        if self.output_capacity.profile not in OUTPUT_PROFILES:
            value["output_capacity"] = {
                "profile": "custom",
                **self.output_capacity.bounds(),
            }
        if self.action_evidence.profile not in ACTION_PROFILES:
            value["action_evidence"] = {
                "profile": "custom",
                "action_evidence_allowance_bytes": self.action_evidence.allowance(),
            }
        return value

    @model_validator(mode="after")
    def explicit_hypothesis_contract(self):
        validate_paths(self.hypothesis_read_paths)
        if self.hypothesis_read_paths and (
            "file_read" not in self.allowed_tools
            or self.action_evidence.allowance() <= 0
            or set(self.hypothesis_read_paths) & set(self.verified_input_paths)
        ):
            raise ValueError(
                "hypothesis_reads_require_separate_scope_explicit_file_read_and_action_budget"
            )
        return self


class DataflowGraphWireV26(ProfileModel):
    allocation_mode: Literal["core_measured"]
    nodes: list[DataflowNodeWireV26] = Field(min_length=1, max_length=8)

    def decoded(self):
        return DataflowGraphWireV25.model_validate(
            {
                "allocation_mode": self.allocation_mode,
                "nodes": [node.v25_data() for node in self.nodes],
            }
        )

    def proposal_data(self, **kwargs):
        graph = self.decoded().proposal_data(**kwargs)
        graph["capacity_contract"].update(
            {
                "planner_capacity_protocol": "v26",
                "planner_capacity_choices": {
                    node.key: {
                        "output_capacity": node.output_capacity.model_dump(),
                        "action_evidence": node.action_evidence.model_dump(),
                    }
                    for node in self.nodes
                },
                "hypothesis_read_paths": {
                    node.key: list(node.hypothesis_read_paths) for node in self.nodes
                },
            }
        )
        if kwargs.get("completed_nodes"):
            graph["capacity_contract"]["preserved_completed_keys"] = [
                node["key"] for node in kwargs["completed_nodes"]
            ]
        validate_graph(graph)
        return graph


class CapacityChoiceV26(CapacityChoice):
    output_capacity: OutputCapacityV26
    action_evidence: ActionEvidenceV26


def validate_capacity_choices_v26(graph):
    """Check original V26 choices against effective bytes, never resize them."""
    from .planner_dataflow_v25 import validate_capacity_choices

    contract = graph["capacity_contract"]
    choices = contract.get("planner_capacity_choices")
    if not isinstance(choices, dict):
        raise TypeError("v26_requires_explicit_capacity_choices")
    converted = {}
    for key, value in choices.items():
        choice = CapacityChoiceV26.model_validate(value)
        converted[key] = {
            "output_capacity": {"profile": "custom", **choice.output_capacity.bounds()},
            "action_evidence": {
                "profile": "custom",
                "action_evidence_allowance_bytes": choice.action_evidence.allowance(),
            },
        }
        if isinstance(choice.output_capacity, OutputProfileV26):
            selected = next((node for node in graph["nodes"] if node["key"] == key), None)
            if selected is None or choice.output_capacity.profile.startswith("inline_") != (
                selected.get("expected_artifact") is None
            ):
                raise ValueError("capacity_profile_must_match_explicit_artifact_contract")
    validate_capacity_choices(
        {
            **graph,
            "capacity_contract": {
                **{
                    key: value
                    for key, value in contract.items()
                    if key != "planner_capacity_protocol"
                },
                "planner_capacity_choices": converted,
            },
        }
    )
