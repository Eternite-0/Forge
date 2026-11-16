"""Core-owned recovery classifications and settled read-only failure proofs."""

import hashlib
import json
from pathlib import Path

from .governance import POLICY_VERSION, parameter_digest

CORE_READ_ONLY_TOOLS = frozenset(
    {
        "worker_read_context_source",
        "worker_read_action_receipt",
        "worker_read_completed_delivery",
        "recall_object",
        "read_trigger",
    }
)


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
    if reason in {"approval_required", "tool_write_scope_rejected"}:
        return "authorization"
    if reason in {"help_requested", "help_request_rejected"}:
        return "dependency"
    if reason in {
        "python_compile_failed",
        "numeric_ast_failed",
        "python_test_failed",
        "python_unittest_failed",
    }:
        return "code"
    if reason == "resource_unavailable":
        return "external_resource"
    if reason.startswith("tool_") or reason in {"unresolved_tool_recovery", "source_read_rejected"}:
        return "tool"
    return "runtime"


def settled_read_failure(connection, run_id, call, *, require_repair=True):
    """Verify a settled diagnostic or explicitly approved Python test failure.

    This never authorizes replay. A new model-selected call must have a new ID,
    and Python execution needs a new exact approval. Its code may have OS-level
    side effects; only the observed process/test outcome is settled here. The
    failed call, receipt and recovery event remain in the audit history.
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
            "python_unittest",
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
            or (
                row["risk"] != "R0"
                and not (call["tool_name"] == "python_unittest" and row["risk"] == "R2")
            )
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
        if call["tool_name"] == "python_unittest":
            intent = connection.execute(
                "SELECT * FROM action_intents WHERE id=?", (row["intent_id"],)
            ).fetchone()
            approval = connection.execute(
                "SELECT * FROM approvals WHERE id=?", (intent["approval_id"],)
            ).fetchone()
            output = json.loads(row["result_json"])
            parameters = json.loads(row["parameters_json"])
            if (
                approval is None
                or approval["status"] != "APPROVED"
                or approval["decided_by"] != "local_user"
                or not approval["consumed_at"]
                or approval["risk"] != "R2"
                or approval["policy_version"] != POLICY_VERSION
                or approval["action_type"] != "python_unittest"
                or approval["parameter_digest"] != row["parameter_digest"]
                or approval["resource_scope"] != intent["resource_scope"]
                or approval["quest_id"] != intent["quest_id"]
                or approval["plan_revision"] != intent["plan_revision"]
                or output.get("runtime") != "cpython_unittest_v1"
                or output.get("exit_code") != 2
                or output.get("manifest_sha256") != parameter_digest(parameters["manifest"])
                or output.get("manifest_json")
                != json.dumps(
                    parameters["manifest"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                or output.get("test_module") != parameters["test_module"]
                or type(output.get("tests_run")) is not int
                or output["tests_run"] < 1
                or output.get("failures", 0) + output.get("errors", 0) < 1
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


def local_write_rejection_feedback(connection, run_id, call):
    """Prove a local R1 path denial and expose only existing task write roots.

    This is diagnostic data, never a grant or permission to replay. The Worker
    must choose a new call, which undergoes all normal authorization checks.
    """
    try:
        if call["tool_name"] not in {"file_write", "file_revise", "directory_create"}:
            return None
        stored = connection.execute(
            "SELECT c.*,r.quest_id,r.plan_revision,q.plan_revision AS current_revision "
            "FROM worker_tool_calls c JOIN worker_runs r ON r.id=c.run_id "
            "JOIN quests q ON q.id=r.quest_id WHERE c.run_id=? AND c.call_id=?",
            (run_id, call["call_id"]),
        ).fetchone()
        if (
            stored is None
            or stored["tool_name"] != call["tool_name"]
            or stored["model_call_id"] != call["model_call_id"]
            or stored["state"] not in {"running", "failed"}
            or stored["receipt_id"] is not None
            or stored["plan_revision"] != stored["current_revision"]
            or parameter_digest(json.loads(stored["arguments_json"]))
            != parameter_digest(json.loads(call["arguments_json"]))
            or connection.execute(
                "SELECT 1 FROM action_intents WHERE call_id=?", (call["call_id"],)
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM action_intents WHERE run_id=? AND state IN ('prepared','executing','uncertain')",
                (run_id,),
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM worker_tool_calls WHERE run_id=? AND state='uncertain'", (run_id,)
            ).fetchone()
        ):
            return None
        workspace = connection.execute("SELECT id,root FROM workspace WHERE singleton=1").fetchone()
        root = Path(workspace["root"]).resolve()
        parameters = json.loads(stored["arguments_json"])
        if not isinstance(parameters.get("path"), str):
            return None
        target = (root / parameters["path"]).resolve()
        if not target.is_relative_to(root):
            return None
        grants = connection.execute(
            "SELECT resource_scope FROM task_grants WHERE quest_id=? AND workspace_id=? "
            "AND plan_revision=? AND revoked_at IS NULL ORDER BY resource_scope LIMIT 65",
            (stored["quest_id"], workspace["id"], stored["plan_revision"]),
        ).fetchall()
        if not 1 <= len(grants) <= 64:
            return None
        scopes = [Path(item["resource_scope"]).resolve() for item in grants]
        if any(not scope.is_relative_to(root) or target.is_relative_to(scope) for scope in scopes):
            return None
        return {
            "error_code": "local_write_outside_existing_grant",
            "allowed_write_roots": [scope.relative_to(root).as_posix() for scope in scopes],
            "authorization_source": "existing_task_grants",
            "no_effect": True,
        }
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None


def core_read_rejection_feedback(connection, run_id, call):
    """Bind a no-effect Core read denial; reveal own archive references only."""
    try:
        if call["tool_name"] not in CORE_READ_ONLY_TOOLS:
            return None
        stored = connection.execute(
            "SELECT c.*,r.plan_revision,q.plan_revision AS current_revision "
            "FROM worker_tool_calls c JOIN worker_runs r ON r.id=c.run_id "
            "JOIN quests q ON q.id=r.quest_id WHERE c.run_id=? AND c.call_id=?",
            (run_id, call["call_id"]),
        ).fetchone()
        if (
            stored is None
            or stored["tool_name"] != call["tool_name"]
            or stored["model_call_id"] != call["model_call_id"]
            or stored["state"] not in {"pending", "running", "failed"}
            or stored["receipt_id"] is not None
            or stored["plan_revision"] != stored["current_revision"]
            or parameter_digest(json.loads(stored["arguments_json"]))
            != parameter_digest(json.loads(call["arguments_json"]))
            or connection.execute(
                "SELECT 1 FROM action_intents WHERE call_id=?", (call["call_id"],)
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM action_intents WHERE run_id=? AND state IN ('prepared','executing','uncertain')",
                (run_id,),
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM worker_tool_calls WHERE run_id=? AND (state='uncertain' OR (state='running' AND call_id<>?))",
                (run_id, call["call_id"]),
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM worker_core_source_reads WHERE run_id=? AND state IN ('running','failed','uncertain')",
                (run_id,),
            ).fetchone()
            or connection.execute(
                "SELECT 1 FROM worker_model_reservations WHERE run_id=? AND state='reserved' AND json_extract(context_json,'$.purpose')='context_compaction'",
                (run_id,),
            ).fetchone()
        ):
            return None
        references = connection.execute(
            "SELECT source_id FROM context_archive_sources WHERE scope='worker' AND scope_id=? ORDER BY CAST(source_id AS INTEGER) LIMIT 32",
            (run_id,),
        ).fetchall()
        return {
            "error_code": "source_read_rejected",
            "available_archive_refs": [
                int(item[0]) for item in references if str(item[0]).isdigit()
            ],
            "no_effect": True,
        }
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None


def settled_preexecution_rejection(connection, run_id, call):
    """Verify Core's R0 or local R1 denial without inventing an executed receipt."""
    try:
        from .tool_domain import CONTRACTS

        core_read = call["tool_name"] in CORE_READ_ONLY_TOOLS
        contract = next((item for item in CONTRACTS if item.name == call["tool_name"]), None)
        if (
            (not core_read and (contract is None or contract.risk not in {"R0", "R1"}))
            or call["state"] != "failed"
            or call["receipt_id"] is not None
        ):
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
        if core_read and json.loads(event[0]).get("reason_code") == "source_read_rejected":
            feedback = core_read_rejection_feedback(connection, run_id, call)
            output = result.get("output")
            if (
                feedback is None
                or not isinstance(output, dict)
                or set(output) != {"error_code", "available_archive_refs", "no_effect"}
                or output.get("error_code") != "source_read_rejected"
                or output.get("no_effect") is not True
                or not isinstance(output.get("available_archive_refs"), list)
                or len(output["available_archive_refs"]) > 32
                or any(
                    type(ref) is not int
                    or not connection.execute(
                        "SELECT 1 FROM context_archive_sources WHERE scope='worker' AND scope_id=? AND source_id=?",
                        (run_id, str(ref)),
                    ).fetchone()
                    for ref in output["available_archive_refs"]
                )
            ):
                return False
        elif core_read and contract is None:
            return False
        if contract is not None and contract.risk == "R1":
            feedback = local_write_rejection_feedback(connection, run_id, call)
            if (
                feedback is None
                or result.get("output") != feedback
                or json.loads(event[0]).get("reason_code") != "tool_write_scope_rejected"
                or json.loads(event[0]).get("failure_category") != "authorization"
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
