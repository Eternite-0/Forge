"""Core-minted historical read proofs; these never substitute for action receipts."""

import hashlib
import json

import jsonschema

from .domain import DomainError, new_id, now_iso
from .recall_chain import _hash, _load, _validate_scope
from .superagent_events import TriggerService
from .worker_superagent import NAMES, TOOLS

_KIND = "worker_core_source"
_VERSION = "worker_core_source_v1"
# Keep immutable identity/content bindings, while allowing normal lifecycle updates.
# The exact historical wire result is separately bound by the Core proof below.
_MUTABLE = {
    "quest": {
        "title",
        "goal",
        "status",
        "priority",
        "updated_at",
        "metadata",
        "version",
        "plan_revision",
    },
    "worker": {"status", "metadata"},
    "run": {"state", "generation", "steps", "token_spent", "tool_spent"},
    "plan": {"status"},
    "approval": {"status", "decided_at", "decided_by", "consumed_at"},
    "intent": {"state"},
    "memory": {"status", "valid_until"},
    "trigger": {"status", "reason", "quest_id"},
    "router": {"event_cursor", "capsule"},
    "project": {"name", "markers_json", "detected_at"},
}


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _owner(connection, run_id):
    row = connection.execute(
        "SELECT r.*,q.conversation_id FROM worker_runs r JOIN quests q ON q.id=r.quest_id WHERE r.id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise DomainError("Core source Worker is unavailable")
    return row


def _trigger_source(store, connection, run, arguments, *, handback=False):
    service = TriggerService(store)
    trigger = service._record(connection, arguments["trigger_id"])
    service._run(connection, run["id"], trigger["thread_id"])
    reference = f"trigger:{trigger['id']}"
    kind, record = _load(connection, reference, run["conversation_id"], run["id"])
    delivery = connection.execute(
        "SELECT * FROM superagent_trigger_deliveries WHERE trigger_id=? AND run_id=?",
        (trigger["id"], run["id"]),
    ).fetchone()
    inbox = connection.execute(
        "SELECT payload_json FROM worker_inbox WHERE run_id=? AND event_id=?",
        (run["id"], trigger["event_id"]),
    ).fetchone()
    event = connection.execute("SELECT * FROM events WHERE id=?", (trigger["event_id"],)).fetchone()
    if delivery is None or inbox is None or event is None:
        raise DomainError("Core source requires actual Trigger delivery")
    envelope, payload = json.loads(inbox["payload_json"]), json.loads(event["payload"])
    if (
        event["event_type"] != "trigger.received"
        or event["conversation_id"] != run["conversation_id"]
        or payload.get("trigger_id") != trigger["id"]
        or payload.get("thread_id") != trigger["thread_id"]
        or envelope.get("kind") != "thread_trigger"
        or any(
            envelope.get(key) != trigger[key] for key in ("thread_id", "event_id", "payload_sha256")
        )
        or envelope.get("trigger_id") != trigger["id"]
    ):
        raise DomainError("Core source Trigger delivery provenance differs")
    if handback and (
        delivery["handed_back"] != 1 or delivery["handback_reason"] != arguments["reason"]
    ):
        raise DomainError("Core source handback fact differs")
    if handback:
        facts = connection.execute(
            "SELECT payload FROM events WHERE event_type='trigger.delivery_handed_back' "
            "AND quest_id=? AND worker_id=? AND json_extract(payload,'$.run_id')=? "
            "AND json_extract(payload,'$.trigger_id')=?",
            (run["quest_id"], run["worker_id"], run["id"], trigger["id"]),
        ).fetchall()
        if len(facts) != 1 or json.loads(facts[0]["payload"]) != {
            "trigger_id": trigger["id"],
            "thread_id": trigger["thread_id"],
            "run_id": run["id"],
            "reason": arguments["reason"],
        }:
            raise DomainError("Core source handback event differs")
    return reference, kind, record


def _source(store, connection, run, name, arguments):
    _validate_scope(connection, run["conversation_id"], run["id"])
    if name != "recall_object":
        return _trigger_source(
            store, connection, run, arguments, handback=name == "handback_trigger"
        )
    reference = arguments["reference"]
    kind, record = _load(connection, reference, run["conversation_id"], run["id"])
    if kind == "trigger":
        _trigger_source(store, connection, run, {"trigger_id": record["id"]})
    return reference, kind, record


def _binding(kind, record):
    return _hash({key: value for key, value in record.items() if key not in _MUTABLE.get(kind, ())})


def _proof(run, call, arguments, result, reference, kind, record):
    output = json.loads(result)
    if call["tool_name"] == "recall_object":
        page = {key: output[key] for key in ("offset", "next_offset", "complete", "content_bytes")}
    elif call["tool_name"] == "read_trigger":
        page = {key: output[key] for key in ("offset_chars", "next_offset_chars", "total_chars")}
    else:
        page = None
    return {
        "version": _VERSION,
        **{
            key: run[key]
            for key in ("quest_id", "worker_id", "plan_id", "plan_revision", "execution_mode")
        },
        "run_id": run["id"],
        **{key: call[key] for key in ("call_id", "model_call_id", "tool_name")},
        "arguments_sha256": _digest(_json(arguments)),
        "result_sha256": _digest(result),
        "reference": reference,
        "source_sha256": _hash(record),
        "source_binding_sha256": _binding(kind, record),
        "page": page,
    }


def checkpoint(store, connection, run_id, call, result):
    """Called only inside the fenced transaction that persists success + transcript."""
    if call["tool_name"] not in NAMES or call["run_id"] != run_id:
        raise DomainError("Invalid Core source checkpoint")
    run, arguments = _owner(connection, run_id), json.loads(call["arguments_json"])
    definition = next(item for item in TOOLS if item["name"] == call["tool_name"])
    jsonschema.validate(arguments, definition["input_schema"])
    reference, kind, record = _source(store, connection, run, call["tool_name"], arguments)
    output = json.loads(result)
    if call["tool_name"] == "recall_object" and (
        output["ref"] != reference or output["source_hash"] != _hash(record)
    ):
        raise DomainError("Core recall source changed before checkpoint")
    proof = _proof(run, call, arguments, result, reference, kind, record)
    connection.execute(
        "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
        (new_id("evidence"), run["quest_id"], _KIND, 1, _json(proof), now_iso()),
    )


def verify(store, connection, run_id, call):
    """Read-only: verify a historical checkpoint and current scope/integrity."""
    try:
        if (
            call["tool_name"] not in NAMES
            or call["run_id"] != run_id
            or call["state"] != "succeeded"
            or call["receipt_id"] is not None
        ):
            return False
        run, arguments = _owner(connection, run_id), json.loads(call["arguments_json"])
        definition = next(item for item in TOOLS if item["name"] == call["tool_name"])
        jsonschema.validate(arguments, definition["input_schema"])
        reference, kind, record = _source(store, connection, run, call["tool_name"], arguments)
        expected = _proof(run, call, arguments, call["result_json"], reference, kind, record)
        rows = connection.execute(
            "SELECT payload FROM evidence WHERE quest_id=? AND kind=? AND trusted=1 "
            "AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.call_id')=?",
            (run["quest_id"], _KIND, run_id, call["call_id"]),
        ).fetchall()
        if len(rows) != 1:
            return False
        saved = json.loads(rows[0]["payload"])
        # Dynamic records may advance after a read. Their Core-minted historical
        # hash remains bound to the result, while stable identity/content must match.
        if kind in _MUTABLE:
            expected["source_sha256"] = saved.get("source_sha256")
        if saved != expected:
            return False
        output = json.loads(call["result_json"])
        if call["tool_name"] == "recall_object":
            if (
                output.get("ref") != reference
                or output.get("source_hash") != saved["source_sha256"]
            ):
                return False
            for relation in output.get("relations", []):
                _load(connection, relation["ref"], run["conversation_id"], run_id)
        elif call["tool_name"] == "handback_trigger" and output != {
            "handed_back": True,
            "trigger_id": arguments["trigger_id"],
        }:
            return False
        return True
    except (DomainError, KeyError, TypeError, ValueError, jsonschema.ValidationError):
        return False
