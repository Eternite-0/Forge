"""Source-verified, conversation-scoped subagent presentation.

Only persistent delegated Workers belong here. A bare Worker row (for example a
manual test run) is not evidence of conversational delegation. No raw messages,
model reasoning, tool parameters, lease tokens or provider secrets are exposed.
"""

import base64
import hashlib
import json
import math
import re
import time

from .timeline import EVENT_TITLES

_TITLES = {
    **EVENT_TITLES,
    "worker.progress": "Worker progress recorded",
    "worker.model_response_rejected": "Response requires correction",
}
_TITLES = {
    key: value
    for key, value in _TITLES.items()
    if key.startswith("worker.") or key == "task.verified"
}
_OUTCOMES = {
    "queued": ("queued", "Waiting to run"),
    "running": ("running", "Working"),
    "paused": ("paused", "Paused"),
    "blocked": ("blocked", "Blocked; needs review"),
    "review_pending": ("awaiting_review", "Awaiting independent review"),
    "failed": ("failed", "Failed"),
    "cancelled": ("cancelled", "Cancelled"),
    "succeeded": ("verified", "Worker result verified"),
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_PHASE_STATES = {
    "active": ("queued", "running", "paused", "blocked", "review_pending"),
    "terminal": ("succeeded", "failed", "cancelled"),
}


class SubagentNotFound(KeyError):
    """The requested resource does not belong to the requested visible scope."""


def _text(value, maximum):
    return _CONTROL.sub(" ", value)[:maximum] if isinstance(value, str) else ""


def _identity(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or _CONTROL.search(value):
        raise ValueError("Invalid activity scope")
    return value


def _scope_key(workspace_id, conversation_id, project_id, phase):
    raw = json.dumps([workspace_id, conversation_id, project_id, phase], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _encode_cursor(ordinal, scope):
    raw = f"2:{scope}:{ordinal}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value, scope):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
            raise ValueError
        match = re.fullmatch(r"2:([0-9a-f]{24}):([1-9][0-9]{0,18})", raw.decode("ascii"))
        if not match or match[1] != scope or int(match[2]) > 2**63 - 1:
            raise ValueError
        return int(match[2])
    except (ValueError, UnicodeError):
        raise ValueError("Invalid subagent cursor") from None


def _source(connection, row, workspace_id):
    """Prove the original source without confusing current authorization with history."""
    if row["source_message_id"] is not None and row["source_trigger_id"] is None:
        source = connection.execute(
            "SELECT role,content,conversation_id FROM messages WHERE id=?",
            (row["source_message_id"],),
        ).fetchone()
        if (
            source is not None
            and source["role"] == "user"
            and source["conversation_id"] == row["conversation_id"]
            and hashlib.sha256(source["content"].encode("utf-8")).hexdigest()
            == row["source_sha256"]
        ):
            return {"kind": "message", "id": row["source_message_id"]}
    elif row["source_trigger_id"] is not None and row["source_message_id"] is None:
        source = connection.execute(
            "SELECT t.payload_json,t.payload_sha256,h.workspace_id,h.conversation_id,"
            "h.source_id AS thread_source,s.id AS source_id,s.workspace_id AS source_workspace,"
            "s.conversation_id AS source_conversation FROM superagent_triggers t "
            "JOIN superagent_threads h ON h.id=t.thread_id "
            "JOIN superagent_sources s ON s.id=t.source_id WHERE t.id=?",
            (row["source_trigger_id"],),
        ).fetchone()
        if (
            source is not None
            and source["workspace_id"] == source["source_workspace"] == workspace_id
            and source["conversation_id"] == source["source_conversation"] == row["conversation_id"]
            and source["thread_source"] == source["source_id"]
            and hashlib.sha256(source["payload_json"].encode("utf-8")).hexdigest()
            == source["payload_sha256"]
            == row["source_sha256"]
        ):
            return {"kind": "trigger", "id": row["source_trigger_id"]}
    return None


def _milestones(connection, row, limit):
    placeholders = ",".join("?" for _ in _TITLES)
    events = connection.execute(
        "SELECT id,event_type,created_at,seq FROM events "
        "WHERE conversation_id=? AND quest_id=? AND worker_id=? "
        f"AND event_type IN ({placeholders}) "
        "AND json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.run_id')=? "
        "ORDER BY seq DESC LIMIT ?",
        (row["conversation_id"], row["quest_id"], row["worker_id"], *_TITLES, row["id"], limit),
    ).fetchall()
    return [
        {
            "event_id": event["id"],
            "event_type": event["event_type"],
            "title": _TITLES[event["event_type"]],
            "created_at": event["created_at"],
            "seq": event["seq"],
        }
        for event in reversed(events)
    ]


def _project(connection, row, source, *, now, milestone_limit):
    lease_status = "none"
    if row["state"] == "running":
        valid_times = all(
            type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] > now
            for key in ("lease_until", "deadline")
        )
        lease_status = "active" if valid_times else "expired"
    kind, label = _OUTCOMES.get(row["state"], ("unknown", "State unavailable"))
    if lease_status == "expired":
        kind, label = "lease_expired", "Lease expired; recovery pending"
    return {
        "run_id": row["id"],
        "worker_id": row["worker_id"],
        "quest_id": row["quest_id"],
        "role": _text(row["role_id"], 128),
        "task_title": _text(row["task_title"], 320),
        "task_objective": _text(row["task_objective"], 1000),
        "plan_id": row["plan_id"],
        "node_key": row["node_key"],
        "model_id": _text(row["model_id"], 128),
        "provider_id": _text(row["provider_id"], 128),
        "state": row["state"],
        "execution_mode": row["execution_mode"],
        "created_at": row["created_at"],
        "parent_message_id": source["id"] if source["kind"] == "message" else None,
        "source": source,
        "project_id": row["project_id"],
        "lease_status": lease_status,
        "steps": row["steps"],
        "tool_spent": row["tool_spent"],
        "delegation_state": row["delegation_state"],
        "outcome": {"kind": kind, "label": label},
        "milestones": _milestones(connection, row, milestone_limit),
    }


_SELECT = (
    "SELECT r.rowid AS ordinal,r.*,q.conversation_id,q.title AS task_title,"
    "n.objective AS task_objective,m.source_message_id,m.source_trigger_id,m.source_sha256,"
    "m.state AS delegation_state,p.project_id FROM worker_runs r "
    "JOIN quests q ON q.id=r.quest_id "
    "JOIN workers w ON w.id=r.worker_id AND w.quest_id=q.id "
    "JOIN quest_plans plan ON plan.id=r.plan_id AND plan.quest_id=q.id "
    "JOIN task_nodes n ON n.plan_id=r.plan_id AND n.node_key=r.node_key "
    "JOIN delegation_mandates m ON m.quest_id=q.id "
    "JOIN team_proposals team ON team.quest_id=q.id AND team.plan_revision=r.plan_revision "
    "AND team.proposal_digest=r.team_digest AND team.plan_digest=r.plan_digest "
    "AND team.approved_by='router_mandate:'||m.id "
    "LEFT JOIN task_project_links p ON p.quest_id=q.id "
)


def read_subagents(
    store, conversation_id, *, project_id=None, before=None, limit=50, run_id=None, phase="all"
):
    """One snapshot with bounded candidates; cursors are tied to the exact scope.

    The active phase includes blocked/paused/reviewing work and expired leases;
    only a durable terminal state moves a run to terminal history. Phase is
    applied before pagination, so newer completed runs cannot hide older work.

    A candidate with unavailable/corrupt source is omitted, not reassigned to a
    neighboring turn. Such a page may be shorter (even empty); next_before still
    advances past the scanned candidates, so clients can continue without loss.
    """
    _identity(conversation_id)
    if project_id is not None:
        _identity(project_id)
    if run_id is not None:
        _identity(run_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid subagent page size")
    if not isinstance(phase, str) or phase not in {"all", *_PHASE_STATES}:
        raise ValueError("Invalid subagent phase")
    if run_id is not None and before is not None:
        raise ValueError("Detail cannot be paginated")
    if run_id is not None and phase != "all":
        raise ValueError("Detail cannot be phase-filtered")
    scope = _scope_key(store.workspace.id, conversation_id, project_id, phase)
    boundary = _decode_cursor(before, scope)
    now = time.time()
    with store.connection() as connection:
        connection.execute("BEGIN")
        if (
            connection.execute(
                "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
            is None
        ):
            raise SubagentNotFound("Conversation not found")
        if (
            project_id is not None
            and connection.execute(
                "SELECT 1 FROM workspace_projects WHERE id=?", (project_id,)
            ).fetchone()
            is None
        ):
            raise SubagentNotFound("Project not found")
        predicate = (
            " WHERE q.conversation_id=? AND plan.approved_by='router_mandate:'||m.id"
            " AND plan.digest=r.plan_digest"
        )
        params = [conversation_id]
        if phase != "all":
            states = _PHASE_STATES[phase]
            predicate += " AND r.state IN (" + ",".join("?" for _ in states) + ")"
            params.extend(states)
        if project_id is not None:
            predicate += " AND p.project_id=?"
            params.append(project_id)
        if run_id is not None:
            predicate += " AND r.id=?"
            params.append(run_id)
        if boundary is not None:
            predicate += " AND r.rowid<?"
            params.append(boundary)
        rows = connection.execute(
            _SELECT + predicate + " ORDER BY r.rowid DESC LIMIT ?",
            (*params, 1 if run_id is not None else limit + 1),
        ).fetchall()
        items = []
        for row in rows[:limit]:
            source = _source(connection, row, store.workspace.id)
            if source is not None:
                items.append(
                    _project(
                        connection,
                        row,
                        source,
                        now=now,
                        milestone_limit=20 if run_id else 5,
                    )
                )
        envelope = {
            "conversation_id": conversation_id,
            "workspace_id": store.workspace.id,
            "project_id": project_id,
            "phase": phase,
        }
        if run_id is not None:
            if not items:
                raise SubagentNotFound("Subagent not found")
            return {**envelope, "item": items[0]}
        more = len(rows) > limit
        return {
            **envelope,
            "items": items,
            "next_before": _encode_cursor(rows[limit - 1]["ordinal"], scope) if more else None,
            "has_more": more,
        }
