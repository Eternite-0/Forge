from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IndependentDispatch(BaseModel):
    """One coarse task; its Worker owns detailed planning and execution."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    objective: str = Field(min_length=1, max_length=1200)
    role: Literal[
        "research", "math", "planner", "architect", "coding", "design", "experiment", "reviewer"
    ]


class RouterDecision(BaseModel):
    """Structured Router decision protocol.

    The first six fields are the original contract. The remaining optional fields
    carry the Phase 2 agent-loop reasoning: why the decision was made, how sure the
    Router is, which user constraints it extracted, what should happen next, and
    whether it needs the user. They are optional so an older/limited model can
    still satisfy the strict schema; Core never invents values for them.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Literal[
        "chat",
        "create_quest",
        "continue_quest",
        "update_quest",
        "status_query",
        "approval_response",
        "ambiguous",
    ]
    reply: str = Field(min_length=1, max_length=10000)
    title: str | None
    goal: str | None
    quest_id: str | None
    candidates: list[str] = Field(max_length=100)
    rationale: str | None = Field(default=None, max_length=2000)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    priority: int | None = Field(default=None, ge=1, le=5)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    next_action: str | None = Field(default=None, max_length=500)
    needs_user_help: bool | None = None
    task_kind: (
        Literal[
            "chat",
            "single_step",
            "multi_step",
            "long_research",
            "software",
            "multimodal",
            "multi_quest",
        ]
        | None
    ) = None
    blockers: list[str] = Field(default_factory=list, max_length=10)
    recalled_requirement_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Exact IDs of presented memory items explicitly selected as requirements for a new "
            "or updated Quest. Core carries their full statements and original references as "
            "hypotheses, never as verified file contents."
        ),
    )
    independent_dispatches: list[IndependentDispatch] = Field(
        default_factory=list,
        max_length=4,
        description="Optional independent Worker objectives and roles for create_quest only.",
    )

    @model_validator(mode="after")
    def dispatch_new_tasks_only(self):
        if self.independent_dispatches and self.intent != "create_quest":
            raise ValueError("Independent dispatches require a new task")
        return self


class TaskCapsule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    goal: str = ""
    quest_id: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    next_action: str = ""
    source_event_cursor: int = Field(default=0, ge=0)


class NoticeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["silent", "update_user", "replan_remaining"]
    reply: str | None
