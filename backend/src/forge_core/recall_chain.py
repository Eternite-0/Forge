"""Read-only object recall over existing authoritative records, never new authority."""

import hashlib
import json
from collections import deque

from .domain import DomainError


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


# Explicit projections exclude ownership tokens, credentials and transport state.
_OBJECTS = {
    "workspace": ("workspace", "id", "id,root"),
    "conversation": ("conversations", "id", "id,created_at"),
    "router": (
        "router_state",
        "conversation_id",
        "conversation_id,router_session_id,event_cursor,capsule",
    ),
    "quest": (
        "quests",
        "id",
        (
            "id,conversation_id,title,goal,status,priority,created_at,updated_at,metadata,"
            "version,plan_revision"
        ),
    ),
    "worker": ("workers", "id", "id,quest_id,role,model_id,status,hidden,created_at,metadata"),
    "run": (
        "worker_runs",
        "id",
        (
            "id,worker_id,quest_id,plan_id,node_key,plan_revision,role_id,provider_id,model_id,"
            "execution_mode,state,generation,steps,token_spent,tool_spent,created_at"
        ),
    ),
    "event": (
        "events",
        "id",
        "id,seq,event_type,conversation_id,quest_id,worker_id,payload,created_at",
    ),
    "message": ("messages", "id", "id,conversation_id,role,content,created_at,metadata"),
    "plan": (
        "quest_plans",
        "id",
        "id,quest_id,revision,digest,status,graph_json,created_at",
    ),
    "approval": (
        "approvals",
        "id",
        (
            "id,quest_id,plan_revision,action_type,parameter_digest,resource_scope,risk,reason,"
            "status,created_at,expires_at,decided_at,decided_by,consumed_at"
        ),
    ),
    "evidence": ("evidence", "id", "id,quest_id,kind,trusted,payload,created_at"),
    "intent": (
        "action_intents",
        "id",
        (
            "id,quest_id,worker_id,run_id,plan_id,plan_revision,tool_name,parameter_digest,"
            "resource_scope,risk,approval_id,execution_mode,state,created_at"
        ),
    ),
    "receipt": (
        "action_receipts",
        "id",
        "id,intent_id,execution_mode,result_json,result_digest,created_at",
    ),
    "memory": (
        "memories",
        "id",
        "id,statement,kind,layer,status,source_refs,created_at,valid_until,related_quest_ids,memory_key",
    ),
    "archive": (
        "context_archives",
        "id",
        "id,scope,scope_id,source_json,source_hash,capsule_json,capsule_hash,created_at",
    ),
    "trigger": (
        "superagent_triggers",
        "id",
        (
            "id,event_id,thread_id,source_id,external_id,actor,addressed,payload_json,"
            "payload_sha256,status,reason,quest_id,created_at"
        ),
    ),
    "thread": (
        "superagent_threads",
        "id",
        "id,workspace_id,conversation_id,source_id,thread_key,created_at",
    ),
    "source": (
        "superagent_sources",
        "id",
        "id,workspace_id,conversation_id,kind,mode,allowed_actors_json,created_at",
    ),
    "project": (
        "workspace_projects",
        "id",
        "id,root,name,markers_json,detected_at",
    ),
}
_JSON_FIELDS = {
    "metadata",
    "payload",
    "capsule",
    "graph_json",
    "result_json",
    "source_refs",
    "related_quest_ids",
    "source_json",
    "capsule_json",
    "payload_json",
    "allowed_actors_json",
    "markers_json",
}
_DIRECT = {
    "conversation_id": "conversation",
    "quest_id": "quest",
    "worker_id": "worker",
    "run_id": "run",
    "plan_id": "plan",
    "approval_id": "approval",
    "intent_id": "intent",
    "receipt_id": "receipt",
    "result_event_id": "event",
    "review_event_id": "event",
    "source_message_id": "message",
    "message_id": "message",
    "artifact_id": "evidence",
    "trigger_id": "trigger",
    "source_trigger_id": "trigger",
    "original_request_message_id": "message",
    "goal_update_message_id": "message",
    "thread_id": "thread",
    "source_id": "source",
    "project_id": "project",
}


def _ref(reference):
    if not isinstance(reference, str) or not 3 <= len(reference) <= 512:
        raise ValueError("Invalid recall reference")
    kind, separator, identity = reference.partition(":")
    if (
        not separator
        or kind not in _OBJECTS
        or not identity
        or any(ord(character) < 32 for character in identity)
    ):
        raise ValueError("Invalid recall reference")
    return kind, identity


def _rows(connection, query, parameters=()):
    cursor = connection.execute(query, parameters)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _one(connection, kind, identity):
    table, key, columns = _OBJECTS[kind]
    rows = _rows(connection, f"SELECT {columns} FROM {table} WHERE {key}=?", (identity,))
    if not rows:
        raise DomainError("Recall object unavailable in this scope")
    return rows[0]


def _scope(connection, kind, record, conversation_id, run_id):
    if "workspace_id" in record:
        workspace = _rows(connection, "SELECT id FROM workspace WHERE singleton=1")
        if not workspace or record["workspace_id"] != workspace[0]["id"]:
            raise DomainError("Recall object unavailable in this scope")
    quest_id = record.get("quest_id")
    if kind == "quest":
        quest_id = record["id"]
    elif kind == "receipt":
        quest_id = _one(connection, "intent", record["intent_id"])["quest_id"]
    elif kind == "trigger":
        thread = _one(connection, "thread", record["thread_id"])
        _scope(connection, "thread", thread, conversation_id, run_id)
        if run_id is not None and not _rows(
            connection,
            "SELECT 1 FROM superagent_trigger_deliveries WHERE trigger_id=? AND run_id=?",
            (record["id"], run_id),
        ):
            raise DomainError("Recall object unavailable in this scope")
        # A delivered thread may be shared deliberately across tasks. The exact
        # delivery, rather than its current cold-start Quest, grants recall scope.
        if run_id is not None:
            return
    elif kind == "thread" and run_id is not None:
        if not _rows(
            connection,
            "SELECT 1 FROM superagent_subscriptions WHERE thread_id=? AND run_id=? "
            "UNION SELECT 1 FROM superagent_trigger_deliveries d "
            "JOIN superagent_triggers t ON t.id=d.trigger_id WHERE t.thread_id=? AND d.run_id=?",
            (record["id"], run_id, record["id"], run_id),
        ):
            raise DomainError("Recall object unavailable in this scope")
        if record["conversation_id"] != conversation_id:
            raise DomainError("Recall object unavailable in this scope")
        return
    elif kind == "project":
        query = (
            "SELECT 1 FROM task_project_links p JOIN quests q ON q.id=p.quest_id "
            "WHERE p.project_id=? AND q.conversation_id=?"
        )
        parameters = [record["id"], conversation_id]
        if run_id is not None:
            query += " AND p.quest_id=(SELECT quest_id FROM worker_runs WHERE id=?)"
            parameters.append(run_id)
        if not _rows(connection, query, parameters):
            raise DomainError("Recall object unavailable in this scope")
        return
    elif kind == "message" and run_id is not None:
        owner = _one(connection, "run", run_id)
        quest = _one(connection, "quest", owner["quest_id"])
        metadata = json.loads(quest["metadata"])
        bindings = [
            (metadata.get("original_request_message_id"), metadata.get("original_request_sha256")),
            (metadata.get("goal_update_message_id"), metadata.get("goal_update_sha256")),
        ]
        bindings.extend(
            (row["source_message_id"], row["source_sha256"])
            for row in _rows(
                connection,
                "SELECT source_message_id,source_sha256 FROM delegation_mandates WHERE quest_id=?",
                (quest["id"],),
            )
        )
        if (
            record["conversation_id"] != conversation_id
            or record["role"] != "user"
            or not any(
                identity == record["id"]
                and digest == hashlib.sha256(record["content"].encode("utf-8")).hexdigest()
                for identity, digest in bindings
            )
        ):
            raise DomainError("Recall task request source unavailable or changed in this scope")
        return
    elif kind == "archive":
        if record["scope"] == "worker":
            quest_id = _one(connection, "run", record["scope_id"])["quest_id"]
            if run_id is not None and record["scope_id"] != run_id:
                raise DomainError("Recall object unavailable in this scope")
        elif (
            record["scope"] != "router"
            or run_id is not None
            or record["scope_id"] != conversation_id
        ):
            raise DomainError("Recall object unavailable in this scope")
    if kind == "workspace":
        return
    if kind == "memory":
        related = json.loads(record["related_quest_ids"])
        if run_id is not None:
            owner = _one(connection, "run", run_id)
            if owner["quest_id"] not in related:
                raise DomainError("Recall object unavailable in this scope")
        for related_id in related:
            quest = _one(connection, "quest", related_id)
            if quest["conversation_id"] != conversation_id:
                raise DomainError("Recall object unavailable in this scope")
    if quest_id:
        quest = _one(connection, "quest", quest_id)
        if quest["conversation_id"] != conversation_id:
            raise DomainError("Recall object unavailable in this scope")
        if run_id is not None and _one(connection, "run", run_id)["quest_id"] != quest_id:
            raise DomainError("Recall object unavailable in this scope")
    else:
        owner = record.get("conversation_id", record.get("id") if kind == "conversation" else None)
        if owner is not None and owner != conversation_id:
            raise DomainError("Recall object unavailable in this scope")
        if run_id is not None and kind != "memory":
            raise DomainError("Recall object unavailable in this scope")


def _verify_archive_dependencies(connection, row):
    from .context_service import ContextService

    class RecallArchiveVerifier(ContextService):
        def _verify_source(self, connection, scope, scope_id, source_id):
            original = super()._verify_source(connection, scope, scope_id, source_id)
            # Worker transcript sources have a run-local ordinal; this internal
            # revocation key does not make raw transcripts a public recall type.
            reference = (
                f"message:{source_id}"
                if scope == "router"
                else f"worker-message:{scope_id}:{source_id}"
            )
            if connection.execute(
                "SELECT 1 FROM revoked_memory_sources WHERE source_ref=?", (reference,)
            ).fetchone():
                raise DomainError("Recall archive dependency revoked")
            return original

    verifier = RecallArchiveVerifier(None)
    try:
        # Use the existing complete source/index/scope/hash/previous-link and
        # cycle checks even when recalling an older archive rather than its head.
        verifier._verify_model_handoff_chain(connection, row, row["scope"], row["scope_id"])
        while row is not None:
            if connection.execute(
                "SELECT 1 FROM revoked_memory_sources WHERE source_ref=?",
                (f"archive:{row['id']}",),
            ).fetchone():
                raise DomainError("Recall archive dependency revoked")
            capsule = json.loads(row["capsule_json"])
            # Legacy deterministic capsules can retain earlier source IDs in
            # these Core-produced dependency lists without a model handoff.
            for field in ("hypotheses", "completed_steps"):
                for item in capsule.get(field, []):
                    if isinstance(item, dict):
                        for source_id in item.get("source_refs", []):
                            verifier._verify_source(
                                connection, row["scope"], row["scope_id"], source_id
                            )
            previous = capsule.get("model_handoff", {}).get("previous_archive")
            row = (
                connection.execute(
                    "SELECT * FROM context_archives WHERE id=?", (previous["archive_id"],)
                ).fetchone()
                if previous is not None
                else None
            )
    except DomainError:
        raise
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as exc:
        raise DomainError("Recall archive integrity failure") from exc


def _load(connection, reference, conversation_id, run_id):
    kind, identity = _ref(reference)
    row = _one(connection, kind, identity)
    _scope(connection, kind, row, conversation_id, run_id)
    if _rows(connection, "SELECT 1 FROM revoked_memory_sources WHERE source_ref=?", (reference,)):
        raise DomainError("Recall source revoked")
    record = {
        name: json.loads(value) if name in _JSON_FIELDS and value is not None else value
        for name, value in row.items()
    }
    if kind in {"trigger", "thread"}:
        source = _one(connection, "source", record["source_id"])
        thread = _one(connection, "thread", record["thread_id"]) if kind == "trigger" else row
        if thread["source_id"] != source["id"] or (
            thread["workspace_id"],
            thread["conversation_id"],
        ) != (source["workspace_id"], source["conversation_id"]):
            raise DomainError("Recall Trigger relationship integrity failure")
    if kind == "receipt" and _hash(record["result_json"]) != row["result_digest"]:
        raise DomainError("Recall receipt integrity failure")
    if kind == "trigger" and _hash(record["payload_json"]) != row["payload_sha256"]:
        raise DomainError("Recall Trigger integrity failure")
    if kind == "archive":
        _verify_archive_dependencies(connection, row)
    return kind, record


def _validate_scope(connection, conversation_id, run_id):
    if not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 128:
        raise ValueError("Invalid recall conversation")
    _one(connection, "conversation", conversation_id)
    if run_id is not None:
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
            raise ValueError("Invalid recall Worker scope")
        owner = _one(connection, "run", run_id)
        if _one(connection, "quest", owner["quest_id"])["conversation_id"] != conversation_id:
            raise DomainError("Recall object unavailable in this scope")


def _relations(connection, kind, record, offset=0):
    links = []

    def add(relation, target):
        try:
            _ref(target)
        except ValueError:
            return
        item = {"relation": relation, "ref": target}
        if item not in links:
            links.append(item)

    for field, target_kind in _DIRECT.items():
        value = record.get(field)
        if isinstance(value, str) and value:
            add(field, f"{target_kind}:{value}")
    for field in ("payload", "metadata", "capsule", "result_json", "payload_json"):
        value = record.get(field)
        if not isinstance(value, dict):
            continue
        for name, target_kind in _DIRECT.items():
            if isinstance(value.get(name), str) and value[name]:
                add(f"{field}.{name}", f"{target_kind}:{value[name]}")
        for target in (
            value.get("source_refs", [])[:32] if isinstance(value.get("source_refs"), list) else []
        ):
            add(f"{field}.source_refs", target)
        for target in (
            value.get("thread_ids", [])[:32] if isinstance(value.get("thread_ids"), list) else []
        ):
            if isinstance(target, str):
                add(f"{field}.thread_ids", f"thread:{target}")
    for target in record.get("source_refs", [])[:32]:
        add("source_refs", target)
    for identity in record.get("related_quest_ids", [])[:32]:
        add("related_quest", f"quest:{identity}")
    reverse = []
    if kind == "conversation":
        reverse = [
            ("quest", "quests", "conversation_id", record["id"]),
            ("thread", "superagent_threads", "conversation_id", record["id"]),
        ]
        add("router", f"router:{record['id']}")
    elif kind == "quest":
        reverse = [
            (target, table, "quest_id", record["id"])
            for target, table in (
                ("worker", "workers"),
                ("plan", "quest_plans"),
                ("event", "events"),
                ("evidence", "evidence"),
                ("approval", "approvals"),
            )
        ]
        for workspace in _rows(connection, "SELECT id FROM workspace WHERE singleton=1"):
            add("workspace", f"workspace:{workspace['id']}")
        for project in _rows(
            connection,
            "SELECT project_id FROM task_project_links WHERE quest_id=?",
            (record["id"],),
        ):
            add("project", f"project:{project['project_id']}")
        reverse.append(("trigger", "superagent_triggers", "quest_id", record["id"]))
        for thread in _rows(
            connection,
            "SELECT DISTINCT s.thread_id FROM superagent_subscriptions s "
            "JOIN worker_runs r ON r.id=s.run_id WHERE r.quest_id=? ORDER BY s.thread_id LIMIT ?",
            (record["id"], offset + 33),
        ):
            add("subscribed_thread", f"thread:{thread['thread_id']}")
    elif kind == "worker":
        reverse = [("run", "worker_runs", "worker_id", record["id"])]
    elif kind == "run":
        reverse = [("intent", "action_intents", "run_id", record["id"])]
        for thread in _rows(
            connection,
            "SELECT thread_id FROM superagent_subscriptions WHERE run_id=? "
            "ORDER BY thread_id LIMIT ?",
            (record["id"], offset + 33),
        ):
            add("subscribed_thread", f"thread:{thread['thread_id']}")
    elif kind == "thread":
        reverse = [("trigger", "superagent_triggers", "thread_id", record["id"])]
    elif kind == "source":
        reverse = [("thread", "superagent_threads", "source_id", record["id"])]
    elif kind == "project":
        for quest in _rows(
            connection,
            "SELECT quest_id FROM task_project_links WHERE project_id=? ORDER BY quest_id LIMIT ?",
            (record["id"], offset + 33),
        ):
            add("project_task", f"quest:{quest['quest_id']}")
    elif kind == "intent":
        reverse = [("receipt", "action_receipts", "intent_id", record["id"])]
    elif kind == "router":
        reverse = [
            ("quest", "quests", "conversation_id", record["conversation_id"]),
            ("archive", "context_archives", "scope_id", record["conversation_id"]),
        ]
    elif kind == "archive":
        # Archives retain original sources, not only summaries. Router source IDs
        # are message identities; expose typed next hops so the Agent can choose
        # the actual originals after a restart. Worker ordinals remain private
        # and use the existing worker_read_context_source permission path.
        if record["scope"] == "router":
            for source in record["source_json"][: offset + 33]:
                add("archived_message", f"message:{source['id']}")
        previous = record["capsule_json"].get("model_handoff", {}).get("previous_archive")
        if isinstance(previous, dict) and type(previous.get("archive_id")) is int:
            add("previous_archive", f"archive:{previous['archive_id']}")
    for target_kind, table, field, identity in reverse:
        for row in _rows(
            connection,
            f"SELECT id FROM {table} WHERE {field}=? ORDER BY rowid DESC LIMIT ?",
            (identity, offset + 33),
        ):
            add(f"related_{target_kind}", f"{target_kind}:{row['id']}")
    return links[offset : offset + 16], len(links) > offset + 16 or any(
        sum(link["relation"] == f"related_{target}" for link in links) >= offset + 33
        for target, *_ in reverse
    )


def _node(connection, reference, conversation_id, run_id, relation_offset=0, project_id=None):
    from .project_continuity import project_record_allowed

    kind, record = _load(connection, reference, conversation_id, run_id)
    if not project_record_allowed(connection, kind, record, project_id):
        raise DomainError("Recall object unavailable in this project context")
    relations, omitted = _relations(connection, kind, record, relation_offset)
    visible = []
    for link in relations:
        try:
            target_kind, target = _load(connection, link["ref"], conversation_id, run_id)
            if not project_record_allowed(connection, target_kind, target, project_id):
                continue
        except (ValueError, KeyError):
            continue
        visible.append(link)
    summary = {
        field: value.encode("utf-8")[:192].decode("utf-8", "ignore")
        if isinstance(value, str)
        else value
        for field, value in record.items()
        if field
        in {
            "title",
            "goal",
            "status",
            "state",
            "role",
            "event_type",
            "kind",
            "statement",
            "tool_name",
            "plan_revision",
            "execution_mode",
            "scope",
        }
    }
    return {
        "ref": reference,
        "kind": kind,
        "summary": summary,
        "source_hash": _hash(record),
        "content_bytes": len(_json(record).encode("utf-8")),
        "trust": "historical record only; model claims and referenced contents remain untrusted; "
        "no current permission or domain correctness is established",
        "relations": visible,
        "relations_omitted": omitted,
        "relation_offset": relation_offset,
        "next_relation_offset": relation_offset + len(relations) if omitted else None,
    }, record


def recall_object(
    connection,
    reference,
    *,
    conversation_id="main",
    run_id=None,
    project_id=None,
    max_bytes=4096,
    offset=0,
    expected_hash=None,
    relation_offset=0,
):
    """Read a bounded UTF-8 slice and typed next references from an existing object."""
    if type(max_bytes) is not int or not 512 <= max_bytes <= 16384:
        raise ValueError("Invalid recall byte bound")
    if type(offset) is not int or offset < 0:
        raise ValueError("Invalid recall offset")
    if type(relation_offset) is not int or not 0 <= relation_offset <= 4096:
        raise ValueError("Invalid recall relation offset")
    _validate_scope(connection, conversation_id, run_id)
    node, record = _node(
        connection, reference, conversation_id, run_id, relation_offset, project_id
    )
    if expected_hash is not None and expected_hash != node["source_hash"]:
        raise DomainError("Recall source changed")
    data = _json(record).encode("utf-8")
    if offset > len(data) or (offset < len(data) and data[offset] & 0xC0 == 0x80):
        raise ValueError("Recall offset is not a UTF-8 boundary")
    # The limit applies to the entire wire result, including metadata and refs.
    node.update(content="", offset=offset, next_offset=offset, complete=False)
    while node["relations"] and len(_json(node).encode("utf-8")) > max_bytes - 128:
        node["relations"].pop()
        node["relations_omitted"] = True
        node["next_relation_offset"] = relation_offset + len(node["relations"])
    room = max_bytes - len(_json(node).encode("utf-8")) - 32
    content = data[offset : offset + max(0, room)].decode("utf-8", "ignore")
    low, high = 0, len(content)
    while low < high:
        middle = (low + high + 1) // 2
        node.update(
            content=content[:middle], next_offset=offset + len(content[:middle].encode("utf-8"))
        )
        if len(_json(node).encode("utf-8")) <= max_bytes:
            low = middle
        else:
            high = middle - 1
    node.update(content=content[:low], next_offset=offset + len(content[:low].encode("utf-8")))
    if not node["content"] and offset < len(data):
        raise ValueError("Recall byte bound cannot fit object metadata")
    node["complete"] = node["next_offset"] == len(data)
    return node


def recall_chain(
    connection,
    references,
    *,
    conversation_id="main",
    run_id=None,
    project_id=None,
    max_depth=2,
    max_nodes=24,
    max_bytes=16384,
):
    if (
        not isinstance(references, (list, tuple))
        or not 1 <= len(references) <= 8
        or type(max_depth) is not int
        or not 0 <= max_depth <= 4
        or type(max_nodes) is not int
        or not 1 <= max_nodes <= 64
        or type(max_bytes) is not int
        or not 1024 <= max_bytes <= 65536
    ):
        raise ValueError("Invalid recall chain bounds")
    for reference in references:
        _ref(reference)
    _validate_scope(connection, conversation_id, run_id)
    queue, visited = deque((reference, 0) for reference in references), set()
    result = {"nodes": [], "incomplete": False, "unavailable": [], "next_refs": []}
    while queue and len(result["nodes"]) < max_nodes:
        reference, depth = queue.popleft()
        if reference in visited:
            continue
        visited.add(reference)
        try:
            node, _ = _node(connection, reference, conversation_id, run_id, project_id=project_id)
        except (ValueError, KeyError):
            result["unavailable"].append(reference)
            continue
        proposal = {**result, "nodes": result["nodes"] + [node]}
        if len(_json(proposal).encode("utf-8")) > max_bytes - 1024:
            queue.appendleft((reference, depth))
            result["incomplete"] = True
            break
        result["nodes"].append(node)
        if node["relations_omitted"]:
            result["incomplete"] = True
        for relation in node["relations"]:
            target = relation["ref"]
            if target not in visited:
                if depth < max_depth:
                    queue.append((target, depth + 1))
                elif target not in result["next_refs"]:
                    result["next_refs"].append(target)
    for reference, _ in queue:
        if reference not in result["next_refs"]:
            result["next_refs"].append(reference)
    result["incomplete"] |= bool(result["next_refs"])
    # Continuations are hints; callers can always use each node's stable refs.
    result["next_refs"] = result["next_refs"][:64]
    while len(_json(result).encode("utf-8")) > max_bytes:
        if result["next_refs"]:
            result["next_refs"].pop()
        elif result["unavailable"]:
            result["unavailable"].pop()
        elif result["nodes"]:
            result["nodes"].pop()
        result["incomplete"] = True
    return result
