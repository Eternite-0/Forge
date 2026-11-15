"""Persist component health and recover fenced, replay-safe local work."""

import logging
import math
import time

from .domain import DomainError, ForgeEvent
from .worker_environment import worker_environment
from .worker_model_usage import reconcile_worker_model_calls
from .worker_recovery import unresolved_calls


def migrate(connection):
    connection.execute("""CREATE TABLE runtime_heartbeats(
        component TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL,
        last_seen REAL NOT NULL, timeout_seconds REAL NOT NULL,
        restart_count INTEGER NOT NULL DEFAULT 0,
        next_restart_at REAL NOT NULL DEFAULT 0)""")


def _positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"Invalid {name}")
    return float(value)


class Watchdog:
    """Recovery does not grant authority or replay an unknown external action.

    `tick` only requeues expired Workers with no unresolved effects/admissions.
    The normal runtime must recheck every existing authorization at its claim.
    A live but stalled task is reported, never cancelled or started twice.
    """

    def __init__(
        self, store, *, clock=time.time, heartbeat_interval=10, stable_seconds=300, batch_size=64
    ):
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError("Invalid watchdog batch size")
        self.store, self.clock = store, clock
        self.heartbeat_interval = _positive(heartbeat_interval, "heartbeat interval")
        self.stable_seconds = _positive(stable_seconds, "component stability window")
        if self.stable_seconds > 3600:
            raise ValueError("Invalid component stability window")
        self.batch_size = batch_size
        self._heartbeats = {}
        self._healthy_since = {}

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Invalid watchdog clock")
        return float(value)

    def heartbeat(self, component, *, owner="core", state="ok", timeout_seconds=120):
        if (
            not isinstance(component, str)
            or not 1 <= len(component) <= 128
            or not isinstance(owner, str)
            or not 1 <= len(owner) <= 256
            or state not in {"ok", "retrying", "restarting", "stopped", "unavailable"}
        ):
            raise ValueError("Invalid component heartbeat")
        timeout = _positive(timeout_seconds, "component timeout")
        now = self._now()
        previous = self._heartbeats.get(component)
        signature = (owner, state, timeout)
        if (
            previous is not None
            and previous[0] == signature
            and 0 <= now - previous[1] < min(self.heartbeat_interval, timeout / 3)
        ):
            return False
        # Recovery credit requires observed healthy operation in this host,
        # rather than merely time elapsed while the Core was stopped. A new
        # owner, retry or missing heartbeat starts a fresh stability window.
        continuous = (
            state == "ok"
            and previous is not None
            and previous[0] == signature
            and 0 <= now - previous[1] < timeout
        )
        healthy_since = self._healthy_since.get(component, now) if continuous else now
        reset_restarts = state == "ok" and now - healthy_since >= self.stable_seconds
        with self.store.unit_of_work() as work:
            work.connection.execute(
                "INSERT INTO runtime_heartbeats(component,owner,state,last_seen,timeout_seconds) "
                "VALUES (?,?,?,?,?) ON CONFLICT(component) DO UPDATE SET owner=excluded.owner,"
                "state=excluded.state,last_seen=excluded.last_seen,"
                "timeout_seconds=excluded.timeout_seconds,"
                "restart_count=CASE WHEN ? THEN 0 ELSE runtime_heartbeats.restart_count END,"
                "next_restart_at=CASE WHEN ? THEN 0 ELSE runtime_heartbeats.next_restart_at END",
                (component, owner, state, now, timeout, reset_restarts, reset_restarts),
            )
        self._heartbeats[component] = (signature, now)
        if state == "ok":
            self._healthy_since[component] = healthy_since
        else:
            self._healthy_since.pop(component, None)
        return True

    def snapshot(self):
        with self.store.connection() as connection:
            return [
                dict(row)
                for row in connection.execute("SELECT * FROM runtime_heartbeats ORDER BY component")
            ]

    def supervise(self, tasks, restart, *, max_restarts=5):
        """Replace exited loop tasks through a trusted `restart(name)` callback.

        Example: `watchdog.supervise(loops, lambda name:
        asyncio.create_task(run_loop(name, operations[name])))`. `loops` is the
        same mutable name-to-Task mapping retained for Core shutdown. Do not call
        this method during shutdown. Pending tasks, including timed-out Provider
        waits, are never cancelled or duplicated by this method.
        """
        if type(max_restarts) is not int or not 1 <= max_restarts <= 100:
            raise ValueError("Invalid component restart limit")
        now = self._now()
        restarted = []
        for name, task in list(tasks.items()):
            if not task.done():
                continue
            if not task.cancelled():
                task.exception()  # Observe the failure without persisting remote exception text.
            with self.store.unit_of_work() as work:
                row = work.connection.execute(
                    "SELECT * FROM runtime_heartbeats WHERE component=?", (name,)
                ).fetchone()
                if row is None or row["next_restart_at"] > now:
                    continue
                if row["restart_count"] >= max_restarts:
                    work.connection.execute(
                        "UPDATE runtime_heartbeats SET state='unavailable' WHERE component=?",
                        (name,),
                    )
                    continue
                count = row["restart_count"] + 1
                self._healthy_since.pop(name, None)
                work.connection.execute(
                    "UPDATE runtime_heartbeats SET state='restarting',restart_count=?,"
                    "next_restart_at=? WHERE component=?",
                    (count, now + min(2**count, 30), name),
                )
                work.append_event(
                    ForgeEvent(
                        event_type="runtime.component_restarting",
                        payload={"component": name, "attempt": count, "reason_code": "loop_exited"},
                    )
                )
            try:
                replacement = restart(name)
            except Exception:  # noqa: BLE001 - a failed factory remains eligible after backoff.
                logging.getLogger(__name__).warning("Component restart factory failed")
                continue
            tasks[name] = replacement
            restarted.append(name)
        return restarted

    def tick(self):
        now = self._now()
        report = {
            "requeued": [],
            "blocked": [],
            "failed": [],
            "deliveries_released": 0,
            "stalled_components": [],
        }
        with self.store.unit_of_work() as work:
            connection = work.connection
            stale = connection.execute(
                "SELECT * FROM runtime_heartbeats WHERE state IN ('ok','retrying','restarting') "
                "AND last_seen+timeout_seconds<=? ORDER BY last_seen LIMIT ?",
                (now, self.batch_size),
            ).fetchall()
            for row in stale:
                connection.execute(
                    "UPDATE runtime_heartbeats SET state='stalled' WHERE component=?",
                    (row["component"],),
                )
                report["stalled_components"].append(row["component"])
                work.append_event(
                    ForgeEvent(
                        event_type="runtime.component_stalled",
                        payload={"component": row["component"], "reason_code": "heartbeat_timeout"},
                    )
                )
            deliveries = connection.execute(
                "SELECT consumer_id,event_id,lease_token FROM event_deliveries "
                "WHERE state='leased' AND lease_until<=? ORDER BY lease_until LIMIT ?",
                (now, self.batch_size),
            ).fetchall()
            for row in deliveries:
                changed = connection.execute(
                    "UPDATE event_deliveries SET state='pending',lease_token=NULL,lease_until=NULL,"
                    "last_error='watchdog_lease_expired' WHERE consumer_id=? AND event_id=? "
                    "AND state='leased' AND lease_token=? AND lease_until<=?",
                    (row["consumer_id"], row["event_id"], row["lease_token"], now),
                )
                report["deliveries_released"] += changed.rowcount
            runs = connection.execute(
                "SELECT r.*,q.conversation_id FROM worker_runs r JOIN quests q ON q.id=r.quest_id "
                "WHERE r.state='running' AND (r.lease_until IS NULL OR r.lease_until<=?) "
                "ORDER BY r.deadline,r.id LIMIT ?",
                (now, self.batch_size),
            ).fetchall()
            for row in runs:
                # Reuse the exact Gateway proof checker: settling usage never
                # consumes an old response or authorizes a new external action.
                reconcile_worker_model_calls(connection, row["id"])
                environment_unavailable = False
                try:
                    worker_environment(self.store, row["id"], create=False)
                except (DomainError, OSError):
                    environment_unavailable = True
                uncertain = (
                    bool(unresolved_calls(connection, row["id"]))
                    or bool(
                        connection.execute(
                            "SELECT 1 FROM action_intents WHERE run_id=? "
                            "AND state IN ('prepared','executing','uncertain') LIMIT 1",
                            (row["id"],),
                        ).fetchone()
                    )
                    or bool(
                        connection.execute(
                            "SELECT 1 FROM worker_model_reservations WHERE run_id=? AND state='reserved' "
                            "LIMIT 1",
                            (row["id"],),
                        ).fetchone()
                    )
                )
                state = (
                    "blocked"
                    if uncertain or environment_unavailable
                    else "failed"
                    if row["deadline"] <= now
                    else "queued"
                )
                reason = (
                    "unresolved_tool_recovery"
                    if uncertain
                    else "environment_unavailable"
                    if environment_unavailable
                    else "deadline"
                    if state == "failed"
                    else "expired_owner_no_unresolved_effects"
                )
                changed = connection.execute(
                    "UPDATE worker_runs SET state=?,generation=generation+1,lease_token=NULL,"
                    "lease_until=NULL WHERE id=? AND state='running' AND generation=? "
                    "AND (lease_until IS NULL OR lease_until<=?)",
                    (state, row["id"], row["generation"], now),
                )
                if not changed.rowcount:
                    continue
                if uncertain:
                    connection.execute(
                        "UPDATE worker_tool_calls SET state='uncertain' WHERE run_id=? AND state='running'",
                        (row["id"],),
                    )
                    connection.execute(
                        "UPDATE worker_core_source_reads SET state='uncertain' "
                        "WHERE run_id=? AND state='running'",
                        (row["id"],),
                    )
                connection.execute(
                    "UPDATE workers SET status=? WHERE id=?", (state, row["worker_id"])
                )
                connection.execute(
                    "UPDATE task_nodes SET status=? WHERE plan_id=? AND node_key=? AND worker_id=?",
                    (state, row["plan_id"], row["node_key"], row["worker_id"]),
                )
                report[
                    {"queued": "requeued", "blocked": "blocked", "failed": "failed"}[state]
                ].append(row["id"])
                work.append_event(
                    ForgeEvent(
                        event_type="runtime.worker_recovered"
                        if state == "queued"
                        else f"worker.{state}",
                        conversation_id=row["conversation_id"],
                        quest_id=row["quest_id"],
                        worker_id=row["worker_id"],
                        payload={
                            "protocol_version": 2,
                            "plan_id": row["plan_id"],
                            "task_node_key": row["node_key"],
                            "run_id": row["id"],
                            "reason_code": reason,
                            "source": "watchdog",
                        },
                    )
                )
        return report
