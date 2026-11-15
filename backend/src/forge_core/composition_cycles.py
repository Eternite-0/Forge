"""Durable independently measured Composer cycles for exact Worker help events."""

import copy
import hashlib
import json

from .composer_request import canonical
from .composition_capacity import _attempt_floor, _typed_measurement
from .domain import DomainError, ForgeEvent

VERSION = "bounded-composer-reassignment-v1"


def _digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _context(connection, quest, plan, event_id):
    event = connection.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if (
        event is None
        or event["event_type"] != "worker.help_requested"
        or event["quest_id"] != quest["id"]
    ):
        raise DomainError("Reassignment cycle requires its original Worker help event")
    payload = json.loads(event["payload"])
    run = connection.execute(
        "SELECT * FROM worker_runs WHERE id=? AND quest_id=?", (payload.get("run_id"), quest["id"])
    ).fetchone()
    if (
        run is None
        or run["state"] not in {"blocked", "failed", "cancelled"}
        or run["worker_id"] != event["worker_id"]
        or payload.get("plan_id") != plan["id"]
        or payload.get("task_node_key") != run["node_key"]
        or run["plan_id"] != plan["id"]
        or run["plan_revision"] != plan["revision"]
        or run["plan_digest"] != plan["digest"]
        or quest["plan_revision"] != plan["revision"]
        or plan["status"] != "approved"
    ):
        raise DomainError("Reassignment cycle Worker or approved plan binding changed")
    if (
        connection.execute(
            "SELECT 1 FROM model_budget_attempts WHERE worker_run_id=? AND "
            "(state!='settled' OR input_charged IS NULL OR output_charged IS NULL) LIMIT 1",
            (run["id"],),
        ).fetchone()
        or connection.execute(
            "SELECT 1 FROM worker_model_reservations WHERE run_id=? AND state='reserved' LIMIT 1",
            (run["id"],),
        ).fetchone()
    ):
        raise DomainError("Reassignment requires settled Worker model calls")
    graph = json.loads(plan["graph_json"])
    if _digest(graph) != plan["digest"]:
        raise DomainError("Reassignment approved plan content changed")
    completed = {
        row[0]
        for row in connection.execute(
            "SELECT node_key FROM task_nodes WHERE plan_id=? AND status='succeeded'", (plan["id"],)
        )
    }
    pending = sorted(node["key"] for node in graph["nodes"] if node["key"] not in completed)
    if run["node_key"] not in pending:
        raise DomainError("Reassignment target no longer owns pending work")
    # The full approved graph remains present. This view marks only real completed nodes.
    graph = copy.deepcopy(graph)
    graph["capacity_contract"]["preserved_completed_keys"] = sorted(completed)
    binding = {
        "quest_id": quest["id"],
        "plan_id": plan["id"],
        "plan_digest": plan["digest"],
        "plan_revision": plan["revision"],
        "source_event_id": event_id,
        "source_payload_sha256": _digest(payload),
        "run_id": run["id"],
        "worker_id": run["worker_id"],
        "pending_node_keys": pending,
    }
    help_request = {"source_event_id": event_id, "request": payload}
    return binding, graph, help_request


def _record(connection, quest_id, event_id):
    rows = connection.execute(
        "SELECT payload FROM events WHERE quest_id=? AND event_type='composer.reassignment_measured' "
        "AND json_extract(payload,'$.binding.source_event_id')=? ORDER BY seq",
        (quest_id, event_id),
    ).fetchall()
    if len(rows) > 1:
        raise DomainError("Duplicate reassignment composition cycle")
    return json.loads(rows[0][0]) if rows else None


def validate_cycle(store, connection, quest, plan, event_id, provider, measurer, *, cycle_id=None):
    binding, graph, help_request = _context(connection, quest, plan, event_id)
    record = _record(connection, quest["id"], event_id)
    if record is None or set(record) != {
        "version",
        "binding",
        "measurement",
        "attempt_rowid_floor",
        "cycle_id",
    }:
        raise DomainError("Reassignment lacks its measured composition cycle")
    if record["version"] != VERSION or record["binding"] != binding:
        raise DomainError("Reassignment composition cycle binding changed")
    measured = _typed_measurement(record["measurement"])
    identity = _digest({"version": VERSION, "binding": binding, "measurement": measured})
    if record["cycle_id"] != identity or (cycle_id is not None and cycle_id != identity):
        raise DomainError("Reassignment composition cycle identity changed")
    floor = record["attempt_rowid_floor"]
    if type(floor) is not int or not 0 <= floor <= _attempt_floor(connection):
        raise DomainError("Invalid reassignment composition attempt floor")
    if (
        provider is None
        or measurer is None
        or _typed_measurement(
            measurer(store, connection, quest, graph, provider, help_request=help_request)
        )
        != measured
    ):
        raise DomainError("Reassignment native composition request binding changed")
    return record, graph, help_request


def bind_cycle(work, store, quest, plan, event_id, provider, measurer):
    if _record(work.connection, quest["id"], event_id) is None:
        binding, graph, help_request = _context(work.connection, quest, plan, event_id)
        measured = _typed_measurement(
            measurer(store, work.connection, quest, graph, provider, help_request=help_request)
        )
        identity = _digest({"version": VERSION, "binding": binding, "measurement": measured})
        record = {
            "version": VERSION,
            "binding": binding,
            "measurement": measured,
            "attempt_rowid_floor": _attempt_floor(work.connection),
            "cycle_id": identity,
        }
        work.append_event(
            ForgeEvent(
                event_type="composer.reassignment_measured",
                quest_id=quest["id"],
                worker_id=binding["worker_id"],
                payload=record,
            )
        )
    return validate_cycle(store, work.connection, quest, plan, event_id, provider, measurer)


def cycle_state(connection, record):
    measured, binding = record["measurement"], record["binding"]
    rows = connection.execute(
        "SELECT call_id,state,input_charged,output_charged FROM model_budget_attempts "
        "WHERE rowid>? AND quest_id=? AND provider_id=? AND model_id=? "
        "AND parent_call_id=? AND purpose='team_composer' ORDER BY rowid",
        (
            record["attempt_rowid_floor"],
            binding["quest_id"],
            measured["provider_id"],
            measured["model_id"],
            record["cycle_id"],
        ),
    ).fetchall()
    calls = {}
    for row in rows:
        calls.setdefault(row["call_id"], []).append(row)
    if len(calls) > 2 or any(len(attempts) != 1 for attempts in calls.values()):
        raise DomainError(
            "Reassignment permits only one initial call and one repair without retries"
        )
    settled = sum(
        row["state"] == "settled"
        and type(row["input_charged"]) is int
        and type(row["output_charged"]) is int
        for row in rows
    )
    accepted = connection.execute(
        "SELECT 1 FROM team_proposals WHERE quest_id=? AND plan_digest=? "
        "AND json_extract(body_json,'$.composition_cycle_id')=? LIMIT 1",
        (binding["quest_id"], binding["plan_digest"], record["cycle_id"]),
    ).fetchone()
    return {
        "settled_composer_calls": settled,
        "unresolved_composer_calls": len(rows) - settled,
        "accepted_semantic_team": bool(accepted),
    }


def validate_proposal_cycle(store, connection, quest, plan, body, provider, measurer):
    rows = connection.execute(
        "SELECT payload FROM events WHERE quest_id=? AND event_type='composer.reassignment_measured' "
        "AND json_extract(payload,'$.cycle_id')=?",
        (quest["id"], body["composition_cycle_id"]),
    ).fetchall()
    if len(rows) != 1:
        raise DomainError("Team reassignment lacks its exact composition cycle")
    record = json.loads(rows[0][0])
    cycle, _, _ = validate_cycle(
        store,
        connection,
        quest,
        plan,
        record["binding"]["source_event_id"],
        provider,
        measurer,
        cycle_id=body["composition_cycle_id"],
    )
    if cycle["binding"]["run_id"] != body["reassignment"]["run_id"] or {
        item["task_node_key"] for item in body["members"]
    } != set(cycle["binding"]["pending_node_keys"]):
        raise DomainError("Reassignment team differs from its exact composition cycle")
    state = cycle_state(connection, cycle)
    if state["unresolved_composer_calls"] or not 1 <= state["settled_composer_calls"] <= 2:
        raise DomainError("Reassignment composition calls must be fully settled before approval")
