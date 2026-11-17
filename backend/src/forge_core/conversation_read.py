"""Durable, bounded desktop reads, independent of Router/model history."""

import base64
import hashlib
import json
import re

from .store import Repository

_MAX_ROWID = 2**63 - 1
_ZERO_MARKER = "0" * 32


class ConversationCursorConflict(ValueError):
    """The forward cursor is no longer anchored in this transcript."""


def _b64_encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(raw, max_length):
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        raise ValueError("Invalid conversation cursor")
    if len(raw) > max_length:
        raise ValueError("Invalid conversation cursor")
    decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    if _b64_encode(decoded) != raw:
        raise ValueError("Invalid conversation cursor")
    return decoded.decode("ascii")


def _before_cursor(value):
    if value is None:
        return None
    digits = _b64_decode(value, 32)
    if not digits.isdecimal() or len(digits) > 18 or int(digits) < 1:
        raise ValueError("Invalid conversation cursor")
    return int(digits)


def _marker(message_id):
    return hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:32]


def _forward_cursor(row=None):
    ordinal, marker = (row["rowid"], _marker(row["id"])) if row else (0, _ZERO_MARKER)
    return _b64_encode(f"1:{ordinal}:{marker}".encode("ascii"))


def _decode_forward(value):
    raw = _b64_decode(value, 96)
    parts = re.fullmatch(r"1:(0|[1-9][0-9]{0,18}):([0-9a-f]{32})", raw)
    if parts is None:
        raise ValueError("Invalid conversation cursor")
    ordinal, marker = int(parts[1]), parts[2]
    if ordinal > _MAX_ROWID or (ordinal == 0 and marker != _ZERO_MARKER):
        raise ValueError("Invalid conversation cursor")
    return ordinal, marker


def read_conversation(store, *, before=None, after=None, limit=200):
    """Read one coherent Router snapshot and a page of committed user-visible rows.

    Reverse pagination uses the existing position-only token. Forward reads are
    anchored to the actual message so a restored transcript that reuses a position
    cannot silently skip new messages. No in-memory checkpoint survives a restart.
    """
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Invalid conversation page")
    if before is not None and after is not None:
        raise ValueError("Invalid conversation page")
    boundary = _before_cursor(before)
    resume = _decode_forward(after) if after is not None else None
    with store.connection() as connection:
        connection.execute("BEGIN")
        router = Repository(connection).ensure_router()
        if resume is not None:
            ordinal, marker = resume
            if ordinal:
                anchor = connection.execute(
                    "SELECT id FROM messages WHERE rowid=? AND conversation_id='main'",
                    (ordinal,),
                ).fetchone()
                if anchor is None or _marker(anchor["id"]) != marker:
                    raise ConversationCursorConflict("Conversation cursor must be refreshed")
            rows = connection.execute(
                "SELECT rowid,* FROM messages WHERE rowid>? AND conversation_id='main'"
                " ORDER BY rowid LIMIT ?",
                (ordinal, limit + 1),
            ).fetchall()
            page = rows[:limit]
            return {
                "conversation_id": "main",
                "router": router,
                "messages": [dict(row) for row in page],
                "mode": "delta",
                "resume_after": _forward_cursor(page[-1]) if page else after,
                "has_newer": len(rows) > limit,
                "next_before": None,
                "has_more": False,
            }
        if boundary is None:
            rows = connection.execute(
                "SELECT rowid,* FROM messages WHERE conversation_id='main'"
                " ORDER BY rowid DESC LIMIT ?",
                (limit + 1,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT rowid,* FROM messages WHERE rowid<? AND conversation_id='main'"
                " ORDER BY rowid DESC LIMIT ?",
                (boundary, limit + 1),
            ).fetchall()
        page = list(reversed(rows[:limit]))
        more = len(rows) > limit
        return {
            "conversation_id": "main",
            "router": router,
            "messages": [dict(row) for row in page],
            "mode": "snapshot",
            "resume_after": (
                _forward_cursor(page[-1] if page else None) if boundary is None else None
            ),
            "has_newer": False,
            "next_before": _b64_encode(str(page[0]["rowid"]).encode("ascii")) if more else None,
            "has_more": more,
        }


def read_committed_turn(store, request_id):
    """Look up a committed response without running the Router or a Provider."""
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
        raise ValueError("Invalid request ID")
    with store.connection() as connection:
        row = connection.execute(
            "SELECT result_json FROM router_turns WHERE request_id=? AND conversation_id='main'",
            (request_id,),
        ).fetchone()
    # Not yet present does not establish whether the original request is still
    # executing, was transport-lost, or was never admitted.
    if row is None:
        return {"state": "unknown"}
    result = json.loads(row["result_json"])
    return {
        "state": "committed",
        "user_message": result["user_message"],
        "assistant_message": result["assistant_message"],
    }
