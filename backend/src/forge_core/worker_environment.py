"""Stable private working directories for durable Worker runs, not an OS sandbox."""

import os
import re
import stat
from contextlib import nullcontext
from pathlib import Path

from .domain import DomainError


def _path_context(store, connection, quest_id):
    """Read identity only; project association is never a filesystem grant."""
    row = connection.execute(
        "SELECT p.id,p.root FROM task_project_links l "
        "JOIN workspace_projects p ON p.id=l.project_id WHERE l.quest_id=?",
        (quest_id,),
    ).fetchone()
    storage_root = str(Path(store.workspace.root).resolve())
    return {
        "storage_root": storage_root,
        "tool_path_base": storage_root,
        "project": {
            "id": row["id"],
            "root": row["root"],
            "reference": "project:" + row["id"],
            "source": "persisted_task_project_link",
            "grants_tool_access": False,
        }
        if row is not None
        else None,
    }


def preview_worker_environment(store, quest_id, *, connection):
    """Conservative framing before a run exists; never inspect/create its paths."""
    if connection.execute("SELECT 1 FROM quests WHERE id=?", (quest_id,)).fetchone() is None:
        raise DomainError("Worker environment requires a persisted Quest")
    return _environment(
        store, quest_id, "run_" + "0" * 32, _path_context(store, connection, quest_id)
    )


def _environment(store, quest_id, run_id, context):
    root = Path(context["storage_root"]) / "data" / "worker-environments" / quest_id / run_id
    return {
        "version": 1,
        "workspace_id": store.workspace.id,
        "quest_id": quest_id,
        "run_id": run_id,
        "root": str(root),
        "cwd": str(root / "work"),
        "tmp": str(root / "tmp"),
        "output": str(root / "output"),
        "isolation": "private_working_directory",
        "os_sandbox": False,
        **context,
    }


def worker_environment(store, run_id, *, create=True, connection=None):
    """Resolve only a persisted run; no caller path can select an execution root.

    Directory identity survives a Core restart because workspace, Quest and run
    IDs already persist. Approved shared file operations remain governed by their
    original resource scope; this helper grants no additional filesystem access.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise DomainError("Invalid Worker environment identity")
    with store.connection() if connection is None else nullcontext(connection) as opened:
        row = opened.execute("SELECT quest_id FROM worker_runs WHERE id=?", (run_id,)).fetchone()
        if row is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row[0]):
            raise DomainError("Worker environment requires a persisted run")
        context = _path_context(store, opened, row[0])
    quest_id = row[0]
    workspace = Path(store.workspace.root).resolve()

    def checked_directory(path):
        # Reject links/junctions at every component, before and after creation.
        current = workspace
        for part in path.relative_to(workspace).parts:
            current = current / part
            if create:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            try:
                info = current.lstat()
            except FileNotFoundError:
                if create:
                    raise DomainError("Worker environment directory unavailable") from None
                continue
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or current.resolve() != current
            ):
                raise DomainError("Worker environment cannot use a linked directory")
        return str(path)

    environment = _environment(store, quest_id, run_id, context)
    for key in ("root", "cwd", "tmp", "output"):
        environment[key] = checked_directory(Path(environment[key]))
    return environment


def process_environment(base, environment):
    """Use per-run temporary storage without inheriting secrets or a shell home."""
    result = dict(base)
    result.update(TEMP=environment["tmp"], TMP=environment["tmp"], TMPDIR=environment["tmp"])
    if os.name != "nt":
        result["XDG_CACHE_HOME"] = environment["tmp"]
    return result
