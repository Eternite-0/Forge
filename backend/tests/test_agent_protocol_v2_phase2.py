"""Native ACP v2 validates message-specific contracts without rewriting history."""

import copy
import json

import pytest

from forge_core.agent_protocol import AgentMessage, AgentMessageType
from forge_core.domain import DomainError


def event(kind):
    return {"id": "event-1", "event_type": kind, "quest_id": "quest-1", "worker_id": "worker-1"}


def payload(**extra):
    return {
        "protocol_version": 2,
        "plan_id": "plan-1",
        "task_node_key": "node-1",
        "run_id": "run-1",
        **extra,
    }


CASES = (
    ("worker.proposed", {"execution_mode": "live"}, AgentMessageType.TASK_ASSIGNMENT),
    ("worker.accepted", {"generation": 1}, AgentMessageType.TASK_ACCEPTED),
    ("task.verified", {}, AgentMessageType.TASK_COMPLETED),
    (
        "worker.help_requested",
        {
            "requested_role": "math",
            "reason": "Need a source-bound derivation",
            "requires_approved_reassignment": True,
        },
        AgentMessageType.HELP_REQUEST,
    ),
    (
        "worker.result",
        {"claim": '{"summary":"delivered"}', "execution_mode": "live"},
        AgentMessageType.REVIEW_REQUEST,
    ),
    ("worker.review_requested", {"result_event_id": "result-1"}, AgentMessageType.REVIEW_REQUEST),
    (
        "worker.revision_requested",
        {
            "review_event_id": "review-1",
            "revision_goals": ["Correct boundary case"],
            "repair_attempt": 1,
        },
        AgentMessageType.REVISION_REQUEST,
    ),
    (
        "worker.blocked",
        {"reason_code": "dependency_wait", "failure_category": "dependency"},
        AgentMessageType.DEPENDENCY_BLOCKED,
    ),
    (
        "worker.blocked",
        {"reason_code": "model_error", "failure_category": "provider"},
        AgentMessageType.MODEL_UNAVAILABLE,
    ),
    (
        "worker.blocked",
        {"reason_code": "token_budget", "failure_category": "budget"},
        AgentMessageType.BUDGET_WARNING,
    ),
    (
        "worker.failed",
        {"reason_code": "deadline", "failure_category": "budget"},
        AgentMessageType.AGENT_FAILED,
    ),
    (
        "review.blocked",
        {"reason_code": "review_budget", "result_event_id": None},
        AgentMessageType.DEPENDENCY_BLOCKED,
    ),
)


@pytest.mark.parametrize("kind,fields,message_type", CASES)
def test_all_message_contracts_have_typed_identity_and_direction(kind, fields, message_type):
    value = payload(**fields)
    message = AgentMessage.from_event(event(kind), value)
    assert message.message_type == message_type
    assert message.contract_version == 2 and message.payload_contract == "validated_v2"
    assert message.plan_id == "plan-1" and message.task_node_key == "node-1"
    assert message.payload == value
    if kind == "worker.proposed":
        assert (message.sender_id, message.receiver_id) == ("router", "worker-1")
    else:
        assert (message.sender_id, message.receiver_id) == ("worker-1", "router")


@pytest.mark.parametrize("kind,fields,message_type", CASES)
def test_message_specific_missing_required_fields_are_rejected(kind, fields, message_type):
    if kind == "task.verified":
        return
    for name in fields:
        if name in {"failure_category", "result_event_id"} and kind != "worker.review_requested":
            continue
        invalid = payload(**fields)
        del invalid[name]
        with pytest.raises(DomainError):
            AgentMessage.from_event(event(kind), invalid)


@pytest.mark.parametrize("version", [0, 3, True, "2", None])
def test_unknown_versions_do_not_downgrade_to_history(version):
    with pytest.raises(DomainError):
        AgentMessage.from_event(
            event("worker.accepted"), payload(protocol_version=version, generation=1)
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", True),
        ("generation", 0),
        ("generation", -1),
        ("run_id", ""),
        ("correlation_id", []),
        ("message_type", "TASK_COMPLETED"),
    ],
)
def test_malformed_fields_cannot_claim_a_valid_native_contract(field, value):
    with pytest.raises(DomainError):
        AgentMessage.from_event(
            event("worker.accepted"), payload(**{"generation": 1, field: value})
        )


def test_help_and_revision_cannot_authorize_unbounded_work():
    help_data = payload(
        requested_role="math", reason="reason", requires_approved_reassignment=False
    )
    with pytest.raises(DomainError):
        AgentMessage.from_event(event("worker.help_requested"), help_data)
    goals = payload(review_event_id="review-1", revision_goals=["g"] * 9, repair_attempt=1)
    with pytest.raises(DomainError):
        AgentMessage.from_event(event("worker.revision_requested"), goals)


def test_native_unicode_keeps_the_existing_producer_character_bounds():
    value = payload(
        requested_role="数学专家", reason="源" * 2048, requires_approved_reassignment=True
    )
    assert AgentMessage.from_event(event("worker.help_requested"), value).payload == value
    value = payload(review_event_id="review-1", revision_goals=["修" * 2048], repair_attempt=1)
    assert AgentMessage.from_event(event("worker.revision_requested"), value).payload == value


def test_finite_bounded_payload_and_delivery_declaration():
    value = payload(claim="source" * 50000, execution_mode="live")
    with pytest.raises(DomainError):
        AgentMessage.from_event(event("worker.result"), value)
    value = payload(
        claim="{}", execution_mode="live", delivery_fields_supplied=["summary", "summary"]
    )
    with pytest.raises(DomainError):
        AgentMessage.from_event(event("worker.result"), value)
    value = payload(claim="{}", execution_mode="live", extra=float("nan"))
    with pytest.raises(DomainError):
        AgentMessage.from_event(event("worker.result"), value)


def test_historic_native_payload_keeps_its_actual_weaker_contract():
    value = {"plan_id": "plan-1", "task_node_key": "node-1", "run_id": "run-1"}
    original = json.dumps(value, sort_keys=True)
    message = AgentMessage.from_event(event("worker.accepted"), value)
    assert message.message_type == AgentMessageType.TASK_ACCEPTED
    assert message.contract_version == 1 and message.payload_contract == "legacy_identity_only"
    assert json.dumps(value, sort_keys=True) == original


def test_completed_task_uses_bound_worker_without_inventing_run_id():
    value = payload()
    del value["run_id"]
    original = copy.deepcopy(value)
    message = AgentMessage.from_event(event("task.verified"), value)
    assert message.message_type == AgentMessageType.TASK_COMPLETED
    assert message.correlation_id == "event-1"
    assert value == original
