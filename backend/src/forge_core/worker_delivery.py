"""Worker delivery stage: validate a claim, then commit under the owner fence.

A parsed delivery is not evidence and never completes its Quest. The commit
stage atomically retains late context, admits pending tools, or hands the claim
to independent review. This module does not invoke models or execute tools.
"""

import hashlib
import json
import math
from enum import Enum
from itertools import islice

import jsonschema

from .domain import DomainError, ForgeEvent
from .worker_envelope import canonical as _json
from .worker_recovery import failure_category
from .worker_schema import DELIVERY_FIELDS


class DeliveryOutcome(str, Enum):
    TOOLS_PENDING = "tools_pending"
    CONTEXT_PENDING = "context_pending"
    REVIEW_PENDING = "review_pending"
    BLOCKED = "blocked"


def reply_format_repair_active(connection, run_id):
    row = connection.execute(
        "SELECT next_action FROM worker_capsules WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise DomainError("Worker capsule missing")
    return row[0] == "format_reply"


def _delivery_schema_error_path(error):
    return [
        part
        if type(part) is int and 0 <= part <= 127
        else part
        if isinstance(part, str) and part in DELIVERY_FIELDS
        else "field"
        for part in list(error.absolute_path)[:8]
    ]


def _delivery_schema_diagnostics(value, schema):
    """At most three rule/location diagnostics, with no rejected input values."""
    try:
        validator = jsonschema.validators.validator_for(schema)(schema)
        return [
            {
                "validator": error.validator
                if error.validator
                in {"type", "required", "additionalProperties", "maxItems", "minimum", "maximum"}
                else "schema_rule",
                "path": _delivery_schema_error_path(error),
            }
            for error in islice(validator.iter_errors(value), 3)
        ]
    except (jsonschema.SchemaError, TypeError, ValueError):
        return [{"validator": "invalid_approved_schema", "path": []}]


def validate_worker_delivery(capsule, text):
    """Return parsed JSON or bounded diagnostics without persistent mutation."""
    from .worker_envelope import DeliveryCapacityError, validate_delivery

    validation_stage = "json_syntax"
    try:
        final_value = json.loads(text)
        validation_stage = "canonical_json"
        _json(final_value)
        validation_stage = "capacity_contract"
        validate_delivery(capsule, final_value)
        validation_stage = "output_schema"
        jsonschema.validate(final_value, capsule["output_schema"])
        validation_stage = "delivery_review"
        if any(
            item in {"delivery_review", "json_review:@delivery"}
            for item in capsule["success_criteria"]
        ):
            if not isinstance(final_value, dict) or not set(DELIVERY_FIELDS) <= set(final_value):
                raise jsonschema.ValidationError(
                    "The approved delivery review requires all eight delivery fields"
                )
            confidence = final_value.get("confidence")
            if (
                type(confidence) not in (int, float)
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
            ):
                raise jsonschema.ValidationError("Delivery confidence must be between 0 and 1")
    except (
        ValueError,
        TypeError,
        jsonschema.ValidationError,
        jsonschema.SchemaError,
    ) as exc:
        detail = {
            "schema": "output_schema",
            "error": type(exc).__name__,
            "validation_stage": validation_stage,
            "response_bytes": len(text.encode("utf-8")),
            "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        if isinstance(exc, DeliveryCapacityError):
            detail.update(
                code=exc.code,
                actual_bytes=exc.actual_bytes,
                max_bytes=exc.max_bytes,
                message="Complete delivery exceeds its approved canonical UTF-8 byte bound. Preserve all assigned requirements; remove duplicated prose and share citations once within the unchanged limit.",
                schema_errors=_delivery_schema_diagnostics(final_value, capsule["output_schema"]),
            )
        elif isinstance(exc, json.JSONDecodeError):
            detail.update(
                code="invalid_json_syntax",
                line=exc.lineno,
                column=exc.colno,
                position=exc.pos,
                message="response was not valid JSON",
            )
        elif isinstance(exc, jsonschema.ValidationError):
            if validation_stage == "delivery_review":
                missing = [
                    field
                    for field in DELIVERY_FIELDS
                    if not isinstance(final_value, dict) or field not in final_value
                ]
                detail.update(
                    code="invalid_output_contract",
                    message="Parsed delivery violates the approved eight-field delivery review contract",
                    missing_fields=missing,
                    path=[] if missing else ["confidence"],
                )
            else:
                detail.update(
                    code="output_schema",
                    message="Parsed delivery does not match the approved output schema",
                    path=".".join(str(part) for part in _delivery_schema_error_path(exc)),
                    schema_errors=_delivery_schema_diagnostics(
                        final_value, capsule["output_schema"]
                    ),
                )
        elif isinstance(exc, ValueError) and validation_stage == "canonical_json":
            detail.update(
                code="invalid_json_value",
                message="Response cannot be serialized as finite canonical JSON",
            )
        else:
            detail.update(
                code="invalid_output_contract",
                message="Delivery contract validation failed",
            )
        return None, detail
    return final_value, None


def commit_worker_response(manager, *, run_id, token, row, calls, allowed, text, final_value):
    """Commit one already charged response within its existing lease/authority.

    The manager supplies the unchanged ownership fence. No API caller or model
    receives this authority. Any exception rolls back the full unit of work.
    """
    outcome = DeliveryOutcome.TOOLS_PENDING
    with manager.store.unit_of_work() as work:
        c = work.connection
        current = manager._fenced(c, run_id, token)
        format_reply = reply_format_repair_active(c, run_id)
        if format_reply:
            from .worker_recovery import CORE_READ_ONLY_TOOLS

            # Enforce before staging any tool, even if an adapter ignores the
            # narrowed request definitions. Model output cannot grant execution.
            if any(call.get("name") not in CORE_READ_ONLY_TOOLS for call in calls):
                raise DomainError("Reply format repair cannot admit action tools")
        from .superagent_events import TriggerService

        trigger_debt = (
            TriggerService(manager.store).flush_run(run_id, connection=c)
            if not calls
            else {"pending": False}
        )
        continuation_debt = []
        if not calls:
            from .worker_continuations import pending_continuations

            try:
                continuation_debt = pending_continuations(
                    manager.store,
                    c,
                    run_id,
                    before_ordinal=c.execute(
                        "SELECT COALESCE(MAX(ordinal),-1)+1 FROM worker_messages WHERE run_id=?",
                        (run_id,),
                    ).fetchone()[0],
                )
            except DomainError:
                # Retain the charged draft and block atomically under the
                # same owner fence; no extra model call or source replay.
                ordinal = c.execute(
                    "SELECT COALESCE(MAX(ordinal),-1)+1 FROM worker_messages WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
                c.execute(
                    "INSERT INTO worker_messages VALUES (?,?,'assistant',?)",
                    (run_id, ordinal, _json({"role": "assistant", "content": text})),
                )
                c.execute(
                    "UPDATE worker_runs SET state='blocked',lease_token=NULL,lease_until=NULL,"
                    "awaiting_authorization=0 WHERE id=?",
                    (run_id,),
                )
                c.execute("UPDATE workers SET status='blocked' WHERE id=?", (current["worker_id"],))
                c.execute(
                    "UPDATE task_nodes SET status='blocked' WHERE plan_id=? AND node_key=? AND worker_id=?",
                    (current["plan_id"], current["node_key"], current["worker_id"]),
                )
                work.append_event(
                    ForgeEvent(
                        event_type="worker.blocked",
                        quest_id=current["quest_id"],
                        worker_id=current["worker_id"],
                        payload={
                            "protocol_version": 2,
                            "plan_id": current["plan_id"],
                            "task_node_key": current["node_key"],
                            "run_id": run_id,
                            "reason_code": "continuation_source_unavailable",
                            "failure_category": failure_category("continuation_source_unavailable"),
                        },
                    )
                )
                return DeliveryOutcome.BLOCKED
        if not calls and (
            trigger_debt["pending"]
            or continuation_debt
            or c.execute(
                "SELECT 1 FROM worker_inbox WHERE run_id=? AND consumed=0 LIMIT 1",
                (run_id,),
            ).fetchone()
        ):
            # An event arriving during inference belongs to this task.
            # Preserve the charged draft but consume the new input before
            # publishing a result. The write lock makes the boundary
            # atomic with subscription delivery and final state change.
            ordinal = c.execute(
                "SELECT COALESCE(MAX(ordinal),-1)+1 FROM worker_messages WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            from .worker_continuations import continuation_feedback

            feedback = (
                continuation_feedback(continuation_debt)
                if continuation_debt
                else "Core delivery boundary: new task events arrived during that draft. "
                "Consume the following inbox context and full referenced contents before finalizing."
            )
            c.execute(
                "INSERT INTO worker_messages VALUES (?,?,'assistant',?)",
                (run_id, ordinal, _json({"role": "assistant", "content": text})),
            )
            c.execute(
                "INSERT INTO worker_messages VALUES (?,?,'user',?)",
                (
                    run_id,
                    ordinal + 1,
                    _json(
                        {
                            "role": "user",
                            "content": feedback,
                        }
                    ),
                ),
            )
            c.execute(
                "UPDATE worker_capsules SET next_action=?,revision=revision+1 WHERE run_id=?",
                ("format_reply" if format_reply else "model", run_id),
            )
            return DeliveryOutcome.CONTEXT_PENDING
        if current["tool_spent"] + len(calls) > current["tool_limit"]:
            raise DomainError("Tool call budget exceeded")
        if any(
            not isinstance(call, dict)
            or not isinstance(call.get("id"), str)
            or not call["id"]
            or call.get("name") not in allowed
            or not isinstance(call.get("arguments"), dict)
            for call in calls
        ) or len({call["id"] for call in calls}) != len(calls):
            raise DomainError("Invalid or unauthorized tool call")
        c.execute(
            "UPDATE worker_runs SET tool_spent=tool_spent+? WHERE id=?",
            (len(calls), run_id),
        )
        ordinal = c.execute(
            "SELECT COALESCE(MAX(ordinal),-1)+1 FROM worker_messages WHERE run_id=?",
            (run_id,),
        ).fetchone()[0]
        message = {
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": _json(call["arguments"]),
                    },
                }
                for call in calls
            ],
        }
        c.execute(
            "INSERT INTO worker_messages VALUES (?,?,'assistant',?)",
            (run_id, ordinal, _json(message)),
        )
        for call in calls:
            call_id = "call_" + hashlib.sha256(f"{run_id}:{call['id']}".encode()).hexdigest()
            c.execute(
                "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) VALUES (?,?,?,?,?,'pending')",
                (run_id, call_id, call["id"], call["name"], _json(call["arguments"])),
            )
        c.execute(
            "UPDATE worker_capsules SET next_action=?,revision=revision+1 WHERE run_id=?",
            ("format_reply" if calls and format_reply else "tool" if calls else "review", run_id),
        )
        if not calls:
            c.execute(
                "UPDATE worker_runs SET state='review_pending',lease_token=NULL,lease_until=NULL WHERE id=?",
                (run_id,),
            )
            c.execute("UPDATE workers SET status='review_pending' WHERE id=?", (row["worker_id"],))
            c.execute(
                "UPDATE task_nodes SET status='review_pending' WHERE plan_id=? AND node_key=? AND worker_id=?",
                (row["plan_id"], row["node_key"], row["worker_id"]),
            )
            work.append_event(
                ForgeEvent(
                    event_type="worker.result",
                    quest_id=row["quest_id"],
                    worker_id=row["worker_id"],
                    payload={
                        "protocol_version": 2,
                        "plan_id": row["plan_id"],
                        "task_node_key": row["node_key"],
                        "run_id": run_id,
                        "execution_mode": row["execution_mode"],
                        "claim": text,
                        "delivery_fields_supplied": [
                            name
                            for name in DELIVERY_FIELDS
                            if isinstance(final_value, dict) and name in final_value
                        ],
                    },
                )
            )
            outcome = DeliveryOutcome.REVIEW_PENDING
    return outcome
