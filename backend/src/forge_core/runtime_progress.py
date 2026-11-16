"""Durable activity and outcome observations, separate from process heartbeat.

This observes source counters in the existing transaction and event log. It does
not infer task quality, steal a live lease or replay an external operation.
"""

import hashlib
import json

from .domain import ForgeEvent

# Inspect a fixed prefix, rather than letting an unbounded history grow the
# Watchdog's work. Saturation is reported; it never grants recovery authority.
_MAX_TOOL_SOURCES = 256
_TOOL_FIELDS = {
    "file_read": ("sha256",),
    "file_write": ("path", "sha256"),
    "file_revise": ("path", "sha256"),
    "directory_list": ("entries",),
    "directory_create": ("path",),
    "browser_get": ("status_code", "sha256"),
    "mcp_call": ("status_code", "sha256"),
    "git_status": ("exit_code", "stdout_sha256"),
    "python_compile": ("source_sha256", "exit_code"),
    "python_test": ("source_sha256", "suite_sha256", "passed", "failed", "exit_code"),
    "python_unittest": (
        "manifest_sha256", "test_module", "tests_run", "failures", "errors", "skipped", "exit_code"
    ),
    "numeric_ast": ("source_sha256", "input_sha256", "result_json", "exit_code"),
    "worker_read_completed_delivery": ("node_key", "delivery_sha256"),
    "worker_read_action_receipt": ("result_sha256",),
    "worker_read_context_source": ("sha256", "source_sha256", "content_sha256"),
}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def productive_fingerprint(connection, row):
    """Observe distinct source/results, not reasoning volume or receipt IDs.

    This is a conservative activity-loop diagnostic, not a semantic quality
    judgment. Re-reading the same path/content or alternating previously seen
    revisions does not advance this frontier. Only fixed Core tool result fields
    are used; message text and volatile execution metadata cannot reset it.
    """
    signatures = set()
    calls = connection.execute(
        "SELECT tool_name,arguments_json,result_json FROM worker_tool_calls "
        "WHERE run_id=? AND state='succeeded' ORDER BY rowid LIMIT ?",
        (row["id"], _MAX_TOOL_SOURCES + 1),
    ).fetchall()
    for call in calls[:_MAX_TOOL_SOURCES]:
        fields = _TOOL_FIELDS.get(call["tool_name"])
        if fields is None:
            continue
        try:
            result = json.loads(call["result_json"] or "null")
            arguments = json.loads(call["arguments_json"])
        except (ValueError, TypeError):
            continue
        if not isinstance(result, dict) or not isinstance(arguments, dict):
            continue
        output = result.get("output", result)
        if not isinstance(output, dict):
            continue
        stable = {name: output[name] for name in fields if name in output}
        if stable:
            # Keep resource identity, not prior receipt IDs or other volatile
            # parameters. Events expose neither paths nor source text.
            resource = {
                name: arguments[name]
                for name in ("path", "url", "server", "tool", "node_key", "ordinal", "offset", "limit")
                if name in arguments
            }
            signatures.add(_digest([call["tool_name"], resource, stable]))
    capsule = connection.execute(
        "SELECT inbox_cursor FROM worker_capsules WHERE run_id=?", (row["id"],)
    ).fetchone()
    node = connection.execute(
        "SELECT status FROM task_nodes WHERE plan_id=? AND node_key=?",
        (row["plan_id"], row["node_key"]),
    ).fetchone()
    source = {
        "kind": "productive-v1",
        "state": row["state"],
        "node_state": node[0] if node else None,
        "inbox_cursor": capsule[0] if capsule else None,
        "distinct_tool_results": sorted(signatures),
        "tool_history_saturated": len(calls) > _MAX_TOOL_SOURCES,
    }
    return _digest(source), source


def progress_fingerprint(connection, row):
    """Only completed/consumed checkpoints count; lease renewal is not progress."""
    run_id = row["id"]
    messages = connection.execute(
        "SELECT COALESCE(MAX(ordinal),-1),COUNT(*) FROM worker_messages WHERE run_id=?",
        (run_id,),
    ).fetchone()
    tools = connection.execute(
        "SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM worker_tool_calls "
        "WHERE run_id=? AND state IN ('succeeded','failed')",
        (run_id,),
    ).fetchone()
    settled = connection.execute(
        "SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM worker_model_reservations "
        "WHERE run_id=? AND state!='reserved'",
        (run_id,),
    ).fetchone()
    capsule = connection.execute(
        "SELECT revision,inbox_cursor FROM worker_capsules WHERE run_id=?", (run_id,)
    ).fetchone()
    source = {
        "state": row["state"],
        "message_frontier": list(messages),
        "settled_tools": list(tools),
        "settled_model_calls": list(settled),
        "capsule_frontier": list(capsule) if capsule else None,
    }
    return _digest(source), source


def _observe(work, row, event_type, fingerprint, source, now):
    c = work.connection
    previous = c.execute(
        "SELECT id,payload FROM events WHERE event_type=? "
        "AND worker_id=? AND json_extract(payload,'$.run_id')=? ORDER BY seq DESC LIMIT 1",
        (event_type, row["worker_id"], row["id"]),
    ).fetchone()
    baseline = json.loads(previous["payload"]) if previous else None
    if (
        baseline is None
        or baseline.get("fingerprint") != fingerprint
        or baseline.get("observed_at", now) > now
    ):
        work.append_event(
            ForgeEvent(
                event_type=event_type,
                conversation_id=row["conversation_id"],
                quest_id=row["quest_id"],
                worker_id=row["worker_id"],
                payload={
                    "run_id": row["id"],
                    "fingerprint": fingerprint,
                    "observed_at": now,
                    "frontier": source,
                },
            )
        )
        return None
    return previous["id"], now - baseline["observed_at"]


def observe_worker_progress(work, row, *, now, timeout_seconds):
    """Persist bounded, deduplicated observations without changing execution.

    Both clocks survive restart. A fresh Watchdog starts its observation window
    rather than guessing the age of old state. Known in-flight accounting only
    permits observation, never cancellation/reissue or an implied task success.
    """
    c = work.connection
    activity, activity_source = progress_fingerprint(c, row)
    productive, productive_source = productive_fingerprint(c, row)
    activity_window = _observe(
        work, row, "runtime.worker_progress_observed", activity, activity_source, now
    )
    productive_window = _observe(
        work, row, "runtime.worker_productive_progress_observed", productive, productive_source, now
    )
    if activity_window and activity_window[1] >= timeout_seconds:
        previous_id, elapsed = activity_window
        fingerprint, reason = activity, "worker_no_checkpoint_progress"
    elif productive_window and productive_window[1] >= timeout_seconds:
        previous_id, elapsed = productive_window
        fingerprint, reason = productive, "worker_no_productive_progress"
    else:
        return None
    unsettled = bool(
        c.execute(
            "SELECT 1 FROM worker_model_reservations WHERE run_id=? AND state='reserved' LIMIT 1",
            (row["id"],),
        ).fetchone()
        or c.execute(
            "SELECT 1 FROM action_intents WHERE run_id=? "
            "AND state IN ('prepared','executing','uncertain') LIMIT 1",
            (row["id"],),
        ).fetchone()
        or c.execute(
            "SELECT 1 FROM model_budget_attempts WHERE worker_run_id=? "
            "AND state IN ('reserved','unknown') LIMIT 1",
            (row["id"],),
        ).fetchone()
        or c.execute(
            "SELECT 1 FROM worker_tool_calls WHERE run_id=? AND state IN ('running','uncertain') LIMIT 1",
            (row["id"],),
        ).fetchone()
    )
    diagnostic = {
        "run_id": row["id"],
        "state": row["state"],
        "fingerprint": fingerprint,
        "reason_code": reason,
        "elapsed_seconds": elapsed,
        "unsettled_effect_or_call": unsettled,
        "source_reference": "event:" + previous_id,
        "activity_fingerprint": activity,
        "productive_fingerprint": productive,
        "tool_history_saturated": productive_source["tool_history_saturated"],
        "recovery": "observe_only_unknown_effect" if unsettled else "request_router_assessment",
    }
    prior_help = c.execute(
        "SELECT 1 FROM events WHERE event_type='worker.help_requested' AND worker_id=? "
        "AND json_extract(payload,'$.source')='watchdog' "
        "AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.fingerprint')=? LIMIT 1",
        (row["worker_id"], row["id"], fingerprint),
    ).fetchone()
    if not prior_help:
        work.append_event(
            ForgeEvent(
                event_type="worker.help_requested",
                conversation_id=row["conversation_id"],
                quest_id=row["quest_id"],
                worker_id=row["worker_id"],
                payload={
                    "protocol_version": 2,
                    "plan_id": row["plan_id"],
                    "task_node_key": row["node_key"],
                    "source": "watchdog",
                    "requested_role": "router",
                    "reason": (
                        "Assess the source-bound stall observation and report progress or a blocker. "
                        "This diagnostic requests no expert expansion or execution authority. "
                        "Retain current permissions and budgets; never replay an operation with "
                        "an unknown result."
                    ),
                    "requires_approved_reassignment": True,
                    **diagnostic,
                },
            )
        )
    return diagnostic
