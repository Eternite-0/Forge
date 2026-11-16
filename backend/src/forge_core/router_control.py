"""Conversational controls on an existing mandate, within the Router commit."""

from .domain import DomainError, ForgeEvent, now_iso


def control_task(work, quest, action, *, source_message_id):
    from .superagent_delegation import _active

    c = work.connection
    mandate = c.execute(
        "SELECT id FROM delegation_mandates WHERE quest_id=?", (quest["id"],)
    ).fetchone()
    if mandate is None:
        raise DomainError("Conversational control requires an autonomous task mandate")
    record, _policy = _active(c, mandate["id"], quest["id"])
    states = {
        "pause": {"approved": "paused", "queued": "paused", "running": "paused"},
        "resume": {"paused": "queued"},
        "cancel": {
            state: "cancelled"
            for state in (
                "proposed",
                "awaiting_approval",
                "approved",
                "queued",
                "running",
                "paused",
                "blocked",
                "needs_input",
            )
        },
    }
    target = states.get(action, {}).get(quest["status"])
    if target is None:
        raise DomainError("Task control is invalid in the current state")
    actor = "router_mandate:" + record["id"]
    changed = c.execute(
        "UPDATE quests SET status=?,version=version+1,updated_at=? WHERE id=? AND version=?",
        (target, now_iso(), quest["id"], quest["version"]),
    )
    if changed.rowcount != 1:
        raise DomainError("Task control lost its revision")
    plan_status = {"pause": "paused", "resume": "approved", "cancel": "cancelled"}[action]
    c.execute(
        "UPDATE quest_plans SET status=? WHERE quest_id=? AND revision=? AND status IN ('proposed','approved','paused')",
        (plan_status, quest["id"], quest["plan_revision"]),
    )
    work.append_event(
        ForgeEvent(
            event_type="quest.status_changed",
            quest_id=quest["id"],
            payload={
                "from": quest["status"],
                "to": target,
                "actor": actor,
                "source_message_id": source_message_id,
            },
        )
    )
    for row in c.execute(
        "SELECT worker_id,plan_id,node_key FROM worker_runs WHERE quest_id=? "
        "AND state IN ('queued','running','paused','blocked')",
        (quest["id"],),
    ):
        work.append_event(
            ForgeEvent(
                event_type="worker.command." + action,
                quest_id=quest["id"],
                worker_id=row["worker_id"],
                payload={
                    "plan_id": row["plan_id"],
                    "task_node_key": row["node_key"],
                    "actor": actor,
                    "control_scope": "quest",
                    "source_message_id": source_message_id,
                },
            )
        )
