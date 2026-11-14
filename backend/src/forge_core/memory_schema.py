"""Durable, provenance-bearing recall records."""


def migrate_memory(connection):
    connection.execute("""CREATE TABLE memories(
        id TEXT PRIMARY KEY, statement TEXT NOT NULL, kind TEXT NOT NULL,
        layer TEXT NOT NULL, status TEXT NOT NULL, source_refs TEXT NOT NULL,
        created_at TEXT NOT NULL, valid_until TEXT, related_quest_ids TEXT NOT NULL,
        memory_key TEXT)""")
    connection.execute("CREATE INDEX idx_memories_key ON memories(memory_key)")
    connection.execute("CREATE TABLE revoked_memory_sources(source_ref TEXT PRIMARY KEY)")
    connection.execute("""CREATE TABLE memory_source_index(
        source_ref TEXT PRIMARY KEY, source_hash TEXT NOT NULL, quest_id TEXT NOT NULL,
        plan_revision INTEGER, source_type TEXT NOT NULL, trust TEXT NOT NULL,
        snapshot TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)))""")
    connection.execute(
        "CREATE TABLE memory_source_cursor(singleton INTEGER PRIMARY KEY CHECK(singleton=1), event_seq INTEGER NOT NULL, quest_rowid INTEGER NOT NULL, evidence_rowid INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO memory_source_cursor VALUES (1,0,0,0)")
    connection.execute(
        "CREATE TABLE memory_terms(term TEXT NOT NULL, ref TEXT NOT NULL, PRIMARY KEY(term,ref)) WITHOUT ROWID"
    )
    connection.execute(
        "CREATE INDEX idx_memory_source_quest ON memory_source_index(quest_id,active,source_ref)"
    )
    connection.execute("CREATE INDEX idx_memory_terms_ref ON memory_terms(ref)")
