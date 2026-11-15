"""Source-bound user constraints and bounded message indexing (Phase 2)."""


def migrate_constraints(connection):
    connection.execute("""CREATE TABLE constraint_records (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, quest_id TEXT,
        statement TEXT NOT NULL, dimension TEXT NOT NULL,
        origin TEXT NOT NULL CHECK(origin IN ('pending_model','explicit_user','user_confirmed')),
        source_message_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL, valid_until TEXT,
        status TEXT NOT NULL CHECK(status IN ('active','revoked')),
        confirmation_message_id TEXT, confirmation_sha256 TEXT)""")
    connection.execute(
        "CREATE INDEX idx_constraints_quest ON constraint_records(quest_id,status,id)"
    )
    connection.execute(
        "CREATE INDEX idx_constraints_source ON constraint_records(source_message_id)"
    )
    connection.execute(
        "CREATE INDEX idx_constraints_dimension ON constraint_records(dimension,quest_id,status)"
    )
    connection.execute("""CREATE TABLE constraint_message_index (
        message_id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
        quest_id TEXT, created_at TEXT NOT NULL)""")
    connection.execute(
        "CREATE INDEX idx_constraint_messages_quest ON constraint_message_index(quest_id,message_id)"
    )
    connection.execute("""CREATE TABLE constraint_index_cursor (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), message_rowid INTEGER NOT NULL)""")
    connection.execute("INSERT INTO constraint_index_cursor VALUES (1,0)")
