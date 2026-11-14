"""Sequential SQLite migrations; all pending steps share one rollback boundary."""

import sqlite3
from collections.abc import Callable, Mapping

SCHEMA_VERSION = 9
Migration = Callable[[sqlite3.Connection], None]


class SchemaVersionError(ValueError):
    pass


class UnsupportedSchemaVersion(SchemaVersionError):
    pass


def _version(connection: sqlite3.Connection) -> int:
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "schema_meta" not in tables:
        if tables - {"sqlite_sequence"}:
            raise SchemaVersionError("Database has tables but no schema version")
        return 0
    values = [row[0] for row in connection.execute("SELECT version FROM schema_meta")]
    if any(not isinstance(value, int) or value < 1 for value in values):
        raise SchemaVersionError("Invalid database schema version")
    if any(value > SCHEMA_VERSION for value in values):
        raise UnsupportedSchemaVersion(
            f"Database version {max(values)} is newer than {SCHEMA_VERSION}"
        )
    if not values or len(set(values)) != 1:
        raise SchemaVersionError("Ambiguous or missing database schema version")
    version = values[0]
    columns = {row[1] for row in connection.execute("PRAGMA table_info(schema_meta)")}
    if version >= 2:
        if columns != {"singleton", "version"} or len(values) != 1:
            raise SchemaVersionError(
                "Schema version metadata must contain exactly one canonical row"
            )
        if connection.execute("SELECT singleton FROM schema_meta").fetchone()[0] != 1:
            raise SchemaVersionError("Invalid schema version singleton")
    elif columns != {"version"}:
        raise SchemaVersionError("Invalid legacy version metadata")
    return version


def _initial_schema(connection):
    statements = [
        "CREATE TABLE schema_meta(version INTEGER NOT NULL)",
        "CREATE TABLE conversations(id TEXT PRIMARY KEY, created_at TEXT NOT NULL)",
        """CREATE TABLE messages(
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
            role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, metadata TEXT NOT NULL)""",
        """CREATE TABLE router_state(
            conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
            router_session_id TEXT NOT NULL, event_cursor INTEGER NOT NULL DEFAULT 0,
            capsule TEXT NOT NULL DEFAULT '{}')""",
        """CREATE TABLE quests(
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
            title TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL,
            priority INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            metadata TEXT NOT NULL)""",
        """CREATE TABLE workers(
            id TEXT PRIMARY KEY, quest_id TEXT NOT NULL REFERENCES quests(id),
            role TEXT NOT NULL, model_id TEXT, status TEXT NOT NULL,
            hidden INTEGER NOT NULL, created_at TEXT NOT NULL, metadata TEXT NOT NULL)""",
        """CREATE TABLE events(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL, conversation_id TEXT NOT NULL,
            quest_id TEXT, worker_id TEXT, payload TEXT NOT NULL, created_at TEXT NOT NULL)""",
        "CREATE INDEX idx_events_conversation_seq ON events(conversation_id,seq)",
        "CREATE INDEX idx_quests_status ON quests(status)",
        "CREATE TABLE models(id TEXT PRIMARY KEY, profile TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "INSERT INTO schema_meta VALUES (1)",
    ]
    for statement in statements:
        connection.execute(statement)


def _reliable_state(connection):
    connection.execute("ALTER TABLE schema_meta RENAME TO legacy_schema_meta")
    connection.execute("""CREATE TABLE schema_meta(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        version INTEGER NOT NULL CHECK(version>=1))""")
    connection.execute("INSERT INTO schema_meta VALUES (1,1)")
    connection.execute("DROP TABLE legacy_schema_meta")
    connection.execute(
        "ALTER TABLE quests ADD COLUMN version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0)"
    )
    connection.execute("""CREATE TABLE outbox(
        event_id TEXT PRIMARY KEY REFERENCES events(id),
        created_at TEXT NOT NULL, delivered_at TEXT)""")
    connection.execute("CREATE INDEX idx_outbox_pending ON outbox(delivered_at,event_id)")
    # Historical events have never been acknowledged; future consumers must be idempotent.
    connection.execute("INSERT INTO outbox(event_id,created_at) SELECT id,created_at FROM events")


def _workspace_binding(connection):
    connection.execute("""CREATE TABLE workspace(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        id TEXT UNIQUE NOT NULL, root TEXT NOT NULL)""")


def _governance(connection):
    connection.execute(
        "ALTER TABLE quests ADD COLUMN plan_revision INTEGER NOT NULL DEFAULT 0 CHECK(plan_revision>=0)"
    )
    connection.execute("""CREATE TABLE approvals(
        id TEXT PRIMARY KEY, quest_id TEXT NOT NULL REFERENCES quests(id),
        plan_revision INTEGER NOT NULL,
        action_type TEXT NOT NULL, parameter_digest TEXT NOT NULL, resource_scope TEXT NOT NULL,
        risk TEXT NOT NULL, policy_version TEXT NOT NULL, reason TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED','EXPIRED','REVOKED')),
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT,
        consumed_at TEXT)""")
    connection.execute("""CREATE TABLE evidence(
        id TEXT PRIMARY KEY, quest_id TEXT NOT NULL REFERENCES quests(id), kind TEXT NOT NULL,
        trusted INTEGER NOT NULL CHECK(trusted IN (0,1)), payload TEXT NOT NULL, created_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE task_grants(
        quest_id TEXT NOT NULL REFERENCES quests(id), workspace_id TEXT NOT NULL,
        plan_revision INTEGER NOT NULL,
        resource_scope TEXT NOT NULL, granted_by TEXT NOT NULL, created_at TEXT NOT NULL,
        revoked_at TEXT, PRIMARY KEY(quest_id,workspace_id,resource_scope))""")
    connection.execute("""CREATE TABLE quest_requirements(
        quest_id TEXT PRIMARY KEY REFERENCES quests(id), plan_revision INTEGER NOT NULL,
        criteria_json TEXT NOT NULL, required_tests_json TEXT NOT NULL,
        required_approvals_json TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE execution_runs(
        id TEXT PRIMARY KEY, quest_id TEXT NOT NULL REFERENCES quests(id),
        plan_revision INTEGER NOT NULL, worker_id TEXT NOT NULL REFERENCES workers(id),
        kind TEXT NOT NULL CHECK(kind IN ('model_worker','json_validation')),
        execution_mode TEXT NOT NULL CHECK(execution_mode IN ('live','mock')),
        command_json TEXT NOT NULL, cwd TEXT NOT NULL, provenance TEXT NOT NULL,
        exit_code INTEGER NOT NULL, stdout_sha256 TEXT NOT NULL,
        stderr_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE approval_executions(
        approval_id TEXT PRIMARY KEY REFERENCES approvals(id),
        quest_id TEXT NOT NULL REFERENCES quests(id), plan_revision INTEGER NOT NULL,
        run_id TEXT NOT NULL REFERENCES execution_runs(id), created_at TEXT NOT NULL)""")


def _router_and_registry(connection):
    from .registry_schema import migrate_registry

    connection.execute("ALTER TABLE router_state ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
    connection.execute(
        "ALTER TABLE router_state ADD COLUMN capsule_version INTEGER NOT NULL DEFAULT 0"
    )
    connection.execute("""CREATE TABLE router_turns(
        request_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
        input_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
    migrate_registry(connection)


def _graph_and_dispatcher(connection):
    from .dispatcher_schema import migrate_dispatcher
    from .graph_schema import migrate_graph

    migrate_graph(connection)
    migrate_dispatcher(connection)


def _worker_team_tools(connection):
    from .team_schema import migrate_team
    from .tool_schema import migrate_tools
    from .worker_schema import migrate_worker

    migrate_tools(connection)
    migrate_team(connection)
    migrate_worker(connection)


def _context_memory_budget(connection):
    from .context_schema import migrate_context
    from .memory_schema import migrate_memory
    from .model_budget_schema import migrate_model_budget

    migrate_context(connection)
    migrate_memory(connection)
    migrate_model_budget(connection)


def _worker_output_contract(connection):
    import json

    from .domain import new_id, now_iso

    connection.execute(
        "ALTER TABLE worker_runs ADD COLUMN output_limit INTEGER CHECK(output_limit > 0)"
    )
    connection.execute(
        "ALTER TABLE worker_runs ADD COLUMN model_max_output_tokens INTEGER CHECK(model_max_output_tokens > 0)"
    )
    connection.execute(
        "ALTER TABLE worker_runs ADD COLUMN model_context_window INTEGER CHECK(model_context_window > 0)"
    )
    connection.execute("ALTER TABLE worker_runs ADD COLUMN next_attempt_at REAL NOT NULL DEFAULT 0")
    connection.execute("""CREATE TABLE restore_reviews (
        fence_sha256 TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL,
        rollback_directory TEXT NOT NULL, reason TEXT NOT NULL, reviewed_at TEXT NOT NULL)""")
    connection.execute(
        "CREATE TABLE model_budget_worker_teams (worker_run_id TEXT PRIMARY KEY,team_digest TEXT NOT NULL)"
    )
    connection.execute("""CREATE TABLE semantic_test_runs (
        id TEXT PRIMARY KEY, quest_id TEXT NOT NULL REFERENCES quests(id),
        plan_id TEXT NOT NULL REFERENCES quest_plans(id), plan_revision INTEGER NOT NULL,
        worker_id TEXT NOT NULL REFERENCES workers(id), worker_run_id TEXT NOT NULL REFERENCES worker_runs(id),
        receipt_id TEXT NOT NULL REFERENCES action_receipts(id), artifact_path TEXT NOT NULL,
        source_sha256 TEXT NOT NULL, suite_id TEXT NOT NULL, suite_version INTEGER NOT NULL,
        suite_digest TEXT NOT NULL, command_json TEXT NOT NULL, exit_code INTEGER NOT NULL,
        stdout_sha256 TEXT NOT NULL, stderr_sha256 TEXT NOT NULL, report_json TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    connection.execute(
        "CREATE INDEX idx_model_budget_worker_team ON model_budget_worker_teams(team_digest,worker_run_id)"
    )
    connection.execute(
        "INSERT INTO model_budget_worker_teams SELECT id,team_digest FROM worker_runs"
    )
    rows = connection.execute(
        "SELECT id,worker_id,quest_id,plan_id,node_key FROM worker_runs WHERE state IN ('queued','running','paused','blocked')"
    ).fetchall()
    for run_id, worker_id, quest_id, plan_id, node_key in rows:
        connection.execute(
            "UPDATE worker_runs SET state='blocked',generation=generation+1,lease_token=NULL,lease_until=NULL,awaiting_authorization=0 WHERE id=?",
            (run_id,),
        )
        connection.execute("UPDATE workers SET status='blocked' WHERE id=?", (worker_id,))
        connection.execute(
            "UPDATE task_nodes SET status='blocked' WHERE plan_id=? AND node_key=? AND worker_id=?",
            (plan_id, node_key, worker_id),
        )
        event_id, created = new_id("evt"), now_iso()
        payload = json.dumps(
            {
                "plan_id": plan_id,
                "task_node_key": node_key,
                "run_id": run_id,
                "reason_code": "output_contract_review_required",
            }
        )
        connection.execute(
            "INSERT INTO events(id,event_type,conversation_id,quest_id,worker_id,payload,created_at) VALUES (?,'worker.blocked','main',?,?,?,?)",
            (event_id, quest_id, worker_id, payload, created),
        )
        connection.execute(
            "INSERT INTO outbox(event_id,created_at) VALUES (?,?)", (event_id, created)
        )


MIGRATIONS: Mapping[int, Migration] = {
    1: _initial_schema,
    2: _reliable_state,
    3: _workspace_binding,
    4: _governance,
    5: _router_and_registry,
    6: _graph_and_dispatcher,
    7: _worker_team_tools,
    8: _context_memory_budget,
    9: _worker_output_contract,
}


def migrate(
    connection: sqlite3.Connection,
    *,
    target_version: int = SCHEMA_VERSION,
    migrations: Mapping[int, Migration] | None = None,
) -> None:
    if connection.in_transaction:
        raise RuntimeError("Migrations require a connection outside a transaction")
    if not 1 <= target_version <= SCHEMA_VERSION:
        raise SchemaVersionError(f"Unsupported target version: {target_version}")
    steps = MIGRATIONS if migrations is None else migrations
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = _version(connection)
        if current > target_version:
            raise SchemaVersionError("Database version downgrade is not supported")
        for version in range(current + 1, target_version + 1):
            if version not in steps:
                raise SchemaVersionError(f"Missing migration for version {version}")
            # executescript implicitly commits; individual execute calls are essential.
            steps[version](connection)
            connection.execute("UPDATE schema_meta SET version=?", (version,))
        if _version(connection) != target_version:
            raise SchemaVersionError("Migration did not produce the expected schema version")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
