"""Local project discovery and durable associations, independent of tool grants."""

import hashlib
import json
import os
import re
from pathlib import Path

from .domain import ForgeEvent, now_iso

MARKERS = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "CMakeLists.txt",
    ".git",
)


def migrate(connection):
    connection.execute("""CREATE TABLE workspace_projects(
        id TEXT PRIMARY KEY, root TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        markers_json TEXT NOT NULL, detected_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE task_project_links(
        quest_id TEXT PRIMARY KEY REFERENCES quests(id),
        project_id TEXT NOT NULL REFERENCES workspace_projects(id),
        associated_at TEXT NOT NULL)""")


def detect_project(path, *, boundary=None, max_ancestors=12):
    """Inspect only named markers along a supplied local path's bounded ancestors.

    Discovery establishes identity, not authority. Explicit absolute paths may
    refer to other projects; this does not add a filesystem grant to any Worker.
    """
    if type(max_ancestors) is not int or not 0 <= max_ancestors <= 64:
        raise ValueError("Invalid project discovery depth")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError("Project discovery requires an absolute path")
    candidate = candidate.resolve(strict=True)
    if candidate.is_file():
        candidate = candidate.parent
    if not candidate.is_dir():
        raise ValueError("Project path is not a directory or regular file")
    stop = Path(boundary).resolve(strict=True) if boundary is not None else None
    if stop is not None and not candidate.is_relative_to(stop):
        raise ValueError("Project path is outside the discovery boundary")
    current = candidate
    markers = []
    for _ in range(max_ancestors + 1):
        markers = [name for name in MARKERS if (current / name).exists()]
        if markers:
            candidate = current
            break
        if current == stop or current.parent == current:
            break
        current = current.parent
    canonical = os.path.normcase(str(candidate))
    return {
        "id": "project_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
        "root": str(candidate),
        "name": candidate.name or str(candidate),
        "markers": markers,
        "source": "local_marker_discovery" if markers else "local_directory",
        "grants_tool_access": False,
    }


class WorkspaceDiscovery:
    def __init__(self, store):
        self.store = store

    def register_user_request(self, content, *, connection):
        """Present explicitly named user projects; never bind tasks or grant tools.

        Only Router.handle_user_message supplies the current authenticated user's
        unmodified text. Call after request-ID replay/conflict checks and inside
        the existing pre-inference snapshot transaction, never on model output,
        historical/tool text, or a background Trigger payload.
        """
        projects = {}
        for path in _named_paths(content):
            try:
                project = detect_project(path)
            except (OSError, ValueError):
                continue
            projects[project["id"]] = project
        timestamp = now_iso()
        for project in projects.values():
            connection.execute(
                "INSERT INTO workspace_projects VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET markers_json=excluded.markers_json,"
                "detected_at=excluded.detected_at",
                (
                    project["id"],
                    project["root"],
                    project["name"],
                    json.dumps(project["markers"]),
                    timestamp,
                ),
            )
        return list(projects.values())

    def associate_request(self, quest_id):
        """Recognize a uniquely named local project from the immutable request.

        Inspect named paths and manifest presence only. Discovery never reads
        project content, follows a model-selected path, or adds tool grants.
        Multiple distinct projects retain the neutral storage-workspace link.
        """
        existing = self.for_quest(quest_id)
        with self.store.connection() as connection:
            quest = connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
            if quest is None:
                raise KeyError(quest_id)
            mandate = connection.execute(
                "SELECT id FROM delegation_mandates WHERE quest_id=?", (quest_id,)
            ).fetchone()
            content = quest["goal"]
            if mandate:
                from .superagent_delegation import _active

                source, _ = _active(connection, mandate["id"], quest_id)
                if source["source_message_id"]:
                    content = source["content"]
                else:
                    content = connection.execute(
                        "SELECT payload_json FROM superagent_triggers WHERE id=?",
                        (source["source_trigger_id"],),
                    ).fetchone()[0]
                    # Decode once so Windows paths keep their actual separators.
                    content = "\n".join(_text_values(json.loads(content)))
        projects = {}
        for path in _named_paths(content):
            try:
                project = detect_project(path)
            except (OSError, ValueError):
                continue
            projects[project["id"]] = project
        # Only a verified immutable user/Trigger request may correct an existing
        # model-selected identity. Missing, ambiguous or mutable paths preserve
        # that identity, and association never changes tool grants.
        if existing and (not mandate or len(projects) != 1):
            return existing
        if existing and existing["id"] in projects:
            return existing
        path = next(iter(projects.values()))["root"] if len(projects) == 1 else None
        return self.associate(quest_id, path)

    def associate(self, quest_id, path=None):
        """Bind a task to its detected project in the same event transaction."""
        supplied = self.store.workspace.root if path is None else Path(path)
        boundary = (
            self.store.workspace.root
            if supplied.resolve().is_relative_to(self.store.workspace.root)
            else None
        )
        project = detect_project(supplied, boundary=boundary)
        timestamp = now_iso()
        with self.store.unit_of_work() as work:
            quest = work.get_quest(quest_id)
            if quest is None:
                raise KeyError(quest_id)
            old = work.connection.execute(
                "SELECT project_id FROM task_project_links WHERE quest_id=?", (quest_id,)
            ).fetchone()
            work.connection.execute(
                "INSERT INTO workspace_projects VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET markers_json=excluded.markers_json",
                (
                    project["id"],
                    project["root"],
                    project["name"],
                    json.dumps(project["markers"]),
                    timestamp,
                ),
            )
            if old is None or old[0] != project["id"]:
                work.connection.execute(
                    "INSERT INTO task_project_links VALUES (?,?,?) "
                    "ON CONFLICT(quest_id) DO UPDATE SET project_id=excluded.project_id,"
                    "associated_at=excluded.associated_at",
                    (quest_id, project["id"], timestamp),
                )
                work.append_event(
                    ForgeEvent(
                        event_type="workspace.task_associated",
                        conversation_id=quest["conversation_id"],
                        quest_id=quest_id,
                        payload={"project_id": project["id"], "source": project["source"]},
                    )
                )
        return project

    def for_quest(self, quest_id):
        with self.store.connection() as connection:
            row = connection.execute(
                "SELECT p.* FROM workspace_projects p JOIN task_project_links l "
                "ON l.project_id=p.id WHERE l.quest_id=?",
                (quest_id,),
            ).fetchone()
        return self._project(row) if row else None

    def list_projects(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid project listing limit")
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM workspace_projects ORDER BY detected_at,id LIMIT ?", (limit,)
            ).fetchall()
        return [self._project(row) for row in rows]

    @staticmethod
    def _project(row):
        markers = json.loads(row["markers_json"])
        return {
            "id": row["id"],
            "root": row["root"],
            "name": row["name"],
            "markers": markers,
            "source": "local_marker_discovery" if markers else "local_directory",
            "grants_tool_access": False,
        }


def _text_values(value, depth=0):
    if depth > 6:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in list(value.values())[:64] for text in _text_values(item, depth + 1)]
    if isinstance(value, list):
        return [text for item in value[:64] for text in _text_values(item, depth + 1)]
    return []


def _named_paths(content):
    # Quotes preserve spaces; bare paths deliberately stop at punctuation or
    # whitespace. Limit work even for a large spilled original request.
    text = content[:262144]
    quoted = re.findall(r"[`\"']([^`\"'\r\n]{1,4096})[`\"']", text)
    bare = re.findall(r"(?<![\w:/])(?:[A-Za-z]:[\\/]|/)[^\s`\"'<>|，。；：、]{1,4096}", text)
    seen = set()
    for token in quoted + bare:
        candidate = Path(token.rstrip(".,;:)]}"))
        if not candidate.is_absolute() or str(candidate) in seen:
            continue
        seen.add(str(candidate))
        if len(seen) > 8:
            break
        yield candidate
