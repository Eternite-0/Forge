"""Explicit V23 lossless verifier wire; V21 remains unchanged."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .graph_domain import OutputCapacityBounds
from .planner_dataflow import DataflowGraphWire, DataflowNodeWire, DataflowVerifierWire

ARTIFACT_KINDS = ("json_review", "json_valid", "semantic_numeric:scheduler-v1")
INLINE_KINDS = ("delivery_review", "json_review:@delivery")
CanonicalArtifactKind = Annotated[
    str,
    Field(
        min_length=1,
        max_length=2100,
        pattern=r"^(?:json_review|json_valid|semantic_numeric:scheduler-v1):[^\r\n]+$",
    ),
]


class DataflowVerifierWireV23(DataflowVerifierWire):
    kind: (
        Literal[
            "delivery_review",
            "json_review:@delivery",
            "json_review",
            "json_valid",
            "semantic_numeric:scheduler-v1",
        ]
        | CanonicalArtifactKind
    )

    def criterion(self, expected_artifact):
        if self.kind in INLINE_KINDS or self.kind in ARTIFACT_KINDS:
            return super().criterion(expected_artifact)
        if expected_artifact is None:
            raise ValueError("canonical_artifact_verifier_requires_expected_artifact")
        # Exact equality is the entire decoder. No path cleanup, substring,
        # prefix stripping, case folding, default verifier or kind fallback.
        matches = [
            prefix for prefix in ARTIFACT_KINDS if self.kind == prefix + ":" + expected_artifact
        ]
        if len(matches) != 1:
            raise ValueError("canonical_artifact_verifier_path_must_equal_expected_artifact")
        return self.kind


class OutputCapacityBoundsV23(OutputCapacityBounds):
    action_evidence_allowance_bytes: int = Field(
        ge=0,
        le=32768,
        description="Contribution to total source-receipt sizing for planned node action evidence; not an independent cap, authority or executed evidence. Read-only may choose 0.",
    )


class DataflowNodeWireV23(DataflowNodeWire):
    success_criteria: list[DataflowVerifierWireV23] = Field(min_length=1, max_length=5)
    capacity_bounds: OutputCapacityBoundsV23

    @model_validator(mode="after")
    def exact_verifier_encoding(self):
        compiled = [item.criterion(self.expected_artifact) for item in self.success_criteria]
        if len(compiled) != len(set(compiled)):
            raise ValueError("duplicate_semantic_verifier_selection")
        artifact = [item.kind for item in self.success_criteria if item.kind not in INLINE_KINDS]
        if any(item in ARTIFACT_KINDS for item in artifact) and any(
            item not in ARTIFACT_KINDS for item in artifact
        ):
            raise ValueError("mixed_artifact_verifier_encodings")
        return self


class DataflowGraphWireV23(DataflowGraphWire):
    nodes: list[DataflowNodeWireV23] = Field(min_length=1, max_length=8)

    def proposal_data(self, **kwargs):
        result = super().proposal_data(**kwargs)
        pending = {item.key for item in self.nodes}
        allowances = {
            node["key"]: node["capacity_bounds"].pop("action_evidence_allowance_bytes")
            for node in result["nodes"]
            if node["key"] in pending
        }
        result["capacity_contract"] = {
            "input_acquisition_policy": "core-declared-source-prefetch-v1",
            "action_evidence_allowances": allowances,
        }
        return result
