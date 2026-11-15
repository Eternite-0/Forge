import json
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class IntentType(str, Enum):
    CHAT = "chat"
    CREATE_QUEST = "create_quest"
    CONTINUE_QUEST = "continue_quest"
    UPDATE_QUEST = "update_quest"
    STATUS_QUERY = "status_query"
    APPROVAL_RESPONSE = "approval_response"
    AMBIGUOUS = "ambiguous"


class QuestStatus(str, Enum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    PAUSED = "paused"
    NEEDS_INPUT = "needs_input"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


ALLOWED_QUEST_TRANSITIONS = {
    QuestStatus.PROPOSED: {
        QuestStatus.AWAITING_APPROVAL,
        QuestStatus.REJECTED,
        QuestStatus.CANCELLED,
    },
    QuestStatus.AWAITING_APPROVAL: {
        QuestStatus.APPROVED,
        QuestStatus.REJECTED,
        QuestStatus.CANCELLED,
    },
    QuestStatus.APPROVED: {QuestStatus.QUEUED, QuestStatus.PAUSED, QuestStatus.CANCELLED},
    QuestStatus.QUEUED: {QuestStatus.RUNNING, QuestStatus.PAUSED, QuestStatus.CANCELLED},
    QuestStatus.RUNNING: {
        QuestStatus.BLOCKED,
        QuestStatus.PAUSED,
        QuestStatus.NEEDS_INPUT,
        QuestStatus.REVIEWING,
        QuestStatus.COMPLETED,
        QuestStatus.FAILED,
        QuestStatus.CANCELLED,
    },
    QuestStatus.BLOCKED: {
        QuestStatus.QUEUED,
        QuestStatus.RUNNING,
        QuestStatus.CANCELLED,
        QuestStatus.FAILED,
    },
    QuestStatus.PAUSED: {QuestStatus.QUEUED, QuestStatus.CANCELLED},
    QuestStatus.NEEDS_INPUT: {QuestStatus.QUEUED, QuestStatus.CANCELLED},
    QuestStatus.REVIEWING: {QuestStatus.RUNNING, QuestStatus.COMPLETED, QuestStatus.FAILED},
    QuestStatus.COMPLETED: set(),
    QuestStatus.FAILED: set(),
    QuestStatus.CANCELLED: set(),
    QuestStatus.REJECTED: set(),
}


class DomainError(ValueError):
    pass


class QuestVersionConflict(DomainError):
    pass


def validate_quest_transition(current: QuestStatus, target: QuestStatus) -> None:
    if target not in ALLOWED_QUEST_TRANSITIONS[current]:
        raise DomainError(f"Illegal Quest transition: {current.value} -> {target.value}")


class ConversationMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    role: str
    content: str
    created_at: str = Field(default_factory=now_iso)
    metadata: dict = Field(default_factory=dict)


class Quest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("quest"))
    title: str
    goal: str
    status: QuestStatus = QuestStatus.PROPOSED
    version: int = Field(default=0, ge=0)
    priority: int = Field(default=3, ge=1, le=5)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    metadata: dict = Field(default_factory=dict)


class WorkerSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("worker"))
    quest_id: str
    role: str
    model_id: str | None = None
    status: str = "proposed"
    hidden: bool = True
    created_at: str = Field(default_factory=now_iso)
    metadata: dict = Field(default_factory=dict)


class ForgeEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    event_type: str
    conversation_id: str = "main"
    quest_id: str | None = None
    worker_id: str | None = None
    payload: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


class Measurement(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    value: float = Field(ge=0, le=100)
    source: str = Field(min_length=1)
    benchmark_version: str | None = None
    n_samples: int | None = Field(default=None, ge=1)
    observed_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class PerformanceMeasurement(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    value: float = Field(ge=0)
    source: str = Field(min_length=1)
    benchmark_version: str | None = None
    n_samples: int | None = Field(default=None, ge=1)
    observed_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class ProtocolSupport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supported: bool = Field(strict=True)
    source: str = Field(min_length=1)


Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Capability = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
CAPABILITY_NAMES = (
    "mathematics",
    "coding",
    "architecture",
    "research",
    "planning",
    "design",
    "vision",
    "writing",
    "tool_calling",
    "instruction_following",
)


class ModelProfile(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    id: str = Field(min_length=1, max_length=512)
    provider: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=512)
    enabled: bool = True
    model_id: str | None = None
    version: str | None = None
    endpoint: str | None = None
    capabilities: dict[str, Capability] = Field(default_factory=dict)
    capability_measurements: dict[str, Measurement] = Field(default_factory=dict)
    performance_measurements: dict[str, PerformanceMeasurement] = Field(default_factory=dict)
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    tool_calling_support: ProtocolSupport | None = None
    structured_output_support: ProtocolSupport | None = None
    input_cost_per_million: Nonnegative | None = None
    output_cost_per_million: Nonnegative | None = None
    cached_input_cost_per_million: Nonnegative | None = None
    currency: str | None = None
    tokens_per_second: Nonnegative | None = None
    ttft_seconds: Nonnegative | None = None
    latency_p50_seconds: Nonnegative | None = None
    latency_p95_seconds: Nonnegative | None = None
    reliability: float | None = Field(default=None, ge=0, le=1)
    timeout_rate: float | None = Field(default=None, ge=0, le=1)
    health: str | None = None
    custom_attributes: dict = Field(default_factory=dict)
    quarantined_raw: str | None = None
    quarantine_reason: str | None = None
    provenance: dict = Field(default_factory=dict)

    @field_validator("endpoint")
    @classmethod
    def endpoint_without_credentials(cls, value):
        if value is not None:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "Endpoint must be an HTTP URL without credentials, query or fragment"
                )
        return value

    @model_validator(mode="after")
    def legacy_capabilities_are_overrides(self):
        try:
            json.dumps([self.custom_attributes, self.provenance], allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Custom attributes and provenance must be finite JSON") from exc
        for name, value in self.capabilities.items():
            if (
                name in self.capability_measurements
                and self.capability_measurements[name].value != value
            ):
                raise ValueError("Capability value conflicts with its measurement")
            if name not in self.capability_measurements:
                self.capability_measurements[name] = Measurement(
                    value=value, source="user_override"
                )
        for name in (
            "tokens_per_second",
            "ttft_seconds",
            "latency_p50_seconds",
            "latency_p95_seconds",
            "reliability",
            "timeout_rate",
        ):
            value = getattr(self, name)
            measurement = self.performance_measurements.get(name)
            if (
                measurement is not None
                and name in {"reliability", "timeout_rate"}
                and measurement.value > 1
            ):
                raise ValueError("Reliability and timeout rate must not exceed 1")
            if measurement is not None and value is not None and measurement.value != value:
                raise ValueError("Performance value conflicts with its measurement")
            if value is not None and name not in self.performance_measurements:
                self.performance_measurements[name] = PerformanceMeasurement(
                    value=value, source="user_override"
                )
        return self


class RoleTemplate(BaseModel):
    id: str
    name: str
    description: str
    required_capabilities: dict[str, float] = Field(default_factory=dict)
    default_tools: list[str] = Field(default_factory=list)
