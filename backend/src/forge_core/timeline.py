"""Bounded read-only projections of durable execution facts; no raw payloads."""

import base64
import hashlib
import json
import re
from pathlib import Path

EVENT_TITLES = {
    "quest.proposed": "Quest proposed",
    "quest.status_changed": "Quest status changed",
    "quest.plan_proposed": "Plan proposed",
    "quest.plan_approved": "Plan approved",
    "team.proposed": "Team proposed",
    "team.approved": "Team approved",
    "team.reassigned": "Team reassigned",
    "team.revoked": "Team approval revoked",
    "worker.proposed": "Worker proposed",
    "worker.started": "Worker started",
    "worker.blocked": "Worker blocked",
    "worker.failed": "Worker failed",
    "worker.result": "Worker result recorded",
    "worker.retry": "Worker retry",
    "worker.help_requested": "Worker requested help",
    "worker.pause": "Worker paused",
    "worker.resume": "Worker resumed",
    "worker.cancel": "Worker cancelled",
    "tool.intent_prepared": "Tool action prepared",
    "tool.receipt_recorded": "Tool receipt recorded",
    "tool.execution_uncertain": "Tool outcome uncertain",
    "approval.requested": "Approval requested",
    "approval.decided": "Approval decided",
    "approval.revoked": "Approval revoked",
    "evidence.json_checked": "JSON syntax checked",
    "task.verified": "Task verified",
}
SAFE_TOOLS = {
    "file_read",
    "file_write",
    "directory_list",
    "directory_create",
    "shell_inspect",
    "python_compile",
    "git_status",
    "numeric_ast",
    "browser_get",
    "mcp_call",
}


def _encode(value):
    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def _decode(value):
    if value is None:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if (
            not isinstance(decoded, list)
            or len(decoded) != 3
            or any(not isinstance(item, str) or len(item) > 200 for item in decoded)
            or decoded[1] not in {"event", "tool_receipt", "context_archive", "semantic_test"}
        ):
            raise ValueError
        return tuple(decoded)
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Invalid timeline cursor") from None


def _predicate(kind, cursor, column="id"):
    if cursor is None:
        return "1=1", ()
    timestamp, cursor_kind, cursor_id = cursor
    if kind < cursor_kind:
        return "created_at<=?", (timestamp,)
    if kind > cursor_kind:
        return "created_at<?", (timestamp,)
    ident = int(cursor_id) if kind == "context_archive" and cursor_id.isdecimal() else cursor_id
    return f"(created_at<? OR (created_at=? AND {column}<?))", (timestamp, timestamp, ident)


def read_timeline(store, *, before=None, limit=100):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Invalid timeline limit")
    cursor = _decode(before)
    result = []
    with store.connection() as connection:
        connection.execute("BEGIN")
        where, parameters = _predicate("event", cursor)
        placeholders = ",".join("?" for _ in EVENT_TITLES)
        events = connection.execute(
            f"SELECT id,seq,event_type,created_at,quest_id,worker_id,payload FROM events WHERE event_type IN ({placeholders}) AND {where} ORDER BY created_at DESC,id DESC LIMIT ?",
            (*EVENT_TITLES, *parameters, limit + 1),
        ).fetchall()
        for row in events:
            try:
                payload = json.loads(row["payload"])
                run_id = payload.get("run_id") if isinstance(payload, dict) else None
                if not isinstance(run_id, str) or not re.fullmatch(
                    r"run_[A-Za-z0-9]{1,100}", run_id
                ):
                    run_id = None
            except (ValueError, TypeError):
                run_id = None
            mode = "unknown"
            if run_id:
                run = connection.execute(
                    "SELECT execution_mode FROM worker_runs WHERE id=?", (run_id,)
                ).fetchone()
                if run and run[0] in {"live", "mock"}:
                    mode = run[0]
            result.append(
                {
                    "kind": "event",
                    "event_id": row["id"],
                    "seq": row["seq"],
                    "created_at": row["created_at"],
                    "quest_id": row["quest_id"],
                    "worker_id": row["worker_id"],
                    "run_id": run_id,
                    "title": EVENT_TITLES[row["event_type"]],
                    "status": row["event_type"],
                    "detail": None,
                    "execution_mode": mode,
                }
            )
        where, parameters = _predicate("tool_receipt", cursor, "r.id")
        where = where.replace("created_at", "r.created_at")
        receipts = connection.execute(
            f"SELECT r.id,r.created_at,r.execution_mode,r.result_digest,r.result_json,i.quest_id,i.worker_id,i.run_id,i.tool_name,i.state,i.resource_scope FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE {where} ORDER BY r.created_at DESC,r.id DESC LIMIT ?",
            (*parameters, limit + 1),
        ).fetchall()
        for row in receipts:
            tool = row["tool_name"] if row["tool_name"] in SAFE_TOOLS else "tool"
            digest = (
                row["result_digest"]
                if re.fullmatch(r"[0-9a-f]{64}", row["result_digest"] or "")
                else None
            )
            detail = {"tool": tool, "result_sha256": digest}
            integrity = (
                digest is not None
                and hashlib.sha256(row["result_json"].encode("utf-8")).hexdigest() == digest
            )
            if integrity:
                try:
                    output = json.loads(row["result_json"])
                    exit_code = output.get("exit_code") if isinstance(output, dict) else None
                    if type(exit_code) is int and -(2**31) <= exit_code < 2**31:
                        detail["exit_code"] = exit_code
                except (TypeError, ValueError):
                    integrity = False
            if tool in {
                "file_read",
                "file_write",
                "directory_list",
                "directory_create",
                "git_status",
            }:
                try:
                    relative = (
                        Path(row["resource_scope"])
                        .resolve()
                        .relative_to(store.workspace.root)
                        .as_posix()
                    )
                    if (
                        len(relative) <= 512
                        and relative.isprintable()
                        and not re.search(
                            r"(?i)(secret|token|credential|password|\.env|\.key|\.pem)", relative
                        )
                    ):
                        detail["path"] = relative
                except (ValueError, OSError):
                    pass
            commands = {
                "shell_inspect": "python -I --version",
                "python_compile": "Python syntax check",
                "git_status": "git status --short --untracked-files=no",
                "numeric_ast": "bounded numeric interpreter",
            }
            if tool in commands:
                detail["command"] = commands[tool]
            status = (
                row["state"]
                if integrity and row["state"] in {"succeeded", "failed", "uncertain"}
                else "unknown"
            )
            result.append(
                {
                    "kind": "tool_receipt",
                    "event_id": row["id"],
                    "seq": None,
                    "created_at": row["created_at"],
                    "quest_id": row["quest_id"],
                    "worker_id": row["worker_id"],
                    "run_id": row["run_id"],
                    "title": "Tool execution recorded",
                    "status": status,
                    "detail": detail,
                    "execution_mode": row["execution_mode"]
                    if row["execution_mode"] in {"live", "mock"}
                    else "unknown",
                }
            )
        where, parameters = _predicate("semantic_test", cursor, "s.id")
        where = where.replace("created_at", "s.created_at")
        semantic_runs = connection.execute(
            f"""SELECT s.*,w.execution_mode FROM semantic_test_runs s
            JOIN worker_runs w ON w.id=s.worker_run_id WHERE {where}
            ORDER BY s.created_at DESC,s.id DESC LIMIT ?""",
            (*parameters, limit + 1),
        ).fetchall()
        for row in semantic_runs:
            status = "unknown"
            try:
                report = json.loads(row["report_json"])
                if (
                    isinstance(report, dict)
                    and hashlib.sha256(row["report_json"].encode("utf-8")).hexdigest()
                    == row["stdout_sha256"]
                    and report.get("suite_id") == row["suite_id"] == "scheduler-v1"
                    and report.get("suite_version") == row["suite_version"]
                    and report.get("suite_digest") == row["suite_digest"]
                    and report.get("source_sha256") == row["source_sha256"]
                    and row["exit_code"] == 0
                    and type(report.get("passed")) is bool
                ):
                    status = "passed" if report["passed"] else "failed"
            except (TypeError, ValueError):
                pass
            result.append(
                {
                    "kind": "semantic_test",
                    "event_id": row["id"],
                    "seq": None,
                    "created_at": row["created_at"],
                    "quest_id": row["quest_id"],
                    "worker_id": row["worker_id"],
                    "run_id": row["worker_run_id"],
                    "title": "Independent semantic test",
                    "status": status,
                    "detail": {"suite_id": "scheduler-v1"},
                    "execution_mode": row["execution_mode"]
                    if row["execution_mode"] in {"live", "mock"}
                    else "unknown",
                }
            )
        where, parameters = _predicate("context_archive", cursor)
        archives = connection.execute(
            f"SELECT id,scope,scope_id,source_json,created_at FROM context_archives WHERE {where} ORDER BY created_at DESC,id DESC LIMIT ?",
            (*parameters, limit + 1),
        ).fetchall()
        for row in archives:
            if row["scope"] not in {"router", "worker"}:
                continue
            run = (
                connection.execute(
                    "SELECT quest_id,worker_id,execution_mode FROM worker_runs WHERE id=?",
                    (row["scope_id"],),
                ).fetchone()
                if row["scope"] == "worker"
                else None
            )
            try:
                sources = json.loads(row["source_json"])
                count = len(sources) if isinstance(sources, list) else 0
            except ValueError:
                count = 0
            result.append(
                {
                    "kind": "context_archive",
                    "event_id": str(row["id"]),
                    "seq": None,
                    "created_at": row["created_at"],
                    "quest_id": run["quest_id"] if run else None,
                    "worker_id": run["worker_id"] if run else None,
                    "run_id": row["scope_id"] if run else None,
                    "title": "Context archived",
                    "status": "archived",
                    "detail": {"scope": row["scope"], "source_count": count},
                    "execution_mode": run["execution_mode"]
                    if run and run["execution_mode"] in {"live", "mock"}
                    else "unknown",
                }
            )
        connection.commit()

    def key(item):
        identity = (
            item["event_id"].zfill(20) if item["kind"] == "context_archive" else item["event_id"]
        )
        return item["created_at"], item["kind"], identity

    result.sort(key=key, reverse=True)
    more = len(result) > limit
    page = result[:limit]
    return {
        "items": page,
        "next_before": _encode(list(key(page[-1]))) if more and page else None,
        "has_more": more,
    }
