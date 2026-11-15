"""Persistent attempt reservations. Called inside the schema migration transaction."""


def migrate_model_budget(connection):
    connection.execute("""CREATE TABLE model_budget_attempts (
        attempt_id TEXT PRIMARY KEY, call_id TEXT NOT NULL, purpose TEXT NOT NULL,
        workspace_id TEXT NOT NULL, quest_id TEXT, worker_run_id TEXT, parent_call_id TEXT,
        provider_id TEXT NOT NULL, model_id TEXT NOT NULL, input_reserved INTEGER NOT NULL,
        output_reserved INTEGER NOT NULL, cost_reserved INTEGER, currency TEXT,
        state TEXT NOT NULL, created_at REAL NOT NULL, recover_after REAL,
        input_charged INTEGER, output_charged INTEGER, cost_charged INTEGER,
        input_price INTEGER, output_price INTEGER, price_version TEXT)""")
    connection.execute(
        "CREATE INDEX idx_model_budget_scope ON model_budget_attempts(workspace_id,quest_id,worker_run_id)"
    )
    connection.execute("CREATE INDEX idx_model_budget_quest ON model_budget_attempts(quest_id)")
    connection.execute(
        "CREATE INDEX idx_model_budget_worker ON model_budget_attempts(worker_run_id)"
    )
    connection.execute(
        "CREATE INDEX idx_model_budget_active ON model_budget_attempts(state,recover_after)"
    )
    connection.execute(
        "CREATE TABLE model_provider_cooldowns (provider_id TEXT PRIMARY KEY, retry_after REAL NOT NULL)"
    )
    connection.execute("CREATE TABLE model_budget_legacy_quests (quest_id TEXT PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE model_budget_scope_reconciliations (scope TEXT NOT NULL, scope_id TEXT NOT NULL, currency TEXT NOT NULL, cost INTEGER NOT NULL, PRIMARY KEY(scope,scope_id,currency))"
    )
    connection.execute("""CREATE TABLE model_budget_metadata (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), historical_usage TEXT NOT NULL,
        reconciled_cost INTEGER, reconciled_currency TEXT, breached INTEGER NOT NULL DEFAULT 0)""")
    connection.execute(
        "INSERT INTO model_budget_metadata VALUES (1,'unknown_before_cutover',NULL,NULL,0)"
    )
    connection.execute("""CREATE TABLE model_budget_opening (
        worker_run_id TEXT PRIMARY KEY, workspace_id TEXT, quest_id TEXT NOT NULL,
        tokens INTEGER NOT NULL, cost INTEGER, currency TEXT)""")
    connection.execute("CREATE INDEX idx_model_opening_quest ON model_budget_opening(quest_id)")
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "worker_runs" in tables:
        invalid = connection.execute("""SELECT 1 FROM worker_runs WHERE
            typeof(token_spent) != 'integer' OR token_spent < 0 OR
            (cost_spent_microunits IS NOT NULL AND (typeof(cost_spent_microunits) != 'integer' OR cost_spent_microunits < 0 OR currency IS NULL OR typeof(currency) != 'text' OR length(currency) != 3)) LIMIT 1""").fetchone()
        if invalid:
            raise ValueError("Invalid historical Worker budget opening")
        workspace = connection.execute("SELECT id FROM workspace WHERE singleton=1").fetchone()
        workspace_id = workspace[0] if workspace else None
        connection.execute(
            """INSERT INTO model_budget_opening
            SELECT id,?,quest_id,token_spent,cost_spent_microunits,currency FROM worker_runs""",
            (workspace_id,),
        )
    if "quests" in tables:
        connection.execute("INSERT INTO model_budget_legacy_quests SELECT id FROM quests")
