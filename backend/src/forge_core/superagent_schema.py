"""Durable event sources, threads and task subscriptions for the Super-agent."""


def migrate_superagent(connection):
    connection.execute("""CREATE TABLE superagent_sources (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        kind TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('candidate','explicit','ignore')),
        allowed_actors_json TEXT, created_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE superagent_threads (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        source_id TEXT NOT NULL REFERENCES superagent_sources(id),
        thread_key TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(source_id,thread_key))""")
    connection.execute("""CREATE TABLE superagent_triggers (
        id TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL REFERENCES events(id),
        thread_id TEXT NOT NULL REFERENCES superagent_threads(id),
        source_id TEXT NOT NULL REFERENCES superagent_sources(id), external_id TEXT NOT NULL,
        actor TEXT, addressed INTEGER NOT NULL, payload_json TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL, status TEXT NOT NULL
        CHECK(status IN ('filtered','pending','passed_through','routed','ignored')),
        reason TEXT NOT NULL, quest_id TEXT REFERENCES quests(id), created_at TEXT NOT NULL,
        lease_token TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
        UNIQUE(source_id,external_id))""")
    connection.execute("""CREATE TABLE superagent_subscriptions (
        thread_id TEXT NOT NULL REFERENCES superagent_threads(id),
        run_id TEXT NOT NULL REFERENCES worker_runs(id),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        created_at TEXT NOT NULL, PRIMARY KEY(thread_id,run_id))""")
    connection.execute("""CREATE TABLE superagent_trigger_deliveries (
        trigger_id TEXT NOT NULL REFERENCES superagent_triggers(id),
        run_id TEXT NOT NULL REFERENCES worker_runs(id),
        handed_back INTEGER NOT NULL DEFAULT 0 CHECK(handed_back IN (0,1)),
        handback_reason TEXT, created_at TEXT NOT NULL, PRIMARY KEY(trigger_id,run_id))""")
    connection.execute("""CREATE INDEX idx_superagent_pending
        ON superagent_triggers(status,lease_until,created_at,id)""")
    connection.execute("""CREATE INDEX idx_superagent_subscription_run
        ON superagent_subscriptions(run_id,active)""")
    connection.execute("""CREATE TABLE superagent_timers (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES superagent_sources(id),
        thread_key TEXT NOT NULL, payload_json TEXT NOT NULL, actor TEXT,
        addressed INTEGER NOT NULL, next_at REAL NOT NULL, interval_seconds REAL,
        occurrence INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1)""")
    connection.execute("""CREATE TABLE superagent_file_watches (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES superagent_sources(id),
        thread_key TEXT NOT NULL, relative_path TEXT NOT NULL, fingerprint TEXT NOT NULL,
        occurrence INTEGER NOT NULL DEFAULT 0, last_error TEXT,
        checked_at REAL NOT NULL DEFAULT 0,
        UNIQUE(source_id,relative_path))""")
