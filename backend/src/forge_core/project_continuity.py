"""Project-focused context from existing links; identities never grant tool access."""

import json
import re

from .workspace_discovery import _named_paths


def project_context(connection, text, *, limit=32):
    rows = connection.execute(
        "SELECT p.id,p.root,p.name FROM workspace_projects p ORDER BY p.detected_at DESC,p.id LIMIT ?",
        (limit + 1,),
    ).fetchall()
    lowered = text.casefold().replace("\\", "/")
    projects = [{**dict(row), "reference": "project:" + row["id"]} for row in rows[:limit]]
    selected = set()
    # Parse paths exactly as discovery does. A path identifies its most specific
    # known project; neither a sibling prefix nor a duplicate basename is a match.
    prose = lowered
    for path in _named_paths(text):
        # Discovery records resolved identities. Normalize dot segments and
        # symlink aliases identically before selecting the project fence; an
        # unresolved lexical prefix can otherwise select the wrong sibling.
        try:
            named = str(path.resolve()).casefold().replace("\\", "/").rstrip("/") or "/"
        except (OSError, RuntimeError, ValueError):
            continue
        matches = [
            project for project in projects
            if named == project["root"].casefold().replace("\\", "/").rstrip("/")
            or named.startswith(project["root"].casefold().replace("\\", "/").rstrip("/") + "/")
        ]
        if matches:
            depth = max(len(project["root"].rstrip("/\\")) for project in matches)
            selected.update(project["id"] for project in matches
                            if len(project["root"].rstrip("/\\")) == depth)
        prose = prose.replace(str(path).casefold().replace("\\", "/"), " ")
    for project in projects:
        name = project["name"].casefold()
        # Chinese prose commonly touches an ASCII project name without spaces.
        # Keep ordinary word/hyphen boundaries so "alpha" never matches
        # "alphabet" or "alpha-plugin".
        boundary_left, boundary_right = r"(?<![\w-])", r"(?![\w-])"
        if name.isascii():
            cjk = r"\u3400-\u4dbf\u4e00-\u9fff"
            boundary_left = rf"(?:(?<![\w-])|(?<=[{cjk}]))"
            boundary_right = rf"(?:(?![\w-])|(?=[{cjk}]))"
        # A shared short name is ambiguous. Exact IDs/roots remain available.
        if (
            re.search(r"(?<![\w-])" + re.escape(project["id"]) + r"(?![\w-])", text)
            or (
                len(name) >= 3
                and re.search(boundary_left + re.escape(name) + boundary_right, prose)
            )
        ):
            selected.add(project["id"])
    explicit_quests = re.findall(r"\bquest_[A-Za-z0-9]+\b", text)
    if explicit_quests:
        for quest_id in explicit_quests[:20]:
            row = connection.execute(
                "SELECT project_id FROM task_project_links WHERE quest_id=?", (quest_id,)
            ).fetchone()
            if row:
                selected.add(row[0])
    return {
        "projects": projects,
        "selected_project_ids": sorted(selected),
        "focused_project_id": next(iter(selected)) if len(selected) == 1 else None,
        "ambiguous": len(selected) > 1,
        "truncated": len(rows) > limit,
        "grants_tool_access": False,
    }


def task_projects(connection, quests):
    result = {}
    for quest in quests:
        row = connection.execute(
            "SELECT project_id FROM task_project_links WHERE quest_id=?", (quest["id"],)
        ).fetchone()
        if row:
            result[quest["id"]] = row[0]
    return result


def coordination_context(connection, quests, projects):
    """Bounded Core facts, distinct from model conclusions about progress."""
    result = []
    for quest in sorted(quests, key=lambda item: (-item["priority"], item["created_at"]))[:24]:
        runs = [
            dict(row)
            for row in connection.execute(
                "SELECT id,state,steps,tool_spent FROM worker_runs WHERE quest_id=? "
                "ORDER BY created_at DESC LIMIT 8",
                (quest["id"],),
            )
        ]
        result.append(
            {
                "quest_ref": "quest:" + quest["id"],
                "project_ref": "project:" + projects[quest["id"]]
                if quest["id"] in projects
                else None,
                "priority": quest["priority"],
                "status": quest["status"],
                "workers": [
                    {
                        "reference": "run:" + run["id"],
                        **{key: run[key] for key in ("state", "steps", "tool_spent")},
                    }
                    for run in runs
                ],
                "blockers": ["run:" + run["id"] for run in runs if run["state"] == "blocked"],
            }
        )
    return result


def bind_presented_project(work, quest_id, project_id, presented):
    """Bind only a source-presented identity inside the original Router commit."""
    if project_id is None:
        return
    from .domain import DomainError, ForgeEvent, now_iso

    if project_id not in {item["id"] for item in presented["projects"]}:
        raise DomainError("Router project identity was not presented")
    focus = presented["focused_project_id"]
    if focus is not None and focus != project_id:
        raise DomainError("Router project conflicts with the user's project focus")
    work.connection.execute(
        "INSERT INTO task_project_links(quest_id,project_id,associated_at) VALUES (?,?,?)",
        (quest_id, project_id, now_iso()),
    )
    work.append_event(
        ForgeEvent(
            event_type="workspace.task_associated",
            quest_id=quest_id,
            payload={
                "project_id": project_id,
                "source": "router_presented_project",
                "grants_tool_access": False,
            },
        )
    )


def project_record_allowed(connection, kind, record, project_id):
    """Context relevance fence; project association never becomes tool authority.

    Global mixed transcripts/archives stay externally stored. Explicit multi-project
    requests have no single focus and can still traverse the complete history.
    """
    if project_id is None:
        return True
    if kind == "project":
        return record["id"] == project_id
    quest_ids = []
    if kind == "quest":
        quest_ids = [record["id"]]
    elif record.get("quest_id"):
        quest_ids = [record["quest_id"]]
    elif kind == "receipt":
        row = connection.execute(
            "SELECT quest_id FROM action_intents WHERE id=?", (record["intent_id"],)
        ).fetchone()
        quest_ids = [row[0]] if row else []
    elif kind == "message":
        metadata = record.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        # Prefer authoritative task/source links; metadata is generated by Core.
        quest_ids = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM quests WHERE json_extract(metadata,'$.original_request_message_id')=? "
                "OR json_extract(metadata,'$.goal_update_message_id')=?",
                (record["id"], record["id"]),
            )
        ]
        if record.get("role") == "assistant":
            cycle = metadata.get("cycle")
            if isinstance(cycle, dict) and isinstance(cycle.get("quest_id"), str):
                # Core's committed turn binds its reply to the selected task;
                # later explicit-path corrections must move both turn halves.
                quest_ids.append(cycle["quest_id"])
        if metadata.get("event_id"):
            row = connection.execute(
                "SELECT quest_id FROM events WHERE id=?", (metadata["event_id"],)
            ).fetchone()
            if row and row[0]:
                quest_ids.append(row[0])
        if not quest_ids:
            return metadata.get("project_id") == project_id
    elif kind == "memory":
        quest_ids = record.get("related_quest_ids", [])
        if isinstance(quest_ids, str):
            quest_ids = json.loads(quest_ids)
    elif kind == "archive" and record.get("scope") == "worker":
        row = connection.execute(
            "SELECT quest_id FROM worker_runs WHERE id=?", (record["scope_id"],)
        ).fetchone()
        quest_ids = [row[0]] if row else []
    return bool(quest_ids) and all(
        connection.execute(
            "SELECT 1 FROM task_project_links WHERE quest_id=? AND project_id=?",
            (quest_id, project_id),
        ).fetchone()
        for quest_id in quest_ids
    )
