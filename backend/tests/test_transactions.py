import asyncio
import hashlib
import hmac
import importlib
import json
import secrets
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from conftest import RouterMockProvider
from fastapi.testclient import TestClient

from forge_core.domain import ConversationMessage, ForgeEvent, Quest, QuestStatus
from forge_core.quest_service import QuestService
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.store import Store


def install_abort_trigger(path, table, condition="1"):
    # All table/condition values are test constants. The failure is raised by real SQLite.
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            f"CREATE TRIGGER abort_audit BEFORE INSERT ON {table} WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'audit real SQLite failure'); END;"
        )


@pytest.mark.parametrize(
    ("table", "condition"),
    [("quests", "1"), ("events", "1"), ("outbox", "1"), ("messages", "NEW.role='assistant'")],
)
def test_router_cross_entity_failure_rolls_back(tmp_path, table, condition):
    store = Store(str(tmp_path / "router.sqlite3"))
    install_abort_trigger(store.path, table, condition)
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=RouterMockProvider("create_quest"),
        model_id="mock",
    )
    with pytest.raises(sqlite3.IntegrityError, match="audit real SQLite failure"):
        asyncio.run(router.handle_user_message("帮我开发事务回归测试"))
    assert store.list_messages() == []
    assert store.list_quests() == []
    assert store.list_events() == []
    assert store.list_outbox() == []


@pytest.mark.parametrize("table", ["events", "outbox"])
def test_status_and_event_failure_rolls_back(tmp_path, table):
    store = Store(str(tmp_path / "transition.sqlite3"))
    quest = Quest(title="Rollback", goal="Keep proposed")
    store.save_quest(quest)
    install_abort_trigger(store.path, table)
    with pytest.raises(sqlite3.IntegrityError, match="audit real SQLite failure"):
        QuestService(store).transition(quest.id, QuestStatus.AWAITING_APPROVAL)
    assert store.get_quest(quest.id)["status"] == "proposed"
    assert store.get_quest(quest.id)["version"] == 0
    assert store.list_events() == []
    assert store.list_outbox() == []


@pytest.mark.parametrize("table", ["events", "outbox"])
def test_worker_proposal_and_event_failure_rolls_back(tmp_path, monkeypatch, table):
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "worker-api.sqlite3"))
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    main = importlib.import_module("forge_api.main")
    importlib.reload(main)
    quest = Quest(title="Worker", goal="Atomic worker proposal")
    main.store.save_quest(quest)
    with TestClient(
        main.app,
        raise_server_exceptions=False,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 12345),
    ) as client:
        session = client.get("/health").json()["session"]
        token = hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
        client.headers.update({"Authorization": f"Bearer {token}"})
        before_events = main.store.list_events()
        before_outbox = main.store.list_outbox(pending_only=False)
        before_quest = main.store.get_quest(quest.id)
        assert [event["event_type"] for event in before_events] == ["core.started"]
        install_abort_trigger(main.store.path, table)
        try:
            response = client.post(f"/api/quests/{quest.id}/workers?role=research")
            assert response.status_code == 500
            assert main.store.list_workers() == []
            assert main.store.get_quest(quest.id) == before_quest
            assert main.store.list_events() == before_events
            assert [row["event_id"] for row in main.store.list_outbox(pending_only=False)] == [
                row["event_id"] for row in before_outbox
            ]
        finally:
            with closing(sqlite3.connect(main.store.path)) as connection:
                connection.execute("DROP TRIGGER abort_audit")
    assert response.status_code == 500
    assert main.store.list_workers() == []
    events = main.store.list_events()
    assert [event["event_type"] for event in events] == ["core.started", "core.stopped"]
    assert (
        json.loads(events[0]["payload"])["generation"]
        == json.loads(events[1]["payload"])["generation"]
    )
    assert {event["id"] for event in events} == {
        row["event_id"] for row in main.store.list_outbox(pending_only=False)
    }


@pytest.mark.parametrize("exception_type", [RuntimeError, KeyboardInterrupt])
def test_unit_of_work_exception_rolls_back_and_closes(tmp_path, exception_type):
    store = Store(str(tmp_path / "unit-of-work.sqlite3"))
    with pytest.raises(exception_type), store.unit_of_work() as work:
        connection = work.connection
        work.add_message(ConversationMessage(role="user", content="Not committed"))
        quest = Quest(title="Transient", goal="Rollback")
        work.save_quest(quest)
        work.append_event(ForgeEvent(event_type="quest.proposed", quest_id=quest.id))
        raise exception_type("Abort transaction")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    assert store.list_messages() == []
    assert store.list_quests() == []
    assert store.list_events() == []
    assert store.list_outbox() == []


def test_commit_failure_rolls_back_and_closes(tmp_path):
    store = Store(str(tmp_path / "commit.sqlite3"))
    with closing(sqlite3.connect(store.path)) as connection:
        connection.executescript(
            "CREATE TABLE test_parent(id INTEGER PRIMARY KEY);"
            "CREATE TABLE test_deferred(parent_id INTEGER REFERENCES test_parent(id) "
            "DEFERRABLE INITIALLY DEFERRED);"
        )
    with (
        pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"),
        store.unit_of_work() as work,
    ):
        connection = work.connection
        work.add_message(ConversationMessage(role="user", content="No partial commit"))
        connection.execute("INSERT INTO test_deferred VALUES (99)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    assert store.list_messages() == []


class TrackingStore(Store):
    """Keep references to actual SQLite connections to verify their explicit close."""

    def __init__(self, *args, **kwargs):
        self.opened_connections = []
        super().__init__(*args, **kwargs)

    def _connect(self):
        connection = super()._connect()
        self.opened_connections.append(connection)
        return connection


def test_repeated_reads_and_writes_explicitly_close_every_connection(tmp_path):
    store = TrackingStore(str(tmp_path / "resources.sqlite3"))
    for number in range(30):
        store.add_message(ConversationMessage(role="user", content=str(number)))
        assert len(store.list_messages()) == number + 1
    for connection in store.opened_connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    with store.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_begin_failure_releases_connection_and_allows_retry(tmp_path):
    store = TrackingStore(str(tmp_path / "begin-failure.sqlite3"), busy_timeout_ms=25)
    with closing(sqlite3.connect(store.path, isolation_level=None)) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"), store.unit_of_work():
            pytest.fail("A competing write transaction must not enter")
        blocker.rollback()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.opened_connections[-1].execute("SELECT 1")
    store.add_message(ConversationMessage(role="user", content="Retry succeeded"))
    assert len(store.list_messages()) == 1


def test_event_and_outbox_insert_are_atomic(tmp_path):
    store = Store(str(tmp_path / "outbox-abort.sqlite3"))
    install_abort_trigger(store.path, "outbox")
    with pytest.raises(sqlite3.IntegrityError):
        store.append_event(ForgeEvent(event_type="audit.event"))
    assert store.list_events() == []
    assert store.list_outbox() == []


def test_outbox_survives_restart_and_ack_is_idempotent(tmp_path):
    path = str(tmp_path / "outbox.sqlite3")
    store = Store(path)
    event = ForgeEvent(event_type="audit.event", payload={"value": 1})
    store.append_event(event)
    reopened = Store(path)
    assert [row["event_id"] for row in reopened.list_outbox()] == [event.id]
    assert reopened.mark_outbox_delivered(event.id)
    assert reopened.mark_outbox_delivered(event.id)
    assert not reopened.mark_outbox_delivered("missing")
    assert Store(path).list_outbox() == []
    assert len(Store(path).list_outbox(pending_only=False)) == 1


def test_process_crash_discards_uncommitted_cross_entity_work(tmp_path):
    path = str(tmp_path / "process-crash.sqlite3")
    store = Store(path)
    identity = store.ensure_router()["router_session_id"]
    code = """
import os, sys
from forge_core.store import Store
from forge_core.domain import ConversationMessage, Quest, ForgeEvent
store = Store(sys.argv[1])
with store.unit_of_work() as work:
    work.add_message(ConversationMessage(role='user', content='uncommitted'))
    quest = Quest(title='Crash fixture', goal='Rollback on crash')
    work.save_quest(quest)
    work.append_event(ForgeEvent(event_type='quest.proposed', quest_id=quest.id))
    os._exit(23)
"""
    child = subprocess.run(
        [sys.executable, "-c", code, path], check=False, capture_output=True, timeout=20
    )
    assert child.returncode == 23, child.stderr
    reopened = Store(path)
    assert reopened.ensure_router()["router_session_id"] == identity
    assert reopened.list_messages() == []
    assert reopened.list_quests() == []
    assert reopened.list_events() == []
    assert reopened.list_outbox() == []
    reopened.add_message(ConversationMessage(role="user", content="Writer lock released"))


def test_uncommitted_work_is_not_visible_to_other_connections(tmp_path):
    path = str(tmp_path / "visibility.sqlite3")
    writer, reader = Store(path), Store(path)
    with writer.unit_of_work() as work:
        work.add_message(ConversationMessage(role="user", content="Only after commit"))
        work.append_event(ForgeEvent(event_type="audit.event"))
        assert reader.list_messages() == []
        assert reader.list_events() == []
        assert reader.list_outbox() == []
    assert (
        len(reader.list_messages()) == len(reader.list_events()) == len(reader.list_outbox()) == 1
    )


def test_initial_workspace_conversation_and_router_seed_is_atomic(tmp_path):
    from forge_core.migrations import migrate

    path = tmp_path / "seed-abort.sqlite3"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        migrate(connection)
    install_abort_trigger(path, "router_state")
    with pytest.raises(sqlite3.IntegrityError):
        Store(path)
    with closing(sqlite3.connect(path)) as connection:
        for table in ("workspace", "conversations", "router_state"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
