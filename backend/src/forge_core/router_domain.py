from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RouterDecision(BaseModel):
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
    action: Literal["silent", "update_user"]
    reply: str | None
