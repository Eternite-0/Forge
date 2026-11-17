"""Task-centric, integrity-checked context archives backed by SQLite."""

import hashlib
import json

from .domain import DomainError, now_iso


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _router_handoff_input(snapshot, selected, previous):
    """Bind a model handoff to its exact complete inputs and archive frontier."""
    return {
        "version": "router-model-handoff-input-v1",
        "archive_head": snapshot["archive_head"],
        "router_state_hash": _hash(snapshot["state"]),
        "previous_context": previous.get("model_handoff", {}).get("text", previous),
        "messages": [
            {"id": row["id"], "role": row["role"], "content": row["content"]} for row in selected
        ],
        "source_hashes": [{"id": row["id"], "hash": _hash(row)} for row in selected],
    }


def _worker_handoff_input(prepared, selected):
    return {
        "version": "worker-model-handoff-input-v1",
        "frontier_digest": _hash(prepared["snapshot"]),
        "task": prepared["task"],
        "previous_context": prepared["previous"]
        .get("model_handoff", {})
        .get("text", prepared["previous"]),
        "messages": [
            {"ordinal": row["ordinal"], "message": json.loads(row["message_json"])}
            for row in selected
        ],
        "source_hashes": [{"id": row["ordinal"], "hash": _hash(row)} for row in selected],
    }


def _worker_batch_boundaries(rows):
    """Prefixes end only after every assistant tool call has all its results."""
    pending, boundaries = set(), []
    for index, row in enumerate(rows, 1):
        message = json.loads(row["message_json"])
        if row["role"] == "assistant":
            ids = [call.get("id") for call in message.get("tool_calls", [])]
            if None in ids or len(set(ids)) != len(ids) or pending.intersection(ids):
                raise DomainError("Invalid Worker tool call pairing")
            pending.update(ids)
        elif row["role"] == "tool":
            identity = message.get("tool_call_id")
            if identity not in pending:
                raise DomainError("Invalid Worker tool result pairing")
            pending.remove(identity)
        if not pending:
            boundaries.append(index)
    return boundaries


def _excerpt(value, max_bytes=1024):
    return value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def _bounded_items(items, *, limit, max_bytes, anchors=4):
    """Keep initial anchors and recent items; omitted sources remain archived."""
    selected, used = [], 2
    candidates = list(enumerate(items[:anchors])) + list(
        reversed(list(enumerate(items[anchors:], start=anchors)))
    )
    for index, item in candidates:
        size = len(_json(item).encode("utf-8")) + bool(selected)
        if len(selected) < limit and used + size <= max_bytes:
            selected.append((index, item))
            used += size
    selected.sort(key=lambda entry: entry[0])
    return [item for _, item in selected], len(items) - len(selected)


def _settled_receipted_exchange(store, connection, run_id, rows):
    """Only a whole successful, receipted tail may leave the live protocol."""
    if not rows or rows[0]["role"] != "assistant":
        return False
    request = json.loads(rows[0]["message_json"])
    calls = request.get("tool_calls", [])
    identities = [call.get("id") for call in calls]
    if not identities or None in identities or len(set(identities)) != len(identities):
        return False
    replies = [json.loads(row["message_json"]) for row in rows[1:]]
    if (
        len(replies) != len(calls)
        or any(row["role"] != "tool" for row in rows[1:])
        or {reply.get("tool_call_id") for reply in replies} != set(identities)
    ):
        return False
    for call in calls:
        name = call.get("function", {}).get("name")
        if name == "worker_read_completed_delivery":
            from .evidence import _verify_worker_context_call

            stored = connection.execute(
                "SELECT * FROM worker_tool_calls WHERE run_id=? AND model_call_id=? AND tool_name=?",
                (run_id, call["id"], name),
            ).fetchone()
            if stored is None or not _verify_worker_context_call(store, connection, run_id, stored):
                return False
            continue
        verified = connection.execute(
            "SELECT 1 FROM worker_tool_calls c "
            "JOIN action_receipts r ON r.id=c.receipt_id "
            "JOIN action_intents i ON i.id=r.intent_id "
            "JOIN worker_runs w ON w.id=c.run_id "
            "WHERE c.run_id=? AND c.model_call_id=? AND c.tool_name=? "
            "AND c.state='succeeded' AND i.state='succeeded' "
            "AND i.run_id=c.run_id AND i.call_id=c.call_id AND i.tool_name=c.tool_name "
            "AND i.worker_id=w.worker_id AND i.plan_id=w.plan_id "
            "AND i.plan_revision=w.plan_revision "
            "AND r.execution_mode=i.execution_mode AND i.execution_mode=w.execution_mode",
            (run_id, call["id"], name),
        ).fetchone()
        if verified is None:
            return False
    return True


def _merge_messages(previous, rows):
    merged = [item for item in previous if item.get("role") == "user"]
    highlights = [item for item in previous if item.get("role") != "user"]
    positions = {(item["text"], item["status"]): item for item in merged}
    for row in rows:
        if row["role"] != "user":
            highlights.append(
                {
                    "text": _excerpt(row["content"], 256),
                    "source_refs": [row["id"]],
                    "status": "unverified",
                    "role": row["role"],
                    "omitted": len(row["content"].encode("utf-8")) > 256,
                }
            )
            continue
        text = _excerpt(row["content"])
        key = (text, "unverified")
        if key not in positions:
            item = {
                "text": text,
                "source_refs": [],
                "status": "unverified",
                "role": "user",
                "omitted": text != row["content"],
            }
            positions[key] = item
            merged.append(item)
        positions[key]["source_refs"].append(row["id"])
        if len(positions[key]["source_refs"]) > 4:
            positions[key]["source_refs"] = (
                positions[key]["source_refs"][:1] + positions[key]["source_refs"][-3:]
            )
            positions[key]["omitted"] = True
    merged, omitted_users = _bounded_items(merged, limit=64, max_bytes=12288, anchors=8)
    highlights, omitted_highlights = _bounded_items(highlights, limit=16, max_bytes=4096, anchors=0)
    return merged + highlights, omitted_users + omitted_highlights


def _task_context(connection):
    quests = connection.execute(
        "SELECT * FROM quests WHERE conversation_id='main' AND status NOT IN ('completed','cancelled','failed') ORDER BY id LIMIT 101"
    ).fetchall()
    if len(quests) > 100:
        raise DomainError("Too many active Quests for safe compaction")
    result = []
    for quest in quests:
        qid = quest["id"]
        requirement = connection.execute(
            "SELECT * FROM quest_requirements WHERE quest_id=?", (qid,)
        ).fetchone()
        current_plan = connection.execute(
            "SELECT * FROM quest_plans WHERE quest_id=? AND revision=?",
            (qid, quest["plan_revision"]),
        ).fetchone()
        plan = (
            current_plan
            if current_plan
            and current_plan["approved_at"] is not None
            and current_plan["status"] in ("approved", "paused")
            else None
        )
        plan_state = "approved" if plan else "pending" if quest["plan_revision"] else "none"
        nodes = (
            connection.execute(
                "SELECT * FROM task_nodes WHERE plan_id=? ORDER BY node_key", (plan["id"],)
            ).fetchall()
            if plan
            else []
        )
        evidence = connection.execute(
            "SELECT id,kind,trusted,payload,created_at FROM evidence WHERE quest_id=? ORDER BY id LIMIT 65",
            (qid,),
        ).fetchall()
        receipts = connection.execute(
            "SELECT r.id,r.execution_mode,r.result_digest,i.tool_name,i.resource_scope,i.plan_revision FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE i.quest_id=? ORDER BY r.id LIMIT 65",
            (qid,),
        ).fetchall()
        evidence_omitted, receipts_omitted = len(evidence) > 64, len(receipts) > 64
        evidence, receipts = evidence[:64], receipts[:64]
        tasks = [
            {
                "key": node["node_key"],
                "objective": node["objective"],
                "success_criteria": json.loads(node["criteria_json"]),
                "status": node["status"],
                "expected_artifact": node["expected_artifact"],
                "allowed_tools": json.loads(node["allowed_tools"]),
                "evidence_refs": json.loads(node["evidence_refs"]),
            }
            for node in nodes
        ]
        result.append(
            {
                "quest_id": qid,
                "goal": quest["goal"],
                "status": quest["status"],
                "version": quest["version"],
                "source_refs": [f"quest:{qid}"],
                "success_criteria": json.loads(requirement["criteria_json"]) if requirement else [],
                "constraints": json.loads(quest["metadata"]),
                "source_omissions": {"evidence": evidence_omitted, "receipts": receipts_omitted},
                "plan_id": plan["id"] if plan else None,
                "plan_state": plan_state,
                "plan_revision": quest["plan_revision"],
                "pending_plan_id": current_plan["id"]
                if current_plan and not plan and current_plan["status"] == "proposed"
                else None,
                "decisions": [
                    {
                        "type": "approved_plan",
                        "source_refs": [f"plan:{plan['id']}"],
                        "digest": plan["digest"],
                    }
                ]
                if plan
                else [],
                "completed_steps": [node for node in tasks if node["status"] == "succeeded"],
                "pending_steps": [
                    node for node in tasks if node["status"] not in ("succeeded", "skipped")
                ],
                "blockers": [node for node in tasks if node["status"] == "blocked"],
                "artifacts": [
                    node["expected_artifact"] for node in tasks if node["expected_artifact"]
                ],
                "paths": [
                    receipt["resource_scope"]
                    for receipt in receipts
                    if receipt["plan_revision"] == quest["plan_revision"]
                ],
                "evidence_refs": [row["id"] for row in evidence if row["trusted"]],
                "facts": [
                    {
                        "statement": "Trusted evidence recorded",
                        "kind": row["kind"],
                        "source_refs": [f"evidence:{row['id']}"],
                    }
                    for row in evidence
                    if row["trusted"]
                ],
                "hypotheses": [
                    {
                        "statement": row["payload"],
                        "source_refs": [f"evidence:{row['id']}"],
                        "status": "unverified",
                    }
                    for row in evidence
                    if not row["trusted"]
                ],
                "receipts": [
                    {**dict(row), "historical": row["plan_revision"] != quest["plan_revision"]}
                    for row in receipts
                ],
            }
        )
    return result


class ContextService:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _through(connection, scope, scope_id):
        row = connection.execute(
            "SELECT through FROM context_archive_heads WHERE scope=? AND scope_id=?",
            (scope, scope_id),
        ).fetchone()
        return row["through"] if row else -1

    def _record_archive(self, connection, scope, scope_id, sources, capsule, through):
        inserted = connection.execute(
            "INSERT INTO context_archives(scope,scope_id,source_json,source_hash,capsule_json,capsule_hash,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                scope,
                scope_id,
                _json(sources),
                _hash(sources),
                _json(capsule),
                _hash(capsule),
                now_iso(),
            ),
        )
        archive_id = inserted.lastrowid
        connection.executemany(
            "INSERT INTO context_archive_sources VALUES (?,?,?,?)",
            [(scope, scope_id, str(source["id"]), archive_id) for source in sources],
        )
        connection.execute(
            "INSERT INTO context_archive_heads VALUES (?,?,?,?) ON CONFLICT(scope,scope_id) DO UPDATE SET through=excluded.through,archive_id=excluded.archive_id",
            (scope, scope_id, through, archive_id),
        )

    def _verify_source(self, connection, scope, scope_id, source_id):
        index = connection.execute(
            "SELECT a.source_json,a.source_hash FROM context_archive_sources s JOIN context_archives a ON a.id=s.archive_id WHERE s.scope=? AND s.scope_id=? AND s.source_id=?",
            (scope, scope_id, str(source_id)),
        ).fetchone()
        if index is None:
            raise DomainError("Context source integrity failure")
        sources = json.loads(index["source_json"])
        if _hash(sources) != index["source_hash"]:
            raise DomainError("Context archive integrity failure")
        entry = next((item for item in sources if str(item["id"]) == str(source_id)), None)
        if scope == "router":
            current = connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND id=?", (scope_id, source_id)
            ).fetchone()
        else:
            current = connection.execute(
                "SELECT * FROM worker_messages WHERE run_id=? AND ordinal=?", (scope_id, source_id)
            ).fetchone()
        if entry is None or current is None or _hash(dict(current)) != entry["hash"]:
            raise DomainError("Context source integrity failure")
        return current

    def router_snapshot(self):
        with self.store.connection() as connection:
            connection.execute("BEGIN")
            state = dict(
                connection.execute(
                    "SELECT * FROM router_state WHERE conversation_id='main'"
                ).fetchone()
            )
            through = self._through(connection, "router", "main")
            messages = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM messages WHERE conversation_id='main' AND rowid>? ORDER BY rowid LIMIT 510",
                    (through,),
                )
            ]
            archive_head = self._router_archive_head(connection)
            connection.commit()
        return {"state": state, "messages": messages, "archive_head": archive_head}

    @staticmethod
    def _router_archive_head(connection):
        row = connection.execute(
            "SELECT h.through,h.archive_id,a.source_hash,a.capsule_hash "
            "FROM context_archive_heads h JOIN context_archives a ON a.id=h.archive_id "
            "WHERE h.scope='router' AND h.scope_id='main'"
        ).fetchone()
        return dict(row) if row else None

    def _archives(self, connection, scope, scope_id):
        rows = connection.execute(
            "SELECT a.* FROM context_archive_heads h JOIN context_archives a ON a.id=h.archive_id WHERE h.scope=? AND h.scope_id=?",
            (scope, scope_id),
        ).fetchall()
        for row in rows:
            sources, capsule = json.loads(row["source_json"]), json.loads(row["capsule_json"])
            if _hash(sources) != row["source_hash"] or _hash(capsule) != row["capsule_hash"]:
                raise DomainError("Context archive integrity failure")
            refs = [
                ref for item in capsule.get("hypotheses", []) for ref in item.get("source_refs", [])
            ]
            refs += [
                ref
                for item in capsule.get("completed_steps", [])
                if isinstance(item, dict)
                for ref in item.get("source_refs", [])
            ]
            for ref in refs:
                self._verify_source(connection, scope, scope_id, ref)
            # Model summaries are source-bound, untrusted handoffs. Verify their
            # immutable chain, including older inputs no longer quoted in text.
            if capsule.get("model_handoff"):
                self._verify_model_handoff_chain(connection, row, scope, scope_id)
        return rows

    def _verify_model_handoff_chain(self, connection, row, scope, scope_id):
        seen = set()
        while row is not None:
            if row["id"] in seen or row["scope"] != scope or row["scope_id"] != scope_id:
                raise DomainError("Context handoff integrity failure")
            seen.add(row["id"])
            sources, capsule = json.loads(row["source_json"]), json.loads(row["capsule_json"])
            if _hash(sources) != row["source_hash"] or _hash(capsule) != row["capsule_hash"]:
                raise DomainError("Context handoff integrity failure")
            for source in sources:
                self._verify_source(connection, scope, scope_id, source["id"])
            previous = capsule.get("model_handoff", {}).get("previous_archive")
            if previous is None:
                return
            row = connection.execute(
                "SELECT * FROM context_archives WHERE id=?", (previous["archive_id"],)
            ).fetchone()
            if row is None or any(
                row[key] != previous[key] for key in ("capsule_hash", "source_hash")
            ):
                raise DomainError("Context handoff integrity failure")

    def prepare_router_compaction(self, *, keep_recent=10, snapshot=None):
        """Read complete eligible messages; no DB transaction spans a model call."""
        if type(keep_recent) is not int or keep_recent < 0:
            raise ValueError("keep_recent must be nonnegative")
        snapshot = snapshot or self.router_snapshot()
        with self.store.connection() as c:
            c.execute("BEGIN")
            rows = self._archives(c, "router", "main")
            if self._router_archive_head(c) != snapshot["archive_head"]:
                raise DomainError("Context changed concurrently")
            previous = json.loads(rows[-1]["capsule_json"]) if rows else {}
            c.commit()
        eligible = snapshot["messages"][:-keep_recent] if keep_recent else snapshot["messages"]
        return {"snapshot": snapshot, "eligible": eligible, "previous": previous}

    def compact_router(
        self,
        *,
        keep_recent=10,
        snapshot=None,
        max_sources=None,
        model_handoff=None,
        _model_fence=None,
    ):
        if type(keep_recent) is not int or keep_recent < 0:
            raise ValueError("keep_recent must be nonnegative")
        if max_sources is not None and (type(max_sources) is not int or max_sources < 1):
            raise ValueError("max_sources must be positive")
        snapshot = snapshot or self.router_snapshot()
        with self.store.unit_of_work() as work:
            c = work.connection
            rows = self._archives(c, "router", "main")
            through = self._through(c, "router", "main")
            current = [
                dict(row)
                for row in c.execute(
                    "SELECT * FROM messages WHERE conversation_id='main' AND rowid>? ORDER BY rowid LIMIT 510",
                    (through,),
                )
            ]
            state = dict(
                c.execute("SELECT * FROM router_state WHERE conversation_id='main'").fetchone()
            )
            if (
                current != snapshot["messages"]
                or state != snapshot["state"]
                or self._router_archive_head(c) != snapshot["archive_head"]
            ):
                raise DomainError("Context changed concurrently")
            if _model_fence is not None:
                _model_fence(c)
            eligible = current[:-keep_recent] if keep_recent else current
            selected = eligible[:max_sources] if max_sources is not None else eligible
            if not selected:
                return {"source_count": 0, "source_ids": []}
            previous = json.loads(rows[-1]["capsule_json"]) if rows else {}
            if model_handoff is not None and (
                not isinstance(model_handoff, dict)
                or set(model_handoff) != {"text", "input_digest"}
                or not isinstance(model_handoff["text"], str)
                or not model_handoff["text"].strip()
                or len(model_handoff["text"].encode("utf-8")) > 65536
                or model_handoff["input_digest"]
                != _hash(_router_handoff_input(snapshot, selected, previous))
            ):
                raise DomainError("Invalid source-bound model handoff")
            legacy = json.loads(state["capsule"] or "{}")
            quest_id = legacy.get("quest_id")
            quest = (
                c.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
                if quest_id
                else None
            )
            requirements = (
                c.execute(
                    "SELECT criteria_json FROM quest_requirements WHERE quest_id=?", (quest_id,)
                ).fetchone()
                if quest_id
                else None
            )
            from .constraints import ConstraintService

            constraints = ConstraintService(self.store)
            # Index in the same transaction as the archive. A rollback cannot
            # advance either the source cursor or constraint records alone.
            constraints.index_messages(connection=c, limit=256)
            constraint_context = constraints.context(quest_id=quest_id, limit=8, connection=c)
            hypotheses, omitted = _merge_messages(previous.get("hypotheses", []), selected)
            capsule = {
                "quest_id": quest_id,
                "goal": quest["goal"] if quest else legacy.get("goal", ""),
                "success_criteria": json.loads(requirements["criteria_json"])
                if requirements
                else legacy.get("success_criteria", []),
                "facts": previous.get("facts", []),
                "hypotheses": hypotheses,
                "history_recall": {
                    "scope": "router",
                    "scope_id": "main",
                    "archived_message_count": previous.get("history_recall", {}).get(
                        "archived_message_count", 0
                    )
                    + len(selected),
                    "omitted_highlights": previous.get("history_recall", {}).get(
                        "omitted_highlights", 0
                    )
                    + omitted,
                    "lookup": "memory_recall",
                    "read_source": "read_context_source",
                },
                "decisions": previous.get("decisions", []),
                "completed_steps": previous.get("completed_steps", []),
                "pending_steps": previous.get("pending_steps", []),
                "blockers": previous.get("blockers", []),
                "constraints": legacy.get("constraints", previous.get("constraints", [])),
                "constraint_records": constraint_context,
                "paths": previous.get("paths", []),
                "artifacts": previous.get("artifacts", []),
                "evidence_refs": previous.get("evidence_refs", []),
                "next_action": legacy.get("next_action", previous.get("next_action", "")),
                "source_event_cursor": state["event_cursor"],
                "tasks": _task_context(c),
            }
            if model_handoff is not None:
                head = snapshot["archive_head"]
                capsule["model_handoff"] = {
                    **model_handoff,
                    "status": "unverified",
                    "source_refs": [row["id"] for row in selected],
                    "previous_archive": {
                        key: head[key] for key in ("archive_id", "source_hash", "capsule_hash")
                    }
                    if head
                    else None,
                }
            elif previous.get("model_handoff"):
                head = snapshot["archive_head"]
                capsule["model_handoff"] = {
                    **previous["model_handoff"],
                    "previous_archive": {
                        key: head[key] for key in ("archive_id", "source_hash", "capsule_hash")
                    },
                }
            sources = [{"id": row["id"], "hash": _hash(row)} for row in selected]
            boundary = c.execute(
                "SELECT rowid FROM messages WHERE id=?", (selected[-1]["id"],)
            ).fetchone()[0]
            self._record_archive(c, "router", "main", sources, capsule, boundary)
            changed = c.execute(
                "UPDATE router_state SET revision=revision+1 WHERE conversation_id='main' AND revision=? AND event_cursor=?",
                (state["revision"], state["event_cursor"]),
            )
            if changed.rowcount != 1:
                raise DomainError("Context changed concurrently")
            return {
                "source_count": len(sources),
                "source_ids": [source["id"] for source in sources],
            }

    def rebuild_router(self):
        with self.store.connection() as c:
            c.execute("BEGIN")
            rows = self._archives(c, "router", "main")
            through = self._through(c, "router", "main")
            messages = [
                dict(row)
                for row in c.execute(
                    "SELECT * FROM messages WHERE conversation_id='main' AND rowid>? ORDER BY rowid LIMIT 5001",
                    (through,),
                )
            ]
            capsule = json.loads(rows[-1]["capsule_json"]) if rows else {}
            if capsule:
                capsule["tasks"] = _task_context(c)
                from .constraints import ConstraintService

                capsule["constraint_records"] = ConstraintService(self.store).context(
                    quest_id=capsule.get("quest_id"), limit=8, connection=c
                )
                task = next(
                    (
                        item
                        for item in capsule["tasks"]
                        if item["quest_id"] == capsule.get("quest_id")
                    ),
                    None,
                )
                if task:
                    capsule["goal"] = task["goal"]
                    capsule["success_criteria"] = task["success_criteria"]
                elif capsule.get("quest_id"):
                    capsule["goal"], capsule["success_criteria"], capsule["next_action"] = (
                        "",
                        [],
                        "",
                    )
            c.commit()
        return {"capsule": capsule, "recent_messages": messages}

    def build_worker_context(
        self,
        run_id: str,
        *,
        input_budget: int,
        source_readback_available: bool = False,
        per_message_overhead: int = 0,
    ) -> list[dict]:
        return self._worker_context(
            run_id,
            input_budget=input_budget,
            source_readback_available=source_readback_available,
            per_message_overhead=per_message_overhead,
        )

    def _worker_context(
        self,
        run_id,
        *,
        input_budget,
        source_readback_available=False,
        per_message_overhead=0,
        _prepare=False,
        _fence=None,
        _expected=None,
        _handoff=None,
        _max_sources=None,
    ):
        if type(input_budget) is not int or input_budget <= 0:
            raise DomainError("Invalid Worker context budget")
        if type(per_message_overhead) is not int or per_message_overhead < 0:
            raise DomainError("Invalid Worker message framing")

        def measured(messages):
            # Admit the same per-message framing later charged by the request ledger.
            # Otherwise content can fit here but fail before the next revision call.
            return len(_json(messages).encode("utf-8")) + per_message_overhead * len(messages)

        if type(source_readback_available) is not bool:
            raise DomainError("Invalid Worker source readback availability")
        with self.store.unit_of_work() as work:
            c = work.connection
            if _fence is not None:
                _fence(c)
            archives = self._archives(c, "worker", run_id)
            through = self._through(c, "worker", run_id)
            rows = [
                dict(row)
                for row in c.execute(
                    "SELECT * FROM worker_messages WHERE run_id=? AND ordinal>? ORDER BY ordinal LIMIT 5001",
                    (run_id, through),
                )
            ]
            capsule_row = c.execute(
                "SELECT * FROM worker_capsules WHERE run_id=?", (run_id,)
            ).fetchone()
            if capsule_row is None:
                raise DomainError("Unknown Worker context")
            # Initial system and task messages remain live across every archive.
            initial = (
                [
                    dict(row)
                    for row in c.execute(
                        "SELECT * FROM worker_messages WHERE run_id=? AND ordinal IN (0,1) ORDER BY ordinal",
                        (run_id,),
                    )
                ]
                if through >= 0
                else []
            )
            remaining = initial + rows
            protected_count = 2
            from .worker_prefetch import POLICY, verified_sources

            if json.loads(capsule_row["capsule_json"]).get("input_acquisition_policy") == POLICY:
                run = c.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone()
                if run is None:
                    raise DomainError("Unknown Worker source context")
                verified_sources(self.store, c, run, historical=False)
                source_ordinals = {
                    item[0]
                    for item in c.execute(
                        "SELECT DISTINCT message_ordinal FROM worker_core_source_reads "
                        "WHERE run_id=? AND state='succeeded'",
                        (run_id,),
                    )
                }
                if any(type(ordinal) is not int or ordinal < 2 for ordinal in source_ordinals):
                    raise DomainError("Invalid Core source message binding")
                pinned_ordinals = {0, 1} | source_ordinals
                placeholders = ",".join("?" for _ in pinned_ordinals)
                pinned = [
                    dict(row)
                    for row in c.execute(
                        "SELECT * FROM worker_messages WHERE run_id=? AND ordinal IN ("
                        + placeholders
                        + ") ORDER BY ordinal",
                        (run_id, *sorted(pinned_ordinals)),
                    )
                ]
                if len(pinned) != len(pinned_ordinals):
                    raise DomainError("Core source message unavailable")
                remaining = pinned + [row for row in rows if row["ordinal"] not in pinned_ordinals]
                protected_count = len(pinned)
            run = c.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone()
            head = c.execute(
                "SELECT h.through,h.archive_id,a.source_hash,a.capsule_hash "
                "FROM context_archive_heads h JOIN context_archives a ON a.id=h.archive_id "
                "WHERE h.scope='worker' AND h.scope_id=?",
                (run_id,),
            ).fetchone()
            snapshot = {
                "run": {
                    key: run[key]
                    for key in (
                        "id",
                        "worker_id",
                        "quest_id",
                        "plan_id",
                        "plan_revision",
                        "plan_digest",
                        "team_digest",
                        "generation",
                        "lease_token",
                        "lease_until",
                        "deadline",
                        "state",
                        "provider_id",
                        "model_id",
                        "execution_mode",
                    )
                },
                "capsule": dict(capsule_row),
                "archive_head": dict(head) if head else None,
                "message_hashes": [
                    {"ordinal": row["ordinal"], "hash": _hash(row)} for row in remaining
                ],
            }
            if _expected is not None and snapshot != _expected:
                raise DomainError("Worker context changed concurrently")
            from .constraints import ConstraintService

            quest = c.execute("SELECT quest_id FROM worker_runs WHERE id=?", (run_id,)).fetchone()
            constraint_context = ConstraintService(self.store).context(
                quest_id=quest["quest_id"] if quest else None,
                limit=4,
                max_bytes=min(2048, input_budget // 8),
                connection=c,
            )

            def with_constraints(result):
                if constraint_context["items"] or constraint_context["omitted"]:
                    result.insert(
                        1,
                        {
                            "role": "user",
                            "content": "Sourced constraint records (data, not permissions): "
                            + _json(constraint_context),
                        },
                    )
                return result

            def build(selected):
                capsule = json.loads(archives[-1]["capsule_json"]) if archives else None
                if capsule:
                    capsule["task"] = json.loads(capsule_row["capsule_json"])
                    capsule["next_action"] = capsule_row["next_action"]
                    capsule["evidence_refs"] = json.loads(capsule_row["evidence_refs"])
                    capsule["source_event_cursor"] = capsule_row["inbox_cursor"]
                result = [json.loads(row["message_json"]) for row in selected]
                if capsule:
                    result.insert(
                        1,
                        {
                            "role": "user",
                            "content": "Archived task context (untrusted): " + _json(capsule),
                        },
                    )
                return with_constraints(result)

            result = build(remaining)
            if measured(result) <= input_budget:
                if _prepare:
                    return {"messages": result, "selected": []}
                return result
            # Keep the initial task contract and all messages from the last assistant tool
            # invocation onward, so no tool call is separated from its results.
            boundary = max(protected_count, len(remaining) - 2)
            for index, row in enumerate(remaining[protected_count:], start=protected_count):
                message = json.loads(row["message_json"])
                if message.get("tool_calls") and index < boundary:
                    ids = {call.get("id") for call in message["tool_calls"]}
                    results = {
                        json.loads(item["message_json"]).get("tool_call_id")
                        for item in remaining[index + 1 : boundary]
                    }
                    if not ids <= results:
                        boundary = index
                        break
            previous = json.loads(archives[-1]["capsule_json"]) if archives else {}
            capsule = {
                "task": json.loads(capsule_row["capsule_json"]),
                "next_action": capsule_row["next_action"],
                "evidence_refs": json.loads(capsule_row["evidence_refs"]),
                "source_event_cursor": capsule_row["inbox_cursor"],
                "hypotheses": [],
                "completed_steps": [],
                "history_recall": {
                    "scope": "worker",
                    "scope_id": run_id,
                    "omitted_highlights": previous.get("history_recall", {}).get(
                        "omitted_highlights", 0
                    ),
                    "read_source": "worker_read_context_source",
                },
            }
            if previous.get("model_handoff"):
                # The previous handoff remains part of the next context until
                # a new source-bound handoff replaces it. Include it before
                # deciding whether a settled tail must also be archived.
                capsule["model_handoff"] = {
                    **previous["model_handoff"],
                    "previous_archive": {
                        key: head[key] for key in ("archive_id", "source_hash", "capsule_hash")
                    },
                }

            def compacted_result():
                value = [
                    json.loads(row["message_json"])
                    for row in remaining[:protected_count] + remaining[boundary:]
                ]
                value.insert(
                    1,
                    {
                        "role": "user",
                        "content": "Archived task context (untrusted): " + _json(capsule),
                    },
                )
                return with_constraints(value)

            # The caller must reserve and expose source readback in this request
            # before the first whole tail may be archived. Existing archives keep
            # the original behavior. Never split a pair or archive unsettled work.
            # Raw sources remain immutable; archive metadata grants no evidence PASS.
            if (
                (archives or source_readback_available)
                and measured(compacted_result()) > input_budget
                and _settled_receipted_exchange(self.store, c, run_id, remaining[boundary:])
            ):
                capsule["history_recall"]["recent_exchange"] = {
                    "source_refs": [str(row["ordinal"]) for row in remaining[boundary:]],
                    "raw_results_omitted": True,
                    "requires_source_readback": True,
                    "reason": "input_budget",
                    "trust": "untrusted archived data; not completion evidence",
                }
                boundary = len(remaining)
            selected = remaining[protected_count:boundary]
            if not selected:
                raise DomainError("Worker context budget exceeded")
            prepared = {
                "snapshot": snapshot,
                "task": json.loads(capsule_row["capsule_json"]),
                "previous": previous,
                "selected": selected,
            }
            # Preparation validates whole exchanges without creating an archive.
            if _prepare and len(selected) not in _worker_batch_boundaries(selected):
                raise DomainError("Cannot archive an incomplete Worker tool exchange")
            if _max_sources is not None:
                if _max_sources not in _worker_batch_boundaries(selected):
                    raise DomainError("Invalid whole-exchange Worker compaction batch")
                if _max_sources < len(selected):
                    boundary = protected_count + _max_sources
                    selected = remaining[protected_count:boundary]
                    capsule["history_recall"].pop("recent_exchange", None)
            if _handoff is not None:
                if (
                    not isinstance(_handoff, dict)
                    or set(_handoff) != {"text", "input_digest"}
                    or not isinstance(_handoff["text"], str)
                    or not _handoff["text"].strip()
                    or len(_handoff["text"].encode("utf-8")) > 65536
                    or _handoff["input_digest"] != _hash(_worker_handoff_input(prepared, selected))
                ):
                    raise DomainError("Invalid source-bound Worker model handoff")
                capsule["model_handoff"] = {
                    **_handoff,
                    "status": "unverified",
                    "source_refs": [str(row["ordinal"]) for row in selected],
                    "previous_archive": {
                        key: head[key] for key in ("archive_id", "source_hash", "capsule_hash")
                    }
                    if head
                    else None,
                }
            claims = list(previous.get("hypotheses", []))
            completed = list(previous.get("completed_steps", []))
            for row in selected:
                message = json.loads(row["message_json"])
                ref = str(row["ordinal"])
                if row["role"] == "tool":
                    paired = [
                        str(item["ordinal"])
                        for item in selected
                        if item["role"] == "assistant"
                        and any(
                            call.get("id") == message.get("tool_call_id")
                            for call in json.loads(item["message_json"]).get("tool_calls", [])
                        )
                    ]
                    if len(paired) != 1:
                        raise DomainError("Worker tool result has no unique archived call")
                    call = c.execute(
                        "SELECT * FROM worker_tool_calls WHERE run_id=? AND model_call_id=?",
                        (run_id, message.get("tool_call_id")),
                    ).fetchone()
                    if call is None or call["state"] not in ("succeeded", "failed"):
                        raise DomainError("Cannot archive unresolved Worker tool call")
                    receipt = (
                        c.execute(
                            "SELECT r.*,i.resource_scope,i.parameter_digest FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE r.id=?",
                            (call["receipt_id"],),
                        ).fetchone()
                        if call["receipt_id"]
                        else None
                    )
                    completed.append(
                        {
                            "tool": call["tool_name"],
                            "state": call["state"],
                            "receipt_id": call["receipt_id"],
                            "result_hash": _hash(message),
                            "source_refs": paired + [ref],
                            "resource_scope": receipt["resource_scope"] if receipt else None,
                            "receipt_result_digest": receipt["result_digest"] if receipt else None,
                        }
                    )
                elif row["role"] == "user":
                    content = str(message.get("content", ""))
                    claims.append(
                        {
                            "message": {"role": "user", "content": _excerpt(content)},
                            "source_refs": [ref],
                            "status": "unverified",
                            "omitted": len(content.encode("utf-8")) > 1024,
                        }
                    )
                elif row["role"] == "assistant" and not message.get("tool_calls"):
                    content = str(message.get("content", ""))
                    claims.append(
                        {
                            "text": _excerpt(content, 256),
                            "source_refs": [ref],
                            "status": "unverified",
                            "omitted": len(content.encode("utf-8")) > 256,
                        }
                    )
            claims, omitted_claims = _bounded_items(
                claims, limit=24, max_bytes=min(8192, input_budget // 4), anchors=2
            )
            completed, omitted_completed = _bounded_items(
                completed, limit=32, max_bytes=min(8192, input_budget // 4), anchors=4
            )
            capsule["hypotheses"] = claims
            capsule["completed_steps"] = completed
            capsule["history_recall"]["omitted_highlights"] += omitted_claims + omitted_completed

            result = compacted_result()
            # Fixed task contracts and live tool pairs take priority over archived
            # highlights. Every removed highlight still has its original source.
            while measured(result) > input_budget and (
                capsule["hypotheses"] or capsule["completed_steps"]
            ):
                target = capsule["hypotheses"] or capsule["completed_steps"]
                target.pop()
                capsule["history_recall"]["omitted_highlights"] += 1
                result = compacted_result()
            if measured(result) > input_budget and (
                _handoff is None or _max_sources == len(prepared["selected"])
            ):
                raise DomainError("Worker context budget exceeded after compaction")
            if _prepare:
                return prepared
            sources = [{"id": row["ordinal"], "hash": _hash(row)} for row in selected]
            self._record_archive(c, "worker", run_id, sources, capsule, selected[-1]["ordinal"])
            changed = c.execute(
                "UPDATE worker_capsules SET revision=revision+1 WHERE run_id=? AND revision=?",
                (run_id, capsule_row["revision"]),
            )
            if changed.rowcount != 1:
                raise DomainError("Worker context changed concurrently")
            return result

    def read_source(self, scope, scope_id, source_id):
        if scope not in {"router", "worker"}:
            raise ValueError("Invalid context scope")
        with self.store.connection() as c:
            c.execute("BEGIN")
            row = self._verify_source(c, scope, scope_id, source_id)
            c.commit()
            return dict(row)
