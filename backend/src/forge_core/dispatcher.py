"""Durable at-least-once, per-consumer local event delivery.

Only trusted Core code may register handlers. A handler participates in the same
SQLite transaction and must not perform external side effects.
"""

import json
import math
import re
import secrets
import time
from dataclasses import asdict

from .agent_protocol import COORDINATION_EVENTS, AgentMessage
from .domain import DomainError
from .event_domain import URGENT_EVENTS, WORKER_COMMANDS, WORKER_FACTS, DeliveryLease, EventEnvelope

_URGENT_PLACEHOLDERS = ",".join("?" for _ in URGENT_EVENTS)


class LeaseError(RuntimeError):
    pass


class Dispatcher:
    def __init__(
        self, store, *, clock=time.time, lease_seconds=30, max_attempts=5, base_backoff_seconds=1
    ):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValueError("Invalid delivery attempts")
        if any(
            type(x) not in (float, int) or not math.isfinite(x) or x <= 0
            for x in (lease_seconds, base_backoff_seconds)
        ):
            raise ValueError("Invalid delivery timing")
        self.store, self.clock = store, clock
        self.lease_seconds, self.max_attempts, self.base_backoff_seconds = (
            lease_seconds,
            max_attempts,
            base_backoff_seconds,
        )
        self._worker_cursor = ""
        self._consumer_cursor = 0

    def _now(self):
        now = self.clock()
        if type(now) not in (float, int) or not math.isfinite(now):
            raise ValueError("Invalid clock value")
        return float(now)

    def register(self, consumer_id, *, kind="router", worker_id=None, replay_from_seq=0):
        if (
            not isinstance(consumer_id, str)
            or not consumer_id
            or len(consumer_id) > 256
            or type(replay_from_seq) is not int
            or replay_from_seq < 0
        ):
            raise ValueError("Invalid consumer")
        if (
            kind not in {"router", "worker"}
            or (kind == "router" and worker_id is not None)
            or (kind == "worker" and (not isinstance(worker_id, str) or not worker_id))
        ):
            raise ValueError("Invalid consumer kind")
        with self.store.unit_of_work() as work:
            connection = work.connection
            old = connection.execute(
                "SELECT * FROM event_consumers WHERE id=?", (consumer_id,)
            ).fetchone()
            if old is not None:
                if (old["kind"], old["worker_id"], old["replay_from_seq"]) != (
                    kind,
                    worker_id,
                    replay_from_seq,
                ):
                    raise ValueError("Consumer registration differs from existing subscription")
                return
            connection.execute(
                "INSERT INTO event_consumers VALUES (?,?,?,?,?,?,?)",
                (
                    consumer_id,
                    kind,
                    worker_id,
                    replay_from_seq,
                    replay_from_seq,
                    replay_from_seq,
                    replay_from_seq,
                ),
            )

    @staticmethod
    def _materialize(connection, consumer_id):
        consumer = connection.execute(
            "SELECT scan_seq,priority_scan_seq FROM event_consumers WHERE id=?", (consumer_id,)
        ).fetchone()
        rows = connection.execute(
            "SELECT id,seq FROM events WHERE seq>? ORDER BY seq LIMIT 100", (consumer["scan_seq"],)
        ).fetchall()
        connection.executemany(
            "INSERT OR IGNORE INTO event_deliveries(consumer_id,event_id,seq,state) VALUES (?,?,?,'pending')",
            [(consumer_id, row["id"], row["seq"]) for row in rows],
        )
        if rows:
            connection.execute(
                "UPDATE event_consumers SET scan_seq=? WHERE id=?", (rows[-1]["seq"], consumer_id)
            )
        urgent = connection.execute(
            f"""SELECT e.id,e.seq FROM events e WHERE e.seq>? AND e.event_type IN
            ({_URGENT_PLACEHOLDERS}) ORDER BY e.seq LIMIT 16""",
            (consumer["priority_scan_seq"], *URGENT_EVENTS),
        ).fetchall()
        connection.executemany(
            "INSERT OR IGNORE INTO event_deliveries(consumer_id,event_id,seq,state) VALUES (?,?,?,'pending')",
            [(consumer_id, row["id"], row["seq"]) for row in urgent],
        )
        if urgent:
            connection.execute(
                "UPDATE event_consumers SET priority_scan_seq=? WHERE id=?",
                (urgent[-1]["seq"], consumer_id),
            )

    @staticmethod
    def _advance(connection, consumer_id, seq):
        scan = connection.execute(
            "SELECT scan_seq FROM event_consumers WHERE id=?", (consumer_id,)
        ).fetchone()[0]
        gap = connection.execute(
            "SELECT MIN(seq) FROM event_deliveries WHERE consumer_id=? AND state IN ('pending','leased')",
            (consumer_id,),
        ).fetchone()[0]
        if gap is None:
            last = connection.execute(
                "SELECT MAX(seq) FROM event_deliveries WHERE consumer_id=?", (consumer_id,)
            ).fetchone()[0]
        else:
            last = connection.execute(
                "SELECT MAX(seq) FROM event_deliveries WHERE consumer_id=? AND seq<? AND state IN ('delivered','dead','skipped')",
                (consumer_id, gap),
            ).fetchone()[0]
        if last is not None:
            connection.execute(
                "UPDATE event_consumers SET cursor_seq=MAX(cursor_seq,?) WHERE id=?",
                (min(last, scan), consumer_id),
            )

    @staticmethod
    def _routable(connection, consumer, row):
        is_command = row["event_type"].startswith("worker.command.")
        if consumer["id"] == "planner" and consumer["kind"] == "router":
            return row["event_type"] in {
                "quest.proposed",
                "quest.proposal_updated",
                "quest.replan_requested",
            }
        if consumer["id"] == "composer" and consumer["kind"] == "router":
            return row["event_type"] in {"quest.plan_approved", "worker.help_requested"}
        if consumer["kind"] == "worker":
            worker = connection.execute(
                "SELECT status FROM workers WHERE id=?", (consumer["worker_id"],)
            ).fetchone()
            return bool(
                worker
                and worker["status"] not in {"completed", "failed", "cancelled", "rejected"}
                and row["event_type"] in WORKER_COMMANDS
                and row["worker_id"] == consumer["worker_id"]
            )
        return not is_command

    def claim(self, consumer_id):
        with self.store.unit_of_work() as work:
            now = self._now()
            connection = work.connection
            consumer = connection.execute(
                "SELECT * FROM event_consumers WHERE id=?", (consumer_id,)
            ).fetchone()
            if consumer is None:
                raise KeyError(consumer_id)
            self._materialize(connection, consumer_id)
            while True:
                row = connection.execute(
                    """SELECT d.*,e.event_type,e.worker_id FROM event_deliveries d
                    JOIN events e ON e.id=d.event_id WHERE d.consumer_id=? AND d.state IN ('pending','leased')
                    ORDER BY d.seq LIMIT 1""",
                    (consumer_id,),
                ).fetchone()
                if row is None:
                    return None
                urgent = connection.execute(
                    f"""SELECT d.*,e.event_type,e.worker_id FROM event_deliveries d
                    JOIN events e ON e.id=d.event_id WHERE d.consumer_id=?
                    AND e.event_type IN ({_URGENT_PLACEHOLDERS})
                    AND ((d.state='pending' AND d.next_attempt<=?) OR (d.state='leased' AND d.lease_until<=?))
                    ORDER BY d.seq LIMIT 1""",
                    (consumer_id, *URGENT_EVENTS, now, now),
                ).fetchone()
                if urgent is not None:
                    row = urgent
                if not self._routable(connection, consumer, row):
                    connection.execute(
                        "UPDATE event_deliveries SET state='skipped' WHERE consumer_id=? AND event_id=?",
                        (consumer_id, row["event_id"]),
                    )
                    self._advance(connection, consumer_id, row["seq"])
                    continue
                if row["state"] == "leased" and row["lease_until"] > now:
                    return None
                if row["state"] == "pending" and row["next_attempt"] > now:
                    return None
                if row["attempts"] >= self.max_attempts:
                    connection.execute(
                        "UPDATE event_deliveries SET state='dead',lease_token=NULL,lease_until=NULL,last_error='lease_expired' WHERE consumer_id=? AND event_id=?",
                        (consumer_id, row["event_id"]),
                    )
                    self._advance(connection, consumer_id, row["seq"])
                    continue
                token = secrets.token_hex(32)
                attempt = row["attempts"] + 1
                connection.execute(
                    "UPDATE event_deliveries SET state='leased',lease_token=?,lease_until=?,attempts=? WHERE consumer_id=? AND event_id=?",
                    (token, now + self.lease_seconds, attempt, consumer_id, row["event_id"]),
                )
                return DeliveryLease(consumer_id, row["event_id"], row["seq"], token, attempt)

    def _leased(self, connection, lease, now):
        if not isinstance(lease, DeliveryLease):
            raise LeaseError("Invalid delivery lease")
        row = connection.execute(
            """SELECT d.*,e.event_type,e.conversation_id,e.quest_id,e.worker_id,e.payload
            FROM event_deliveries d JOIN events e ON e.id=d.event_id
            WHERE d.consumer_id=? AND d.event_id=?""",
            (lease.consumer_id, lease.event_id),
        ).fetchone()
        if (
            row is None
            or row["state"] != "leased"
            or row["lease_token"] != lease.token
            or row["lease_until"] <= now
            or row["seq"] != lease.seq
            or row["attempts"] != lease.attempt
        ):
            raise LeaseError("Delivery lease is stale")
        return row

    @staticmethod
    def _agent_protocol(connection, row, payload):
        if row["event_type"] not in COORDINATION_EVENTS:
            return {}
        native = any(name in payload for name in ("run_id", "plan_id", "task_node_key"))
        if not native:
            return {"execution_protocol": "legacy_nonexecution", "agent_message": None}
        try:
            event = {**dict(row), "id": row["event_id"]}
            message = AgentMessage.from_event(event, payload)
            run_id = payload.get("run_id")
            if run_id is not None and (not isinstance(run_id, str) or not 1 <= len(run_id) <= 256):
                raise ValueError("Invalid Agent run identity")
            run = connection.execute(
                "SELECT * FROM worker_runs WHERE worker_id=? AND quest_id=? AND plan_id=? AND node_key=? "
                + ("AND id=?" if run_id is not None else "ORDER BY created_at DESC LIMIT 1"),
                (
                    message.sender_id if message.receiver_id == "router" else message.receiver_id,
                    message.quest_id,
                    message.plan_id,
                    message.task_node_key,
                    *([run_id] if run_id is not None else []),
                ),
            ).fetchone()
            if (
                run is None
                or payload.get("message_type", message.message_type) != message.message_type
            ):
                raise ValueError("Agent protocol association does not match a native assignment")
            return {"execution_protocol": "native", "agent_message": asdict(message)}
        except (DomainError, TypeError, KeyError) as exc:
            raise ValueError("Invalid native Agent protocol envelope") from exc

    def deliver(self, lease, *, handler=None):
        with self.store.unit_of_work() as work:
            now = self._now()
            connection = work.connection
            row = self._leased(connection, lease, now)
            try:
                payload = json.loads(row["payload"])
            except (ValueError, TypeError):
                raise ValueError("Invalid event payload") from None
            envelope = EventEnvelope.from_row(row, payload)
            coordination = self._agent_protocol(connection, row, payload)
            if row["event_type"] in WORKER_COMMANDS | WORKER_FACTS:
                worker = connection.execute(
                    "SELECT w.quest_id,q.conversation_id FROM workers w JOIN quests q ON q.id=w.quest_id WHERE w.id=?",
                    (row["worker_id"],),
                ).fetchone()
                if (
                    worker is None
                    or worker["quest_id"] != row["quest_id"]
                    or worker["conversation_id"] != row["conversation_id"]
                ):
                    raise ValueError("Worker event association is invalid")
                if envelope.plan_id is not None:
                    node = connection.execute(
                        """SELECT p.quest_id,n.worker_id FROM task_nodes n
                        JOIN quest_plans p ON p.id=n.plan_id WHERE n.plan_id=? AND n.node_key=?""",
                        (envelope.plan_id, envelope.task_node_key),
                    ).fetchone()
                    if (
                        node is None
                        or node["quest_id"] != envelope.quest_id
                        or node["worker_id"] != envelope.worker_id
                    ):
                        raise ValueError("Worker node association is invalid")
            item = {
                "event_id": envelope.event_id,
                "seq": envelope.seq,
                "event_type": envelope.event_type,
                "conversation_id": envelope.conversation_id,
                "quest_id": envelope.quest_id,
                "worker_id": envelope.worker_id,
                "task_node_id": envelope.task_node_id,
                "correlation_id": envelope.correlation_id,
                "payload": payload,
                **coordination,
            }
            connection.execute(
                """INSERT INTO event_inbox(consumer_id,event_id,seq,event_type,conversation_id,quest_id,worker_id,payload,received_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    lease.consumer_id,
                    row["event_id"],
                    row["seq"],
                    row["event_type"],
                    row["conversation_id"],
                    row["quest_id"],
                    row["worker_id"],
                    row["payload"],
                    now,
                ),
            )
            if handler is not None:
                handler(connection, item)
            if lease.consumer_id.startswith("worker:"):
                run = connection.execute(
                    "SELECT * FROM worker_runs WHERE worker_id=? AND state IN ('queued','running','paused','blocked') ORDER BY created_at DESC LIMIT 1",
                    (envelope.worker_id,),
                ).fetchone()
                if run is not None:
                    control = {
                        "worker.command.pause": "paused",
                        "worker.command.resume": "queued",
                        "worker.command.cancel": "cancelled",
                    }.get(envelope.event_type)
                    quest_control = payload.get("control_scope") == "quest"
                    if quest_control and control == "queued" and not run["quest_paused"]:
                        control = None
                    if control is not None:
                        allowed = {
                            "paused": {"queued", "running", "paused"},
                            "queued": {"paused", "queued"},
                            "cancelled": {"queued", "running", "paused", "blocked"},
                        }
                        if run["state"] not in allowed[control]:
                            raise ValueError("Worker command state conflict")
                        if control == "queued":
                            quest = connection.execute(
                                "SELECT status FROM quests WHERE id=?", (run["quest_id"],)
                            ).fetchone()
                            if quest[0] not in {"approved", "queued", "running"}:
                                raise ValueError("Quest does not authorize Worker resume")
                        quest_paused = int(
                            control == "paused"
                            and quest_control
                            and (run["state"] != "paused" or run["quest_paused"])
                        )
                        connection.execute(
                            "UPDATE worker_runs SET state=?,generation=generation+1,lease_token=NULL,lease_until=NULL,quest_paused=? WHERE id=?",
                            (control, quest_paused, run["id"]),
                        )
                        connection.execute(
                            "UPDATE workers SET status=? WHERE id=?", (control, run["worker_id"])
                        )
                        connection.execute(
                            "UPDATE task_nodes SET status=? WHERE plan_id=? AND node_key=? AND worker_id=?",
                            (control, run["plan_id"], run["node_key"], run["worker_id"]),
                        )
                    if (
                        len(row["payload"].encode("utf-8")) > 16384
                        or connection.execute(
                            "SELECT COUNT(*) FROM worker_inbox WHERE run_id=?", (run["id"],)
                        ).fetchone()[0]
                        >= 64
                    ):
                        raise ValueError("Worker inbox resource limit")
                    connection.execute(
                        "INSERT OR IGNORE INTO worker_inbox(run_id,event_id,payload_json) VALUES (?,?,?)",
                        (run["id"], envelope.event_id, row["payload"]),
                    )
            self._leased(connection, lease, self._now())
            connection.execute(
                "UPDATE event_deliveries SET state='delivered',lease_token=NULL,lease_until=NULL WHERE consumer_id=? AND event_id=?",
                (lease.consumer_id, lease.event_id),
            )
            self._advance(connection, lease.consumer_id, row["seq"])
            return item

    def fail(self, lease, error_code):
        if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
            raise ValueError("Invalid error code")
        with self.store.unit_of_work() as work:
            now = self._now()
            row = self._leased(work.connection, lease, now)
            dead = row["attempts"] >= self.max_attempts
            delay = min(self.base_backoff_seconds * (2 ** min(row["attempts"] - 1, 20)), 3600)
            work.connection.execute(
                "UPDATE event_deliveries SET state=?,next_attempt=?,last_error=?,lease_token=NULL,lease_until=NULL WHERE consumer_id=? AND event_id=?",
                (
                    "dead" if dead else "pending",
                    now + delay,
                    error_code,
                    lease.consumer_id,
                    lease.event_id,
                ),
            )
            if dead:
                self._advance(work.connection, lease.consumer_id, row["seq"])

    def inbox(self, consumer_id, *, after_seq=0, limit=100, pending_only=False):
        if (
            type(after_seq) is not int
            or after_seq < 0
            or type(limit) is not int
            or not 1 <= limit <= 500
            or type(pending_only) is not bool
        ):
            raise ValueError("Invalid inbox pagination")
        with self.store.connection() as connection:
            query = "SELECT * FROM event_inbox WHERE consumer_id=? AND seq>?"
            if pending_only:
                query += " AND processed_at IS NULL"
            rows = connection.execute(
                query + " ORDER BY seq LIMIT ?", (consumer_id, after_seq, limit)
            ).fetchall()
            return [
                {
                    **dict(row),
                    "payload": (payload := json.loads(row["payload"])),
                    **self._agent_protocol(connection, row, payload),
                }
                for row in rows
            ]

    def dead_letters(self, consumer_id):
        with self.store.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM event_deliveries WHERE consumer_id=? AND state='dead' ORDER BY seq",
                    (consumer_id,),
                )
            ]

    def replay_dead_letter(self, consumer_id, event_id, *, actor, reason_code):
        if (
            not isinstance(actor, str)
            or not actor.strip()
            or not isinstance(reason_code, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason_code)
        ):
            raise ValueError("Invalid replay authorization or reason")
        with self.store.unit_of_work() as work:
            now = self._now()
            row = work.connection.execute(
                "SELECT * FROM event_deliveries WHERE consumer_id=? AND event_id=? AND state='dead'",
                (consumer_id, event_id),
            ).fetchone()
            if row is None:
                raise LeaseError("Dead letter unavailable")
            work.connection.execute(
                "INSERT INTO event_delivery_replays(consumer_id,event_id,actor,reason_code,previous_error,created_at) VALUES (?,?,?,?,?,?)",
                (consumer_id, event_id, actor, reason_code, row["last_error"], now),
            )
            work.connection.execute(
                "UPDATE event_deliveries SET state='pending',attempts=0,next_attempt=0,lease_token=NULL,lease_until=NULL WHERE consumer_id=? AND event_id=?",
                (consumer_id, event_id),
            )
            work.connection.execute(
                "UPDATE event_consumers SET cursor_seq=MIN(cursor_seq,?) WHERE id=?",
                (row["seq"] - 1, consumer_id),
            )

    def tick(self):
        deadline = time.monotonic() + 0.05
        self.register("router")
        self.register("planner")
        self.register("composer")
        consumers = ["router", "planner", "composer"]
        with self.store.connection() as connection:
            workers = connection.execute(
                "SELECT id FROM workers WHERE id>? AND status NOT IN ('completed','failed','cancelled','rejected') ORDER BY id LIMIT 16",
                (self._worker_cursor,),
            ).fetchall()
        self._worker_cursor = workers[-1]["id"] if workers else ""
        for worker in workers:
            name = f"worker:{worker['id']}"
            self.register(name, kind="worker", worker_id=worker["id"])
            consumers.append(name)
        delivered = 0
        start = self._consumer_cursor % len(consumers)
        ordered = consumers[start:] + consumers[:start]
        attempted = 0
        for consumer in ordered:
            if attempted and (delivered >= 16 or time.monotonic() >= deadline):
                break
            attempted += 1
            lease = self.claim(consumer)
            if lease is not None:
                try:
                    self.deliver(lease)
                    delivered += 1
                except ValueError:
                    self.fail(lease, "invalid_event")
        self._consumer_cursor = (start + attempted) % len(consumers)
        return delivered
