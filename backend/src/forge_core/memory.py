"""Bounded recall of sourced memories; retrieval never promotes model claims."""

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime


def _terms(text):
    words = re.findall(r"[a-z0-9_./\\:-]+", text.casefold())
    words.extend(re.findall(r"[a-z0-9_]+", text.casefold()))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(run[i : i + 2] for i in range(len(run) - 1))
    return sorted(set(words))[:128]


def _index_terms(db, ref, text):
    db.execute("DELETE FROM memory_terms WHERE ref=?", (ref,))
    db.executemany(
        "INSERT OR IGNORE INTO memory_terms VALUES (?,?)", ((word, ref) for word in _terms(text))
    )


def _kind_selected(item, kinds):
    return (
        not kinds
        or item["kind"] in kinds
        or ("path" in kinds and item["kind"] == "artifact" and '"path"' in item["statement"])
    )


class MemoryService:
    def __init__(self, store):
        self.store = store
        self.path = str(getattr(store, "path", store))

    @contextmanager
    def _connection(self, connection=None):
        if connection is not None:
            yield connection
        elif hasattr(self.store, "connection"):
            with self.store.connection() as owned:
                yield owned
        else:
            with closing(sqlite3.connect(self.path)) as owned:
                yield owned

    def remember(
        self,
        statement,
        *,
        kind,
        layer,
        source_refs,
        related_quest_ids=(),
        status="hypothesis",
        valid_until=None,
        key=None,
        connection=None,
    ):
        if (
            not isinstance(statement, str)
            or not statement.strip()
            or len(statement.encode()) > 8192
        ):
            raise ValueError("Invalid memory statement")
        if layer not in {"L0", "L1", "L2", "L3"} or status not in {
            "hypothesis",
            "confirmed",
            "invalid",
        }:
            raise ValueError("Invalid memory layer or status")
        if status == "confirmed":
            raise ValueError("Confirmed facts require a Core-derived source")
        if kind not in {
            "task",
            "project",
            "path",
            "artifact",
            "decision",
            "failure",
            "fact",
            "constraint",
        }:
            raise ValueError("Invalid memory kind")
        refs = list(source_refs)
        quests = list(related_quest_ids)
        if (
            not refs
            or len(refs) > 32
            or len(quests) > 32
            or any(not isinstance(x, str) or not x or len(x) > 512 for x in refs + quests)
        ):
            raise ValueError("Memory requires bounded source references and Quest IDs")
        if valid_until is not None:
            expiry = datetime.fromisoformat(valid_until)
            if expiry.tzinfo is None:
                raise ValueError("Expiry must include timezone")
            valid_until = expiry.astimezone(UTC).isoformat()
        if key is not None and (not isinstance(key, str) or not key or len(key) > 512):
            raise ValueError("Invalid memory key")
        item_id = uuid.uuid4().hex
        with self._connection(connection) as db:
            db.execute(
                "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    statement,
                    kind,
                    layer,
                    status,
                    json.dumps(refs, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                    valid_until,
                    json.dumps(quests, ensure_ascii=False),
                    key,
                ),
            )
            _index_terms(
                db, f"memory:{item_id}", " ".join([statement, kind, key or "", *refs, *quests])
            )
            if connection is None:
                db.commit()
        return item_id

    def revoke_source(self, source_ref, *, connection=None):
        if not isinstance(source_ref, str) or not source_ref:
            raise ValueError("Invalid source reference")
        with self._connection(connection) as db:
            db.execute("INSERT OR IGNORE INTO revoked_memory_sources VALUES (?)", (source_ref,))
            if connection is None:
                db.commit()

    def invalidate(self, memory_id, *, connection=None):
        """Invalidate a record without erasing its provenance."""
        with self._connection(connection) as db:
            changed = db.execute(
                "UPDATE memories SET status='invalid' WHERE id=?", (memory_id,)
            ).rowcount
            if connection is None:
                db.commit()
        return bool(changed)

    def sync_sources(self, *, connection=None):
        """Index at most 256 rows per source per call within the caller transaction."""
        with self._connection(connection) as db:
            if connection is None:
                db.execute("BEGIN IMMEDIATE")
            event_cursor, quest_cursor, evidence_cursor = db.execute(
                "SELECT event_seq,quest_rowid,evidence_rowid FROM memory_source_cursor WHERE singleton=1"
            ).fetchone()

            def upsert(ref, quest, revision, source_type, trust, snapshot):
                encoded = json.dumps(
                    snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                db.execute(
                    """INSERT INTO memory_source_index VALUES (?,?,?,?,?,?,?,1)
                    ON CONFLICT(source_ref) DO UPDATE SET source_hash=excluded.source_hash,
                    quest_id=excluded.quest_id,plan_revision=excluded.plan_revision,
                    source_type=excluded.source_type,trust=excluded.trust,snapshot=excluded.snapshot,
                    active=1""",
                    (ref, digest, quest, revision, source_type, trust, encoded),
                )
                _index_terms(db, ref, f"{encoded} {source_type} {quest} {ref}")

            workspace = (
                db.execute("SELECT id,root FROM workspace WHERE singleton=1").fetchone()
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace'"
                ).fetchone()
                else None
            )
            if workspace:
                upsert(
                    f"workspace:{workspace[0]}",
                    "",
                    None,
                    "project",
                    "core_workspace",
                    {"id": workspace[0], "root": workspace[1]},
                )

            new_quests = db.execute(
                "SELECT rowid,id FROM quests WHERE rowid>? ORDER BY rowid LIMIT 256",
                (quest_cursor,),
            ).fetchall()
            events = db.execute(
                "SELECT seq,id,event_type,quest_id,payload,created_at FROM events WHERE seq>? ORDER BY seq LIMIT 256",
                (event_cursor,),
            ).fetchall()
            evidence = db.execute(
                "SELECT rowid,id,quest_id,kind,trusted,payload,created_at FROM evidence WHERE rowid>? ORDER BY rowid LIMIT 256",
                (evidence_cursor,),
            ).fetchall()
            affected = {row[1] for row in new_quests}
            affected.update(row[3] for row in events if row[3])
            affected.update(row[2] for row in evidence if row[2])
            revisions = {}
            for quest_id in sorted(affected):
                row = db.execute(
                    "SELECT title,goal,created_at,plan_revision FROM quests WHERE id=?", (quest_id,)
                ).fetchone()
                if not row:
                    continue
                title, goal, created, revision = row
                revisions[quest_id] = revision
                upsert(
                    f"quest:{quest_id}",
                    quest_id,
                    revision,
                    "quest",
                    "core_record",
                    {"title": title, "goal": goal, "created_at": created},
                )
                db.execute(
                    "UPDATE memory_source_index SET active=0 WHERE quest_id=? AND source_type!='quest' AND plan_revision IS NOT NULL AND plan_revision!=?",
                    (quest_id, revision),
                )
            event_types = {
                "approval.decided",
                "quest.plan_approved",
                "worker.failed",
                "worker.blocked",
                "tool.execution_uncertain",
                "evidence.json_checked",
                "evidence.artifact_verified",
            }
            for seq, event_id, event_type, quest_id, payload, created in events:
                if event_type not in event_types or quest_id not in revisions:
                    continue
                data = json.loads(payload)
                revision = data.get("plan_revision")
                # Resolve references against Core-owned records, never infer the
                # event's old revision from the Quest's current revision.
                for field, table, column in (
                    ("plan_id", "quest_plans", "revision"),
                    ("approval_id", "approvals", "plan_revision"),
                    ("intent_id", "action_intents", "plan_revision"),
                    ("run_id", "execution_runs", "plan_revision"),
                ):
                    if revision is not None or not data.get(field):
                        continue
                    if not db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone():
                        continue
                    source = db.execute(
                        f"SELECT {column} FROM {table} WHERE id=? AND quest_id=?",
                        (data[field], quest_id),
                    ).fetchone()
                    if source:
                        revision = source[0]
                if type(revision) is not int:
                    continue
                upsert(
                    f"event:{event_id}",
                    quest_id,
                    revision,
                    event_type,
                    "core_event",
                    {"payload": data, "created_at": created},
                )
                if revision != revisions[quest_id]:
                    db.execute(
                        "UPDATE memory_source_index SET active=0 WHERE source_ref=?",
                        (f"event:{event_id}",),
                    )
            for _, evidence_id, quest_id, kind, trusted, payload, created in evidence:
                if not trusted or quest_id not in revisions:
                    continue
                data = json.loads(payload)
                revision = data.get("plan_revision")
                if revision is not None and type(revision) is not int:
                    continue
                upsert(
                    f"evidence:{evidence_id}",
                    quest_id,
                    revision,
                    kind,
                    "verified_evidence",
                    {"payload": data, "created_at": created},
                )
                if revision is not None and revision != revisions[quest_id]:
                    db.execute(
                        "UPDATE memory_source_index SET active=0 WHERE source_ref=?",
                        (f"evidence:{evidence_id}",),
                    )
            for quest_id in affected:
                db.execute(
                    """UPDATE memory_source_index SET active=0 WHERE quest_id=?
                    AND source_ref IN (SELECT source_ref FROM revoked_memory_sources)""",
                    (quest_id,),
                )
            db.execute(
                "UPDATE memory_source_cursor SET event_seq=?,quest_rowid=?,evidence_rowid=? WHERE singleton=1",
                (
                    events[-1][0] if events else event_cursor,
                    new_quests[-1][0] if new_quests else quest_cursor,
                    evidence[-1][0] if evidence else evidence_cursor,
                ),
            )
            if connection is None:
                db.commit()
            return {
                "event_seq": events[-1][0] if events else event_cursor,
                "has_more": len(events) == 256 or len(new_quests) == 256 or len(evidence) == 256,
            }

    def retrieve_context(
        self,
        query,
        quest_ids=(),
        source_refs=(),
        limit=20,
        max_bytes=16384,
        connection=None,
        cursor=None,
        kinds=(),
        layers=(),
    ):
        """Read at most 512 indexed candidates; cursor continues in stable ref order."""
        if not isinstance(query, str) or len(query.encode()) > 8192:
            raise ValueError("Invalid query")
        if not 1 <= limit <= 100 or not 0 <= max_bytes <= 65536:
            raise ValueError("Invalid recall bounds")
        quests, refs = list(quest_ids), list(source_refs)
        kinds, layers = set(kinds), set(layers)
        if len(kinds) > 16 or len(layers) > 4:
            raise ValueError("Invalid recall filters")
        if (
            len(quests) > 32
            or len(refs) > 32
            or any(
                not isinstance(value, str) or not value or len(value) > 512
                for value in quests + refs
            )
        ):
            raise ValueError("Invalid recall selectors")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 600):
            raise ValueError("Invalid recall cursor")
        words = _terms(" ".join([query, *quests, *refs]))
        if not words and not quests and not refs:
            return {"items": [], "omitted": 0, "bytes": 0, "cursor": None}
        items, used, omitted, next_cursor = [], 2, 0, None
        now = datetime.now(UTC)
        with self._connection(connection) as db:
            # Bound each indexed range independently; a popular term cannot force
            # an unbounded DISTINCT/sort over the entire database.
            candidate_set = set()
            for word in words:
                candidate_set.update(
                    row[0]
                    for row in db.execute(
                        "SELECT ref FROM memory_terms WHERE term=? AND ref>? ORDER BY ref LIMIT 513",
                        (word, cursor or ""),
                    )
                )
            for quest_id in quests:
                candidate_set.update(
                    row[0]
                    for row in db.execute(
                        "SELECT source_ref FROM memory_source_index WHERE quest_id=? AND active=1 AND source_ref>? ORDER BY source_ref LIMIT 513",
                        (quest_id, cursor or ""),
                    )
                )
            candidate_set.update(ref for ref in refs if ref > (cursor or ""))
            candidates = sorted(candidate_set)[:513]
            previous = cursor or ""
            for position, ref in enumerate(candidates[:512]):
                if len(items) >= limit:
                    omitted += len(candidates) - position
                    next_cursor = next_cursor or previous
                    break
                previous = ref
                item = None
                if ref.startswith("memory:"):
                    row = db.execute(
                        "SELECT id,statement,kind,layer,status,source_refs,created_at,valid_until,related_quest_ids,memory_key FROM memories WHERE id=?",
                        (ref[7:],),
                    ).fetchone()
                    if not row:
                        continue
                    (
                        ident,
                        statement,
                        kind,
                        layer,
                        status,
                        raw_refs,
                        created,
                        expiry,
                        raw_quests,
                        key,
                    ) = row
                    item_refs, item_quests = json.loads(raw_refs), json.loads(raw_quests)
                    if status == "invalid" or (expiry and datetime.fromisoformat(expiry) <= now):
                        continue
                    if any(
                        db.execute(
                            "SELECT 1 FROM revoked_memory_sources WHERE source_ref=?", (source,)
                        ).fetchone()
                        for source in item_refs
                    ):
                        continue
                    conflict = False
                    if key:
                        others = db.execute(
                            "SELECT statement,valid_until,source_refs FROM memories WHERE memory_key=? AND status!='invalid' AND id!=? LIMIT 101",
                            (key, ident),
                        ).fetchall()
                        conflict = len(others) > 100
                        for other_statement, other_expiry, other_refs in others[:100]:
                            if other_expiry and datetime.fromisoformat(other_expiry) <= now:
                                continue
                            if any(
                                db.execute(
                                    "SELECT 1 FROM revoked_memory_sources WHERE source_ref=?",
                                    (source,),
                                ).fetchone()
                                for source in json.loads(other_refs)
                            ):
                                continue
                            if other_statement != statement:
                                conflict = True
                                break
                    item = {
                        "id": ident,
                        "statement": statement,
                        "kind": kind,
                        "layer": layer,
                        "status": status,
                        "source_refs": item_refs,
                        "created_at": created,
                        "valid_until": expiry,
                        "related_quest_ids": item_quests,
                        "key": key,
                        "freshness": "unexpired" if expiry else "unknown",
                        "conflicted": conflict,
                    }
                else:
                    row = db.execute(
                        "SELECT source_hash,quest_id,plan_revision,source_type,trust,snapshot,active FROM memory_source_index WHERE source_ref=?",
                        (ref,),
                    ).fetchone()
                    if (
                        not row
                        or not row[6]
                        or db.execute(
                            "SELECT 1 FROM revoked_memory_sources WHERE source_ref=?", (ref,)
                        ).fetchone()
                    ):
                        continue
                    digest, quest_id, revision, source_type, trust, snapshot, _ = row
                    if hashlib.sha256(snapshot.encode("utf-8")).hexdigest() != digest:
                        continue
                    if trust == "core_workspace":
                        workspace = db.execute(
                            "SELECT id,root FROM workspace WHERE singleton=1"
                        ).fetchone()
                        if not workspace or json.loads(snapshot) != {
                            "id": workspace[0],
                            "root": workspace[1],
                        }:
                            continue
                        item = {
                            "id": ref,
                            "statement": f"workspace {workspace[0]}: {workspace[1]}",
                            "kind": "project",
                            "layer": "L2",
                            "status": "hypothesis",
                            "source_refs": [ref],
                            "created_at": None,
                            "valid_until": None,
                            "related_quest_ids": [],
                            "key": None,
                            "freshness": "current",
                            "conflicted": False,
                            "source_hash": digest,
                            "source_type": trust,
                        }
                    else:
                        item = None
                    if item is not None:
                        if not _kind_selected(item, kinds) or (
                            layers and item["layer"] not in layers
                        ):
                            continue
                        size = len(
                            json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode(
                                "utf-8"
                            )
                        ) + bool(items)
                        if used + size > max_bytes:
                            omitted += 1
                            continue
                        items.append(item)
                        used += size
                        continue
                    current = db.execute(
                        "SELECT title,goal,created_at,plan_revision FROM quests WHERE id=?",
                        (quest_id,),
                    ).fetchone()
                    if not current or (
                        source_type != "quest" and revision is not None and revision != current[3]
                    ):
                        continue
                    expected = None
                    if trust == "core_record":
                        expected = {
                            "title": current[0],
                            "goal": current[1],
                            "created_at": current[2],
                        }
                        statement, kind, created = f"{current[0]}: {current[1]}", "task", current[2]
                    elif trust == "core_event":
                        event = db.execute(
                            "SELECT event_type,payload,created_at FROM events WHERE id=? AND quest_id=?",
                            (ref[6:], quest_id),
                        ).fetchone()
                        if event and event[0] == source_type:
                            event_data = json.loads(event[1])
                            if source_type == "approval.decided" and event_data.get("approval_id"):
                                approval = db.execute(
                                    "SELECT status FROM approvals WHERE id=? AND quest_id=?",
                                    (event_data["approval_id"], quest_id),
                                ).fetchone()
                                if not approval or approval[0] != event_data.get("status"):
                                    continue
                            expected = {"payload": event_data, "created_at": event[2]}
                            statement, created = f"{source_type}: {event[1]}", event[2]
                            kind = (
                                "failure"
                                if source_type
                                in {"worker.failed", "worker.blocked", "tool.execution_uncertain"}
                                else (
                                    "decision"
                                    if source_type in {"approval.decided", "quest.plan_approved"}
                                    else "artifact"
                                )
                            )
                    elif trust == "verified_evidence":
                        evidence = db.execute(
                            "SELECT kind,payload,created_at FROM evidence WHERE id=? AND quest_id=? AND trusted=1",
                            (ref[9:], quest_id),
                        ).fetchone()
                        if evidence and evidence[0] == source_type:
                            expected = {
                                "payload": json.loads(evidence[1]),
                                "created_at": evidence[2],
                            }
                            statement, created = f"{source_type}: {evidence[1]}", evidence[2]
                            kind = "artifact" if source_type == "artifact" else "fact"
                    if (
                        expected is None
                        or json.loads(snapshot) != expected
                        or len(statement.encode()) > 8192
                    ):
                        continue
                    item = {
                        "id": ref,
                        "statement": statement,
                        "kind": kind,
                        "layer": "L3" if kind == "failure" else "L1",
                        "status": "hypothesis",
                        "source_refs": [ref],
                        "created_at": created,
                        "valid_until": None,
                        "related_quest_ids": [quest_id],
                        "key": None,
                        "freshness": "current" if trust == "core_record" else "historical",
                        "conflicted": False,
                        "source_hash": digest,
                        "source_type": trust,
                        "plan_binding": "unbound" if revision is None else "bound",
                        "trust_scope": "source record only; no domain truth or current artifact validation",
                    }
                if not _kind_selected(item, kinds) or (layers and item["layer"] not in layers):
                    continue
                size = len(
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ) + bool(items)
                if used + size > max_bytes:
                    omitted += 1
                    continue
                items.append(item)
                used += size
            if len(candidates) > 512:
                next_cursor = next_cursor or candidates[511]
                omitted += 1
        return {
            "items": items,
            "omitted": omitted,
            "bytes": used if items else 0,
            "cursor": next_cursor,
        }
