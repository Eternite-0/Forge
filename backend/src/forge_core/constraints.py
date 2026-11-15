"""Bounded, source-linked constraint extraction; user instructions are not facts.

Model extracts remain pending. A conservative literal extractor records only
verbatim clauses from an original user message. Confirmation is a separate Core
operation requiring a user confirmation message containing the literal clause.
No instruction or confirmation grants tool permission or approval.
"""

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime

from .domain import DomainError


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _message_hash(row):
    return hashlib.sha256(_json(dict(row)).encode("utf-8")).hexdigest()


_EXECUTION_LIMIT_KEYS = frozenset(
    {
        "max_nodes",
        "max_members",
        "max_concurrency",
        "max_depth",
        "max_total_tokens",
        "max_duration_seconds",
        "max_worker_steps",
        "max_worker_tool_calls",
        "max_review_rounds",
    }
)
_EXECUTION_HEADER = re.compile(
    r"(?:Experiment limits\s*\([^\n]{0,200}\)|Task execution limits|任务执行上限)\s*[:：]\s*",
    re.IGNORECASE,
)


def extract_execution_constraints(message_id, content):
    """Recognize explicit structured user limits, retaining exact Unicode source spans.

    This never interprets role names, a scenario ID, or natural language numbers.
    Multiple explicit blocks intersect; no block can relax an earlier bound.
    """

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DomainError("Duplicate task execution limit")
            result[key] = value
        return result

    decoder = json.JSONDecoder(object_pairs_hook=unique_object)
    limits, spans = {}, []
    for match in _EXECUTION_HEADER.finditer(content):
        try:
            block, length = decoder.raw_decode(content[match.end() :])
        except (ValueError, TypeError) as exc:
            raise DomainError("Invalid structured task execution limits") from exc
        if not isinstance(block, dict) or not block or set(block) - _EXECUTION_LIMIT_KEYS:
            raise DomainError("Unknown structured task execution limit")
        for key, value in block.items():
            if type(value) is not int or value < 1 or value > 1000000:
                raise DomainError("Task execution limits require positive integers")
            limits[key] = min(value, limits.get(key, value))
        spans.append(
            {
                "start": match.end(),
                "end": match.end() + length,
                "text": content[match.end() : match.end() + length],
            }
        )
    if not spans:
        return None
    return {
        "version": "source-execution-limits-v1",
        "source_message_id": message_id,
        "source_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "source_spans": spans,
        "limits": limits,
    }


def execution_constraints(connection, quest_id):
    """Reconstruct enforceable bounds only from the Quest's original user request."""
    row = connection.execute("SELECT metadata FROM quests WHERE id=?", (quest_id,)).fetchone()
    if row is None:
        raise KeyError(quest_id)
    metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
    source_id = metadata.get("original_request_message_id")
    if not source_id:
        return None
    source = connection.execute(
        "SELECT id,content FROM messages WHERE id=? AND role='user' AND conversation_id='main'",
        (source_id,),
    ).fetchone()
    if source is None or hashlib.sha256(
        source["content"].encode("utf-8")
    ).hexdigest() != metadata.get("original_request_sha256"):
        raise DomainError("Original task constraint source integrity check failed")
    return extract_execution_constraints(source["id"], source["content"])


def validate_execution_constraints(connection, quest_id, binding):
    actual = execution_constraints(connection, quest_id)
    if binding != actual:
        raise DomainError("Task execution constraints do not match their original source")
    return (actual or {}).get("limits", {})


def _dimension(statement):
    lowered = statement.casefold()
    for dimension, terms in (
        ("budget", ("budget", "spend", "token", "预算", "成本", "费用")),
        ("deadline", ("deadline", "due by", "截止", "期限")),
        ("language", ("language", "中文", "英文", "chinese", "english")),
        ("retention", ("delete", "remove", "删除", "保留")),
        ("scope", ("only", "scope", "仅限", "只允许", "范围")),
        ("model", ("model", "provider", "模型", "供应商")),
    ):
        if any(term in lowered for term in terms):
            return dimension
    return "unspecified"


_EXPLICIT = re.compile(
    r"\b(?:must|never|do not|don't|only|constraint|budget|deadline|keep)\b"
    r"|必须|不要|禁止|只允许|仅限|预算|截止|保留|不得",
    re.IGNORECASE,
)


def _clauses(content):
    # A clause is copied exactly. Long clauses remain recoverable as message
    # sources instead of being silently truncated into a different constraint.
    result = []
    for clause in re.split(r"[\r\n;；。]+", content):
        clause = clause.strip()
        if clause and len(clause.encode("utf-8")) <= 1024 and _EXPLICIT.search(clause):
            result.append(clause)
            if len(result) == 8:
                break
    return result


class ConstraintService:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def available(connection):
        return bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='constraint_records'"
            ).fetchone()
        )

    @contextmanager
    def _connection(self, connection=None, *, write=False):
        if connection is not None:
            yield connection
        elif write:
            with self.store.unit_of_work() as work:
                yield work.connection
        else:
            with self.store.connection() as owned:
                yield owned

    @staticmethod
    def _source(connection, message_id):
        row = connection.execute(
            "SELECT * FROM messages WHERE conversation_id='main' AND role='user' AND id=?",
            (message_id,),
        ).fetchone()
        if row is None:
            raise DomainError("Constraint requires an original user message")
        return row

    def record_extraction(
        self,
        statement,
        *,
        source_message_id,
        quest_id=None,
        dimension=None,
        valid_until=None,
        origin="pending_model",
        connection=None,
    ):
        if (
            not isinstance(statement, str)
            or not statement.strip()
            or len(statement.encode("utf-8")) > 1024
        ):
            raise ValueError("Constraint statement must be bounded")
        if origin not in {"pending_model", "explicit_user"}:
            raise ValueError("Constraint confirmation requires a separate user message")
        dimension = dimension or _dimension(statement)
        if not isinstance(dimension, str) or not dimension or len(dimension) > 100:
            raise ValueError("Invalid constraint dimension")
        if valid_until is not None:
            expiry = datetime.fromisoformat(valid_until)
            if expiry.tzinfo is None:
                raise ValueError("Constraint expiry must include timezone")
            valid_until = expiry.astimezone(UTC).isoformat()
        with self._connection(connection, write=True) as db:
            source = self._source(db, source_message_id)
            if origin == "explicit_user" and statement not in source["content"]:
                raise DomainError("Explicit constraint must be verbatim user text")
            if (
                quest_id is not None
                and not db.execute(
                    "SELECT 1 FROM quests WHERE id=? AND conversation_id='main'", (quest_id,)
                ).fetchone()
            ):
                raise DomainError("Unknown constraint Quest")
            digest = _message_hash(source)
            ident = hashlib.sha256(
                _json([source_message_id, quest_id, dimension, statement, origin]).encode("utf-8")
            ).hexdigest()
            db.execute(
                """INSERT OR IGNORE INTO constraint_records
                VALUES (?,'main',?,?,?,?,?,?,?,?,'active',NULL,NULL)""",
                (
                    ident,
                    quest_id,
                    statement,
                    dimension,
                    origin,
                    source_message_id,
                    digest,
                    datetime.now(UTC).isoformat(),
                    valid_until,
                ),
            )
            # Import locally to avoid a module dependency cycle.
            from .memory import _index_terms

            _index_terms(db, f"constraint:{ident}", f"{statement} {dimension} {quest_id or ''}")
            return ident

    def confirm(self, constraint_id, *, confirmation_message_id, connection=None):
        with self._connection(connection, write=True) as db:
            row = db.execute(
                "SELECT * FROM constraint_records WHERE id=? AND status='active'", (constraint_id,)
            ).fetchone()
            if row is None or self._record_item(db, row) is None:
                raise DomainError("Unknown or stale constraint")
            message = self._source(db, confirmation_message_id)
            if message["id"] == row["source_message_id"] or not re.search(
                r"\bconfirm\b|确认", message["content"], re.IGNORECASE
            ):
                raise DomainError("Constraint requires explicit user confirmation")
            if row["statement"] not in message["content"]:
                raise DomainError("Confirmation must identify the literal constraint")
            db.execute(
                """UPDATE constraint_records SET origin='user_confirmed',
                confirmation_message_id=?,confirmation_sha256=? WHERE id=?""",
                (confirmation_message_id, _message_hash(message), constraint_id),
            )

    def revoke(self, constraint_id, *, connection=None):
        with self._connection(connection, write=True) as db:
            return bool(
                db.execute(
                    "UPDATE constraint_records SET status='revoked' WHERE id=?", (constraint_id,)
                ).rowcount
            )

    def index_message(self, message_id, *, quest_id=None, connection=None):
        """Bind the current user message to the Router-selected Quest atomically."""
        with self._connection(connection, write=True) as db:
            if not self.available(db):
                return []
            source = self._source(db, message_id)
            if (
                quest_id is not None
                and not db.execute(
                    "SELECT 1 FROM quests WHERE id=? AND conversation_id='main'", (quest_id,)
                ).fetchone()
            ):
                raise DomainError("Unknown constraint Quest")
            existing = db.execute(
                "SELECT * FROM constraint_message_index WHERE message_id=?", (message_id,)
            ).fetchone()
            digest = _message_hash(source)
            if existing and existing["source_sha256"] != digest:
                raise DomainError("Constraint source integrity failure")
            if existing and existing["quest_id"] and quest_id not in (None, existing["quest_id"]):
                raise DomainError("Constraint message already belongs to another Quest")
            db.execute(
                """INSERT INTO constraint_message_index VALUES (?,?,?,?)
                ON CONFLICT(message_id) DO UPDATE SET quest_id=COALESCE(
                constraint_message_index.quest_id, excluded.quest_id)""",
                (message_id, digest, quest_id, source["created_at"]),
            )
            from .memory import _index_terms

            _index_terms(db, f"message:{message_id}", source["content"])
            associated = existing["quest_id"] if existing and existing["quest_id"] else quest_id
            return [
                self.record_extraction(
                    statement,
                    source_message_id=message_id,
                    quest_id=associated,
                    origin="explicit_user",
                    connection=db,
                )
                for statement in _clauses(source["content"])
            ]

    def index_messages(self, *, connection, limit=128):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("Invalid constraint indexing bound")
        if not self.available(connection):
            return {"has_more": False, "processed": 0}
        cursor = connection.execute(
            "SELECT message_rowid FROM constraint_index_cursor WHERE singleton=1"
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT rowid,* FROM messages WHERE rowid>? ORDER BY rowid LIMIT ?", (cursor, limit)
        ).fetchall()
        for row in rows:
            if row["conversation_id"] != "main" or row["role"] != "user":
                continue
            # A caller/model-provided metadata field is not enough to establish
            # Quest association. Router explicitly supplies it when recording.
            self.index_message(row["id"], connection=connection)
        if rows:
            connection.execute(
                "UPDATE constraint_index_cursor SET message_rowid=? WHERE singleton=1",
                (rows[-1]["rowid"],),
            )
        return {"has_more": len(rows) == limit, "processed": len(rows)}

    def _record_item(self, connection, row, *, conflicts=True):
        if row["status"] != "active":
            return None
        expiry = row["valid_until"]
        if expiry and datetime.fromisoformat(expiry) <= datetime.now(UTC):
            return None
        try:
            source = self._source(connection, row["source_message_id"])
        except DomainError:
            return None
        if _message_hash(source) != row["source_sha256"]:
            return None
        if connection.execute(
            "SELECT 1 FROM revoked_memory_sources WHERE source_ref=?",
            (f"message:{source['id']}",),
        ).fetchone():
            return None
        if row["confirmation_message_id"]:
            try:
                confirmation = self._source(connection, row["confirmation_message_id"])
            except DomainError:
                return None
            if _message_hash(confirmation) != row["confirmation_sha256"]:
                return None
        potential_conflict = False
        if conflicts and row["dimension"] != "unspecified":
            others = connection.execute(
                """SELECT * FROM constraint_records WHERE dimension=? AND quest_id IS ?
                AND status='active' AND id!=? ORDER BY id LIMIT 101""",
                (row["dimension"], row["quest_id"], row["id"]),
            ).fetchall()
            potential_conflict = len(others) > 100 or any(
                other["statement"] != row["statement"]
                and self._record_item(connection, other, conflicts=False) is not None
                for other in others[:100]
            )
        refs = [f"message:{source['id']}"]
        if row["confirmation_message_id"]:
            refs.append(f"message:{row['confirmation_message_id']}")
        return {
            "id": f"constraint:{row['id']}",
            "statement": row["statement"],
            "kind": "constraint",
            "layer": "L1" if row["quest_id"] else "L2",
            "status": "hypothesis",
            "confirmation_status": row["origin"],
            "source_refs": refs,
            "source_hash": row["source_sha256"],
            "created_at": row["created_at"],
            "valid_until": expiry,
            "related_quest_ids": [row["quest_id"]] if row["quest_id"] else [],
            "key": row["dimension"],
            "freshness": "unexpired" if expiry else "unknown",
            "conflicted": potential_conflict,
            "source_type": "user_instruction",
            "trust_scope": "user instruction only; no tool permission or domain truth",
        }

    def read_indexed(self, ref, *, connection):
        if not self.available(connection):
            return None
        if ref.startswith("constraint:"):
            row = connection.execute(
                "SELECT * FROM constraint_records WHERE id=?", (ref[11:],)
            ).fetchone()
            return self._record_item(connection, row) if row else None
        if not ref.startswith("message:"):
            return None
        indexed = connection.execute(
            "SELECT * FROM constraint_message_index WHERE message_id=?", (ref[8:],)
        ).fetchone()
        if indexed is None:
            return None
        try:
            source = self._source(connection, ref[8:])
        except DomainError:
            return None
        if (
            _message_hash(source) != indexed["source_sha256"]
            or connection.execute(
                "SELECT 1 FROM revoked_memory_sources WHERE source_ref=?", (ref,)
            ).fetchone()
        ):
            return None
        content = source["content"]
        excerpt = content.encode("utf-8")[:2048].decode("utf-8", errors="ignore")
        archived = bool(
            connection.execute(
                "SELECT 1 FROM context_archive_sources WHERE scope='router' AND scope_id='main' AND source_id=?",
                (source["id"],),
            ).fetchone()
        )
        return {
            "id": ref,
            "statement": excerpt,
            "kind": "interaction",
            "layer": ("L1" if indexed["quest_id"] else "L2") if archived else "L0",
            "status": "hypothesis",
            "source_refs": [ref],
            "source_hash": indexed["source_sha256"],
            "created_at": source["created_at"],
            "valid_until": None,
            "related_quest_ids": [indexed["quest_id"]] if indexed["quest_id"] else [],
            "key": None,
            "freshness": "historical",
            "conflicted": False,
            "omitted": excerpt != content,
            "archived": archived,
            "source_type": "user_message",
            "trust_scope": "original user statement only; no domain truth",
        }

    def context(self, *, quest_id=None, limit=24, max_bytes=12288, connection=None):
        if type(limit) is not int or not 1 <= limit <= 128 or not 0 <= max_bytes <= 65536:
            raise ValueError("Invalid constraint context bound")
        with self._connection(connection) as db:
            if not self.available(db):
                return {"items": [], "omitted": 0, "cursor": None}
            rows = db.execute(
                """SELECT * FROM constraint_records WHERE status='active'
                AND (quest_id IS NULL OR quest_id=?)
                ORDER BY CASE WHEN quest_id=? THEN 0 ELSE 1 END,
                CASE origin WHEN 'user_confirmed' THEN 0 WHEN 'explicit_user' THEN 1
                ELSE 2 END, created_at DESC, id LIMIT 257""",
                (quest_id, quest_id),
            ).fetchall()
            items, used, omitted = [], 2, max(0, len(rows) - 256)
            for row in rows[:256]:
                item = self._record_item(db, row)
                if item is None:
                    continue
                size = len(_json(item).encode("utf-8")) + bool(items)
                if len(items) >= limit or used + size > max_bytes:
                    omitted += 1
                    continue
                items.append(item)
                used += size
            return {
                "items": items,
                "omitted": omitted,
                "cursor": "memory_recall" if omitted else None,
            }
