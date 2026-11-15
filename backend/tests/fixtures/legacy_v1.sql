-- Independent fixture for the schema shipped in FORGE v4 / core 0.2.0.
CREATE TABLE schema_meta(version INTEGER NOT NULL);
INSERT INTO schema_meta VALUES (1), (1), (1);
CREATE TABLE conversations(id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE messages(
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE router_state(
  conversation_id TEXT PRIMARY KEY REFERENCES conversations(id), router_session_id TEXT NOT NULL,
  event_cursor INTEGER NOT NULL DEFAULT 0, capsule TEXT NOT NULL DEFAULT '{}');
CREATE TABLE quests(
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
  title TEXT NOT NULL, goal TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE workers(
  id TEXT PRIMARY KEY, quest_id TEXT NOT NULL REFERENCES quests(id), role TEXT NOT NULL,
  model_id TEXT, status TEXT NOT NULL, hidden INTEGER NOT NULL, created_at TEXT NOT NULL,
  metadata TEXT NOT NULL);
CREATE TABLE events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, event_type TEXT NOT NULL,
  conversation_id TEXT NOT NULL, quest_id TEXT, worker_id TEXT, payload TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX idx_events_conversation_seq ON events(conversation_id, seq);
CREATE INDEX idx_quests_status ON quests(status);
CREATE TABLE models(id TEXT PRIMARY KEY, profile TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO conversations VALUES ('main', '2026-01-01');
INSERT INTO router_state VALUES ('main', 'router-legacy', 7, '{"goal":"preserve"}');
INSERT INTO messages VALUES ('msg-old', 'main', 'user', '保留历史', '2026-01-01', '{}');
INSERT INTO quests VALUES ('quest-old', 'main', 'Old quest', 'Keep data', 'proposed', 3,
  '2026-01-01', '2026-01-01', '{}');
INSERT INTO workers VALUES ('worker-old', 'quest-old', 'research', NULL, 'proposed', 1,
  '2026-01-01', '{}');
INSERT INTO events(id,event_type,conversation_id,quest_id,payload,created_at)
  VALUES ('event-old', 'quest.proposed', 'main', 'quest-old', '{}', '2026-01-01');
INSERT INTO models VALUES ('model-old',
  '{"id":"model-old","provider":"fixture","display_name":"Legacy"}', '2026-01-01');
