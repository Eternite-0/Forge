"""Deterministic admission and subscription pass-through, without model authority.

Only trusted host code configures a source or changes subscriptions. Received
payloads are untrusted context. The complete JSON is stored outside model context;
Worker inboxes receive a small preview and a scoped, exact continuation reference.
"""

import hashlib
import json
import math
import secrets
import time
from contextlib import contextmanager

from .domain import DomainError, ForgeEvent, new_id, now_iso
from .store import Repository

LIVE_RUN_STATES = ("queued", "running", "paused", "blocked")
STOPPED_QUEST_STATES = ("completed", "cancelled", "failed", "rejected")

READ_TRIGGER_TOOL = {
    "name": "read_trigger",
    "description": "Read exact untrusted event content from a delivered Trigger, by reference.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trigger_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "offset_chars": {"type": "integer", "minimum": 0},
            "max_chars": {"type": "integer", "minimum": 1, "maximum": 4096},
        },
        "required": ["trigger_id"],
        "additionalProperties": False,
    },
}
HANDBACK_TRIGGER_TOOL = {
    "name": "handback_trigger",
    "description": "Return an out-of-scope delivered event to the Router; grants no authority.",
    "input_schema": {
        "type": "object",
        "properties": {
            "trigger_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "required": ["trigger_id", "reason"],
        "additionalProperties": False,
    },
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identifier(value, label):
    if not isinstance(value, str) or not 0 < len(value) <= 256 or "\x00" in value:
        raise DomainError(f"Invalid {label}")
    return value


def _admission_reason(source, actor, addressed):
    actors = json.loads(source["allowed_actors_json"]) if source["allowed_actors_json"] else None
    if source["mode"] == "ignore":
        return "source_ignored"
    if actors is not None and actor not in actors:
        return "actor_not_allowed"
    if source["mode"] == "explicit" and not addressed:
        return "not_addressed"
    return "candidate"


def _verify_payload(row):
    actual = hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest()
    if actual != row["payload_sha256"]:
        raise DomainError("Trigger payload integrity check failed")


class TriggerService:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock

    @contextmanager
    def _write(self, connection):
        if connection is not None:
            yield connection
        else:
            with self.store.unit_of_work() as work:
                yield work.connection

    @contextmanager
    def _read(self, connection):
        if connection is not None:
            yield connection
        else:
            with self.store.connection() as opened:
                yield opened

    def _scope(self, connection, conversation_id, workspace_id=None):
        if workspace_id is not None and workspace_id != self.store.workspace.id:
            raise DomainError("Trigger workspace does not match the bound workspace")
        if not connection.execute(
            "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone():
            raise DomainError("Unknown Trigger conversation")

    def register_source(
        self,
        source_id,
        *,
        kind,
        mode="candidate",
        allowed_actors=None,
        conversation_id="main",
        workspace_id=None,
        connection=None,
    ):
        _identifier(source_id, "source ID")
        _identifier(kind, "source kind")
        if mode not in {"candidate", "explicit", "ignore"}:
            raise DomainError("Invalid source admission mode")
        if allowed_actors is not None:
            if not isinstance(allowed_actors, (list, tuple)) or len(allowed_actors) > 256:
                raise DomainError("Invalid source actors")
            allowed_actors = sorted(
                {_identifier(actor, "source actor") for actor in allowed_actors}
            )
        actors = _json(allowed_actors) if allowed_actors is not None else None
        with self._write(connection) as c:
            self._scope(c, conversation_id, workspace_id)
            old = c.execute("SELECT * FROM superagent_sources WHERE id=?", (source_id,)).fetchone()
            values = (self.store.workspace.id, conversation_id, kind, mode, actors)
            if old:
                if (
                    tuple(
                        old[key]
                        for key in (
                            "workspace_id",
                            "conversation_id",
                            "kind",
                            "mode",
                            "allowed_actors_json",
                        )
                    )
                    != values
                ):
                    raise DomainError("Source registration conflicts with its durable identity")
                return dict(old)
            c.execute(
                "INSERT INTO superagent_sources VALUES (?,?,?,?,?,?,?)",
                (source_id, *values, now_iso()),
            )
            return dict(
                c.execute("SELECT * FROM superagent_sources WHERE id=?", (source_id,)).fetchone()
            )

    def receive(
        self,
        source_id,
        external_id,
        thread_key,
        payload,
        *,
        actor=None,
        addressed=False,
        conversation_id="main",
        workspace_id=None,
        connection=None,
    ):
        """Store once, filter deterministically, fan out atomically, or queue for Router.

        Duplicate IDs with changed content or metadata are rejected. No inference,
        external action or user-facing reply occurs on this fast path.
        """
        for value, label in (
            (source_id, "source ID"),
            (external_id, "external ID"),
            (thread_key, "thread key"),
        ):
            _identifier(value, label)
        if actor is not None:
            _identifier(actor, "actor")
        if type(addressed) is not bool or not isinstance(payload, dict):
            raise DomainError("Invalid Trigger envelope")
        try:
            encoded = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise DomainError("Trigger payload must be finite JSON") from exc
        if len(encoded.encode("utf-8")) > 4 * 1024 * 1024:
            raise DomainError("Trigger source payload exceeds the 4 MiB ingress limit")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._write(connection) as c:
            self._scope(c, conversation_id, workspace_id)
            source = c.execute(
                "SELECT * FROM superagent_sources WHERE id=?", (source_id,)
            ).fetchone()
            if source is None or (source["workspace_id"], source["conversation_id"]) != (
                self.store.workspace.id,
                conversation_id,
            ):
                raise DomainError("Source is not registered in this conversation")
            old = c.execute(
                "SELECT t.*,h.thread_key FROM superagent_triggers t JOIN superagent_threads h "
                "ON h.id=t.thread_id WHERE t.source_id=? AND t.external_id=?",
                (source_id, external_id),
            ).fetchone()
            if old:
                if (old["payload_sha256"], old["thread_key"], old["actor"], old["addressed"]) != (
                    digest,
                    thread_key,
                    actor,
                    int(addressed),
                ):
                    raise DomainError("Trigger identity conflicts with stored content")
                return self._projection(c, old["id"], duplicate=True)
            thread = c.execute(
                "SELECT id FROM superagent_threads WHERE source_id=? AND thread_key=?",
                (source_id, thread_key),
            ).fetchone()
            thread_id = thread["id"] if thread else new_id("thread")
            if thread is None:
                c.execute(
                    "INSERT INTO superagent_threads VALUES (?,?,?,?,?,?)",
                    (
                        thread_id,
                        self.store.workspace.id,
                        conversation_id,
                        source_id,
                        thread_key,
                        now_iso(),
                    ),
                )
            trigger_id, event = (
                new_id("trigger"),
                ForgeEvent(
                    event_type="trigger.received",
                    conversation_id=conversation_id,
                    payload={"thread_id": thread_id},
                ),
            )
            event.payload["trigger_id"] = trigger_id
            Repository(c).append_event(event)
            reason = _admission_reason(source, actor, addressed)
            status = "pending" if reason == "candidate" else "filtered"
            c.execute(
                """INSERT INTO superagent_triggers
                (id,event_id,thread_id,source_id,external_id,actor,addressed,payload_json,
                 payload_sha256,status,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trigger_id,
                    event.id,
                    thread_id,
                    source_id,
                    external_id,
                    actor,
                    int(addressed),
                    encoded,
                    digest,
                    status,
                    reason,
                    now_iso(),
                ),
            )
            # Subscription is context routing, never a new grant. Admission remains
            # relevant to cold starts; a source explicitly ignored is disabled entirely.
            if source["mode"] != "ignore":
                delivered = self._fanout(c, trigger_id)
                if delivered:
                    c.execute(
                        "UPDATE superagent_triggers SET status='passed_through',reason='subscribed' "
                        "WHERE id=?",
                        (trigger_id,),
                    )
            return self._projection(c, trigger_id)

    ingest = receive

    def _record(self, connection, trigger_id):
        row = connection.execute(
            "SELECT t.*,h.workspace_id,h.conversation_id,h.thread_key,h.source_id AS thread_source_id,"
            "s.workspace_id AS registered_workspace,s.conversation_id AS registered_conversation "
            "FROM superagent_triggers t JOIN superagent_threads h ON h.id=t.thread_id "
            "JOIN superagent_sources s ON s.id=t.source_id WHERE t.id=?",
            (trigger_id,),
        ).fetchone()
        if row is None:
            raise KeyError(trigger_id)
        if (
            row["workspace_id"] != self.store.workspace.id
            or row["registered_workspace"] != row["workspace_id"]
            or row["registered_conversation"] != row["conversation_id"]
            or row["thread_source_id"] != row["source_id"]
        ):
            raise DomainError("Trigger source and Thread scope differ")
        _verify_payload(row)
        return row

    def _projection(self, connection, trigger_id, *, duplicate=False):
        row = self._record(connection, trigger_id)
        payload = row["payload_json"]
        deliveries = [
            dict(item)
            for item in connection.execute(
                "SELECT run_id,handed_back,handback_reason FROM superagent_trigger_deliveries "
                "WHERE trigger_id=? ORDER BY run_id",
                (trigger_id,),
            )
        ]
        return {
            "id": row["id"],
            "trigger_id": row["id"],
            "event_id": row["event_id"],
            "thread_id": row["thread_id"],
            "source_id": row["source_id"],
            "workspace_id": row["workspace_id"],
            "conversation_id": row["conversation_id"],
            "thread_key": row["thread_key"],
            "external_id": row["external_id"],
            "status": row["status"],
            "reason": row["reason"],
            "quest_id": row["quest_id"],
            "preview": payload[:600],
            "total_chars": len(payload),
            "payload_sha256": row["payload_sha256"],
            "duplicate": duplicate,
            "read": {"trigger_id": row["id"], "offset_chars": 0, "max_chars": 2048},
            "deliveries": deliveries,
        }

    def _run(self, connection, run_id, thread_id):
        row = connection.execute(
            "SELECT r.*,q.conversation_id,q.status AS quest_status FROM worker_runs r "
            "JOIN quests q ON q.id=r.quest_id WHERE r.id=?",
            (run_id,),
        ).fetchone()
        thread = connection.execute(
            "SELECT * FROM superagent_threads WHERE id=?",
            (thread_id,),
        ).fetchone()
        if (
            row is None
            or thread is None
            or thread["workspace_id"] != self.store.workspace.id
            or row["conversation_id"] != thread["conversation_id"]
        ):
            raise DomainError("Thread and Worker have different scope")
        return row

    def subscribe(self, thread_id, run_id, *, connection=None):
        with self._write(connection) as c:
            run = self._run(c, run_id, thread_id)
            if run["state"] not in LIVE_RUN_STATES or run["quest_status"] in STOPPED_QUEST_STATES:
                raise DomainError("Cannot subscribe a stopped Worker")
            c.execute(
                "INSERT INTO superagent_subscriptions VALUES (?,?,1,?) "
                "ON CONFLICT(thread_id,run_id) DO UPDATE SET active=1",
                (thread_id, run_id, now_iso()),
            )
            # Atomically catch events which arrived during cold-start planning.
            for trigger in c.execute(
                "SELECT id FROM superagent_triggers WHERE thread_id=? "
                "AND status IN ('pending','routed','passed_through') ORDER BY rowid LIMIT 64",
                (thread_id,),
            ).fetchall():
                if self._fanout(c, trigger["id"], run_id=run_id):
                    c.execute(
                        "UPDATE superagent_triggers SET status='passed_through',reason='subscribed',"
                        "lease_token=NULL,lease_until=NULL WHERE id=? AND status='pending'",
                        (trigger["id"],),
                    )
            return {"thread_id": thread_id, "run_id": run_id, "active": True}

    def unsubscribe(self, thread_id, run_id, *, connection=None):
        with self._write(connection) as c:
            self._run(c, run_id, thread_id)
            c.execute(
                "UPDATE superagent_subscriptions SET active=0 WHERE thread_id=? AND run_id=?",
                (thread_id, run_id),
            )

    def _fanout(self, connection, trigger_id, *, run_id=None):
        trigger = self._record(connection, trigger_id)
        runs = connection.execute(
            "SELECT s.run_id,r.state,q.status AS quest_status FROM superagent_subscriptions s "
            "JOIN worker_runs r ON r.id=s.run_id JOIN quests q ON q.id=r.quest_id "
            "WHERE s.thread_id=? AND s.active=1 AND q.conversation_id=? "
            + ("AND s.run_id=? " if run_id is not None else "")
            + "ORDER BY s.run_id",
            (trigger["thread_id"], trigger["conversation_id"], *([run_id] if run_id else [])),
        ).fetchall()
        delivered = 0
        for run in runs:
            if run["state"] not in LIVE_RUN_STATES or run["quest_status"] in STOPPED_QUEST_STATES:
                continue
            old = connection.execute(
                "SELECT handed_back FROM superagent_trigger_deliveries WHERE trigger_id=? AND run_id=?",
                (trigger_id, run["run_id"]),
            ).fetchone()
            if old:
                delivered += int(not old["handed_back"])
                continue
            pending = connection.execute(
                "SELECT COUNT(*) FROM worker_inbox WHERE run_id=? AND consumed=0",
                (run["run_id"],),
            ).fetchone()[0]
            if pending >= 64:
                # Full inbox keeps the event pending for Dispatcher reconciliation.
                delivered += 1
                continue
            payload = {
                "kind": "thread_trigger",
                "trigger_id": trigger_id,
                "thread_id": trigger["thread_id"],
                "event_id": trigger["event_id"],
                "preview": trigger["payload_json"][:600],
                "total_chars": len(trigger["payload_json"]),
                "payload_sha256": trigger["payload_sha256"],
                "read": {"tool": "read_trigger", "trigger_id": trigger_id},
                "authority": "context_only",
            }
            connection.execute(
                "INSERT OR IGNORE INTO worker_inbox(run_id,event_id,payload_json) VALUES (?,?,?)",
                (run["run_id"], trigger["event_id"], _json(payload)),
            )
            connection.execute(
                "INSERT INTO superagent_trigger_deliveries VALUES (?,?,0,NULL,?)",
                (trigger_id, run["run_id"], now_iso()),
            )
            delivered += 1
        return delivered

    def _run_backlog(self, connection, run_id, limit):
        return connection.execute(
            """SELECT t.id FROM superagent_triggers t
            JOIN superagent_threads h ON h.id=t.thread_id
            JOIN superagent_subscriptions s ON s.thread_id=t.thread_id AND s.active=1
            JOIN worker_runs r ON r.id=s.run_id
            JOIN quests q ON q.id=r.quest_id AND q.conversation_id=h.conversation_id
            LEFT JOIN superagent_trigger_deliveries d ON d.trigger_id=t.id AND d.run_id=s.run_id
            WHERE s.run_id=? AND h.workspace_id=?
            AND t.status IN ('pending','routed','passed_through') AND d.trigger_id IS NULL
            AND r.state IN ('queued','running','paused','blocked')
            AND q.status NOT IN ('completed','cancelled','failed','rejected')
            ORDER BY t.rowid LIMIT ?""",
            (run_id, self.store.workspace.id, limit),
        ).fetchall()

    def flush_run(self, run_id, *, connection=None):
        """Fill one Worker's inbox and expose remaining subscribed event debt.

        The runtime calls this inside its checkpoint/final write transaction.
        Inbox backpressure therefore cannot hide an event from its final guard
        while waiting for a separate Dispatcher polling turn.
        """
        with self._write(connection) as c:
            if not c.execute("SELECT 1 FROM worker_runs WHERE id=?", (run_id,)).fetchone():
                raise DomainError("Unknown Worker Trigger inbox")
            occupied = c.execute(
                "SELECT COUNT(*) FROM worker_inbox WHERE run_id=? AND consumed=0", (run_id,)
            ).fetchone()[0]
            rows = self._run_backlog(c, run_id, max(0, 64 - occupied))
            delivered = 0
            for row in rows:
                if self._fanout(c, row["id"], run_id=run_id):
                    delivered += 1
                    c.execute(
                        "UPDATE superagent_triggers SET status='passed_through',reason='subscribed',"
                        "lease_token=NULL,lease_until=NULL WHERE id=? AND status='pending'",
                        (row["id"],),
                    )
            return {"delivered": delivered, "pending": bool(self._run_backlog(c, run_id, 1))}

    def reconcile(self, *, limit=100):
        """Retry bounded inbox backpressure independently of Router inference."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise DomainError("Invalid Trigger reconciliation limit")
        with self.store.unit_of_work() as work:
            c = work.connection
            rows = c.execute(
                """SELECT DISTINCT t.id FROM superagent_triggers t
                JOIN superagent_subscriptions s ON s.thread_id=t.thread_id AND s.active=1
                JOIN worker_runs r ON r.id=s.run_id
                JOIN quests q ON q.id=r.quest_id
                LEFT JOIN superagent_trigger_deliveries d ON d.trigger_id=t.id AND d.run_id=s.run_id
                WHERE t.status IN ('pending','routed','passed_through') AND d.trigger_id IS NULL
                AND r.state IN ('queued','running','paused','blocked')
                AND q.status NOT IN ('completed','cancelled','failed','rejected')
                AND (SELECT COUNT(*) FROM worker_inbox i WHERE i.run_id=r.id AND i.consumed=0)<64
                ORDER BY t.rowid LIMIT ?""",
                (limit,),
            ).fetchall()
            count = 0
            for row in rows:
                if self._fanout(c, row["id"]):
                    count += 1
                    c.execute(
                        "UPDATE superagent_triggers SET status='passed_through',reason='subscribed',"
                        "lease_token=NULL,lease_until=NULL WHERE id=? AND status='pending'",
                        (row["id"],),
                    )
            return count

    def pending(self, *, limit=32, conversation_id="main", connection=None):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise DomainError("Invalid Trigger batch limit")
        with self._read(connection) as c:
            self._scope(c, conversation_id)
            return [
                self._projection(c, row["id"])
                for row in c.execute(
                    "SELECT t.id FROM superagent_triggers t JOIN superagent_threads h ON h.id=t.thread_id "
                    "WHERE t.status='pending' AND h.conversation_id=? AND h.workspace_id=? ORDER BY t.rowid LIMIT ?",
                    (conversation_id, self.store.workspace.id, limit),
                ).fetchall()
            ]

    def claim_pending(self, *, limit=16, lease_seconds=60, conversation_id="main"):
        if (
            type(limit) is not int
            or not 1 <= limit <= 256
            or type(lease_seconds) not in (int, float)
            or not math.isfinite(lease_seconds)
            or not 0 < lease_seconds <= 600
        ):
            raise DomainError("Invalid Trigger claim bounds")
        now = self.clock()
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise DomainError("Invalid Trigger clock")
        with self.store.unit_of_work() as work:
            c = work.connection
            self._scope(c, conversation_id)
            ids = [
                row[0]
                for row in c.execute(
                    "SELECT t.id FROM superagent_triggers t JOIN superagent_threads h ON h.id=t.thread_id "
                    "WHERE t.status='pending' AND h.conversation_id=? AND h.workspace_id=? "
                    "AND (t.lease_until IS NULL OR t.lease_until<=?) ORDER BY t.rowid LIMIT ?",
                    (conversation_id, self.store.workspace.id, now, limit),
                )
            ]
            result = []
            for trigger_id in ids:
                token = secrets.token_hex(24)
                c.execute(
                    "UPDATE superagent_triggers SET lease_token=?,lease_until=?,attempts=attempts+1 "
                    "WHERE id=?",
                    (token, now + lease_seconds, trigger_id),
                )
                result.append({**self._projection(c, trigger_id), "lease_token": token})
            return result

    def mark_routed(
        self, trigger_id, quest_id=None, *, lease_token=None, ignored=False, connection=None
    ):
        with self._write(connection) as c:
            trigger = self._record(c, trigger_id)
            if lease_token is not None and (
                trigger["status"] != "pending"
                or trigger["lease_token"] != lease_token
                or trigger["lease_until"] <= self.clock()
            ):
                raise DomainError("Trigger routing lease is stale")
            if trigger["lease_token"] is not None and lease_token is None:
                raise DomainError("Trigger routing lease is required")
            if quest_id is not None:
                quest = c.execute(
                    "SELECT conversation_id FROM quests WHERE id=?", (quest_id,)
                ).fetchone()
                if quest is None or quest[0] != trigger["conversation_id"]:
                    raise DomainError("Trigger and Quest have different scope")
            if trigger["status"] in {"filtered", "ignored"}:
                raise DomainError("Trigger is not eligible for routing")
            c.execute(
                "UPDATE superagent_triggers SET status=?,quest_id=?,lease_token=NULL,lease_until=NULL "
                "WHERE id=?",
                ("ignored" if ignored else "routed", quest_id, trigger_id),
            )
            return self._projection(c, trigger_id)

    def read_trigger(
        self,
        trigger_id,
        *,
        offset_chars=0,
        max_chars=2048,
        run_id=None,
        connection=None,
    ):
        if (
            type(offset_chars) is not int
            or offset_chars < 0
            or type(max_chars) is not int
            or not 1 <= max_chars <= 4096
        ):
            raise DomainError("Invalid Trigger read page")
        with self._read(connection) as c:
            projection = self._projection(c, trigger_id)
            if projection["workspace_id"] != self.store.workspace.id:
                raise DomainError("Trigger workspace scope differs")
            if run_id is not None:
                self._run(c, run_id, projection["thread_id"])
                if not c.execute(
                    "SELECT 1 FROM superagent_trigger_deliveries WHERE trigger_id=? AND run_id=?",
                    (trigger_id, run_id),
                ).fetchone():
                    raise DomainError("Worker has not received this Trigger")
            body = c.execute(
                "SELECT payload_json FROM superagent_triggers WHERE id=?",
                (trigger_id,),
            ).fetchone()[0]
            if hashlib.sha256(body.encode("utf-8")).hexdigest() != projection["payload_sha256"]:
                raise DomainError("Trigger payload integrity check failed")
            end = min(len(body), offset_chars + max_chars)
            return {
                **projection,
                "text": body[offset_chars:end],
                "offset_chars": offset_chars,
                "next_offset_chars": end if end < len(body) else None,
            }

    read = read_trigger

    def handback(self, run_id, trigger_id, reason, *, connection=None):
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise DomainError("Invalid Trigger handback reason")
        with self._write(connection) as c:
            trigger = self._projection(c, trigger_id)
            run = self._run(c, run_id, trigger["thread_id"])
            delivery = c.execute(
                "SELECT handed_back FROM superagent_trigger_deliveries WHERE trigger_id=? AND run_id=?",
                (trigger_id, run_id),
            ).fetchone()
            if delivery is None:
                raise DomainError("Worker cannot hand back an undelivered Trigger")
            if delivery["handed_back"]:
                return False
            c.execute(
                "UPDATE superagent_trigger_deliveries SET handed_back=1,handback_reason=? "
                "WHERE trigger_id=? AND run_id=?",
                (reason, trigger_id, run_id),
            )
            Repository(c).append_event(
                ForgeEvent(
                    event_type="trigger.delivery_handed_back",
                    conversation_id=trigger["conversation_id"],
                    quest_id=run["quest_id"],
                    worker_id=run["worker_id"],
                    payload={
                        "trigger_id": trigger_id,
                        "thread_id": trigger["thread_id"],
                        "run_id": run_id,
                        "reason": reason,
                    },
                )
            )
            owner = c.execute(
                "SELECT 1 FROM superagent_trigger_deliveries d JOIN worker_runs r ON r.id=d.run_id "
                "JOIN quests q ON q.id=r.quest_id WHERE d.trigger_id=? AND d.handed_back=0 "
                "AND r.state IN ('queued','running','paused','blocked') "
                "AND q.status NOT IN ('completed','cancelled','failed','rejected') LIMIT 1",
                (trigger_id,),
            ).fetchone()
            if owner is None:
                # A subscribed owner can still be waiting behind inbox
                # backpressure; its missing delivery is not a handback.
                owner = c.execute(
                    "SELECT 1 FROM superagent_subscriptions s "
                    "JOIN worker_runs r ON r.id=s.run_id JOIN quests q ON q.id=r.quest_id "
                    "LEFT JOIN superagent_trigger_deliveries d ON d.run_id=s.run_id "
                    "AND d.trigger_id=? WHERE s.thread_id=? AND s.active=1 "
                    "AND d.trigger_id IS NULL AND r.state IN ('queued','running','paused','blocked') "
                    "AND q.status NOT IN ('completed','cancelled','failed','rejected') LIMIT 1",
                    (trigger_id, trigger["thread_id"]),
                ).fetchone()
            if owner is None:
                stored = c.execute(
                    "SELECT t.actor,t.addressed,s.* FROM superagent_triggers t "
                    "JOIN superagent_sources s ON s.id=t.source_id WHERE t.id=?",
                    (trigger_id,),
                ).fetchone()
                admission = _admission_reason(stored, stored["actor"], bool(stored["addressed"]))
                c.execute(
                    "UPDATE superagent_triggers SET status=?,reason=?,quest_id=NULL,"
                    "lease_token=NULL,lease_until=NULL WHERE id=?",
                    (
                        "pending" if admission == "candidate" else "filtered",
                        "handed_back" if admission == "candidate" else admission,
                        trigger_id,
                    ),
                )
                Repository(c).append_event(
                    ForgeEvent(
                        event_type="trigger.handed_back",
                        conversation_id=trigger["conversation_id"],
                        payload={
                            "trigger_id": trigger_id,
                            "thread_id": trigger["thread_id"],
                            "run_id": run_id,
                            "reason": reason,
                        },
                    )
                )
            return True


def dispatch_worker_trigger_tool(store, connection, run_id, name, arguments):
    """Trusted runtime adapter; the Worker cannot supply another run's identity."""
    service = TriggerService(store)
    if name == "read_trigger":
        return service.read_trigger(run_id=run_id, connection=connection, **arguments)
    if name == "handback_trigger":
        return {"handed_back": service.handback(run_id=run_id, connection=connection, **arguments)}
    raise DomainError("Unknown Worker Trigger tool")
