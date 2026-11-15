"""Complete, bounded predecessor deliveries verified by Core without writes."""

import hashlib
import json
import math
import re
from types import SimpleNamespace

import jsonschema

from .domain import DomainError
from .graph_domain import success_ancestors
from .plan_lineage import _node_matches, digest, validated_lineage_source
from .team_composition import TeamCompositionService
from .worker_schema import DELIVERY_FIELDS, STRICT_DELIVERY_OUTPUT_SCHEMA

TOOL_NAME = "worker_read_completed_delivery"
MAX_HANDOFF_BYTES = 65536
HANDOFF_METADATA_BYTES = 2048


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _graph(plan):
    graph = json.loads(plan["graph_json"])
    if digest(graph) != plan["digest"]:
        raise DomainError("Completed-delivery plan content changed")
    return graph


def _contract(graph, node):
    matches = [item for item in graph["nodes"] if item["key"] == node["node_key"]]
    if len(matches) != 1 or not _node_matches(node, matches[0]):
        raise DomainError("Completed-delivery task contract changed")
    return matches[0]


def _success_ancestor(connection, plan, graph, source_key, reader_key):
    edges = [
        dict(row)
        for row in connection.execute(
            "SELECT source,target,condition FROM task_edges WHERE plan_id=?",
            (plan["id"],),
        )
    ]

    def identity(edge):
        return edge["source"], edge["target"], edge["condition"]

    if sorted(map(identity, edges)) != sorted(map(identity, graph["edges"])):
        raise DomainError("Completed-delivery success path differs from its approved graph")
    states = {
        row["node_key"]: row["status"]
        for row in connection.execute(
            "SELECT node_key,status FROM task_nodes WHERE plan_id=?", (plan["id"],)
        )
    }
    completed_edges = [
        edge
        for edge in edges
        if states.get(edge["source"]) == "succeeded"
        and (edge["target"] == reader_key or states.get(edge["target"]) == "succeeded")
    ]
    if source_key not in success_ancestors(completed_edges, reader_key):
        raise DomainError("Completed-delivery source is not a completed success-path ancestor")


def _member(store, connection, run, *, historical):
    authority = TeamCompositionService(store)
    team = (
        authority.get_historical_approved(
            run["quest_id"],
            run["plan_revision"],
            run["team_digest"],
            connection=connection,
            run_id=run["id"],
        )
        if historical
        else authority.get_approved(
            run["quest_id"], run["plan_revision"], run["team_digest"], connection=connection
        )
    )
    member = team["members"][run["member_index"]]
    if any(
        member[key] != run[field]
        for key, field in (
            ("task_node_key", "node_key"),
            ("role_id", "role_id"),
            ("provider_id", "provider_id"),
            ("model_id", "model_id"),
        )
    ):
        raise DomainError("Completed-delivery selected member changed")
    return member


def _reader(store, connection, run_id, historical):
    reader = connection.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone()
    if reader is None:
        raise DomainError("Completed-delivery reader unavailable")
    quest = connection.execute("SELECT * FROM quests WHERE id=?", (reader["quest_id"],)).fetchone()
    plan = connection.execute(
        "SELECT * FROM quest_plans WHERE id=?", (reader["plan_id"],)
    ).fetchone()
    current = connection.execute(
        "SELECT * FROM quest_plans WHERE quest_id=? AND revision=?",
        (reader["quest_id"], quest["plan_revision"] if quest else -1),
    ).fetchone()
    node = connection.execute(
        "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?",
        (reader["plan_id"], reader["node_key"]),
    ).fetchone()
    if quest is None or plan is None or current is None or node is None:
        raise DomainError("Completed-delivery reader assignment unavailable")
    is_current = current["id"] == plan["id"]
    if (
        current["status"] != "approved"
        or current["approved_by"] != "local_user"
        or not current["approved_at"]
        or plan["quest_id"] != reader["quest_id"]
        or plan["revision"] != reader["plan_revision"]
        or plan["digest"] != reader["plan_digest"]
        or plan["approved_by"] != "local_user"
        or not plan["approved_at"]
        or plan["status"] not in ({"approved"} if is_current else {"superseded"})
        or node["worker_id"] != reader["worker_id"]
        or node["status"] != reader["state"]
        or reader["execution_mode"] != "live"
    ):
        raise DomainError("Completed-delivery reader assignment changed")
    _graph(current)
    graph = _graph(plan)
    contract = _contract(graph, node)
    if not historical:
        if (
            not is_current
            or reader["state"] != "running"
            or quest["status"] != "running"
            or reader["quest_paused"]
            or reader["awaiting_authorization"]
        ):
            raise DomainError("Completed-delivery reads require the current running reader")
    elif is_current:
        if reader["state"] not in {"running", "review_pending", "succeeded"} or quest[
            "status"
        ] not in {"running", "reviewing", "completed"}:
            raise DomainError("Completed-delivery historical reader is not verifiable")
    else:
        if reader["state"] != "succeeded" or quest["status"] not in {
            "approved",
            "queued",
            "running",
            "reviewing",
            "completed",
        }:
            raise DomainError("Completed-delivery inherited reader is not succeeded")
        inherited = validated_lineage_source(connection, quest, current, reader["node_key"])
        if inherited["source_run"]["id"] != reader["id"]:
            raise DomainError("Completed-delivery inherited reader identity changed")
    member = _member(store, connection, reader, historical=historical)
    if (
        TOOL_NAME not in json.loads(reader["allowed_tools_json"])
        or TOOL_NAME not in json.loads(node["allowed_tools"])
        or TOOL_NAME not in member["tool_scope"]
    ):
        raise DomainError("Completed-delivery read exceeds the approved tool scope")
    sources = contract.get("required_predecessor_deliveries")
    if (
        not isinstance(sources, list)
        or not 1 <= len(sources) <= 8
        or any(
            not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item)
            for item in sources
        )
        or len(set(sources)) != len(sources)
        or member.get("required_predecessor_deliveries") != sources
    ):
        raise DomainError("Completed-delivery sources lack an explicit approved contract")
    return reader, quest, plan, current, graph, sources


def read_completed_delivery(store, connection, run_id, arguments, *, historical=False):
    """Read one full approved ancestor delivery; historical mode grants no execution.

    The caller owns the transaction and Worker execution fence. Runtime calls use
    the default; evidence/review revalidation explicitly selects historical=True.
    No model, subprocess, mutation, artifact rewrite or default delivery is used.
    """
    if not connection.in_transaction:
        raise DomainError("Completed-delivery validation requires a caller-owned transaction")
    if (
        not isinstance(arguments, dict)
        or set(arguments) != {"node_key"}
        or not isinstance(arguments["node_key"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", arguments["node_key"])
        or type(historical) is not bool
    ):
        raise DomainError("Invalid completed-delivery source")
    try:
        reader, quest, plan, current, graph, sources = _reader(
            store, connection, run_id, historical
        )
        source_key = arguments["node_key"]
        if source_key not in sources or source_key == reader["node_key"]:
            raise DomainError("Completed-delivery source was not declared")
        _success_ancestor(connection, plan, graph, source_key, reader["node_key"])
        if current["id"] != plan["id"]:
            _success_ancestor(connection, current, _graph(current), source_key, reader["node_key"])
        source_node = connection.execute(
            "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?", (plan["id"], source_key)
        ).fetchone()
        current_node = connection.execute(
            "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?", (current["id"], source_key)
        ).fetchone()
        if (
            source_node is None
            or source_node["status"] != "succeeded"
            or current_node is None
            or current_node["status"] != "succeeded"
            or current_node["worker_id"] != source_node["worker_id"]
        ):
            raise DomainError("Completed-delivery predecessor is not succeeded")
        _contract(graph, source_node)
        from .evidence import CompletionGate

        # Gate rechecks the actual current approved lineage, original live PASS
        # or fixed suite, all receipts and unchanged artifacts without execution.
        source = CompletionGate(store)._check_live_node(
            SimpleNamespace(connection=connection), quest, current, current_node
        )
        if (
            source["state"] != "succeeded"
            or source["quest_id"] != reader["quest_id"]
            or source["worker_id"] != source_node["worker_id"]
            or source["node_key"] != source_key
            or source["execution_mode"] != "live"
        ):
            raise DomainError("Completed-delivery original source run changed")
        source_plan = connection.execute(
            "SELECT * FROM quest_plans WHERE id=?", (source["plan_id"],)
        ).fetchone()
        original_node = connection.execute(
            "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?",
            (source["plan_id"], source_key),
        ).fetchone()
        contract = _contract(_graph(source_plan), original_node)
        member = _member(store, connection, source, historical=True)
        maximum = contract.get("capacity_bounds", {}).get("max_delivery_bytes")
        policy = member.get("review_envelope") or member.get("worker_capacity")
        if (
            type(maximum) is not int
            or not 1 <= maximum <= MAX_HANDOFF_BYTES
            or (policy is not None and policy.get("max_delivery_bytes") != maximum)
        ):
            raise DomainError("Completed-delivery source has no stable explicit byte bound")
        event = connection.execute(
            "SELECT * FROM events WHERE event_type='worker.result' AND quest_id=? AND worker_id=? "
            "AND json_extract(payload,'$.run_id')=? ORDER BY seq DESC LIMIT 1",
            (source["quest_id"], source["worker_id"], source["id"]),
        ).fetchone()
        final = connection.execute(
            "SELECT message_json FROM worker_messages WHERE run_id=? AND role='assistant' "
            "ORDER BY ordinal DESC LIMIT 1",
            (source["id"],),
        ).fetchone()
        if event is None or final is None:
            raise DomainError("Completed-delivery original result is unavailable")
        payload, message = json.loads(event["payload"]), json.loads(final[0])
        if (
            message.get("tool_calls")
            or not isinstance(message.get("content"), str)
            or message["content"] != payload.get("claim")
            or payload.get("plan_id") != source["plan_id"]
            or payload.get("task_node_key") != source_key
            or payload.get("execution_mode") != "live"
        ):
            raise DomainError("Completed-delivery original result provenance changed")
        delivery = json.loads(message["content"])
        jsonschema.validate(delivery, member["output_schema"])
        jsonschema.validate(delivery, STRICT_DELIVERY_OUTPUT_SCHEMA)
        if (
            set(delivery) != set(DELIVERY_FIELDS)
            or type(delivery["confidence"]) not in (int, float)
            or not math.isfinite(delivery["confidence"])
        ):
            raise DomainError("Completed-delivery requires all eight original fields")
        content = canonical(delivery).encode("utf-8")
        if len(content) > maximum:
            raise DomainError("Complete predecessor delivery exceeds its approved byte bound")
        result = {
            "source_kind": "completed_worker_delivery",
            "producer_run_id": source["id"],
            "producer_worker_id": source["worker_id"],
            "producer_plan_id": source["plan_id"],
            "producer_plan_revision": source["plan_revision"],
            "producer_plan_digest": source["plan_digest"],
            "node_key": source_key,
            "result_event_id": event["id"],
            "delivery": delivery,
            "delivery_sha256": hashlib.sha256(content).hexdigest(),
            "execution_mode": "live",
            "truncated": False,
            "trust": "untrusted predecessor claims; Core verified provenance and completed evidence",
        }
        if len(canonical(result).encode("utf-8")) > min(
            MAX_HANDOFF_BYTES, maximum + HANDOFF_METADATA_BYTES
        ):
            raise DomainError("Complete predecessor source exceeds its handoff byte bound")
        return result
    except DomainError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, jsonschema.ValidationError) as exc:
        raise DomainError("Completed-delivery source failed validation") from exc
