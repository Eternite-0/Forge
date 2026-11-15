"""Worker checkpoint and budget tables; migration runs inside the v7 transaction."""


def migrate_worker(connection):
    connection.execute("""CREATE TABLE worker_inbox (
        run_id TEXT NOT NULL REFERENCES worker_runs(id), event_id TEXT NOT NULL,
        payload_json TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(run_id,event_id))""")
    connection.execute("""CREATE TABLE worker_runs (
        id TEXT PRIMARY KEY, worker_id TEXT NOT NULL REFERENCES workers(id),
        quest_id TEXT NOT NULL REFERENCES quests(id), plan_id TEXT NOT NULL,
        node_key TEXT NOT NULL, plan_revision INTEGER NOT NULL, plan_digest TEXT NOT NULL,
        team_digest TEXT NOT NULL, member_index INTEGER NOT NULL, role_id TEXT NOT NULL, provider_id TEXT NOT NULL,
        model_id TEXT NOT NULL, execution_mode TEXT NOT NULL CHECK(execution_mode IN ('mock','live')),
        state TEXT NOT NULL CHECK(state IN ('queued','running','paused','blocked','cancelled','review_pending','failed','succeeded')),
        generation INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
        quest_paused INTEGER NOT NULL DEFAULT 0,
        awaiting_authorization INTEGER NOT NULL DEFAULT 0,
        allowed_tools_json TEXT NOT NULL, capability_sources_json TEXT NOT NULL,
        token_limit INTEGER NOT NULL, token_spent INTEGER NOT NULL DEFAULT 0,
        tool_limit INTEGER NOT NULL, tool_spent INTEGER NOT NULL DEFAULT 0,
        step_limit INTEGER NOT NULL, steps INTEGER NOT NULL DEFAULT 0,
        cost_limit_microunits INTEGER, cost_spent_microunits INTEGER, currency TEXT, price_bounds_json TEXT,
        deadline REAL NOT NULL, depth INTEGER NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(plan_id,node_key) REFERENCES task_nodes(plan_id,node_key))""")
    connection.execute("CREATE INDEX idx_worker_runs_quest_state ON worker_runs(quest_id,state)")
    connection.execute("""CREATE TABLE worker_messages (
        run_id TEXT NOT NULL REFERENCES worker_runs(id), ordinal INTEGER NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
        message_json TEXT NOT NULL, PRIMARY KEY(run_id,ordinal))""")
    connection.execute("""CREATE TABLE worker_capsules (
        run_id TEXT PRIMARY KEY REFERENCES worker_runs(id), capsule_json TEXT NOT NULL,
        next_action TEXT NOT NULL, evidence_refs TEXT NOT NULL DEFAULT '[]',
        inbox_cursor INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0)""")
    connection.execute("""CREATE TABLE worker_tool_calls (
        run_id TEXT NOT NULL REFERENCES worker_runs(id), call_id TEXT NOT NULL,
        model_call_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','uncertain','failed')),
        receipt_id TEXT, result_json TEXT, PRIMARY KEY(run_id,call_id), UNIQUE(run_id,model_call_id))""")
    connection.execute("""CREATE TABLE worker_budget_ledger (
        scope TEXT NOT NULL, scope_id TEXT NOT NULL, token_limit INTEGER NOT NULL,
        tokens_allocated INTEGER NOT NULL DEFAULT 0, tool_limit INTEGER NOT NULL,
        tools_allocated INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope,scope_id))""")
    connection.execute("""CREATE TABLE worker_cost_ledger (
        scope TEXT NOT NULL, scope_id TEXT NOT NULL, currency TEXT NOT NULL,
        limit_microunits INTEGER NOT NULL, reserved_microunits INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(scope,scope_id,currency))""")
