"""Transactional v6 dispatcher schema, composed by the central migrator."""


def migrate_dispatcher(connection):
    connection.execute("""CREATE TABLE event_consumers (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('router','worker')),
        worker_id TEXT, replay_from_seq INTEGER NOT NULL CHECK(replay_from_seq >= 0),
        cursor_seq INTEGER NOT NULL CHECK(cursor_seq >= 0),
        scan_seq INTEGER NOT NULL CHECK(scan_seq >= 0),
        priority_scan_seq INTEGER NOT NULL CHECK(priority_scan_seq >= 0),
        CHECK((kind='router' AND worker_id IS NULL) OR (kind='worker' AND worker_id IS NOT NULL))
    )""")
    connection.execute("""CREATE TABLE event_deliveries (
        consumer_id TEXT NOT NULL REFERENCES event_consumers(id),
        event_id TEXT NOT NULL REFERENCES events(id), seq INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','leased','delivered','dead','skipped')),
        lease_token TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT,
        PRIMARY KEY(consumer_id,event_id), UNIQUE(consumer_id,seq)
    )""")
    connection.execute("CREATE INDEX idx_delivery_order ON event_deliveries(consumer_id,seq,state)")
    connection.execute("CREATE INDEX idx_events_dispatch_priority ON events(event_type,seq)")
    connection.execute("""CREATE TABLE event_inbox (
        consumer_id TEXT NOT NULL REFERENCES event_consumers(id),
        event_id TEXT NOT NULL REFERENCES events(id), seq INTEGER NOT NULL,
        event_type TEXT NOT NULL, conversation_id TEXT NOT NULL, quest_id TEXT, worker_id TEXT,
        payload TEXT NOT NULL, received_at REAL NOT NULL, processed_at TEXT,
        notice_attempts INTEGER NOT NULL DEFAULT 0, notice_next_attempt REAL NOT NULL DEFAULT 0,
        notice_error TEXT, notice_dead_at TEXT, notice_token TEXT,
        PRIMARY KEY(consumer_id,event_id)
    )""")
    connection.execute(
        "CREATE INDEX idx_inbox_pending ON event_inbox(consumer_id,processed_at,seq)"
    )
    connection.execute("""CREATE TABLE event_delivery_replays (
        id INTEGER PRIMARY KEY, consumer_id TEXT NOT NULL, event_id TEXT NOT NULL,
        actor TEXT NOT NULL, reason_code TEXT NOT NULL, previous_error TEXT, created_at REAL NOT NULL,
        FOREIGN KEY(consumer_id,event_id) REFERENCES event_deliveries(consumer_id,event_id)
    )""")
