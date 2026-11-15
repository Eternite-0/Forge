"""Team proposal schema component; the central migration owns the transaction."""


def migrate_team(connection):
    connection.execute("""CREATE TABLE team_proposals(
        id TEXT PRIMARY KEY,
        quest_id TEXT NOT NULL REFERENCES quests(id),
        plan_revision INTEGER NOT NULL CHECK(plan_revision >= 1),
        plan_digest TEXT NOT NULL,
        proposal_digest TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('proposed','approved','revoked','superseded')),
        body_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        approved_by TEXT,
        approved_at TEXT,
        UNIQUE(quest_id,proposal_digest)
    )""")
    connection.execute(
        "CREATE INDEX idx_team_quest ON team_proposals(quest_id,plan_revision,status)"
    )
