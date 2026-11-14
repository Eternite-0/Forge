"""M5 action intents and immutable receipts, installed by central v7 migration."""


def migrate_tools(connection):
    connection.execute("""CREATE TABLE tool_approval_requests(
        call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES worker_runs(id),
        approval_id TEXT UNIQUE NOT NULL REFERENCES approvals(id))""")
    connection.execute("""CREATE TABLE node_verification_attempts(
        plan_id TEXT NOT NULL, node_key TEXT NOT NULL, worker_id TEXT NOT NULL REFERENCES workers(id),
        attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','checking','verified','blocked')),
        claim_token TEXT, error_code TEXT, PRIMARY KEY(plan_id,node_key,worker_id),
        FOREIGN KEY(plan_id,node_key) REFERENCES task_nodes(plan_id,node_key))""")
    connection.execute("ALTER TABLE quest_plans ADD COLUMN cost_currency TEXT")
    connection.execute("ALTER TABLE task_nodes ADD COLUMN allowed_tools TEXT NOT NULL DEFAULT '[]'")
    connection.execute("""CREATE TABLE action_intents(
        id TEXT PRIMARY KEY, call_id TEXT UNIQUE NOT NULL, quest_id TEXT NOT NULL REFERENCES quests(id),
        worker_id TEXT NOT NULL REFERENCES workers(id), run_id TEXT NOT NULL REFERENCES worker_runs(id),
        plan_id TEXT NOT NULL REFERENCES quest_plans(id), plan_revision INTEGER NOT NULL,
        tool_name TEXT NOT NULL, parameters_json TEXT NOT NULL, parameter_digest TEXT NOT NULL,
        resource_scope TEXT NOT NULL, risk TEXT NOT NULL, approval_id TEXT REFERENCES approvals(id),
        execution_mode TEXT NOT NULL CHECK(execution_mode IN ('live','mock')),
        state TEXT NOT NULL CHECK(state IN ('prepared','executing','succeeded','failed','uncertain')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE action_receipts(
        id TEXT PRIMARY KEY, intent_id TEXT UNIQUE NOT NULL REFERENCES action_intents(id),
        execution_mode TEXT NOT NULL CHECK(execution_mode IN ('live','mock')), result_json TEXT NOT NULL,
        result_digest TEXT NOT NULL, created_at TEXT NOT NULL)""")
    connection.execute("""CREATE TRIGGER action_intents_immutable_binding BEFORE UPDATE OF
        call_id,quest_id,worker_id,run_id,plan_id,plan_revision,tool_name,parameters_json,
        parameter_digest,resource_scope,risk,approval_id,execution_mode,created_at ON action_intents
        BEGIN SELECT RAISE(ABORT,'immutable intent binding'); END""")
    connection.execute(
        "CREATE TRIGGER action_intents_immutable_delete BEFORE DELETE ON action_intents BEGIN SELECT RAISE(ABORT,'immutable intent'); END"
    )
    connection.execute(
        "CREATE TRIGGER action_receipts_immutable_update BEFORE UPDATE ON action_receipts BEGIN SELECT RAISE(ABORT,'immutable receipt'); END"
    )
    connection.execute(
        "CREATE TRIGGER action_receipts_immutable_delete BEFORE DELETE ON action_receipts BEGIN SELECT RAISE(ABORT,'immutable receipt'); END"
    )
