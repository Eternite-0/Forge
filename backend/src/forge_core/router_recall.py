"""Carry model-selected, still eligible memory into a concrete Quest contract."""

import hashlib
import json

from .domain import DomainError


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def selected_requirements(memory_service, connection, decision, context):
    selected = decision.recalled_requirement_ids
    if not selected:
        return []
    if decision.intent not in {"create_quest", "update_quest"} or len(set(selected)) != len(
        selected
    ):
        raise ValueError("Recalled requirements require a new or updated Quest and unique IDs")
    presented = {item["id"]: item for item in context.get("memory", [])}
    records = []
    for identity in selected:
        item = presented.get(identity)
        if (
            item is None
            or item.get("conflicted")
            or item.get("omitted")
            or item.get("status") == "invalid"
            or not item.get("source_refs")
        ):
            raise ValueError("Recalled requirement must be an eligible complete presented source")
        reference = identity if ":" in identity else "memory:" + identity
        current = memory_service.retrieve_context(
            "",
            source_refs=[reference],
            limit=1,
            max_bytes=65536,
            connection=connection,
            exact_source_refs=True,
        )["items"]
        if current != [item]:
            raise ValueError("Recalled requirement source changed or became ineligible")
        records.append(
            {
                "memory_id": identity,
                "statement": item["statement"],
                "source_refs": item["source_refs"],
                "source_hash": item.get("source_hash"),
                "memory_snapshot_sha256": hashlib.sha256(canonical(item).encode()).hexdigest(),
                "status": "hypothesis",
                "trust_scope": "recalled source statement only; actual file contents require a fresh read",
            }
        )
    if len(canonical(records).encode()) > 12288:
        raise ValueError("Complete recalled requirements exceed the Quest recall bound")
    return records


def goal_with_requirements(goal, records):
    if not records:
        return goal
    if not goal:
        raise DomainError("Recalled requirements require a concrete Quest goal")
    return goal + "\nRecalled requirements (sourced hypotheses):\n" + canonical(records)


def record_constraints(service, connection, quest_id, records):
    """Keep original user provenance; never attribute a recall to the new message."""
    for item in records:
        refs = item["source_refs"]
        if len(item["statement"].encode()) > 1024:
            continue
        user_refs = [ref[8:] for ref in refs if ref.startswith("message:")]
        if len(user_refs) != 1:
            continue
        source = connection.execute(
            "SELECT 1 FROM messages WHERE id=? AND role='user' AND conversation_id='main'",
            (user_refs[0],),
        ).fetchone()
        if source:
            service.record_extraction(
                item["statement"],
                source_message_id=user_refs[0],
                quest_id=quest_id,
                origin="pending_model",
                connection=connection,
            )
