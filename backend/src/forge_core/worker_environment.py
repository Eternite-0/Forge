"""Stable private working directories for durable Worker runs, not an OS sandbox."""

import os
import re
import stat
from contextlib import nullcontext
from pathlib import Path

from .domain import DomainError


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
    quest_id = row[0]
    workspace = Path(store.workspace.root).resolve()
    root = workspace / "data" / "worker-environments" / quest_id / run_id

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

    return {
        "version": 1,
        "workspace_id": store.workspace.id,
        "quest_id": quest_id,
        "run_id": run_id,
        "root": checked_directory(root),
        "cwd": checked_directory(root / "work"),
        "tmp": checked_directory(root / "tmp"),
        "output": checked_directory(root / "output"),
        "isolation": "private_working_directory",
        "os_sandbox": False,
    }


def process_environment(base, environment):
    """Use per-run temporary storage without inheriting secrets or a shell home."""
    result = dict(base)
    result.update(TEMP=environment["tmp"], TMP=environment["tmp"], TMPDIR=environment["tmp"])
    if os.name != "nt":
        result["XDG_CACHE_HOME"] = environment["tmp"]
    return result
