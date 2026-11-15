"""Core-owned recovery classifications and settled read-only failure proofs."""

import hashlib
import json

from .governance import parameter_digest


def provider_failure_code(exc):
    from .providers import ProviderError

    if isinstance(exc, TimeoutError):
        return "timeout"
    allowed = {
        "auth",
        "rate_limit",
        "budget_exhausted",
        "budget_unconfigured",
        "configuration",
        "context_exceeded",
        "network",
        "protocol_error",
        "provider_error",
        "refused",
        "timeout",
        "truncated",
        "unsupported",
        "attempt_unknown",
        "concurrency_unavailable",
    }
    return (
        exc.code
        if isinstance(exc, ProviderError) and exc.code in allowed
        else "unexpected_provider_error"
    )


def provider_exception_type(exc):
    """Fixed diagnostic labels; never persist remote text or custom class names."""
    from .domain import DomainError
    from .providers import ProviderError

    for exception_type in (
        ProviderError,
        DomainError,
        TimeoutError,
        TypeError,
        ValueError,
        KeyError,
        RuntimeError,
        OSError,
    ):
        if isinstance(exc, exception_type):
            return exception_type.__name__
    return "UnexpectedError"


def failure_category(reason):
    if reason in {
        "invalid_output_contract",
        "invalid_model_output",
        "incomplete_model_output",
        "model_truncated",
    }:
        return "task_contract"
    if reason in {"provider_unavailable", "provider_mode_changed", "model_error", "usage_unknown"}:
        return "provider"
    if reason in {
        "context_budget",
        "step_budget",
        "tool_budget",
        "token_budget",
        "cost_budget",
        "deadline",
    }:
        return "budget"
    if reason == "approval_required":
        return "authorization"
    if reason in {"help_requested", "help_request_rejected"}:
        return "dependency"
    if reason in {"python_compile_failed", "numeric_ast_failed", "python_test_failed"}:
        return "code"
    if reason == "resource_unavailable":
        return "external_resource"
    if reason.startswith("tool_") or reason == "unresolved_tool_recovery":
        return "tool"
    return "runtime"


def settled_read_failure(connection, run_id, call, *, require_repair=True):
    """A failed local diagnostic is observable, not an uncertain external action.

    This never authorizes replay. A new model-selected call must have a new ID,
    and the failed call, receipt and recovery event remain in the audit history.
    """
    try:
        if call["state"] == "failed" and call["receipt_id"] is None:
            return settled_preexecution_rejection(connection, run_id, call)
        if call["state"] != "failed" or call["tool_name"] not in {
            "shell_inspect",
            "python_compile",
            "numeric_ast",
            "python_test",
            "git_status",
            "file_read",
            "directory_list",
        }:
            return False
        row = connection.execute(
            """SELECT r.*,i.run_id,i.call_id,i.tool_name,i.parameters_json,
            i.parameter_digest,i.state,i.risk,i.execution_mode AS intent_mode
            FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id
            WHERE r.id=? AND i.run_id=?""",
            (call["receipt_id"], run_id),
        ).fetchone()
        result = json.loads(call["result_json"])
        if (
            row is None
            or row["state"] != "failed"
            or row["risk"] != "R0"
            or row["call_id"] != call["call_id"]
            or row["tool_name"] != call["tool_name"]
            or row["execution_mode"] != row["intent_mode"]
            or parameter_digest(json.loads(row["parameters_json"])) != row["parameter_digest"]
            or parameter_digest(json.loads(call["arguments_json"])) != row["parameter_digest"]
            or hashlib.sha256(row["result_json"].encode("utf-8")).hexdigest()
            != row["result_digest"]
            or result.get("call_id") != row["call_id"]
            or result.get("receipt_id") != row["id"]
            or result.get("intent_id") != row["intent_id"]
            or result.get("state") != "failed"
            or result.get("output") != json.loads(row["result_json"])
        ):
            return False
        if not require_repair:
            return True
        return (
            connection.execute(
                """SELECT 1 FROM events WHERE event_type='worker.progress'
            AND json_extract(payload,'$.run_id')=?
            AND json_extract(payload,'$.phase')='self_repair'
            AND json_extract(payload,'$.failed_call_id')=?
            AND json_extract(payload,'$.receipt_id')=? LIMIT 1""",
                (run_id, call["call_id"], row["id"]),
            ).fetchone()
            is not None
        )
    except (TypeError, ValueError, KeyError, IndexError):
        return False


def settled_preexecution_rejection(connection, run_id, call):
    """Verify Core's pre-execution R0 denial without inventing a receipt."""
    try:
        from .tool_domain import CONTRACTS

        contract = next(item for item in CONTRACTS if item.name == call["tool_name"])
        if contract.risk != "R0" or call["state"] != "failed" or call["receipt_id"] is not None:
            return False
        result = json.loads(call["result_json"])
        if (
            result.get("state") != "rejected_before_execution"
            or result.get("call_id") != call["call_id"]
        ):
            return False
        if connection.execute(
            "SELECT 1 FROM action_intents WHERE call_id=?", (call["call_id"],)
        ).fetchone():
            return False
        event = connection.execute(
            "SELECT payload FROM events WHERE event_type='worker.progress' "
            "AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.phase')='self_repair' "
            "AND json_extract(payload,'$.failed_call_id')=? AND json_extract(payload,'$.no_effect')=1 ORDER BY seq DESC LIMIT 1",
            (run_id, call["call_id"]),
        ).fetchone()
        if event is None or json.loads(event[0]).get("parameter_digest") != parameter_digest(
            json.loads(call["arguments_json"])
        ):
            return False
        messages = connection.execute(
            "SELECT message_json FROM worker_messages WHERE run_id=? AND role='tool'", (run_id,)
        ).fetchall()
        return any(
            (item := json.loads(row[0])).get("tool_call_id") == call["model_call_id"]
            and json.loads(item["content"]).get("state") == "rejected_before_execution"
            for row in messages
        )
    except (StopIteration, TypeError, ValueError, KeyError, IndexError):
        return False


def unresolved_calls(connection, run_id):
    from .worker_prefetch import unresolved_sources

    return (
        [
            row
            for row in connection.execute(
                "SELECT * FROM worker_tool_calls WHERE run_id=? AND state IN ('running','uncertain','failed')",
                (run_id,),
            )
            if not settled_read_failure(connection, run_id, row)
        ]
        + unresolved_sources(connection, run_id)
        + list(
            connection.execute(
                "SELECT * FROM worker_model_reservations WHERE run_id=? AND state='reserved' "
                "AND json_extract(context_json,'$.purpose')='context_compaction'",
                (run_id,),
            )
        )
    )
