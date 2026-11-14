"""Registry schema component to run inside the caller-owned v5 migration."""


def migrate_registry(connection):
    connection.execute("""CREATE TABLE benchmark_runs(
        id TEXT PRIMARY KEY, model_id TEXT NOT NULL, provider_id TEXT NOT NULL,
        suite_version TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('mock','live')),
        results_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE model_observations(
        id TEXT PRIMARY KEY, model_id TEXT NOT NULL, provider_id TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('mock','live')),
        outcome TEXT NOT NULL CHECK(outcome IN ('success','timeout','error')),
        latency_seconds REAL, ttft_seconds REAL, output_tokens INTEGER,
        created_at TEXT NOT NULL)""")
    connection.execute(
        "CREATE INDEX idx_model_observations_model ON model_observations(model_id,mode)"
    )
