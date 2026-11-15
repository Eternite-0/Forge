"""Offline schema-cutover preservation and durable reservation boundaries."""

import json
import sqlite3
from contextlib import closing

import pytest

from forge_core.migrations import MIGRATIONS, SCHEMA_VERSION, migrate


def _insert(connection, table, values):
    columns = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO {table}({columns}) VALUES ({placeholders})", tuple(values.values())
    )


def _seed_v11(connection):
    migrate(connection, target_version=11)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO workspace VALUES (1,'workspace-old','D:/old')")
    connection.execute(
        "INSERT INTO quest_plans(id,quest_id,revision,digest,status,graph_json,created_at) VALUES ('plan-old','quest-old',1,'plan-digest','approved','{}','then')"
    )
    _insert(
        connection,
        "task_nodes",
        {
            "plan_id": "plan-old",
            "node_key": "node-old",
            "objective": "original",
            "criteria_json": "[]",
            "budget_tokens": 100000,
            "required_capabilities": "[]",
            "max_attempts": 1,
            "worker_id": "worker-old",
        },
    )
    _insert(
        connection,
        "worker_runs",
        {
            "id": "run-old",
            "worker_id": "worker-old",
            "quest_id": "quest-old",
            "plan_id": "plan-old",
            "node_key": "node-old",
            "plan_revision": 1,
            "plan_digest": "plan-digest",
            "team_digest": "team-digest",
            "member_index": 0,
            "role_id": "role-old",
            "provider_id": "provider-old",
            "model_id": "model-old",
            "execution_mode": "live",
            "state": "paused",
            "generation": 4,
            "allowed_tools_json": "[]",
            "capability_sources_json": "{}",
            "token_limit": 100000,
            "token_spent": 18234,
            "tool_limit": 10,
            "tool_spent": 0,
            "step_limit": 10,
            "steps": 1,
            "deadline": 10000000000,
            "depth": 0,
            "created_at": "then",
            "output_limit": 8192,
        },
    )
    for state in ("settled", "unknown", "reserved"):
        _insert(
            connection,
            "model_budget_attempts",
            {
                "attempt_id": "attempt-" + state,
                "call_id": "legacy-call-" + state,
                "purpose": "worker",
                "workspace_id": "workspace-old",
                "quest_id": "quest-old",
                "worker_run_id": "run-old",
                "provider_id": "provider-old",
                "model_id": "model-old",
                "input_reserved": 10294,
                "output_reserved": 8192,
                "state": state,
                "created_at": 1,
                "input_charged": 2695 if state == "settled" else None,
                "output_charged": 110 if state == "settled" else None,
            },
        )
    connection.execute(
        "INSERT INTO worker_messages VALUES ('run-old',0,'user',?)",
        ('{ "content": "历史内容\\n保留空格", "raw": [ 1, 2 ] }',),
    )


def _snapshot(connection):
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name!='schema_meta' ORDER BY name"
        )
    ]
    return {
        table: json.dumps(
            sorted(
                connection.execute(f'SELECT * FROM "{table}"').fetchall(),
                key=lambda row: json.dumps(row, ensure_ascii=False),
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        for table in tables
    }


@pytest.fixture
def reservation_db(legacy_database):
    with closing(sqlite3.connect(legacy_database, isolation_level=None)) as connection:
        _seed_v11(connection)
        migrate(connection)
        yield connection


def _reservation(**overrides):
    values = {
        "call_id": "new-call",
        "run_id": "run-old",
        "generation": 4,
        "lease_token": "old-owner",
        "provider_id": "provider-old",
        "model_id": "model-old",
        "execution_mode": "live",
        "context_json": '{"workspace_id":"workspace-old"}',
        "reserved_tokens": 100,
        "output_limit": 20,
        "reserved_cost_microunits": None,
        "currency": None,
        "price_bounds_json": None,
        "cost_scopes_json": "[]",
        "state": "reserved",
        "input_tokens": None,
        "output_tokens": None,
        "actual_cost_microunits": None,
        "created_at": "then",
        "settled_at": None,
    }
    values.update(overrides)
    return values


def test_v11_cutover_preserves_all_history_and_does_not_backfill(legacy_database):
    with closing(sqlite3.connect(legacy_database, isolation_level=None)) as connection:
        _seed_v11(connection)
        before = _snapshot(connection)
        schema_before = connection.execute(
            "SELECT name,sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        migrate(connection)
        assert SCHEMA_VERSION >= 14
        assert connection.execute("SELECT version FROM schema_meta").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("SELECT * FROM worker_model_reservations").fetchall() == []
        after = _snapshot(connection)
        assert {table: after[table] for table in before} == before
        schema_after = dict(
            connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
        )
        assert all(schema_after[name] == sql for name, sql in schema_before)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        changes = connection.total_changes
        migrate(connection)
        assert connection.total_changes == changes
        assert _snapshot(connection) == after


def test_v12_failure_rolls_back_new_table_and_preserves_v11(legacy_database):
    with closing(sqlite3.connect(legacy_database, isolation_level=None)) as connection:
        _seed_v11(connection)
        before = _snapshot(connection)
        schema_before = connection.execute(
            "SELECT name,sql FROM sqlite_master ORDER BY name"
        ).fetchall()

        def fail_after_real_step(db):
            MIGRATIONS[12](db)
            raise RuntimeError("interrupted schema cutover")

        steps = {**MIGRATIONS, 12: fail_after_real_step}
        with pytest.raises(RuntimeError, match="interrupted schema cutover"):
            migrate(connection, migrations=steps)
        assert not connection.in_transaction
        assert connection.execute("SELECT version FROM schema_meta").fetchone() == (11,)
        assert (
            connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
            == schema_before
        )
        assert _snapshot(connection) == before


def test_reservation_and_worker_admission_share_rollback_boundary(reservation_db):
    connection = reservation_db
    before = connection.execute("SELECT token_spent,steps FROM worker_runs").fetchall()
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "UPDATE worker_runs SET token_spent=token_spent+100,steps=steps+1 WHERE id='run-old'"
    )
    _insert(connection, "worker_model_reservations", _reservation())
    connection.rollback()  # Simulated crash before the admission transaction commits.
    assert connection.execute("SELECT token_spent,steps FROM worker_runs").fetchall() == before
    assert connection.execute("SELECT * FROM worker_model_reservations").fetchall() == []


def test_reservation_requires_existing_run_and_unique_call(reservation_db):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _insert(reservation_db, "worker_model_reservations", _reservation(run_id="foreign"))
    _insert(reservation_db, "worker_model_reservations", _reservation())
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert(reservation_db, "worker_model_reservations", _reservation())


@pytest.mark.parametrize(
    "changes",
    [
        {"reserved_tokens": -1},
        {"output_limit": 101},
        {"generation": -1},
        {"reserved_tokens": 1.5},
        {"execution_mode": "inferred"},
        {"reserved_cost_microunits": -1},
        {"reserved_cost_microunits": 1},
        {"input_tokens": 0},
        {"state": "settled", "input_tokens": 81, "output_tokens": 1, "settled_at": "now"},
        {"state": "settled", "input_tokens": 1, "output_tokens": 21, "settled_at": "now"},
        {"state": "settled", "input_tokens": None, "output_tokens": 1, "settled_at": "now"},
        {"state": "refunded", "settled_at": "now"},
    ],
)
def test_invalid_reservation_or_settlement_is_rejected(reservation_db, changes):
    with pytest.raises(sqlite3.IntegrityError):
        _insert(reservation_db, "worker_model_reservations", _reservation(**changes))


def test_original_binding_and_terminal_settlement_cannot_be_rewritten(reservation_db):
    connection = reservation_db
    _insert(connection, "worker_model_reservations", _reservation())
    for field, value in (
        ("call_id", "replayed"),
        ("run_id", "foreign"),
        ("generation", 5),
        ("lease_token", "new-owner"),
        ("context_json", "{}"),
        ("reserved_tokens", 200),
        ("price_bounds_json", "{}"),
        ("cost_scopes_json", '[{"scope":"foreign"}]'),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            connection.execute(f"UPDATE worker_model_reservations SET {field}=?", (value,))
    connection.execute(
        "UPDATE worker_model_reservations SET state='settled',input_tokens=10,output_tokens=2,settled_at='now'"
    )
    for assignment in ("state='reserved'", "input_tokens=9", "settled_at='later'"):
        with pytest.raises(sqlite3.IntegrityError, match="settlement is immutable"):
            connection.execute("UPDATE worker_model_reservations SET " + assignment)
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        connection.execute("DELETE FROM worker_model_reservations")
    assert connection.execute(
        "SELECT state,input_tokens,output_tokens FROM worker_model_reservations"
    ).fetchone() == ("settled", 10, 2)
    assert connection.execute("SELECT token_spent,steps FROM worker_runs").fetchone() == (18234, 1)


def test_cost_settlement_is_bounded_and_unsent_refund_is_terminal(reservation_db):
    connection = reservation_db
    _insert(
        connection,
        "worker_model_reservations",
        _reservation(
            reserved_cost_microunits=10,
            currency="USD",
            price_bounds_json='{"input_microunits_per_million":1}',
        ),
    )
    for cost in (-1, 11, None):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE worker_model_reservations SET state='settled',input_tokens=1,output_tokens=1,actual_cost_microunits=?,settled_at='now'",
                (cost,),
            )
    connection.execute(
        "UPDATE worker_model_reservations SET state='refunded',input_tokens=0,output_tokens=0,actual_cost_microunits=0,settled_at='now'"
    )
    with pytest.raises(sqlite3.IntegrityError, match="settlement is immutable"):
        connection.execute(
            "UPDATE worker_model_reservations SET state='settled',input_tokens=1,output_tokens=1"
        )


@pytest.mark.parametrize("state", ["reserved", "settled", "refunded"])
def test_replace_cannot_reopen_or_rebind_existing_call(reservation_db, state):
    connection = reservation_db
    assert connection.execute("PRAGMA recursive_triggers").fetchone() == (0,)
    values = _reservation()
    if state != "reserved":
        values.update(state=state, input_tokens=0, output_tokens=0, settled_at="now")
    _insert(connection, "worker_model_reservations", values)
    before = connection.execute("SELECT * FROM worker_model_reservations").fetchone()
    replacement = _reservation(lease_token="replacement-owner", reserved_tokens=500)
    columns = ",".join(replacement)
    placeholders = ",".join("?" for _ in replacement)
    with pytest.raises(sqlite3.IntegrityError, match="call_id is UNIQUE"):
        connection.execute(
            f"INSERT OR REPLACE INTO worker_model_reservations({columns}) VALUES ({placeholders})",
            tuple(replacement.values()),
        )
    assert connection.execute("SELECT * FROM worker_model_reservations").fetchone() == before
