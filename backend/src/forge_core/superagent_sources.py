"""Local deterministic timer and file sources, driven by the host polling loop."""

import base64
import hashlib
import json
import math
from pathlib import Path

from .domain import DomainError, new_id
from .superagent_events import TriggerService, _identifier


class LocalTriggerSources:
    def __init__(self, store, *, clock=None):
        self.store = store
        self.events = TriggerService(store, **({"clock": clock} if clock is not None else {}))
        self.clock = self.events.clock

    def _source(self, connection, source_id, kind):
        source = connection.execute(
            "SELECT * FROM superagent_sources WHERE id=?",
            (source_id,),
        ).fetchone()
        if (
            source is None
            or source["workspace_id"] != self.store.workspace.id
            or source["kind"] != kind
        ):
            raise DomainError(f"Source is not a registered local {kind} source")
        return source

    def schedule(
        self,
        source_id,
        thread_key,
        payload,
        *,
        due_at,
        interval_seconds=None,
        actor=None,
        addressed=True,
        timer_id=None,
    ):
        _identifier(thread_key, "timer thread")
        if (
            type(due_at) not in (int, float)
            or not math.isfinite(due_at)
            or due_at < 0
            or (
                interval_seconds is not None
                and (
                    type(interval_seconds) not in (int, float)
                    or not math.isfinite(interval_seconds)
                    or interval_seconds < 1
                )
            )
            or not isinstance(payload, dict)
            or type(addressed) is not bool
        ):
            raise DomainError("Invalid timer schedule")
        if actor is not None:
            _identifier(actor, "timer actor")
        timer_id = _identifier(timer_id or new_id("timer"), "timer ID")
        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise DomainError("Timer payload must be finite JSON") from exc
        if len(encoded.encode("utf-8")) > 4 * 1024 * 1024:
            raise DomainError("Timer payload exceeds ingress limit")
        with self.store.unit_of_work() as work:
            c = work.connection
            self._source(c, source_id, "timer")
            old = c.execute("SELECT * FROM superagent_timers WHERE id=?", (timer_id,)).fetchone()
            if old:
                if tuple(
                    old[key]
                    for key in (
                        "source_id",
                        "thread_key",
                        "payload_json",
                        "actor",
                        "addressed",
                        "interval_seconds",
                    )
                ) != (source_id, thread_key, encoded, actor, int(addressed), interval_seconds):
                    raise DomainError("Timer identity conflicts with stored schedule")
                return dict(old)
            c.execute(
                "INSERT INTO superagent_timers VALUES (?,?,?,?,?,?,?,?,0,1)",
                (
                    timer_id,
                    source_id,
                    thread_key,
                    encoded,
                    actor,
                    int(addressed),
                    due_at,
                    interval_seconds,
                ),
            )
            return dict(
                c.execute("SELECT * FROM superagent_timers WHERE id=?", (timer_id,)).fetchone()
            )

    def poll_due(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise DomainError("Invalid timer poll limit")
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise DomainError("Invalid timer clock")
        with self.store.unit_of_work() as work:
            c = work.connection
            timers = c.execute(
                "SELECT * FROM superagent_timers WHERE enabled=1 AND next_at<=? "
                "ORDER BY next_at,id LIMIT ?",
                (now, limit),
            ).fetchall()
            triggers = []
            for timer in timers:
                source = self._source(c, timer["source_id"], "timer")
                occurrence = timer["occurrence"] + 1
                triggers.append(
                    self.events.receive(
                        timer["source_id"],
                        f"{timer['id']}:{occurrence}",
                        timer["thread_key"],
                        json.loads(timer["payload_json"]),
                        actor=timer["actor"],
                        addressed=bool(timer["addressed"]),
                        conversation_id=source["conversation_id"],
                        connection=c,
                    )
                )
                interval = timer["interval_seconds"]
                # Coalesce missed periods into one event; a restart never floods
                # the Router with one event per missed tick.
                next_at = now + interval if interval is not None else timer["next_at"]
                c.execute(
                    "UPDATE superagent_timers SET occurrence=?,next_at=?,enabled=? WHERE id=?",
                    (occurrence, next_at, int(interval is not None), timer["id"]),
                )
            return triggers

    def _path(self, relative_path):
        candidate = Path(relative_path)
        root = self.store.workspace.root
        if (
            candidate.is_absolute()
            or not candidate.parts
            or any(part in {".", ".."} for part in candidate.parts)
        ):
            raise DomainError("File watch must use a workspace relative path")
        if any(
            part.lower().startswith(".env") or part.lower() in {".git", ".codex", ".ssh"}
            for part in candidate.parts
        ):
            raise DomainError("File watch cannot inspect protected configuration")
        path = root / candidate
        for part in (path, *path.parents):
            if part == root.parent:
                break
            if part.is_symlink() or (getattr(part, "is_junction", lambda: False)()):
                raise DomainError("File watch does not follow links")
        resolved = path.resolve()
        database = Path(self.store.path).resolve()
        if not resolved.is_relative_to(root) or resolved in {
            database,
            Path(str(database) + "-wal"),
            Path(str(database) + "-shm"),
        }:
            raise DomainError("File watch path is protected or outside workspace")
        return path

    def _snapshot(self, relative_path):
        path = self._path(relative_path)
        if not path.exists():
            return "missing", {"path": relative_path, "exists": False}
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise DomainError("File watch requires an ordinary file of at most 1 MiB")
        with path.open("rb") as stream:
            content = stream.read(1024 * 1024 + 1)
        self._path(relative_path)
        if len(content) > 1024 * 1024:
            raise DomainError("File watch source grew beyond its ingress limit")
        digest = hashlib.sha256(content).hexdigest()
        return digest, {
            "path": relative_path,
            "exists": True,
            "sha256": digest,
            "size_bytes": len(content),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    def watch_file(self, source_id, relative_path, *, thread_key=None, watch_id=None):
        relative_path = str(relative_path)
        fingerprint, _ = self._snapshot(relative_path)
        thread_key = _identifier(thread_key or relative_path, "file Thread")
        watch_id = _identifier(watch_id or new_id("watch"), "file watch ID")
        with self.store.unit_of_work() as work:
            c = work.connection
            self._source(c, source_id, "file")
            old = c.execute(
                "SELECT * FROM superagent_file_watches WHERE source_id=? AND relative_path=?",
                (source_id, relative_path),
            ).fetchone()
            if old:
                if old["thread_key"] != thread_key:
                    raise DomainError("File watch identity conflicts with stored Thread")
                return dict(old)
            c.execute(
                "INSERT INTO superagent_file_watches VALUES (?,?,?,?,?,0,NULL,0)",
                (
                    watch_id,
                    source_id,
                    thread_key,
                    relative_path,
                    fingerprint,
                ),
            )
            return dict(
                c.execute(
                    "SELECT * FROM superagent_file_watches WHERE id=?", (watch_id,)
                ).fetchone()
            )

    def poll_files(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise DomainError("Invalid file poll limit")
        # Persistent poll order prevents a long watch list from starving files.
        with self.store.unit_of_work() as work:
            c = work.connection
            watches = c.execute(
                "SELECT * FROM superagent_file_watches ORDER BY checked_at,id LIMIT ?", (limit,)
            ).fetchall()
            result = []
            for watch in watches:
                source = self._source(c, watch["source_id"], "file")
                c.execute(
                    "UPDATE superagent_file_watches SET checked_at=? WHERE id=?",
                    (self.clock(), watch["id"]),
                )
                try:
                    fingerprint, payload = self._snapshot(watch["relative_path"])
                except (DomainError, OSError):
                    c.execute(
                        "UPDATE superagent_file_watches SET last_error='source_unavailable' WHERE id=?",
                        (watch["id"],),
                    )
                    continue
                if fingerprint != watch["fingerprint"]:
                    occurrence = watch["occurrence"] + 1
                    result.append(
                        self.events.receive(
                            watch["source_id"],
                            f"{watch['id']}:{occurrence}",
                            watch["thread_key"],
                            payload,
                            addressed=True,
                            conversation_id=source["conversation_id"],
                            connection=c,
                        )
                    )
                    c.execute(
                        "UPDATE superagent_file_watches SET fingerprint=?,occurrence=? WHERE id=?",
                        (
                            fingerprint,
                            occurrence,
                            watch["id"],
                        ),
                    )
                c.execute(
                    "UPDATE superagent_file_watches SET last_error=NULL WHERE id=?", (watch["id"],)
                )
            return result
