"""Evidence types are distinct from model assertions and cannot be chosen by a model as trust."""

from enum import Enum


class EvidenceKind(str, Enum):
    MODEL_CLAIM = "model_claim"
    TOOL = "tool"
    TEST = "test"
    ARTIFACT = "artifact"
    APPROVAL = "approval"
    REVIEW = "review"
