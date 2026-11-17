"""On-demand exact-run historical file receipts, never current file verification."""

import base64
import hashlib
import json
import re
import sqlite3

from .artifact_diff import (
    ArtifactDiffTooLarge,
    ArtifactDiffUnavailable,
    VersionReadBudget,
    _bounded_versions,
    _relative_path,
)
from .artifact_revision import verify_file_revision_chain
from .domain import DomainError
from .subagent_activity import require_scoped_subagent


def _cursor_scope(store, conversation_id, run_id, project_id):
    raw = json.dumps(
        [store.workspace.id, conversation_id, project_id, run_id, "artifact_receipts"],
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _encode_cursor(scope, ordinal):
    return base64.urlsafe_b64encode(f"a1:{scope}:{ordinal}".encode()).decode().rstrip("=")


def _decode_cursor(value, scope):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
            raise ValueError
        match = re.fullmatch(r"a1:([0-9a-f]{24}):([1-9][0-9]{0,18})", raw.decode("ascii"))
        if not match or match[1] != scope or int(match[2]) > 2**63 - 1:
            raise ValueError
        return int(match[2])
    except (ValueError, UnicodeError):
        raise ValueError("Invalid artifact cursor") from None


def read_subagent_artifacts(
    store, conversation_id, run_id, *, project_id=None, before=None, limit=10
):
    """A bounded page of latest retained successful receipts per file path.

    Scope and source proof use the same read transaction as receipt verification.
    Invalid/private/over-budget candidates are omitted, without falling back to
    an older version or asserting no file exists. The scan cursor still advances.
    Returned hashes describe immutable historical content, not current files.
    """
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Invalid artifact page size")
    scope = _cursor_scope(store, conversation_id, run_id, project_id)
    boundary = _decode_cursor(before, scope)
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        run = require_scoped_subagent(
            connection, store, conversation_id, run_id, project_id=project_id
        )
        rows = connection.execute(
            "SELECT r.rowid AS ordinal,r.id AS receipt_id,r.created_at,i.resource_scope "
            "FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id "
            "WHERE i.run_id=? AND i.state='succeeded' "
            "AND i.tool_name IN ('file_write','file_revise') "
            "AND (? IS NULL OR r.rowid<?) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM action_receipts newer JOIN action_intents next ON next.id=newer.intent_id "
            "WHERE next.run_id=i.run_id AND next.resource_scope=i.resource_scope "
            "AND next.state='succeeded' AND next.tool_name IN ('file_write','file_revise') "
            "AND newer.rowid>r.rowid) ORDER BY r.rowid DESC LIMIT ?",
            (run_id, boundary, boundary, limit + 1),
        ).fetchall()
        budget = VersionReadBudget()
        items, unavailable = [], False
        for row in rows[:limit]:
            try:
                path = _relative_path(store, row["resource_scope"])
                _bounded_versions(connection, run_id, row["receipt_id"], budget=budget)
                verified = verify_file_revision_chain(connection, run_id, row["receipt_id"])
                items.append(
                    {
                        "receipt_id": row["receipt_id"],
                        "path": path,
                        "sha256": verified["sha256"],
                        "bytes": verified["bytes"],
                        "created_at": row["created_at"],
                        "execution_mode": run["execution_mode"],
                        "change": "revised" if verified["prior_receipt_id"] else "created",
                    }
                )
            except (
                ArtifactDiffTooLarge,
                ArtifactDiffUnavailable,
                DomainError,
                ValueError,
                TypeError,
                KeyError,
                IndexError,
                OSError,
                sqlite3.Error,
                RecursionError,
            ):
                unavailable = True
        more = len(rows) > limit
        return {
            "conversation_id": conversation_id,
            "workspace_id": store.workspace.id,
            "project_id": project_id,
            "run_id": run_id,
            "state": run["state"],
            "execution_mode": run["execution_mode"],
            "provenance": "historical_receipts",
            "current_files_verified": False,
            "items": items,
            "unavailable": unavailable,
            "has_more": more,
            "next_before": _encode_cursor(scope, rows[limit - 1]["ordinal"]) if more else None,
        }
