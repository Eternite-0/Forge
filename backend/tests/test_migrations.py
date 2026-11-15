import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from forge_core.migrations import SCHEMA_VERSION
from forge_core.store import Store


def test_schema_version_is_singleton_after_reopening(tmp_path):
    path = tmp_path / "singleton.sqlite3"
    for _ in range(3):
        Store(str(path))
    with closing(sqlite3.connect(path)) as connection:
        assert len(connection.execute("SELECT version FROM schema_meta").fetchall()) == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO schema_meta(singleton,version) VALUES (2,1)")


def test_legacy_upgrade_preserves_every_entity(legacy_database):
    store = Store(str(legacy_database))
    with closing(sqlite3.connect(legacy_database)) as connection:
        assert len(connection.execute("SELECT version FROM schema_meta").fetchall()) == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store.ensure_router()["router_session_id"] == "router-legacy"
    assert store.ensure_router()["event_cursor"] == 7
    assert store.list_messages()[0]["content"] == "保留历史"
    assert store.get_quest("quest-old")["version"] == 0
    assert store.list_workers()[0]["hidden"] == 1
    assert store.list_models()[0].id == "model-old"
    assert store.list_events()[0]["id"] == "event-old"
    assert store.list_outbox()[0]["event_id"] == "event-old"


@pytest.mark.parametrize("versions", [(999,), (1, 999)])
def test_future_schema_is_rejected_without_mutation(legacy_database, versions):
    with closing(sqlite3.connect(legacy_database)) as connection:
        connection.execute("DELETE FROM schema_meta")
        connection.executemany("INSERT INTO schema_meta VALUES (?)", [(v,) for v in versions])
        connection.commit()
        before = connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    with pytest.raises(ValueError, match="(?i)(version|newer|unsupported)"):
        Store(str(legacy_database))
    with closing(sqlite3.connect(legacy_database)) as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [
            (v,) for v in versions
        ]
        assert (
            connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
            == before
        )


@pytest.mark.parametrize("versions", [(), (1, 2)])
def test_ambiguous_legacy_version_is_rejected(legacy_database, versions):
    with closing(sqlite3.connect(legacy_database)) as connection:
        connection.execute("DELETE FROM schema_meta")
        connection.executemany("INSERT INTO schema_meta VALUES (?)", [(v,) for v in versions])
        connection.commit()
    with pytest.raises(ValueError, match="(?i)version"):
        Store(str(legacy_database))


def test_each_migration_step(legacy_database, tmp_path):
    from forge_core.migrations import SCHEMA_VERSION, migrate

    with closing(sqlite3.connect(tmp_path / "fresh.sqlite3", isolation_level=None)) as connection:
        migrate(connection, target_version=1)
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [(1,)]
    with closing(sqlite3.connect(legacy_database, isolation_level=None)) as connection:
        migrate(connection, target_version=2)
        assert connection.execute("SELECT singleton,version FROM schema_meta").fetchall() == [
            (1, 2)
        ]
        assert connection.execute("SELECT version FROM quests").fetchall() == [(0,)]
        assert connection.execute("SELECT event_id FROM outbox").fetchall() == [("event-old",)]
        migrate(connection, target_version=3)
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [(3,)]
        assert connection.execute("SELECT COUNT(*) FROM workspace").fetchone()[0] == 0
        migrate(connection, target_version=4)
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [(4,)]
        migrate(connection, target_version=5)
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [(5,)]
        migrate(connection, target_version=6)
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [(6,)]
        migrate(connection, target_version=7)
        assert SCHEMA_VERSION >= 14
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [(7,)]
        migrate(connection)
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [
            (SCHEMA_VERSION,)
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_failure_rolls_back_ddl_data_and_version(legacy_database):
    from forge_core.migrations import MIGRATIONS, migrate

    def invalid_migration(connection):
        connection.execute("CREATE TABLE should_not_survive(id INTEGER)")
        connection.execute("INSERT INTO nonexistent_migration_table VALUES (1)")

    migrations = dict(MIGRATIONS)
    migrations[3] = invalid_migration
    with closing(sqlite3.connect(legacy_database, isolation_level=None)) as connection:
        before = connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="nonexistent_migration_table"):
            migrate(connection, migrations=migrations)
        assert not connection.in_transaction
        assert (
            connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
            == before
        )
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [
            (1,),
            (1,),
            (1,),
        ]
        assert connection.execute("SELECT content FROM messages").fetchone()[0] == "保留历史"
    assert Store(str(legacy_database)).get_quest("quest-old")["version"] == 0


@pytest.mark.parametrize("trial", range(5))
def test_concurrent_initialization_keeps_one_version_and_router(tmp_path, trial):
    path = str(tmp_path / "startup-race.sqlite3")
    barrier = Barrier(4)

    def open_store(_):
        barrier.wait(timeout=10)
        return Store(path).ensure_router()["router_session_id"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        identities = list(executor.map(open_store, range(4)))
    assert len(set(identities)) == 1
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0] == 1


@pytest.mark.parametrize("failing_version", range(1, SCHEMA_VERSION + 1))
def test_each_migration_failure_rolls_back_a_fresh_database(tmp_path, failing_version):
    from forge_core.migrations import MIGRATIONS, migrate

    def fail_after_real_step(connection):
        MIGRATIONS[failing_version](connection)
        connection.execute("INSERT INTO nonexistent_migration_table VALUES (1)")

    steps = dict(MIGRATIONS)
    steps[failing_version] = fail_after_real_step
    with closing(
        sqlite3.connect(tmp_path / "fresh-abort.sqlite3", isolation_level=None)
    ) as connection:
        with pytest.raises(sqlite3.OperationalError, match="nonexistent_migration_table"):
            migrate(connection, migrations=steps)
        assert not connection.in_transaction
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
        migrate(connection, target_version=3)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 3
        migrate(connection, target_version=4)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 4
        migrate(connection, target_version=5)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 5
        migrate(connection, target_version=6)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 6
        migrate(connection, target_version=7)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 7
        migrate(connection)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.parametrize(
    "module_name,function_name",
    [
        ("context_schema", "migrate_context"),
        ("memory_schema", "migrate_memory"),
        ("model_budget_schema", "migrate_model_budget"),
    ],
)
def test_v8_component_failure_rolls_back_v7_data(tmp_path, monkeypatch, module_name, function_name):
    import importlib

    from forge_core.migrations import migrate

    with closing(sqlite3.connect(tmp_path / "v7.sqlite3", isolation_level=None)) as connection:
        migrate(connection, target_version=7)
        connection.execute("INSERT INTO conversations VALUES ('main','now')")
        connection.execute(
            "INSERT INTO router_state(conversation_id,router_session_id) VALUES ('main','router-preserved')"
        )
        connection.execute("INSERT INTO messages VALUES ('m','main','user','original','now','{}')")
        before = connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
        module = importlib.import_module("forge_core." + module_name)
        original = getattr(module, function_name)

        def fail(conn):
            original(conn)
            raise RuntimeError("component failure")

        monkeypatch.setattr(module, function_name, fail)
        with pytest.raises(RuntimeError, match="component failure"):
            migrate(connection)
        assert (
            connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
            == before
        )
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 7
        assert (
            connection.execute("SELECT router_session_id FROM router_state").fetchone()[0]
            == "router-preserved"
        )
        assert connection.execute("SELECT content FROM messages").fetchone()[0] == "original"
        monkeypatch.setattr(module, function_name, original)
        migrate(connection)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION
