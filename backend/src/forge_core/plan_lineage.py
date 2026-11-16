"""Verified completed-node lineage across separately approved remaining DAGs."""

import hashlib
import json

from .domain import DomainError


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _approved_authority(connection, quest_id, row, kind="plan"):
    from .superagent_delegation import validate_contract_authority

    validate_contract_authority(
        connection,
        quest_id,
        row["approved_by"],
        kind,
        row["revision"] if kind == "plan" else row["plan_revision"],
        row["digest"] if kind == "plan" else row["proposal_digest"],
    )


def request_binding(quest):
    metadata = (
        json.loads(quest["metadata"]) if isinstance(quest["metadata"], str) else quest["metadata"]
    )
    return {
        key: metadata.get(key)
        for key in (
            "original_request_message_id",
            "original_request_sha256",
            "goal_update_message_id",
            "goal_update_sha256",
        )
    }


def _graph(plan):
    graph = json.loads(plan["graph_json"])
    if digest(graph) != plan["digest"]:
        raise DomainError("Lineage plan content digest mismatch")
    return graph


def _node_matches(row, contract):
    return row is not None and all(
        (
            row["objective"] == contract["objective"],
            json.loads(row["criteria_json"]) == contract["success_criteria"],
            row["budget_tokens"] == contract["budget_tokens"],
            json.loads(row["required_capabilities"]) == contract["required_capabilities"],
            row["expected_artifact"] == contract.get("expected_artifact"),
            row["max_attempts"] == contract.get("max_attempts", 1),
            json.loads(row["allowed_tools"]) == contract.get("allowed_tools", []),
        )
    )


def _source(connection, quest, newplan, node_key, *, require_approved):
    graph = _graph(newplan)
    lineage = graph.get("revision_lineage")
    if not isinstance(lineage, dict) or lineage.get("version") != "remaining-dag-v1":
        raise DomainError("Completed revision lineage unavailable")
    if require_approved and (
        newplan["status"] != "approved" or quest["plan_revision"] != newplan["revision"]
    ):
        raise DomainError("Current approved revision lineage required")
    if require_approved and (not newplan["approved_by"] or not newplan["approved_at"]):
        raise DomainError("Current lineage plan lacks user approval")
    if require_approved:
        _approved_authority(connection, quest["id"], newplan)
    if lineage.get("quest_goal_sha256") != hashlib.sha256(
        quest["goal"].encode()
    ).hexdigest() or lineage.get("request_binding") != request_binding(quest):
        raise DomainError("Lineage original goal/source binding changed")
    binding = request_binding(quest)
    for key, hash_key in (
        ("original_request_message_id", "original_request_sha256"),
        ("goal_update_message_id", "goal_update_sha256"),
    ):
        if binding[key]:
            source = connection.execute(
                "SELECT content FROM messages WHERE id=? AND role='user' AND conversation_id='main'",
                (binding[key],),
            ).fetchone()
            if (
                source is None
                or hashlib.sha256(source[0].encode()).hexdigest() != binding[hash_key]
            ):
                raise DomainError("Lineage original request content integrity failed")
    entries = [
        item for item in lineage.get("completed_nodes", []) if item.get("node_key") == node_key
    ]
    if len(entries) != 1:
        raise DomainError("Completed lineage identity is ambiguous")
    entry = entries[0]
    source_plan = connection.execute(
        "SELECT * FROM quest_plans WHERE id=? AND quest_id=?",
        (entry["source_plan_id"], quest["id"]),
    ).fetchone()
    if (
        source_plan is None
        or source_plan["revision"] != entry["source_revision"]
        or source_plan["digest"] != entry["source_plan_digest"]
        or not source_plan["approved_at"]
        or not source_plan["approved_by"]
    ):
        raise DomainError("Completed lineage source approval is invalid")
    _approved_authority(connection, quest["id"], source_plan)
    source_graph = _graph(source_plan)
    if graph.get("execution_constraints") != source_graph.get("execution_constraints"):
        raise DomainError("Completed lineage execution constraints changed")
    old_nodes = [item for item in source_graph["nodes"] if item["key"] == node_key]
    new_nodes = [item for item in graph["nodes"] if item["key"] == node_key]
    if (
        len(old_nodes) != 1
        or len(new_nodes) != 1
        or old_nodes[0] != new_nodes[0]
        or digest(old_nodes[0]) != entry["contract_sha256"]
    ):
        raise DomainError("Completed lineage node contract changed")
    old_inventory = {
        item["path"]: item
        for item in source_graph.get("capacity_contract", {}).get("input_inventory", [])
    }
    new_inventory = {
        item["path"]: item for item in graph.get("capacity_contract", {}).get("input_inventory", [])
    }
    if any(
        old_inventory.get(path) != new_inventory.get(path)
        for path in old_nodes[0].get("required_input_paths", [])
    ):
        raise DomainError("Completed lineage verified input identity changed")
    source_node = connection.execute(
        "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?", (source_plan["id"], node_key)
    ).fetchone()
    source_run = connection.execute(
        "SELECT * FROM worker_runs WHERE id=? AND quest_id=?", (entry["run_id"], quest["id"])
    ).fetchone()
    new_node = connection.execute(
        "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?", (newplan["id"], node_key)
    ).fetchone()
    if not _node_matches(source_node, old_nodes[0]) or not _node_matches(new_node, new_nodes[0]):
        raise DomainError("Completed lineage task row differs from its immutable graph contract")
    if (
        source_node is None
        or source_run is None
        or source_node["status"] != "succeeded"
        or source_run["state"] != "succeeded"
        or source_run["plan_id"] != source_plan["id"]
        or source_run["node_key"] != node_key
        or source_run["plan_digest"] != source_plan["digest"]
        or source_run["plan_revision"] != source_plan["revision"]
        or source_node["worker_id"] != entry["worker_id"]
        or source_run["worker_id"] != entry["worker_id"]
        or json.loads(source_node["evidence_refs"]) != entry["evidence_refs"]
    ):
        raise DomainError("Completed lineage source run/evidence changed")
    if new_node is not None and (
        new_node["status"] != "succeeded"
        or new_node["worker_id"] != entry["worker_id"]
        or json.loads(new_node["evidence_refs"]) != entry["evidence_refs"]
    ):
        raise DomainError("Completed lineage target state differs from its source")
    team = connection.execute(
        "SELECT * FROM team_proposals WHERE quest_id=? AND plan_revision=? AND proposal_digest=?",
        (quest["id"], source_run["plan_revision"], source_run["team_digest"]),
    ).fetchone()
    if (
        team is None
        or not team["approved_by"]
        or not team["approved_at"]
        or team["status"] not in {"approved", "superseded"}
        or source_run["created_at"] < team["approved_at"]
    ):
        raise DomainError("Completed lineage original team approval is invalid")
    _approved_authority(connection, quest["id"], team, "team")
    body = json.loads(team["body_json"])
    if (
        digest(body) != source_run["team_digest"]
        or body.get("plan_digest") != source_plan["digest"]
    ):
        raise DomainError("Completed lineage team digest changed")
    members = body.get("members", [])
    if not 0 <= source_run["member_index"] < len(members):
        raise DomainError("Completed lineage member binding is invalid")
    member = members[source_run["member_index"]]
    if (
        member.get("task_node_key") != node_key
        or member.get("provider_id") != source_run["provider_id"]
        or member.get("model_id") != source_run["model_id"]
        or member.get("role_id") != source_run["role_id"]
    ):
        raise DomainError("Completed lineage selected member identity changed")
    # Every link must be a real user-approved successor of the declared parent.
    cursor, seen = newplan, set()
    while cursor["id"] != source_plan["id"]:
        if cursor["id"] in seen or len(seen) >= 8:
            raise DomainError("Completed lineage chain exceeded its bound")
        seen.add(cursor["id"])
        current = _graph(cursor).get("revision_lineage")
        if not isinstance(current, dict):
            raise DomainError("Completed lineage chain is broken")
        parent = connection.execute(
            "SELECT * FROM quest_plans WHERE id=? AND quest_id=?",
            (current["parent_plan_id"], quest["id"]),
        ).fetchone()
        if current.get("quest_goal_sha256") != hashlib.sha256(
            quest["goal"].encode()
        ).hexdigest() or current.get("request_binding") != request_binding(quest):
            raise DomainError("Intermediate lineage original goal/source binding changed")
        if (
            parent is None
            or parent["digest"] != current["parent_digest"]
            or parent["revision"] != current["parent_revision"]
            or parent["revision"] >= cursor["revision"]
            or not parent["approved_at"]
            or not parent["approved_by"]
        ):
            raise DomainError("Completed lineage parent approval changed")
        _approved_authority(connection, quest["id"], parent)
        cursor = parent
    return {
        "source_plan": dict(source_plan),
        "source_node": dict(source_node),
        "source_run": dict(source_run),
        "entry": entry,
    }


def validated_lineage_source(connection, quest, newplan, node_key):
    return _source(connection, quest, newplan, node_key, require_approved=True)


def validate_pending_lineage(connection, quest, newplan):
    graph = _graph(newplan)
    lineage = graph["revision_lineage"]
    if quest["plan_revision"] != lineage["parent_revision"]:
        raise DomainError("Remaining DAG parent revision changed")
    parent = connection.execute(
        "SELECT * FROM quest_plans WHERE id=? AND quest_id=?",
        (lineage["parent_plan_id"], quest["id"]),
    ).fetchone()
    if (
        parent is None
        or parent["revision"] != quest["plan_revision"]
        or parent["digest"] != lineage["parent_digest"]
        or not parent["approved_at"]
        or not parent["approved_by"]
    ):
        raise DomainError("Pending remaining DAG parent approval changed")
    _approved_authority(connection, quest["id"], parent)
    _graph(parent)
    if lineage.get("quest_goal_sha256") != hashlib.sha256(
        quest["goal"].encode()
    ).hexdigest() or lineage.get("request_binding") != request_binding(quest):
        raise DomainError("Pending remaining DAG goal/source binding changed")
    for entry in lineage["completed_nodes"]:
        _source(connection, quest, newplan, entry["node_key"], require_approved=False)
    return lineage
