"""Persistent immutable plan revisions and their task graph."""


def migrate_graph(connection):
    connection.execute("""CREATE TABLE quest_plans(
        id TEXT PRIMARY KEY, quest_id TEXT NOT NULL REFERENCES quests(id), revision INTEGER NOT NULL,
        digest TEXT NOT NULL, status TEXT NOT NULL, graph_json TEXT NOT NULL, created_at TEXT NOT NULL,
        approved_at TEXT, approved_by TEXT, UNIQUE(quest_id,revision))""")
    connection.execute("""CREATE TABLE task_nodes(
        plan_id TEXT NOT NULL REFERENCES quest_plans(id), node_key TEXT NOT NULL,
        objective TEXT NOT NULL, criteria_json TEXT NOT NULL, budget_tokens INTEGER NOT NULL,
        required_capabilities TEXT NOT NULL, expected_artifact TEXT, max_attempts INTEGER NOT NULL,
        assigned_agent TEXT, worker_id TEXT REFERENCES workers(id), evidence_refs TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'proposed', PRIMARY KEY(plan_id,node_key))""")
    connection.execute("""CREATE TABLE task_edges(
        plan_id TEXT NOT NULL REFERENCES quest_plans(id), source TEXT NOT NULL, target TEXT NOT NULL,
        condition TEXT NOT NULL, PRIMARY KEY(plan_id,source,target),
        FOREIGN KEY(plan_id,source) REFERENCES task_nodes(plan_id,node_key),
        FOREIGN KEY(plan_id,target) REFERENCES task_nodes(plan_id,node_key))""")
    connection.execute("""CREATE TABLE plan_attempts(
        id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES quest_plans(id),
        attempt INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(plan_id,attempt))""")
    connection.execute("""CREATE TABLE task_attempts(
        id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, node_key TEXT NOT NULL,
        attempt INTEGER NOT NULL, status TEXT NOT NULL, worker_id TEXT REFERENCES workers(id),
        started_at TEXT, finished_at TEXT, evidence_refs TEXT NOT NULL DEFAULT '[]',
        FOREIGN KEY(plan_id,node_key) REFERENCES task_nodes(plan_id,node_key),
        UNIQUE(plan_id,node_key,attempt))""")
