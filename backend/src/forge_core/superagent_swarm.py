"""Small projections for bounded expert cooperation on existing task contracts.

This module creates no runtime, session store, or scheduling authority. The
existing graph scheduler, team composer and completion gates execute its output.
"""

import json
import time
from copy import deepcopy
from datetime import datetime

from pydantic import ValidationError

from .domain import DomainError


def valid_expert_tool_call(call, policy):
    from .team_composition import PRESET_ROLES

    args = call.get("arguments")
    if (
        not isinstance(args, dict)
        or not isinstance(args.get("reason"), str)
        or not args["reason"].strip()
    ):
        return False
    if call.get("name") == "worker_request_help":
        return (
            set(args) == {"role", "reason"}
            and isinstance(args["role"], str)
            and args["role"] in PRESET_ROLES
            and len(args["reason"]) <= 2048
        )
    if call.get("name") != "worker_request_experts" or policy != "local-superagent-v2":
        return False
    experts = args.get("experts")
    return (
        set(args) == {"experts", "reason"}
        and len(args["reason"]) <= 1000
        and isinstance(experts, list)
        and len(experts) == 2
        and all(
            isinstance(item, dict)
            and set(item) == {"role", "objective"}
            and isinstance(item["role"], str)
            and item["role"] in PRESET_ROLES
            and isinstance(item["objective"], str)
            and 1 <= len(item["objective"].strip()) <= 1000
            for item in experts
        )
    )


def team_assignments(quest):
    """Validate model-selected expert responsibilities, never detailed steps."""
    metadata = json.loads(quest["metadata"])
    raw = metadata.get("execution_team")
    if raw is None:
        return None
    from .router_domain import ExecutionTeam

    if metadata.get("independent_dispatches"):
        raise DomainError("Independent dispatches and an execution team are mutually exclusive")
    try:
        team = ExecutionTeam.model_validate(raw)
    except (ValidationError, ValueError, TypeError) as exc:
        raise DomainError("Invalid execution team") from exc
    assignments = [
        {
            "key": "task" if len(team.assignments) == 1 else f"task_{index + 1}",
            "objective": item.objective,
            "role": item.role,
            "depends_on": [f"task_{source + 1}" for source in item.depends_on],
        }
        for index, item in enumerate(team.assignments)
    ]
    return {"strategy": team.strategy, "assignments": assignments}


def edges_for(assignments):
    return [
        {"source": source, "target": item["key"], "condition": "success"}
        for item in assignments
        for source in item.get("depends_on", [])
    ]


def assignment_tools(policy, assignment):
    """Give executable-code tools only to the responsible coding/test roles."""
    tools = list(policy["tools"])
    if assignment["role"] not in {"coding", "experiment"}:
        tools = [name for name in tools if name != "python_unittest"]
    if assignment.get("depends_on") and "worker_read_completed_delivery" not in policy["tools"]:
        raise DomainError("This historical mandate cannot authorize expert handoffs")
    return tools


def graph_matches_assignments(graph, assignments):
    """Do not reinterpret an admitted task after a metadata/role mutation."""
    actual = {node["key"]: node["objective"] for node in graph["nodes"]}
    expected = {item["key"]: item["objective"] for item in assignments}
    return actual == expected and graph.get("edges", []) == edges_for(assignments)


def check_reassignment_budget(connection, quest_id, body, policy):
    """Hold old allocations; replacements use residual caps rather than refunds."""
    try:
        binding = json.loads(body["reassignment"]["reason"])
        source = connection.execute(
            "SELECT * FROM events WHERE id=? AND quest_id=?",
            (binding["source_event_id"], quest_id),
        ).fetchone()
        original = connection.execute(
            "SELECT * FROM worker_runs WHERE id=? AND quest_id=?",
            (body["reassignment"]["run_id"], quest_id),
        ).fetchone()
        payload = json.loads(source["payload"]) if source else {}
        if (
            source is None
            or original is None
            or original["state"] not in {"blocked", "failed"}
            or source["worker_id"] != original["worker_id"]
            or payload.get("run_id") != original["id"]
            or payload.get("plan_id") != original["plan_id"]
            or payload.get("task_node_key") != original["node_key"]
            or source["event_type"]
            not in {"worker.help_requested", "worker.blocked", "worker.failed"}
        ):
            raise DomainError("Expert replacement source changed before admission")
        from .worker_model_usage import completed_gateway_rejection_proof
        from .worker_recovery import unresolved_calls

        if (
            unresolved_calls(connection, original["id"])
            or connection.execute(
                "SELECT 1 FROM model_budget_attempts WHERE worker_run_id=? AND state IN ('reserved','unknown')",
                (original["id"],),
            ).fetchone()
            or any(
                completed_gateway_rejection_proof(connection, record["call_id"]) is None
                for record in connection.execute(
                    "SELECT call_id FROM worker_model_reservations WHERE run_id=? AND state='reserved'",
                    (original["id"],),
                )
            )
        ):
            raise DomainError("Unsettled effects prevent expert replacement approval")
    except (ValueError, TypeError, KeyError) as exc:
        raise DomainError("Expert replacement requires its original source binding") from exc
    check_residual_allocation(connection, quest_id, body["members"], policy)


def check_residual_allocation(connection, quest_id, members, policy):
    total = connection.execute(
        "SELECT COUNT(*),COALESCE(SUM(token_limit),0),COALESCE(SUM(tool_limit),0) "
        "FROM worker_runs WHERE quest_id=?",
        (quest_id,),
    ).fetchone()
    if (
        total[0] + len(members) > policy["max_workers"]
        or total[1] + sum(member["estimated_tokens"] for member in members) > policy["max_tokens"]
        or total[2] + sum(member["max_tool_calls"] for member in members) > policy["max_tools"]
    ):
        raise DomainError("Replacement exceeds residual mandate allocation")


class _EligibleReplacementModels:
    """Filter a single composition invocation without changing the Registry."""

    def __init__(self, engine, excluded):
        self.engine, self.excluded = engine, excluded

    def compose(self, roles, profiles, limits, **kwargs):
        return self.engine.compose(
            roles,
            [
                profile
                for profile in profiles
                if (profile.provider, profile.model_id) not in self.excluded
            ],
            limits,
            **kwargs,
        )


def reassign_expert(service, event_id):
    """Admit one settled, source-bound expert handoff through existing contracts.

    This replaces an unfinished Worker. It does not pretend to add a child team,
    rerun completed nodes, refund costs, or replay an unknown call.
    """
    from .domain import ForgeEvent, now_iso
    from .superagent_delegation import DelegationAuthority, _active
    from .team_composition import PRESET_ROLES, CompositionLimits, RoleSpec, TeamCompositionService
    from .worker_model_usage import completed_gateway_rejection_proof
    from .worker_recovery import unresolved_calls

    store = service.store
    with store.connection() as c:
        event = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if event is None or event["event_type"] not in {
            "worker.help_requested",
            "worker.blocked",
            "worker.failed",
        }:
            raise DomainError("Expert reassignment requires an original Worker event")
        payload = json.loads(event["payload"])
        run = c.execute("SELECT * FROM worker_runs WHERE id=?", (payload.get("run_id"),)).fetchone()
        quest = c.execute("SELECT * FROM quests WHERE id=?", (event["quest_id"],)).fetchone()
        if (
            run is None
            or quest is None
            or run["quest_id"] != quest["id"]
            or run["worker_id"] != event["worker_id"]
            or run["state"] not in {"blocked", "failed"}
            or run["plan_revision"] != quest["plan_revision"]
            or payload.get("plan_id") != run["plan_id"]
            or payload.get("task_node_key") != run["node_key"]
            or quest["status"] not in {"approved", "queued", "running"}
        ):
            raise DomainError("Expert event no longer owns the current stopped Worker")
        mandate = c.execute(
            "SELECT id FROM delegation_mandates WHERE quest_id=?", (quest["id"],)
        ).fetchone()
        if mandate is None:
            raise DomainError("Expert reassignment requires a host mandate")
        _, policy = _active(c, mandate["id"], quest["id"])
        if policy.get("max_help_depth", 0) < 1:
            raise DomainError("Historical mandate does not grant expert recovery")
        if c.execute(
            "SELECT 1 FROM events WHERE quest_id=? AND event_type='superagent.expert_reassigned' "
            "AND json_extract(payload,'$.source_event_id')=?",
            (quest["id"], event_id),
        ).fetchone():
            return False
        if (
            unresolved_calls(c, run["id"])
            or any(
                completed_gateway_rejection_proof(c, record["call_id"]) is None
                for record in c.execute(
                    "SELECT call_id FROM worker_model_reservations WHERE run_id=? AND state='reserved'",
                    (run["id"],),
                )
            )
            or c.execute(
                "SELECT 1 FROM model_budget_attempts WHERE worker_run_id=? "
                "AND state IN ('reserved','unknown')",
                (run["id"],),
            ).fetchone()
        ):
            raise DomainError("Unsettled Worker calls block expert reassignment")
        if c.execute(
            "SELECT 1 FROM worker_runs WHERE quest_id=? AND id<>? "
            "AND state IN ('queued','running','paused','blocked','review_pending')",
            (quest["id"], run["id"]),
        ).fetchone():
            raise DomainError("Other active Workers must settle before expert reassignment")
        old = c.execute(
            "SELECT * FROM team_proposals WHERE quest_id=? AND proposal_digest=? AND status='approved'",
            (quest["id"], run["team_digest"]),
        ).fetchone()
        if old is None:
            raise DomainError("Original expert team is no longer approved")
        body = json.loads(old["body_json"])
        allocated = c.execute(
            "SELECT COUNT(*),COALESCE(SUM(token_limit),0),COALESCE(SUM(tool_limit),0) FROM worker_runs WHERE quest_id=?",
            (quest["id"],),
        ).fetchone()
        unfinished = {
            row["node_key"]
            for row in c.execute(
                "SELECT node_key FROM task_nodes WHERE plan_id=? AND status<>'succeeded'",
                (run["plan_id"],),
            )
        }
        remaining_tools = policy["max_tools"] - allocated[2]
        if allocated[0] + len(unfinished) > policy["max_workers"] or remaining_tools < len(
            unfinished
        ):
            raise DomainError("Expert recovery budget is exhausted")
        is_help = event["event_type"] == "worker.help_requested"
        if is_help:
            role = payload.get("requested_role")
            reason = payload.get("reason")
            if role not in PRESET_ROLES or not isinstance(reason, str) or not reason.strip():
                raise DomainError("Expert request has no bounded registered role")
            source_call = payload.get("model_call_id")
            matched = False
            for message in c.execute(
                "SELECT message_json FROM worker_messages WHERE run_id=? AND role='assistant'",
                (run["id"],),
            ):
                for call in json.loads(message[0]).get("tool_calls", []):
                    function = call.get("function", {})
                    if (
                        call.get("id") == source_call
                        and function.get("name") == "worker_request_help"
                        and json.loads(function.get("arguments", "{}"))
                        == {"role": role, "reason": reason}
                    ):
                        matched = True
            if not source_call or not payload.get("budget_call_id") or not matched:
                raise DomainError("Expert request lacks its actual model tool transcript")
            excluded = set()
        else:
            if payload.get("reason_code") not in {"model_error", "provider_unavailable"}:
                raise DomainError("Only a settled Provider failure may request model replacement")
            from .model_performance import model_health_snapshot

            health = model_health_snapshot(
                c, run["provider_id"], run["model_id"], mode=run["execution_mode"]
            )
            if health["recommendation"] == "blocked_unknown":
                raise DomainError("Unknown Provider attempts block model replacement")
            role = None
            reason = "Replace the model after a settled Provider failure"
            excluded = {(run["provider_id"], run["model_id"])}
        profiles = store.list_models()
        roles = []
        for original in body["roles"]:
            if original["task_node_key"] not in unfinished:
                continue
            data = deepcopy(original)
            if data["task_node_key"] == run["node_key"] and role:
                data["id"] = role + "_replacement"
                data["dynamic"] = True
                data["required_capabilities"] = {
                    name: 0.0
                    for name in PRESET_ROLES[role]
                    if any(name in profile.capability_measurements for profile in profiles)
                }
                data["responsibility"] = (
                    "Provide the requested expertise for the original unfinished objective. "
                    + reason[:500]
                )
            data["max_tool_calls"] = min(16, remaining_tools // len(unfinished))
            # Start time remains bounded by the unchanged original plan deadline.
            roles.append(RoleSpec.model_validate(data))
        limits = CompositionLimits.model_validate(
            {
                **body["limits"],
                "allowed_tools": sorted({tool for item in roles for tool in item.tools}),
            }
        )
        authority = DelegationAuthority(mandate["id"])
        current_version = quest["version"]
        source_run_id, quest_id, revision = run["id"], quest["id"], run["plan_revision"]
    teams = TeamCompositionService(
        store,
        engine=_EligibleReplacementModels(service.teams.engine, excluded),
        operator_max_concurrency=service.teams.operator_max_concurrency,
        capacity_providers=service.teams.capacity_providers,
        budget_ledger=service.teams.budget_ledger,
        composer_capacity_measurer=service.teams.composer_capacity_measurer,
    )
    proposal = teams.propose_reassignment(
        quest_id,
        source_run_id,
        json.dumps({"source_event_id": event_id, "reason": reason[:1000]}, ensure_ascii=False),
        roles,
        limits,
        strategy="complementarity",
        expected_version=current_version,
    )
    teams.approve(
        quest_id,
        revision,
        proposal["proposal_digest"],
        expected_version=current_version,
        actor=authority.actor,
        _delegation=authority,
    )
    with store.unit_of_work() as work:
        work.append_event(
            ForgeEvent(
                event_type="superagent.expert_reassigned",
                quest_id=quest_id,
                payload={
                    "source_event_id": event_id,
                    "source_run_id": source_run_id,
                    "proposal_digest": proposal["proposal_digest"],
                    "kind": "expert" if is_help else "model",
                    "at": now_iso(),
                },
            )
        )
    return True


def recover_expert_requests(service):
    with service.store.connection() as c:
        events = c.execute(
            "SELECT e.id FROM events e JOIN delegation_mandates m ON m.quest_id=e.quest_id "
            "WHERE m.state='active' AND e.event_type IN ('worker.help_requested','worker.blocked','worker.failed') "
            "ORDER BY e.seq DESC LIMIT 32"
        ).fetchall()
    recovered = 0
    for event in events:
        try:
            with service.store.connection() as c:
                payload = json.loads(
                    c.execute("SELECT payload FROM events WHERE id=?", (event["id"],)).fetchone()[0]
                )
            if payload.get("experts") is not None:
                recovered += bool(expand_experts(service, event["id"]))
            else:
                recovered += bool(reassign_expert(service, event["id"]))
        except (DomainError, ValueError, KeyError):
            # Durable source and last successful phase remain. No model call or
            # external effect is repeated; regular Router handling still sees it.
            continue
    return recovered


def _expansion_source(connection, quest_id, event_id):
    """Read the exact paid model request; event payloads alone are insufficient."""
    from .superagent_delegation import _active
    from .worker_recovery import unresolved_calls

    event = connection.execute(
        "SELECT * FROM events WHERE id=? AND quest_id=? AND event_type='worker.help_requested'",
        (event_id, quest_id),
    ).fetchone()
    payload = json.loads(event["payload"]) if event else {}
    run = connection.execute(
        "SELECT * FROM worker_runs WHERE id=? AND quest_id=?", (payload.get("run_id"), quest_id)
    ).fetchone()
    mandate = connection.execute(
        "SELECT id FROM delegation_mandates WHERE quest_id=?", (quest_id,)
    ).fetchone()
    if (
        not event
        or not run
        or not mandate
        or event["worker_id"] != run["worker_id"]
        or payload.get("plan_id") != run["plan_id"]
        or payload.get("task_node_key") != run["node_key"]
    ):
        raise DomainError("Expert expansion source is unavailable")
    _, policy = _active(connection, mandate["id"], quest_id)
    args = {"experts": payload.get("experts"), "reason": payload.get("reason")}
    if not valid_expert_tool_call(
        {"name": "worker_request_experts", "arguments": args}, policy["version"]
    ):
        raise DomainError("Expert expansion has an invalid model contract")
    matched = any(
        call.get("id") == payload.get("model_call_id")
        and call.get("function", {}).get("name") == "worker_request_experts"
        and json.loads(call["function"].get("arguments", "{}")) == args
        for row in connection.execute(
            "SELECT message_json FROM worker_messages WHERE run_id=? AND role='assistant'",
            (run["id"],),
        )
        for call in json.loads(row[0]).get("tool_calls", [])
    )
    paid_call = connection.execute(
        "SELECT 1 FROM worker_model_reservations WHERE call_id=? AND run_id=? AND state='settled'",
        (payload.get("budget_call_id"), run["id"]),
    ).fetchone()
    if not matched or not paid_call:
        raise DomainError("Expert expansion lacks its original model transcript")
    if (
        unresolved_calls(connection, run["id"])
        or connection.execute(
            "SELECT 1 FROM model_budget_attempts WHERE worker_run_id=? AND state IN ('reserved','unknown')",
            (run["id"],),
        ).fetchone()
        or connection.execute(
            "SELECT 1 FROM worker_model_reservations WHERE run_id=? AND state='reserved'",
            (run["id"],),
        ).fetchone()
    ):
        raise DomainError("Unknown effects block expert expansion")
    parent = connection.execute(
        "SELECT * FROM quest_plans WHERE id=? AND quest_id=?", (run["plan_id"], quest_id)
    ).fetchone()
    if (
        not parent
        or parent["revision"] != run["plan_revision"]
        or parent["digest"] != run["plan_digest"]
    ):
        raise DomainError("Expert expansion parent assignment changed")
    quest = connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
    if quest["status"] not in {"approved", "queued", "running", "blocked", "needs_input"}:
        raise DomainError("Expert expansion cannot resume a stopped Quest")
    if run["state"] == "cancelled":
        # Cancellation is legitimate only after this exact expansion committed;
        # an operator-cancelled Worker never authorizes new work.
        resumed = connection.execute(
            "SELECT 1 FROM quest_plans p JOIN events e ON json_extract(e.payload,'$.plan_id')=p.id "
            "WHERE p.quest_id=? AND p.revision=? AND p.status='approved' "
            "AND json_extract(p.graph_json,'$.revision_lineage.parent_plan_id')=? "
            "AND e.event_type='quest.plan_proposed' AND json_extract(e.payload,'$.expert_source_event_id')=?",
            (quest_id, quest["plan_revision"], parent["id"], event_id),
        ).fetchone()
        if not resumed or parent["status"] != "superseded":
            raise DomainError("Cancelled Worker has no approved expert expansion")
    elif run["state"] not in {"blocked", "failed"} or quest["plan_revision"] != parent["revision"]:
        raise DomainError("Expert expansion requires the current stopped Worker")
    if (
        connection.execute(
            "SELECT 1 FROM action_intents WHERE run_id=? AND state IN ('prepared','executing','uncertain')",
            (run["id"],),
        ).fetchone()
        or connection.execute(
            "SELECT 1 FROM worker_tool_calls WHERE run_id=? AND state='pending'",
            (run["id"],),
        ).fetchone()
    ):
        raise DomainError("Unsettled external effects block expert expansion")
    return run, parent, payload, policy, mandate["id"]


def _expansion_graph(connection, quest_id, event_id):
    """Derive a fixed fan-in from model-selected specialist responsibilities."""
    from .graph_domain import GraphProposal, graph_contract_dump
    from .superagent_delegation import _split_budget

    run, parent, payload, policy, mandate_id = _expansion_source(connection, quest_id, event_id)
    original = json.loads(parent["graph_json"])
    # This first bounded implementation expands a single original Worker only.
    # A team already consuming slots must settle; it never gains a fresh budget.
    if len(original["nodes"]) != 1 or original.get("hierarchical_expansions") or run["depth"] != 0:
        raise DomainError("Dynamic expansion requires a single original Worker")
    old = original["nodes"][0]
    used = connection.execute(
        "SELECT COUNT(*),COALESCE(SUM(token_limit),0),COALESCE(SUM(tool_limit),0) "
        "FROM worker_runs WHERE quest_id=? AND plan_id=?",
        (quest_id, parent["id"]),
    ).fetchone()
    # Include original held allocations even though its run will be cancelled.
    if (
        old["key"] != run["node_key"]
        or used[0] != 1
        or used[0] + 3 > policy["max_workers"]
        or used[2] + 3 > policy["max_tools"]
    ):
        raise DomainError("Dynamic expert expansion exceeds remaining Worker/tool slots")
    parent_allocated = connection.execute(
        "SELECT COALESCE(SUM(token_limit),0) FROM worker_runs WHERE quest_id=? AND node_key=?",
        (quest_id, run["node_key"]),
    ).fetchone()[0]
    residual = old["budget_tokens"] - parent_allocated
    if residual < 3:
        raise DomainError("No original parent allocation remains for expert expansion")
    budgets = _split_budget(residual, 3)
    keys = [
        run["node_key"] + "_expert_1",
        run["node_key"] + "_expert_2",
        run["node_key"] + "_integrator",
    ]
    child_roles = [item["role"] for item in payload["experts"]]
    children = []
    for index, key in enumerate(keys):
        child = deepcopy(old)
        child.update(key=key, budget_tokens=budgets[index])
        child["capacity_bounds"] = child.get("capacity_bounds") or {
            "max_source_receipt_bytes": 32768,
            "max_artifact_bytes": 16384,
            "max_delivery_bytes": 16384,
        }
        if index < 2:
            child.update(
                objective=payload["experts"][index]["objective"],
                success_criteria=["delivery_review"],
                expected_artifact=None,
            )
            if child_roles[index] not in {"coding", "experiment"}:
                child["allowed_tools"] = [
                    tool for tool in child["allowed_tools"] if tool != "python_unittest"
                ]
        else:
            child["objective"] = old["objective"]
            child["required_predecessor_deliveries"] = keys[:2]
            if "worker_read_completed_delivery" not in child["allowed_tools"]:
                raise DomainError("Parent mandate does not allow verified expert handoffs")
        children.append(child)
    raw = deepcopy(original)
    raw.pop("revision_lineage", None)
    raw["nodes"] = [item for item in raw["nodes"] if item["key"] != run["node_key"]] + children
    new_edges = []
    for edge in raw["edges"]:
        if edge["target"] == run["node_key"]:
            new_edges.extend({**edge, "target": key} for key in keys[:2])
        elif edge["source"] == run["node_key"]:
            new_edges.append({**edge, "source": keys[2]})
        else:
            new_edges.append(edge)
    new_edges.extend({"source": key, "target": keys[2], "condition": "success"} for key in keys[:2])
    raw["edges"] = new_edges
    return (
        graph_contract_dump(GraphProposal.model_validate(raw)),
        child_roles,
        run,
        parent,
        policy,
        mandate_id,
    )


def validate_expansion_plan(connection, quest_id, revision, digest, body):
    from .hierarchical_plans import expansion_contract
    from .plan_lineage import validate_pending_lineage

    proposed = connection.execute(
        "SELECT * FROM quest_plans WHERE quest_id=? AND revision=? AND digest=?",
        (quest_id, revision, digest),
    ).fetchone()
    source = connection.execute(
        "SELECT payload FROM events WHERE quest_id=? AND event_type='quest.plan_proposed' AND json_extract(payload,'$.plan_id')=? ORDER BY seq DESC LIMIT 1",
        (quest_id, proposed["id"] if proposed else None),
    ).fetchone()
    source_id = json.loads(source[0]).get("expert_source_event_id") if source else None
    if not source_id:
        raise DomainError("Expansion plan lacks its original model decision")
    raw, _, run, parent, _policy, _mandate_id = _expansion_graph(connection, quest_id, source_id)
    actual = deepcopy(body)
    actual.pop("revision_lineage", None)
    actual.pop("hierarchical_expansions", None)
    expected = deepcopy(raw)
    expected.pop("hierarchical_expansions", None)
    if actual != expected:
        raise DomainError("Expansion plan changed the model-selected expert contracts")
    quest = connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
    lineage = validate_pending_lineage(connection, quest, proposed)
    derived = expansion_contract(
        json.loads(parent["graph_json"]),
        body,
        parent,
        run["node_key"],
        {item["node_key"] for item in lineage["completed_nodes"]},
    )
    if derived != body.get("hierarchical_expansions"):
        raise DomainError("Expansion history differs from the original parent")


def _advance_expansion_grant(service, quest_id, parent, current, policy, authority):
    """Carry only the original mandate's same output scope into its new revision."""
    from .domain import ForgeEvent
    from .governance import GovernanceService
    from .superagent_delegation import _active, validate_contract_authority

    scope = GovernanceService(service.store)._workspace_scope(policy["write_scope"])
    with service.store.unit_of_work() as work:
        _active(work.connection, authority.mandate_id, quest_id)
        quest = work.get_quest(quest_id)
        if quest["plan_revision"] != current["plan_revision"]:
            raise DomainError("Expanded plan changed before output grant continuity")
        validate_contract_authority(
            work.connection,
            quest_id,
            current["approved_by"],
            "plan",
            current["plan_revision"],
            current["digest"],
        )
        grant = work.connection.execute(
            "SELECT * FROM task_grants WHERE quest_id=? AND workspace_id=? AND resource_scope=?",
            (quest_id, service.store.workspace.id, scope),
        ).fetchone()
        if (
            not grant
            or grant["revoked_at"]
            or grant["granted_by"] != authority.actor
            or grant["plan_revision"] not in {parent["revision"], current["plan_revision"]}
        ):
            raise DomainError("Original task output authorization is unavailable or revoked")
        if grant["plan_revision"] == current["plan_revision"]:
            return
        work.connection.execute(
            "UPDATE task_grants SET plan_revision=? WHERE quest_id=? AND workspace_id=? AND resource_scope=? AND revoked_at IS NULL",
            (current["plan_revision"], quest_id, service.store.workspace.id, scope),
        )
        work.append_event(
            ForgeEvent(
                event_type="governance.workspace_granted",
                quest_id=quest_id,
                payload={
                    "scope": scope,
                    "actor": authority.actor,
                    "policy": policy["version"],
                    "source_plan_revision": parent["revision"],
                    "plan_revision": current["plan_revision"],
                    "reason": "unchanged mandate scope after verified expert expansion",
                },
            )
        )


def expand_experts(service, event_id):
    """Resume bounded expansion at persisted plan/team transaction boundaries."""
    from .domain import ForgeEvent
    from .hierarchical_plans import node_depth
    from .superagent_delegation import DelegationAuthority
    from .team_composition import PRESET_ROLES, CompositionLimits, RoleSpec
    from .worker_schema import STRICT_DELIVERY_OUTPUT_SCHEMA

    store = service.store
    with store.connection() as c:
        event = c.execute("SELECT quest_id FROM events WHERE id=?", (event_id,)).fetchone()
        if not event:
            raise DomainError("Expert event is unavailable")
        quest_id = event["quest_id"]
        if c.execute(
            "SELECT 1 FROM events WHERE quest_id=? AND event_type='superagent.experts_expanded' AND json_extract(payload,'$.source_event_id')=?",
            (quest_id, event_id),
        ).fetchone():
            return False
        raw, child_roles, old_run, parent, policy, mandate_id = _expansion_graph(
            c, quest_id, event_id
        )
        staged = c.execute(
            "SELECT p.* FROM quest_plans p JOIN events e ON json_extract(e.payload,'$.plan_id')=p.id WHERE p.quest_id=? AND e.event_type='quest.plan_proposed' AND json_extract(e.payload,'$.expert_source_event_id')=? ORDER BY p.revision DESC LIMIT 1",
            (quest_id, event_id),
        ).fetchone()
        version = c.execute("SELECT version FROM quests WHERE id=?", (quest_id,)).fetchone()[0]
    authority = DelegationAuthority(mandate_id)
    if staged is None:
        staged = service.graph.propose_remaining(
            quest_id,
            raw,
            expected_version=version,
            expansion_parent=old_run["node_key"],
            _planner_provenance={"expert_source_event_id": event_id},
        )
    else:
        staged = {**dict(staged), "plan_revision": staged["revision"]}
    if staged["status"] == "proposed":
        staged = service.graph.approve(
            quest_id,
            staged["digest"],
            staged["plan_revision"],
            expected_version=store.get_quest(quest_id)["version"],
            actor=authority.actor,
            _delegation=authority,
        )
    with store.connection() as c:
        current = service.graph.get(quest_id)
        if current["id"] != staged["id"] or current["status"] != "approved":
            raise DomainError("Expanded current plan changed")
        team = c.execute(
            "SELECT * FROM team_proposals WHERE quest_id=? AND plan_revision=? AND status IN ('proposed','approved') ORDER BY rowid DESC LIMIT 1",
            (quest_id, current["plan_revision"]),
        ).fetchone()
        used = c.execute(
            "SELECT COALESCE(SUM(tool_limit),0) FROM worker_runs WHERE quest_id=?", (quest_id,)
        ).fetchone()[0]
        profiles = store.list_models()
    _advance_expansion_grant(service, quest_id, parent, current, policy, authority)
    if team is None:
        known = [
            profile
            for profile in profiles
            if profile.enabled
            and not profile.quarantine_reason
            and profile.context_window
            and profile.max_output_tokens
            and profile.tool_calling_support
            and profile.tool_calling_support.supported
            and profile.structured_output_support
            and profile.structured_output_support.supported
        ]
        if not known:
            raise DomainError("No available expert model")
        window = min(profile.context_window for profile in known)
        output = min(8192, min(profile.max_output_tokens for profile in known), window // 4)
        remaining_seconds = int(
            datetime.fromisoformat(parent["approved_at"]).timestamp()
            + policy["max_seconds"]
            - time.time()
        )
        if remaining_seconds < 1:
            raise DomainError("Original expert task deadline expired")
        members = []
        for index, node in enumerate(current["nodes"]):
            role = child_roles[index] if index < 2 else "architect"
            member_output = min(output, node["budget_tokens"] // 4)
            if member_output < 1:
                raise DomainError("Insufficient original allocation for expert model output")
            members.append(
                RoleSpec(
                    id=f"{role}_expanded_{index + 1}",
                    dynamic=True,
                    depth=node_depth(current, node["key"]),
                    task_node_key=node["key"],
                    required_capabilities={
                        name: 0.0
                        for name in PRESET_ROLES[role]
                        if any(name in profile.capability_measurements for profile in known)
                    },
                    tools=node["allowed_tools"],
                    input_tokens=min(
                        window - member_output,
                        node["budget_tokens"] - member_output,
                        8192 + len(node["objective"].encode("utf-8")),
                    ),
                    output_tokens=member_output,
                    execution_token_budget=node["budget_tokens"],
                    max_steps=policy["max_steps"],
                    max_tool_calls=(policy["max_tools"] - used) // len(current["nodes"]),
                    max_duration_seconds=min(
                        remaining_seconds, policy["max_seconds"] // len(current["nodes"])
                    ),
                    responsibility=(
                        "Integrate both complete specialist deliveries; satisfy the original acceptance criteria. "
                        if index == 2
                        else "Provide only your assigned specialist findings. "
                    )
                    + node["objective"],
                    input_contract=[
                        "Preserve the complete original request. Read every declared predecessor delivery before integration."
                    ],
                    output_schema=STRICT_DELIVERY_OUTPUT_SCHEMA,
                    acceptance_criteria=node["success_criteria"],
                )
            )
        proposal = service.teams.propose(
            quest_id,
            members,
            CompositionLimits(
                max_members=len(members),
                max_depth=1,
                max_concurrency=min(service.max_concurrency, len(members)),
                max_total_tokens=policy["max_tokens"],
                max_total_tool_calls=policy["max_tools"],
                allowed_tools=sorted({tool for member in members for tool in member.tools}),
            ),
            strategy="complementarity",
            expected_version=store.get_quest(quest_id)["version"],
        )
        team_digest = proposal["proposal_digest"]
    else:
        team_digest = team["proposal_digest"]
    if team is None or team["status"] == "proposed":
        service.teams.approve(
            quest_id,
            current["plan_revision"],
            team_digest,
            expected_version=store.get_quest(quest_id)["version"],
            actor=authority.actor,
            _delegation=authority,
        )
    with store.unit_of_work() as work:
        work.append_event(
            ForgeEvent(
                event_type="superagent.experts_expanded",
                quest_id=quest_id,
                payload={
                    "source_event_id": event_id,
                    "source_run_id": old_run["id"],
                    "plan_id": current["id"],
                    "proposal_digest": team_digest,
                },
            )
        )
    return True
