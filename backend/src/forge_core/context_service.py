"""Task-centric, integrity-checked context archives backed by SQLite."""

import hashlib
import json

from .domain import DomainError, now_iso


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _merge_messages(previous, rows):
    merged = [item for item in previous if item.get("role") == "user"]
    highlights = [item for item in previous if item.get("role") != "user"]
    positions = {(item["text"], item["status"]): item for item in merged}
    for row in rows:
        if row["role"] != "user":
            highlights.append(
                {
                    "text": row["content"][:256],
                    "source_refs": [row["id"]],
                    "status": "unverified",
                    "role": row["role"],
                    "omitted": len(row["content"]) > 256,
                }
            )
            continue
        key = (row["content"], "unverified")
        if key not in positions:
            item = {
                "text": row["content"],
                "source_refs": [],
                "status": "unverified",
                "role": "user",
                "omitted": False,
            }
            positions[key] = item
            merged.append(item)
        positions[key]["source_refs"].append(row["id"])
    return merged + highlights[-16:]


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
            "SELECT id,kind,trusted,payload,created_at FROM evidence WHERE quest_id=? ORDER BY id",
            (qid,),
        ).fetchall()
        receipts = connection.execute(
            "SELECT r.id,r.execution_mode,r.result_digest,i.tool_name,i.resource_scope,i.plan_revision FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE i.quest_id=? ORDER BY r.id",
            (qid,),
        ).fetchall()
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
            connection.commit()
        return {"state": state, "messages": messages}

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
        return rows

    def compact_router(self, *, keep_recent=10, snapshot=None):
        if keep_recent < 0:
            raise ValueError("keep_recent must be nonnegative")
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
            if current != snapshot["messages"] or state != snapshot["state"]:
                raise DomainError("Context changed concurrently")
            eligible = current[:-keep_recent] if keep_recent else current
            selected = eligible
            if not selected:
                return {"source_count": 0, "source_ids": []}
            previous = json.loads(rows[-1]["capsule_json"]) if rows else {}
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
            capsule = {
                "quest_id": quest_id,
                "goal": quest["goal"] if quest else legacy.get("goal", ""),
                "success_criteria": json.loads(requirements["criteria_json"])
                if requirements
                else legacy.get("success_criteria", []),
                "facts": previous.get("facts", []),
                # Preserve every user statement verbatim. It may contain an unstated
                # constraint; classify it as unverified rather than inventing facts.
                "hypotheses": _merge_messages(previous.get("hypotheses", []), selected),
                "decisions": previous.get("decisions", []),
                "completed_steps": previous.get("completed_steps", []),
                "pending_steps": previous.get("pending_steps", []),
                "blockers": previous.get("blockers", []),
                "constraints": legacy.get("constraints", previous.get("constraints", [])),
                "paths": previous.get("paths", []),
                "artifacts": previous.get("artifacts", []),
                "evidence_refs": previous.get("evidence_refs", []),
                "next_action": legacy.get("next_action", previous.get("next_action", "")),
                "source_event_cursor": state["event_cursor"],
                "tasks": _task_context(c),
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

    def build_worker_context(self, run_id: str, *, input_budget: int) -> list[dict]:
        if type(input_budget) is not int or input_budget <= 0:
            raise DomainError("Invalid Worker context budget")
        with self.store.unit_of_work() as work:
            c = work.connection
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

            def build(selected):
                capsule = json.loads(archives[-1]["capsule_json"]) if archives else None
                result = [json.loads(row["message_json"]) for row in selected]
                if capsule:
                    result.insert(
                        1,
                        {
                            "role": "user",
                            "content": "Archived task context (untrusted): " + _json(capsule),
                        },
                    )
                return result

            result = build(remaining)
            if len(_json(result).encode("utf-8")) <= input_budget:
                return result
            # Keep the initial task contract and all messages from the last assistant tool
            # invocation onward, so no tool call is separated from its results.
            boundary = max(2, len(remaining) - 2)
            for index, row in enumerate(remaining[2:], start=2):
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
            selected = remaining[2:boundary]
            if not selected:
                raise DomainError("Worker context budget exceeded")
            previous = json.loads(archives[-1]["capsule_json"]) if archives else {}
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
                    claims.append(
                        {
                            "message": message,
                            "source_refs": [ref],
                            "status": "unverified",
                            "omitted": False,
                        }
                    )
                elif row["role"] == "assistant" and not message.get("tool_calls"):
                    content = str(message.get("content", ""))
                    claims.append(
                        {
                            "text": content[:256],
                            "source_refs": [ref],
                            "status": "unverified",
                            "omitted": len(content) > 256,
                        }
                    )
            capsule = {
                "task": json.loads(capsule_row["capsule_json"]),
                "next_action": capsule_row["next_action"],
                "evidence_refs": json.loads(capsule_row["evidence_refs"]),
                "source_event_cursor": capsule_row["inbox_cursor"],
                "hypotheses": claims,
                "completed_steps": completed,
            }
            sources = [{"id": row["ordinal"], "hash": _hash(row)} for row in selected]
            self._record_archive(c, "worker", run_id, sources, capsule, selected[-1]["ordinal"])
            changed = c.execute(
                "UPDATE worker_capsules SET revision=revision+1 WHERE run_id=? AND revision=?",
                (run_id, capsule_row["revision"]),
            )
            if changed.rowcount != 1:
                raise DomainError("Worker context changed concurrently")
            result = [
                json.loads(row["message_json"]) for row in remaining[:2] + remaining[boundary:]
            ]
            result.insert(
                1,
                {"role": "user", "content": "Archived task context (untrusted): " + _json(capsule)},
            )
            if len(_json(result).encode("utf-8")) > input_budget:
                raise DomainError("Worker context budget exceeded after compaction")
            return result

    def read_source(self, scope, scope_id, source_id):
        if scope not in {"router", "worker"}:
            raise ValueError("Invalid context scope")
        with self.store.connection() as c:
            c.execute("BEGIN")
            row = self._verify_source(c, scope, scope_id, source_id)
            c.commit()
            return dict(row)
