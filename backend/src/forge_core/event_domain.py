"""Opaque delivery lease, never a model-provided authorization."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeliveryLease:
    consumer_id: str
    event_id: str
    seq: int
    token: str
    attempt: int


WORKER_COMMANDS = frozenset(
    {
        "worker.command.assign",
        "worker.command.cancel",
        "worker.command.pause",
        "worker.command.resume",
        "worker.command.request_status",
        "worker.command.request_revision",
    }
)
WORKER_FACTS = frozenset(
    {
        "worker.result",
        "worker.progress",
        "worker.blocked",
        "worker.failed",
        "worker.help_requested",
        "worker.artifact_ready",
        "worker.review_requested",
    }
)
URGENT_EVENTS = (
    "worker.blocked",
    "worker.failed",
    "worker.result",
    "worker.help_requested",
    "approval.requested",
    "worker.command.cancel",
)


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    seq: int
    event_type: str
    conversation_id: str
    quest_id: str | None
    worker_id: str | None
    task_node_id: str | None
    plan_id: str | None
    task_node_key: str | None
    correlation_id: str | None
    payload: dict[str, Any]

    @classmethod
    def from_row(cls, row, payload):
        if not isinstance(payload, dict):
            raise ValueError("Invalid event payload")  # noqa: TRY004 - invalid persisted event
        node, correlation = payload.get("task_node_id"), payload.get("correlation_id")
        plan, node_key = payload.get("plan_id"), payload.get("task_node_key")
        if (
            any(
                value is not None and (not isinstance(value, str) or not value or len(value) > 256)
                for value in (node, correlation, plan, node_key)
            )
            or (node_key is not None and plan is None)
            or (
                row["event_type"] in WORKER_COMMANDS | WORKER_FACTS
                and plan is not None
                and node_key is None
            )
            or (node is not None and (plan is None or node != node_key))
        ):
            raise ValueError("Invalid event references")
        if row["event_type"] in WORKER_COMMANDS | WORKER_FACTS and (
            not row["quest_id"] or not row["worker_id"]
        ):
            raise ValueError("Worker event is missing task identity")
        return cls(
            row["event_id"],
            row["seq"],
            row["event_type"],
            row["conversation_id"],
            row["quest_id"],
            row["worker_id"],
            node,
            plan,
            node_key,
            correlation,
            payload,
        )
