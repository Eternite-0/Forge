"""Cross-process exclusive lock shared by Core and workspace maintenance."""

import hashlib
import os
import threading
from pathlib import Path
from typing import ClassVar


class WorkspaceBusy(RuntimeError):
    pass


def workspace_key(root):
    text = str(Path(root).resolve())
    if os.name == "nt":
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
    return os.path.normcase(text)


class WorkspaceLock:
    _registry: ClassVar[dict] = {}
    _guard = threading.RLock()

    def __init__(self, root):
        root = Path(root).resolve()
        digest = hashlib.sha256(workspace_key(root).encode("utf-8")).hexdigest()[:24]
        self.path = root.parent / f".forge-workspace-{digest}.lock"
        self._file = None
        self._depth = 0

    def __enter__(self):
        with self._guard:
            held = self._registry.get(str(self.path))
            if held is not None:
                if held["thread"] != threading.get_ident():
                    raise WorkspaceBusy("Workspace is already in use")
                held["count"] += 1
                self._file = held["handle"]
                self._depth += 1
                return self
            return self._acquire()

    def _acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise WorkspaceBusy("Workspace is already in use") from None
        self._file = handle
        self._depth += 1
        self._registry[str(self.path)] = {
            "handle": handle,
            "thread": threading.get_ident(),
            "count": 1,
        }
        return self

    def __exit__(self, *args):
        with self._guard:
            if self._file is not None:
                held = self._registry[str(self.path)]
                held["count"] -= 1
                if held["count"] == 0:
                    held["handle"].close()
                    del self._registry[str(self.path)]
                self._depth -= 1
                if self._depth == 0:
                    self._file = None
