"""Source-bound Router feedback requests; this grants no execution approval."""

import json

from .domain import DomainError


def remaining_replan_context(store, connection, quest_id, source_event_id):
    """Offer remaining planning only for a stopped approved, partially done DAG."""
    from .quest_graph import QuestGraphService

    quest = connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
    event = connection.execute(
        "SELECT * FROM events WHERE id=? AND quest_id=?", (source_event_id, quest_id)
    ).fetchone()
    if (
        quest is None
        or quest["status"] not in {"blocked", "needs_input", "paused"}
        or event is None
        or event["event_type"] not in {"worker.blocked", "worker.help_requested", "worker.failed"}
    ):
        return None
    try:
        parent = QuestGraphService._remaining_parent(connection, quest)
    except DomainError:
        return None
    payload = json.loads(event["payload"])
    run = connection.execute(
        "SELECT * FROM worker_runs WHERE id=? AND quest_id=? AND worker_id=?",
        (payload.get("run_id"), quest_id, event["worker_id"]),
    ).fetchone()
    if (
        run is None
        or run["plan_id"] != parent["id"]
        or run["state"] not in {"blocked", "failed"}
        or payload.get("plan_id") != parent["id"]
        or payload.get("task_node_key") != run["node_key"]
        or connection.execute(
            "SELECT 1 FROM quest_plans WHERE quest_id=? AND status='proposed' LIMIT 1", (quest_id,)
        ).fetchone()
        or connection.execute(
            "SELECT 1 FROM team_proposals WHERE quest_id=? AND plan_revision=? "
            "AND status='proposed' LIMIT 1",
            (quest_id, parent["revision"]),
        ).fetchone()
    ):
        return None
    nodes = connection.execute(
        "SELECT node_key,status FROM task_nodes WHERE plan_id=? ORDER BY node_key", (parent["id"],)
    ).fetchall()
    completed = [row["node_key"] for row in nodes if row["status"] == "succeeded"]
    if not completed or all(row["status"] == "succeeded" for row in nodes):
        return None
    return {
        "parent_plan_id": parent["id"],
        "parent_digest": parent["digest"],
        "parent_revision": parent["revision"],
        "source_event_id": source_event_id,
        "completed_node_keys": completed,
        "failed_node_key": run["node_key"],
        "instruction": "replan_remaining requests a new proposal only. Completed evidence, all counters and original task ceilings remain. The user must approve the replacement plan and team before execution.",
    }
