import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys

import pytest

from forge_core.dispatcher import Dispatcher
from forge_core.domain import DomainError
from forge_core.lifecycle import start, stop
from forge_core.settings import Workspace
from forge_core.store import Store


def test_lifecycle_generations_and_unknown_exit_are_persisted(tmp_path):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    first = start(store, session_challenge="a" * 64)
    second = start(store, session_challenge="b" * 64)
    stop(store, second)
    with pytest.raises(DomainError):
        stop(store, second)
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT event_type,payload FROM events WHERE event_type LIKE 'core.%' ORDER BY seq"
        ).fetchall()
        router = connection.execute(
            "SELECT router_session_id FROM router_state WHERE conversation_id='main'"
        ).fetchone()[0]
    assert [row["event_type"] for row in rows] == ["core.started", "core.started", "core.stopped"]
    assert first != second
    assert json.loads(rows[0]["payload"]) == {
        "generation": first,
        "workspace_id": store.workspace.id,
        "router_session_id": router,
        "pid": os.getpid(),
        "session_challenge_sha256": hashlib.sha256(("a" * 64).encode()).hexdigest(),
    }
    assert json.loads(rows[1]["payload"]) == json.loads(rows[2]["payload"])


def test_stop_rejects_wrong_process(tmp_path):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    generation = start(store, session_challenge="a" * 64)
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT id,payload FROM events WHERE event_type='core.started'"
        ).fetchone()
        payload = json.loads(row["payload"])
        payload["pid"] = os.getpid() + 1
        work.connection.execute(
            "UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), row["id"])
        )
    with pytest.raises(DomainError):
        stop(store, generation)


def test_stop_fails_closed_on_corrupt_lifecycle_payload(tmp_path):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    generation = start(store, session_challenge="a" * 64)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE events SET payload='{' WHERE event_type='core.started'")
    with pytest.raises(sqlite3.DatabaseError):
        stop(store, generation)


def test_lifecycle_event_does_not_trigger_router_notice(tmp_path):
    from forge_core.event_domain import URGENT_EVENTS

    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    start(store, session_challenge="a" * 64)
    dispatcher = Dispatcher(store)
    dispatcher.tick()
    allowed = (*URGENT_EVENTS, "worker.progress", "quest.status_changed")
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT event_type FROM event_inbox WHERE consumer_id='router'"
        ).fetchall()
    assert not any(row["event_type"] in allowed for row in rows)


def test_actual_desktop_core_restart_keeps_router_and_changes_generation(tmp_path):
    from forge_api.desktop_host import read_frame, write_frame

    env = {key: value for key, value in os.environ.items() if not key.startswith("FORGE_")}
    observed = []
    for index in range(3):
        process = subprocess.Popen(
            [sys.executable, "-m", "forge_api", "--desktop-host"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            write_frame(
                process.stdin,
                {
                    "version": 1,
                    "type": "bootstrap",
                    "secret": secrets.token_hex(32),
                    "workspace_root": str(tmp_path),
                    "credentials": {},
                },
            )
            assert read_frame(process.stdout)["type"] == "credentials_required"
            write_frame(process.stdin, {"version": 1, "type": "credentials", "values": {}})
            ready = read_frame(process.stdout)
            assert ready is not None, process.stderr.read().decode(errors="replace")
            assert ready["type"] == "ready"
            if index == 2:
                process.kill()
                process.wait(timeout=20)
            else:
                write_frame(process.stdin, {"version": 1, "type": "shutdown", "request_id": "test"})
                assert read_frame(process.stdout)["type"] == "stopped"
                assert process.wait(timeout=20) == 0
            observed.append(ready)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
    assert observed[0]["pid"] != observed[1]["pid"]
    assert observed[0]["generation"] != observed[1]["generation"]
    store = Store(tmp_path / "data" / "forge.sqlite3", workspace=Workspace.from_path(tmp_path))
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT event_type,payload FROM events WHERE event_type LIKE 'core.%' ORDER BY seq"
        ).fetchall()
    assert [row["event_type"] for row in rows] == [
        "core.started",
        "core.stopped",
        "core.started",
        "core.stopped",
        "core.started",
    ]
    events = [json.loads(row["payload"]) for row in rows]
    assert [events[0]["generation"], events[2]["generation"], events[4]["generation"]] == [
        item["generation"] for item in observed
    ]
    assert events[0]["router_session_id"] == events[2]["router_session_id"]
