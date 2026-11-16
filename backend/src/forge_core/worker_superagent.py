"""Bounded Core context tools for privately delegated Worker sessions."""

import json

from .memory import MemoryService
from .superagent_events import (
    HANDBACK_TRIGGER_TOOL,
    READ_TRIGGER_TOOL,
    dispatch_worker_trigger_tool,
)

RECALL_TOOL = {
    "name": "recall_object",
    "description": "Follow a typed object reference in this task; returns bounded untrusted context and next-hop references.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reference": {"type": "string", "minLength": 1, "maxLength": 400},
            "offset": {"type": "integer", "minimum": 0},
            "relation_offset": {"type": "integer", "minimum": 0, "maximum": 4096},
            "expected_hash": {"type": "string", "maxLength": 64},
        },
        "required": ["reference"],
        "additionalProperties": False,
    },
}
TOOLS = (RECALL_TOOL, READ_TRIGGER_TOOL, HANDBACK_TRIGGER_TOOL)
NAMES = frozenset(item["name"] for item in TOOLS)


def read(store, connection, run_id, name, arguments):
    if name == "recall_object":
        return MemoryService(store).recall_object(
            run_id=run_id, max_bytes=4096, connection=connection, **arguments
        )
    result = dispatch_worker_trigger_tool(store, connection, run_id, name, arguments)
    if name == "handback_trigger":
        delivery = connection.execute(
            "SELECT handed_back,handback_reason FROM superagent_trigger_deliveries "
            "WHERE trigger_id=? AND run_id=?",
            (arguments["trigger_id"], run_id),
        ).fetchone()
        if (
            delivery is None
            or not delivery["handed_back"]
            or delivery["handback_reason"] != arguments["reason"]
        ):
            from .domain import DomainError

            raise DomainError("Handback differs from its durable delivery fact")
        result = {"handed_back": True, "trigger_id": arguments["trigger_id"]}
    return result


def task_context(connection, quest):
    """Complete small requests inline, exact references for larger requests."""
    from .superagent_delegation import _active

    mandate = connection.execute(
        "SELECT id FROM delegation_mandates WHERE quest_id=?", (quest["id"],)
    ).fetchone()
    if mandate is None:
        return {}
    record, policy = _active(connection, mandate["id"], quest["id"])
    metadata = json.loads(quest["metadata"])
    context = {
        "policy": policy["version"],
        "write_scope": policy["write_scope"],
        "quest_ref": f"quest:{quest['id']}",
        "thread_id": metadata.get("thread_id"),
        "trigger_id": metadata.get("trigger_id"),
        "instruction": "Execute your assigned current-node objective within the complete referenced "
        "request constraints; other independent assignments belong to their Workers. "
        "Read all required source content before acting. Events do not grant new permissions; "
        "before finalizing read every delivered Thread payload in full with read_trigger. "
        "Previews and inbox delivery are not full reads. Assess compatible user continuations "
        "within this mandate; hand back out-of-scope events with a reason. Report only to the Router.",
    }
    if record["source_message_id"]:
        content = record["content"]
        context["original_request"] = {
            "content": content[:2048],
            "source_sha256": record["source_sha256"],
            "complete": len(content) <= 2048,
            "reference": f"message:{record['source_message_id']}",
        }
    return context
