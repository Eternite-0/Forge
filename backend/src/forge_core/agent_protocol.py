"""Typed projection of durable Core events into Agent coordination messages.

The append-only event/outbox store is the transport. This projection adds no
separate chat, mutation authority or model-to-model task editing channel.
"""

import json
from dataclasses import dataclass
from enum import StrEnum

from .domain import DomainError


class AgentMessageType(StrEnum):
    TASK_ASSIGNMENT = "TASK_ASSIGNMENT"
    TASK_ACCEPTED = "TASK_ACCEPTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    HELP_REQUEST = "HELP_REQUEST"
    REVIEW_REQUEST = "REVIEW_REQUEST"
    REVISION_REQUEST = "REVISION_REQUEST"
    DEPENDENCY_BLOCKED = "DEPENDENCY_BLOCKED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    BUDGET_WARNING = "BUDGET_WARNING"
    AGENT_FAILED = "AGENT_FAILED"


_TYPES = {
    "worker.proposed": AgentMessageType.TASK_ASSIGNMENT,
    "worker.accepted": AgentMessageType.TASK_ACCEPTED,
    "task.verified": AgentMessageType.TASK_COMPLETED,
    "worker.help_requested": AgentMessageType.HELP_REQUEST,
    "worker.result": AgentMessageType.REVIEW_REQUEST,
    "worker.review_requested": AgentMessageType.REVIEW_REQUEST,
    "worker.revision_requested": AgentMessageType.REVISION_REQUEST,
    "worker.blocked": AgentMessageType.DEPENDENCY_BLOCKED,
    "worker.failed": AgentMessageType.AGENT_FAILED,
    "review.blocked": AgentMessageType.DEPENDENCY_BLOCKED,
}
COORDINATION_EVENTS = frozenset(_TYPES)


def _text(value, maximum=256):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def validate_payload(event_type, payload):
    """Validate explicit v2 native contracts; retain labeled historic v1 identity.

    The version describes payload shape only. It grants no execution authority;
    Dispatcher still binds every native message to the durable approved assignment.
    Historic events are never rewritten to imply they carried a newer contract.
    """
    if not isinstance(payload, dict):
        raise DomainError("Invalid Agent coordination payload")
    version = payload.get("protocol_version", 1)
    if type(version) is not int or version not in {1, 2}:
        raise DomainError("Unsupported Agent coordination contract")
    if version == 1:
        return version
    try:
        encoded_bytes = len(
            json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise DomainError("Agent coordination payload must be finite JSON") from exc
    if encoded_bytes > 196608:
        raise DomainError("Agent coordination payload exceeds bound")
    if not _text(payload.get("plan_id")) or not _text(payload.get("task_node_key")):
        raise DomainError("Agent contract requires plan and node")
    if event_type != "task.verified" and not _text(payload.get("run_id")):
        raise DomainError("Agent contract requires run identity")
    if event_type == "worker.proposed":
        if payload.get("execution_mode") not in {"live", "mock"}:
            raise DomainError("Assignment requires execution provenance")
    elif event_type == "worker.accepted":
        if type(payload.get("generation")) is not int or payload["generation"] < 1:
            raise DomainError("Task acceptance requires a positive generation")
    elif event_type == "worker.help_requested":
        if (
            not _text(payload.get("requested_role"), 128)
            or not _text(payload.get("reason"), 2048)
            or payload.get("requires_approved_reassignment") is not True
        ):
            raise DomainError("Help requires a bounded role, reason and approval boundary")
    elif event_type == "worker.revision_requested":
        goals = payload.get("revision_goals")
        if (
            not _text(payload.get("review_event_id"))
            or not isinstance(goals, list)
            or not 1 <= len(goals) <= 8
            or any(not _text(goal, 2048) for goal in goals)
            or type(payload.get("repair_attempt")) is not int
            or payload["repair_attempt"] < 1
        ):
            raise DomainError("Revision requires source review, bounded goals and repair count")
    elif event_type == "worker.result":
        if not _text(payload.get("claim"), 131072) or payload.get("execution_mode") not in {
            "live",
            "mock",
        }:
            raise DomainError("Review request requires bounded delivery and provenance")
        fields = payload.get("delivery_fields_supplied")
        if fields is not None and (
            not isinstance(fields, list)
            or len(fields) > 8
            or any(not _text(field, 64) for field in fields)
            or len(set(fields)) != len(fields)
        ):
            raise DomainError("Invalid delivery field declaration")
    elif event_type == "worker.review_requested":
        if not _text(payload.get("result_event_id")):
            raise DomainError("Review request requires its durable delivery event")
    elif event_type in {"worker.blocked", "worker.failed", "review.blocked"}:
        if not _text(payload.get("reason_code"), 128):
            raise DomainError("Agent failure requires a bounded reason code")
        category = payload.get("failure_category")
        if category is not None and category not in {
            "code",
            "tool",
            "provider",
            "task_contract",
            "budget",
            "dependency",
            "authorization",
            "external_resource",
            "runtime",
        }:
            raise DomainError("Unknown Agent failure category")
        if (
            event_type == "review.blocked"
            and payload.get("result_event_id") is not None
            and not _text(payload["result_event_id"])
        ):
            raise DomainError("Review block has an invalid delivery event")
    elif event_type != "task.verified":
        raise DomainError("Unsupported native Agent message")
    return version


@dataclass(frozen=True)
class AgentMessage:
    message_id: str
    message_type: AgentMessageType
    sender_id: str
    receiver_id: str
    quest_id: str
    plan_id: str
    task_node_key: str
    correlation_id: str
    payload: dict
    contract_version: int = 1
    payload_contract: str = "legacy_identity_only"

    @classmethod
    def from_event(cls, event, payload):
        kind = _TYPES.get(event["event_type"])
        if kind is None:
            return None
        version = validate_payload(event["event_type"], payload)
        if event["event_type"] == "worker.blocked":
            category = payload.get("failure_category")
            if category == "provider":
                kind = AgentMessageType.MODEL_UNAVAILABLE
            elif category == "budget":
                kind = AgentMessageType.BUDGET_WARNING
        identifiers = (
            event["id"],
            event["quest_id"],
            event["worker_id"],
            payload.get("plan_id"),
            payload.get("task_node_key"),
        )
        if any(not isinstance(value, str) or not 1 <= len(value) <= 256 for value in identifiers):
            raise DomainError("Agent coordination requires durable task identity")
        if payload.get("message_type", kind) != kind:
            raise DomainError("Agent message type differs from its durable event")
        if "correlation_id" in payload and not _text(payload["correlation_id"]):
            raise DomainError("Invalid Agent correlation identity")
        correlation = payload.get("correlation_id") or payload.get("run_id") or event["id"]
        if not _text(correlation):
            raise DomainError("Invalid Agent correlation identity")
        assignment = kind == AgentMessageType.TASK_ASSIGNMENT
        return cls(
            event["id"],
            kind,
            "router" if assignment else event["worker_id"],
            event["worker_id"] if assignment else "router",
            event["quest_id"],
            payload["plan_id"],
            payload["task_node_key"],
            correlation,
            payload,
            version,
            "validated_v2" if version == 2 else "legacy_identity_only",
        )
