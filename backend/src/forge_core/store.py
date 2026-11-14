"""SQLite connection ownership, repositories and atomic units of work."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .domain import (
    DomainError,
    QuestStatus,
    new_id,
    now_iso,
    validate_quest_transition,
)
from .migrations import SCHEMA_VERSION, migrate
from .settings import Settings, Workspace

__all__ = ["SCHEMA_VERSION", "Repository", "Store", "UnitOfWork", "WorkspaceMismatchError"]


class WorkspaceMismatchError(ValueError):
    pass


class Repository:
    """Operations on one caller-owned connection; never commits or closes it."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def ensure_router(self, conversation_id="main"):
        row = self.connection.execute(
            "SELECT * FROM router_state WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown conversation: {conversation_id}")
        return dict(row)

    def add_message(self, msg, conversation_id="main"):
        self.connection.execute(
            "INSERT INTO messages(id,conversation_id,role,content,created_at,metadata) VALUES (?,?,?,?,?,?)",
            (
                msg.id,
                conversation_id,
                msg.role,
                msg.content,
                msg.created_at,
                json.dumps(msg.metadata, ensure_ascii=False),
            ),
        )
        return msg

    def list_messages(self, conversation_id="main", limit=200):
        rows = self.connection.execute(
            """SELECT * FROM (SELECT rowid,* FROM messages WHERE conversation_id=?
               ORDER BY rowid DESC LIMIT ?) ORDER BY rowid""",
            (conversation_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_quest(self, quest, conversation_id="main"):
        if quest.status != QuestStatus.PROPOSED or quest.version != 0:
            raise DomainError("New Quest must begin proposed at version zero")
        self.connection.execute(
            """INSERT INTO quests(id,conversation_id,title,goal,status,priority,created_at,
               updated_at,metadata,version) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                quest.id,
                conversation_id,
                quest.title,
                quest.goal,
                quest.status.value,
                quest.priority,
                quest.created_at,
                quest.updated_at,
                json.dumps(quest.metadata, ensure_ascii=False),
                quest.version,
            ),
        )
        return quest

    def get_quest(self, quest_id):
        row = self.connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
        return dict(row) if row else None

    def list_quests(self, conversation_id="main", limit=100):
        rows = self.connection.execute(
            "SELECT * FROM quests WHERE conversation_id=? ORDER BY updated_at DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def transition_quest(
        self, quest_id, new_status, updated_at, *, expected_version, expected_status
    ):
        validate_quest_transition(QuestStatus(expected_status), QuestStatus(new_status))
        if QuestStatus(new_status) in {
            QuestStatus.APPROVED,
            QuestStatus.QUEUED,
            QuestStatus.RUNNING,
            QuestStatus.REVIEWING,
            QuestStatus.COMPLETED,
        }:
            raise DomainError("Execution transitions require a trusted runtime")
        cursor = self.connection.execute(
            """UPDATE quests SET status=?,updated_at=?,version=version+1
               WHERE id=? AND status=? AND version=?""",
            (new_status, updated_at, quest_id, expected_status, expected_version),
        )
        return cursor.rowcount == 1

    def save_worker(self, worker):
        if worker.status != "proposed":
            raise DomainError("New worker must begin proposed")
        self.connection.execute(
            """INSERT INTO workers(id,quest_id,role,model_id,status,hidden,created_at,metadata)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                worker.id,
                worker.quest_id,
                worker.role,
                worker.model_id,
                worker.status,
                int(worker.hidden),
                worker.created_at,
                json.dumps(worker.metadata, ensure_ascii=False),
            ),
        )
        return worker

    def list_workers(self, quest_id=None):
        if quest_id:
            rows = self.connection.execute(
                "SELECT * FROM workers WHERE quest_id=? ORDER BY created_at", (quest_id,)
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM workers ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def append_event(self, event):
        cursor = self.connection.execute(
            """INSERT INTO events(id,event_type,conversation_id,quest_id,worker_id,payload,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                event.id,
                event.event_type,
                event.conversation_id,
                event.quest_id,
                event.worker_id,
                json.dumps(event.payload, ensure_ascii=False),
                event.created_at,
            ),
        )
        self.connection.execute(
            "INSERT INTO outbox(event_id,created_at) VALUES (?,?)", (event.id, event.created_at)
        )
        return cursor.lastrowid

    def list_events(self, after=0, limit=200, conversation_id="main"):
        rows = self.connection.execute(
            "SELECT * FROM events WHERE conversation_id=? AND seq>? ORDER BY seq LIMIT ?",
            (conversation_id, after, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_outbox(self, *, pending_only=True, limit=200):
        predicate = "WHERE o.delivered_at IS NULL" if pending_only else ""
        rows = self.connection.execute(
            f"""SELECT o.event_id,o.created_at,o.delivered_at,e.seq,e.event_type,
                e.conversation_id,e.quest_id,e.worker_id,e.payload
                FROM outbox o JOIN events e ON e.id=o.event_id {predicate}
                ORDER BY e.seq LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_outbox_delivered(self, event_id):
        self.connection.execute(
            "UPDATE outbox SET delivered_at=? WHERE event_id=? AND delivered_at IS NULL",
            (now_iso(), event_id),
        )
        return (
            self.connection.execute("SELECT 1 FROM outbox WHERE event_id=?", (event_id,)).fetchone()
            is not None
        )

    def save_model(self, profile):
        self.connection.execute(
            "INSERT OR REPLACE INTO models(id,profile,updated_at) VALUES (?,?,?)",
            (profile.id, profile.model_dump_json(), now_iso()),
        )

    def list_models(self):
        from .model_intelligence import decode_profile

        rows = self.connection.execute("SELECT id,profile FROM models ORDER BY id").fetchall()
        return [decode_profile(row["profile"], row["id"]) for row in rows]


class Store:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        workspace: Workspace | None = None,
        busy_timeout_ms=5000,
    ):
        if workspace is None:
            settings = Settings.load()
            workspace = settings.workspace
            if path is None:
                path = settings.db_path
        self.workspace = workspace
        self.path = str(workspace.resolve("data/forge.sqlite3" if path is None else path))
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be nonnegative")
        self.busy_timeout_ms = busy_timeout_ms
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            self._enable_wal(connection)
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        except BaseException:
            connection.close()
            raise

    def _enable_wal(self, connection):
        # A concurrent journal-mode lock upgrade can return BUSY without invoking
        # SQLite's busy handler. Bound retries by the same connection timeout.
        deadline = time.monotonic() + self.busy_timeout_ms / 1000
        while True:
            try:
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            except sqlite3.OperationalError as exc:
                remaining = deadline - time.monotonic()
                if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY or remaining <= 0:
                    raise
                time.sleep(min(0.01, remaining))
            else:
                if mode.lower() != "wal":
                    raise sqlite3.OperationalError(f"WAL is required; SQLite selected {mode}")
                return

    @contextmanager
    def connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self.connection() as connection:
            migrate(connection)
        with self.unit_of_work() as work:
            connection = work.connection
            binding = connection.execute("SELECT id FROM workspace WHERE singleton=1").fetchone()
            if binding is not None and binding["id"] != self.workspace.id:
                raise WorkspaceMismatchError("Database belongs to a different workspace")
            connection.execute(
                "INSERT OR IGNORE INTO workspace(singleton,id,root) VALUES (1,?,?)",
                (self.workspace.id, str(self.workspace.root)),
            )
            connection.execute(
                "INSERT OR IGNORE INTO conversations(id,created_at) VALUES (?,?)",
                ("main", now_iso()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO router_state(conversation_id,router_session_id) VALUES (?,?)",
                ("main", new_id("router")),
            )

    def unit_of_work(self):
        return UnitOfWork(self)

    def _read(self, method, *args, **kwargs):
        with self.connection() as connection:
            return getattr(Repository(connection), method)(*args, **kwargs)

    def _write(self, method, *args, **kwargs):
        with self.unit_of_work() as work:
            return getattr(work, method)(*args, **kwargs)

    def ensure_router(self, conversation_id="main"):
        return self._read("ensure_router", conversation_id)

    def add_message(self, msg, conversation_id="main"):
        return self._write("add_message", msg, conversation_id)

    def list_messages(self, conversation_id="main", limit=200):
        return self._read("list_messages", conversation_id, limit)

    def save_quest(self, quest, conversation_id="main"):
        return self._write("save_quest", quest, conversation_id)

    def get_quest(self, quest_id):
        return self._read("get_quest", quest_id)

    def list_quests(self, conversation_id="main", limit=100):
        return self._read("list_quests", conversation_id, limit)

    def transition_quest(
        self, quest_id, new_status, updated_at, *, expected_version, expected_status
    ):
        return self._write(
            "transition_quest",
            quest_id,
            new_status,
            updated_at,
            expected_version=expected_version,
            expected_status=expected_status,
        )

    def save_worker(self, worker):
        return self._write("save_worker", worker)

    def list_workers(self, quest_id=None):
        return self._read("list_workers", quest_id)

    def append_event(self, event):
        return self._write("append_event", event)

    def list_events(self, after=0, limit=200, conversation_id="main"):
        return self._read("list_events", after, limit, conversation_id)

    def list_outbox(self, *, pending_only=True, limit=200):
        return self._read("list_outbox", pending_only=pending_only, limit=limit)

    def mark_outbox_delivered(self, event_id):
        return self._write("mark_outbox_delivered", event_id)

    def save_model(self, profile):
        return self._write("save_model", profile)

    def list_models(self):
        return self._read("list_models")


class UnitOfWork(Repository):
    """Single-use transaction: commit on success, rollback on any exception, always close."""

    def __init__(self, store):
        self.store = store
        self._entered = False

    def __enter__(self):
        if self._entered:
            raise RuntimeError("UnitOfWork is single-use")
        self._entered = True
        self._connection_context = self.store.connection()
        connection = self._connection_context.__enter__()
        try:
            connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._connection_context.__exit__(None, None, None)
            raise
        super().__init__(connection)
        return self

    def __exit__(self, exception_type, exception, traceback):
        try:
            if exception_type is not None:
                self.connection.rollback()
            else:
                try:
                    self.connection.commit()
                except BaseException:
                    self.connection.rollback()
                    raise
        finally:
            self._connection_context.__exit__(None, None, None)
        return False
