"""Durable context archives; called inside the migration transaction."""


def migrate_context(connection):
    connection.execute("""CREATE TABLE context_archives (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, scope_id TEXT NOT NULL,
        source_json TEXT NOT NULL, source_hash TEXT NOT NULL, capsule_json TEXT NOT NULL,
        capsule_hash TEXT NOT NULL, created_at TEXT NOT NULL)""")
    connection.execute(
        "CREATE INDEX idx_context_archives_scope ON context_archives(scope,scope_id,id)"
    )
    connection.execute(
        "CREATE TABLE context_archive_heads(scope TEXT NOT NULL, scope_id TEXT NOT NULL, through INTEGER NOT NULL, archive_id INTEGER NOT NULL REFERENCES context_archives(id), PRIMARY KEY(scope,scope_id))"
    )
    connection.execute(
        "CREATE TABLE context_archive_sources(scope TEXT NOT NULL, scope_id TEXT NOT NULL, source_id TEXT NOT NULL, archive_id INTEGER NOT NULL REFERENCES context_archives(id), PRIMARY KEY(scope,scope_id,source_id))"
    )
