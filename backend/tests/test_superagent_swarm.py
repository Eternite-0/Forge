"""Programmatic SQLite/contract tests; scripted models are not live acceptance."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response, tool
from test_worker_request_framing_v17 import ObservedGateway

from forge_core.domain import DomainError
from forge_core.evidence import NodeVerifier
from forge_core.independent_review import IndependentReviewService
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderGateway
from forge_core.router_domain import ExecutionTeam
from forge_core.superagent_delegation import (
    LEGACY_POLICY,
    LEGACY_SAFE_TOOLS,
    DelegationAuthority,
    _active,
)
from forge_core.superagent_swarm import expand_experts, reassign_expert
from forge_core.team_composition import CompositionLimits, RoleSpec, TeamComposerEngine
from forge_core.tool_engine import ToolEngine
from forge_core.worker_prompts import measure_initial_worker_request
from forge_core.worker_runtime import WorkerManager


def set_team(store, quest_id, strategy, assignments):
    with store.unit_of_work() as work:
        metadata = json.loads(work.get_quest(quest_id)["metadata"])
        metadata["execution_team"] = {"strategy": strategy, "assignments": assignments}
        work.connection.execute(
            "UPDATE quests SET metadata=? WHERE id=?", (json.dumps(metadata), quest_id)
        )


def assignment(role, index, depends_on=()):
    return {
        "role": role,
        "objective": f"Assess responsibility {index} with source evidence",
        "depends_on": list(depends_on),
    }


@pytest.mark.parametrize("kind", ["openai_compatible", "deepseek", "anthropic"])
def test_v2_mandate_framing_reserves_the_actual_expert_definition(delegated, kind):
    store, quest_id, _mandate, graph, _teams, service = delegated
    assert service.prepare(quest_id)
    gateway = ObservedGateway(
        kind=kind,
        transport=httpx.MockTransport(lambda _: pytest.fail("Framing must not make HTTP calls")),
    )
    gateway.config = replace(gateway.config, id="mock-provider")
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = json.loads(c.execute("SELECT body_json FROM team_proposals").fetchone()[0])
        before = c.total_changes
        measured = measure_initial_worker_request(
            store,
            c,
            store.get_quest(quest_id),
            plan,
            plan["nodes"][0],
            team["members"][0],
            provider=gateway,
        )
        assert before == c.total_changes
    request, payload = gateway.serialized[-1]
    assert "worker_request_experts" in {item["name"] for item in request.tools}
    assert (
        measured["native_input_reservation"]
        == len(json.dumps(payload, ensure_ascii=False).encode()) + 64
    )


def test_request_estimate_cumulative_allocation_and_model_window_are_independent(
    delegated, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    store, quest_id, _mandate, graph, teams, service = delegated
    model = store.list_models()[0].model_copy(update={"context_window": 100_000_000})
    store.save_model(model)
    assert service.prepare(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals").fetchone()
        member = json.loads(team["body_json"])["members"][0]
    assert member["model_context_window"] == 100_000_000
    assert (
        member["estimated_tokens"]
        == graph.get(quest_id)["nodes"][0]["budget_tokens"] // 2
        == 500_000
    )
    assert member["estimated_request_tokens"] < 73_728 < member["estimated_tokens"]
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ProtocolModel([])}),
        ToolEngine(store),
        team_authority=teams,
    )
    run = manager.start(
        quest_id,
        graph.get(quest_id)["id"],
        "task",
        team_digest=team["proposal_digest"],
        member_index=0,
    )
    assert run["token_limit"] == 500_000 and run["model_context_window"] == 100_000_000
    assert run["output_limit"] == 8192 and run["step_limit"] == 32 and run["tool_limit"] == 32
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT tokens_allocated FROM worker_budget_ledger WHERE scope='quest' AND scope_id=?",
                (quest_id,),
            ).fetchone()[0]
            == 500_000
        )


def test_explicit_execution_budget_is_validated_and_legacy_composition_stays_compatible(
    delegated, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    store, *_rest = delegated
    model = store.list_models()[0]
    limits = CompositionLimits(
        max_members=1, max_depth=0, max_concurrency=1, max_total_tokens=20_000
    )
    engine = TeamComposerEngine()
    legacy = RoleSpec(id="research", input_tokens=1000, output_tokens=100)
    explicit = legacy.model_copy(update={"execution_token_budget": 20_000})
    old = engine.compose([legacy], [model], limits, strategy="single_model")["members"][0]
    new = engine.compose([explicit], [model], limits, strategy="single_model")["members"][0]
    assert old["estimated_tokens"] == 1100 and "estimated_request_tokens" not in old
    assert new["estimated_tokens"] == 20_000 and new["estimated_request_tokens"] == 1100
    with pytest.raises(ValidationError, match="cannot be smaller"):
        RoleSpec(id="research", input_tokens=1000, output_tokens=100, execution_token_budget=1099)
    with pytest.raises(ValueError, match="Token budget"):
        engine.compose(
            [explicit],
            [model],
            limits.model_copy(update={"max_total_tokens": 19_999}),
            strategy="single_model",
        )


@pytest.mark.parametrize(
    "strategy,assignments,edges",
    [
        ("single", [assignment("coding", 0)], []),
        ("parallel", [assignment("coding", 0), assignment("research", 1)], []),
        (
            "sequential",
            [
                assignment("research", 0),
                assignment("architect", 1, [0]),
                assignment("reviewer", 2, [1]),
            ],
            [("task_1", "task_2"), ("task_2", "task_3")],
        ),
        (
            "hierarchical",
            [
                assignment("research", 0),
                assignment("architect", 1),
                assignment("reviewer", 2, [0, 1]),
            ],
            [("task_1", "task_3"), ("task_2", "task_3")],
        ),
    ],
)
def test_expert_topology_reuses_original_contracts_and_budget(
    delegated, strategy, assignments, edges
):
    store, quest_id, mandate, graph, _teams, service = delegated
    set_team(store, quest_id, strategy, assignments)
    assert service.prepare(quest_id)
    plan = graph.get(quest_id)
    assert [(edge["source"], edge["target"]) for edge in plan["edges"]] == edges
    with store.connection() as c:
        policy = json.loads(c.execute("SELECT policy_json FROM delegation_mandates").fetchone()[0])
        team = json.loads(c.execute("SELECT body_json FROM team_proposals").fetchone()[0])
        assert sum(node["budget_tokens"] for node in plan["nodes"]) == policy["max_tokens"]
        assert sum(member["max_tool_calls"] for member in team["members"]) <= policy["max_tools"]
        assert sum(member["estimated_tokens"] for member in team["members"]) <= policy["max_tokens"]
        assert team["limits"]["max_concurrency"] == (
            1 if strategy == "sequential" else min(2, len(assignments))
        )
        assert plan["approved_by"] == f"router_mandate:{mandate}"
        assert c.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    for index, node in enumerate(plan["nodes"]):
        expected = [f"task_{source + 1}" for source in assignments[index]["depends_on"]]
        assert node.get("required_predecessor_deliveries", []) == expected
        # Latent read capability allows bounded children; each actual read still
        # requires declared dependencies and independently verified source proof.
        assert "worker_read_completed_delivery" in node["allowed_tools"]
        assert ("python_unittest" in node["allowed_tools"]) == (
            assignments[index]["role"] in {"coding", "experiment"}
        )
    assert service.prepare(quest_id)


def test_dependent_worker_cannot_start_until_actual_predecessor_completion(delegated):
    class NoCalls:
        execution_mode = "mock"

        async def generate(self, _request):
            pytest.fail("No model inference is part of this scheduler admission test")

    store, quest_id, _mandate, graph, teams, service = delegated
    set_team(
        store, quest_id, "sequential", [assignment("research", 0), assignment("reviewer", 1, [0])]
    )
    assert service.prepare(quest_id)
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT proposal_digest FROM team_proposals").fetchone()[0]
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": NoCalls()}), ToolEngine(store), team_authority=teams
    )
    with pytest.raises(DomainError, match="dependencies"):
        manager.start(quest_id, plan["id"], "task_2", team_digest=team, member_index=1)
    run = manager.start(quest_id, plan["id"], "task_1", team_digest=team, member_index=0)
    assert run["state"] == "queued"


def test_topology_tampering_cannot_obtain_host_approval(delegated):
    store, quest_id, mandate, graph, _teams, service = delegated
    set_team(store, quest_id, "parallel", [assignment("research", 0), assignment("reviewer", 1)])
    service.prepare(quest_id)
    plan = graph.get(quest_id)
    plan["edges"] = [{"source": "task_1", "target": "task_2", "condition": "success"}]
    with store.connection() as c, pytest.raises(DomainError, match="mandate"):
        DelegationAuthority(mandate).check(c, quest_id, "plan", 1, "forged", plan)


def test_legacy_mandate_retains_exact_tools_and_cannot_gain_new_expert_handoffs(delegated):
    store, quest_id, mandate, _graph, _teams, service = delegated
    with store.unit_of_work() as work:
        policy = json.loads(
            work.connection.execute("SELECT policy_json FROM delegation_mandates").fetchone()[0]
        )
        policy.update(version=LEGACY_POLICY, tools=list(LEGACY_SAFE_TOOLS))
        work.connection.execute(
            "UPDATE delegation_mandates SET policy_json=?", (json.dumps(policy),)
        )
    with store.connection() as c:
        assert _active(c, mandate, quest_id)[1]["tools"] == list(LEGACY_SAFE_TOOLS)
    set_team(
        store, quest_id, "sequential", [assignment("research", 0), assignment("reviewer", 1, [0])]
    )
    with pytest.raises(DomainError, match="historical mandate"):
        service.prepare(quest_id)


@pytest.mark.parametrize(
    "team",
    [
        {"strategy": "single", "assignments": [assignment("coding", 0), assignment("research", 1)]},
        {
            "strategy": "parallel",
            "assignments": [assignment("research", 0), assignment("reviewer", 1, [0])],
        },
        {
            "strategy": "sequential",
            "assignments": [assignment("research", 0), assignment("reviewer", 1)],
        },
        {
            "strategy": "hierarchical",
            "assignments": [assignment("research", 0), assignment("reviewer", 1)],
        },
        {
            "strategy": "sequential",
            "assignments": [assignment("research", 0, [1]), assignment("reviewer", 1, [0])],
        },
    ],
)
def test_invalid_swarm_topologies_fail_before_contract_creation(team):
    with pytest.raises(ValidationError):
        ExecutionTeam.model_validate(team)


def stopped_help(delegated, expert_expansion=False):
    store, quest_id, _mandate, graph, teams, service = delegated
    assert service.prepare(quest_id)
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT proposal_digest FROM team_proposals").fetchone()[0]
    request = (
        tool(
            "worker_request_experts",
            {
                "reason": "Independent research and architecture checks are necessary",
                "experts": [
                    {"role": "research", "objective": "Read sources and report factual findings"},
                    {
                        "role": "architect",
                        "objective": "Check architectural tradeoffs and report findings",
                    },
                ],
            },
        )
        if expert_expansion
        else tool(
            "worker_request_help",
            {"role": "math", "reason": "Need a quantitative consistency check"},
        )
    )
    model = ProtocolModel([request])
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    run = manager.start(quest_id, plan["id"], "task", team_digest=team, member_index=0)
    assert asyncio.run(manager.run(run["id"]))["state"] == "blocked"
    with store.connection() as c:
        event = c.execute(
            "SELECT * FROM events WHERE event_type='worker.help_requested'"
        ).fetchone()
        assert event is not None
    return store, quest_id, plan, teams, service, manager, run, event


def test_model_requested_expansion_retains_original_allocation_and_verified_fan_in(delegated):
    store, quest_id, original, _teams, service, manager, run, event = stopped_help(delegated, True)
    with store.connection() as c:
        before = dict(c.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone())
        ledger = dict(
            c.execute(
                "SELECT * FROM worker_budget_ledger WHERE scope='quest' AND scope_id=?", (quest_id,)
            ).fetchone()
        )
    assert expand_experts(service, event["id"])
    plan = service.graph.get(quest_id)
    assert plan["id"] != original["id"] and plan["plan_revision"] == 2
    assert plan["revision_lineage"]["parent_plan_id"] == original["id"]
    assert plan["hierarchical_expansions"][0]["depth"] == 1
    assert [node["key"] for node in plan["nodes"]] == [
        "task_expert_1",
        "task_expert_2",
        "task_integrator",
    ]
    assert plan["nodes"][2]["required_predecessor_deliveries"] == ["task_expert_1", "task_expert_2"]
    with store.connection() as c:
        parent = dict(c.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone())
        assert parent["state"] == "cancelled"
        assert (parent["token_spent"], parent["tool_spent"]) == (before["token_spent"], 1)
        assert (
            dict(
                c.execute(
                    "SELECT * FROM worker_budget_ledger WHERE scope='quest' AND scope_id=?",
                    (quest_id,),
                ).fetchone()
            )
            == ledger
        )
        assert (
            sum(node["budget_tokens"] for node in plan["nodes"]) + parent["token_limit"]
            == original["nodes"][0]["budget_tokens"]
        )
        team = c.execute(
            "SELECT * FROM team_proposals WHERE plan_revision=2 AND status='approved'"
        ).fetchone()
        body = json.loads(team["body_json"])
        assert len(body["members"]) == 3 and {role["depth"] for role in body["roles"]} == {1}
        assert (
            sum(member["max_tool_calls"] for member in body["members"]) + parent["tool_limit"] <= 64
        )
        assert c.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    with pytest.raises(DomainError, match="dependencies"):
        manager.start(
            quest_id,
            plan["id"],
            "task_integrator",
            team_digest=team["proposal_digest"],
            member_index=2,
            depth=1,
        )
    first = manager.start(
        quest_id,
        plan["id"],
        "task_expert_1",
        team_digest=team["proposal_digest"],
        member_index=0,
        depth=1,
    )
    second = manager.start(
        quest_id,
        plan["id"],
        "task_expert_2",
        team_digest=team["proposal_digest"],
        member_index=1,
        depth=1,
    )
    assert first["state"] == second["state"] == "queued"
    assert not expand_experts(service, event["id"])


@pytest.mark.parametrize(
    "phase", ["plan_proposed", "plan_approved", "team_proposed", "team_approved"]
)
def test_expansion_resumes_committed_boundaries_without_duplicate_workers(delegated, phase):
    store, quest_id, _plan, _teams, service, manager, _run, event = stopped_help(delegated, True)
    target, attribute = {
        "plan_proposed": (service.graph, "approve"),
        "plan_approved": (service.teams, "propose"),
        "team_proposed": (service.teams, "approve"),
        "team_approved": (service.teams, "approve"),
    }[phase]
    original = getattr(target, attribute)

    def crash(*args, **kwargs):
        if phase == "team_approved":
            original(*args, **kwargs)
        raise RuntimeError("simulated process exit at persisted boundary")

    with patch.object(target, attribute, crash), pytest.raises(RuntimeError):
        expand_experts(service, event["id"])
    if phase == "team_approved":
        plan = service.graph.get(quest_id)
        with store.connection() as c:
            team = c.execute(
                "SELECT proposal_digest FROM team_proposals WHERE plan_revision=2"
            ).fetchone()[0]
        manager.start(
            quest_id, plan["id"], "task_expert_1", team_digest=team, member_index=0, depth=1
        )
    # Reopen existing SQLite/Store is exercised by the production restart driver;
    # no service-private recovery state is required at these transaction edges.
    assert expand_experts(service, event["id"])
    assert not expand_experts(service, event["id"])
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM quest_plans").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM team_proposals").fetchone()[0] == 2
        assert (
            c.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='superagent.experts_expanded'"
            ).fetchone()[0]
            == 1
        )
        assert c.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == (
            2 if phase == "team_approved" else 1
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "transcript",
        "unknown_model",
        "uncertain_tool",
        "unsettled_action",
        "cancelled",
        "revoked_mandate",
        "revoked_grant",
        "objective",
    ],
)
def test_expansion_rejects_unknown_effects_revocations_and_changed_decisions(delegated, tamper):
    store, quest_id, _plan, _teams, service, _manager, run, event = stopped_help(delegated, True)
    with store.unit_of_work() as work:
        c = work.connection
        if tamper == "transcript":
            c.execute(
                "DELETE FROM worker_messages WHERE run_id=? AND role='assistant'", (run["id"],)
            )
        elif tamper == "unknown_model":
            c.execute(
                "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at) "
                "VALUES ('unknown','unknown','worker',?,?,?,'mock-provider','mock-model',10,10,'unknown',0)",
                (store.workspace.id, quest_id, run["id"]),
            )
        elif tamper == "uncertain_tool":
            c.execute(
                "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) VALUES (?,'uncertain','uncertain','file_write','{}','uncertain')",
                (run["id"],),
            )
        elif tamper == "unsettled_action":
            source = c.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone()
            c.execute(
                "INSERT INTO action_intents(id,call_id,quest_id,worker_id,run_id,plan_id,plan_revision,tool_name,parameters_json,parameter_digest,resource_scope,risk,execution_mode,state,created_at,updated_at) "
                "VALUES ('unsettled','unsettled',?,?,?,?,?,'file_write','{}','digest','scope','R1','mock','prepared','now','now')",
                (
                    quest_id,
                    source["worker_id"],
                    run["id"],
                    source["plan_id"],
                    source["plan_revision"],
                ),
            )
        elif tamper == "cancelled":
            c.execute("UPDATE worker_runs SET state='cancelled' WHERE id=?", (run["id"],))
        elif tamper == "revoked_mandate":
            c.execute("UPDATE delegation_mandates SET state='revoked'")
        elif tamper == "revoked_grant":
            c.execute("UPDATE task_grants SET revoked_at='2026-11-16T00:00:00+00:00'")
        else:
            payload = json.loads(event["payload"])
            payload["experts"][0]["objective"] = "Changed assignment without model provenance"
            c.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), event["id"]))
    with pytest.raises(DomainError):
        expand_experts(service, event["id"])
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM team_proposals").fetchone()[0] == 1


def test_expanded_specialists_write_real_files_and_integrator_reads_both_verified_deliveries(
    delegated,
):
    """Scripted Gateway fixture exercises actual tools/reviews, never live-model evidence."""

    class OfflineLiveProtocol(ProtocolModel):
        execution_mode = "live"

    store, quest_id, _plan, _teams, service, manager, _run, event = stopped_help(delegated, True)
    assert expand_experts(service, event["id"])
    plan = service.graph.get(quest_id)
    with store.connection() as c:
        team = c.execute(
            "SELECT proposal_digest FROM team_proposals WHERE plan_revision=2"
        ).fetchone()[0]
    for index in range(2):
        key = f"task_expert_{index + 1}"
        model = OfflineLiveProtocol(
            [
                tool(
                    "file_write",
                    {"path": f"work/{quest_id}/{key}.json", "content": json.dumps({"fact": index})},
                ),
                response(delivery(f"Source-verified specialist fact {index}")),
            ]
        )
        manager.providers = ProviderPool({"mock-provider": model})
        run = manager.start(
            quest_id, plan["id"], key, team_digest=team, member_index=index, depth=1
        )
        assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"

        def pass_response(_body, _ordinal):
            return {
                "content": json.dumps(
                    {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
                )
            }

        with offline_loopback_gateway(store, pass_response) as (endpoint, _requests):
            gateway = ProviderGateway(
                replace(endpoint.config, id="mock-provider", supports_json_schema=True),
                credential_resolver=lambda _: "offline-fixture",
                budget_ledger=ModelCallBudgetLedger(
                    store.path, token_limit=500000, concurrency_limit=1
                ),
            )
            manager.providers = ProviderPool({"mock-provider": gateway})
            assert (
                asyncio.run(IndependentReviewService(manager).review(run["id"]))["verdict"]
                == "PASS"
            )
        NodeVerifier(store).verify(quest_id, key)
        assert manager.get(run["id"])["state"] == "succeeded"
    integrator = OfflineLiveProtocol(
        [
            tool("worker_read_completed_delivery", {"node_key": "task_expert_1"}),
            tool("worker_read_completed_delivery", {"node_key": "task_expert_2"}, 2),
            tool(
                "file_write",
                {"path": f"work/{quest_id}/integrated.json", "content": '{"facts":[0,1]}'},
                3,
            ),
            response(delivery("Integrated both complete specialist findings")),
        ]
    )
    manager.providers = ProviderPool({"mock-provider": integrator})
    run = manager.start(
        quest_id, plan["id"], "task_integrator", team_digest=team, member_index=2, depth=1
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    with store.connection() as c:
        calls = c.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND tool_name='worker_read_completed_delivery'",
            (run["id"],),
        ).fetchall()
        assert len(calls) == 2 and all(
            call["state"] == "succeeded" and call["receipt_id"] is None for call in calls
        )
        assert c.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 4
        assert c.execute("SELECT SUM(tool_limit) FROM worker_runs").fetchone()[0] <= 64


def test_actual_model_help_replaces_expert_without_refunding_original_budgets(delegated):
    store, quest_id, plan, _teams, service, manager, run, event = stopped_help(delegated)
    with store.connection() as c:
        before = dict(c.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone())
        ledger = dict(
            c.execute(
                "SELECT * FROM worker_budget_ledger WHERE scope='quest' AND scope_id=?", (quest_id,)
            ).fetchone()
        )
    assert reassign_expert(service, event["id"])
    with store.connection() as c:
        after = dict(c.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone())
        new = c.execute("SELECT * FROM team_proposals WHERE status='approved'").fetchone()
        body = json.loads(new["body_json"])
        assert body["members"][0]["role_id"] == "math_replacement"
        assert after["state"] == "cancelled"
        assert after["token_spent"] == before["token_spent"]
        assert after["tool_spent"] == before["tool_spent"] == 1
        assert (
            dict(
                c.execute(
                    "SELECT * FROM worker_budget_ledger WHERE scope='quest' AND scope_id=?",
                    (quest_id,),
                ).fetchone()
            )
            == ledger
        )
    replacement = manager.start(
        quest_id, plan["id"], "task", team_digest=new["proposal_digest"], member_index=0
    )
    assert replacement["state"] == "queued"
    with store.connection() as c:
        total = c.execute("SELECT SUM(tool_limit) FROM worker_runs").fetchone()[0]
        assert total == 48 and total <= 64
    with pytest.raises(DomainError, match="current stopped Worker"):
        reassign_expert(service, event["id"])


@pytest.mark.parametrize(
    "tamper", ["transcript", "unknown_model", "uncertain_tool", "revoked_mandate"]
)
def test_expert_recovery_rejects_unknown_effects_and_changed_source(delegated, tamper):
    store, quest_id, _plan, _teams, service, _manager, run, event = stopped_help(delegated)
    with store.unit_of_work() as work:
        c = work.connection
        if tamper == "transcript":
            c.execute(
                "DELETE FROM worker_messages WHERE run_id=? AND role='assistant'", (run["id"],)
            )
        elif tamper == "unknown_model":
            c.execute(
                "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at) "
                "VALUES ('unknown','unknown','worker',?,?,?,'mock-provider','mock-model',10,10,'unknown',0)",
                (store.workspace.id, quest_id, run["id"]),
            )
        elif tamper == "uncertain_tool":
            c.execute(
                "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) VALUES (?,'uncertain','uncertain','file_write','{}','uncertain')",
                (run["id"],),
            )
        else:
            c.execute("UPDATE delegation_mandates SET state='revoked'")
    with pytest.raises(DomainError):
        reassign_expert(service, event["id"])
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM team_proposals").fetchone()[0] == 1
        assert (
            c.execute("SELECT state FROM worker_runs WHERE id=?", (run["id"],)).fetchone()[0]
            == "blocked"
        )


def test_mandated_expert_reads_complete_source_verified_predecessor(delegated):
    """Real SQLite/files/Gateway fixture; scripted responses, no live Provider."""

    class OfflineLiveProtocol(ProtocolModel):
        execution_mode = "live"

    store, quest_id, _mandate, graph, teams, service = delegated
    set_team(
        store, quest_id, "sequential", [assignment("research", 0), assignment("reviewer", 1, [0])]
    )
    assert service.prepare(quest_id)
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT proposal_digest FROM team_proposals").fetchone()[0]
    model = OfflineLiveProtocol(
        [
            tool(
                "file_write",
                {"path": f"work/{quest_id}/analysis.json", "content": '{"source_fact":42}'},
            ),
            response(delivery("SOURCE_FACT_42 retained in the complete predecessor delivery")),
        ]
    )
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    producer = manager.start(quest_id, plan["id"], "task_1", team_digest=team, member_index=0)
    assert asyncio.run(manager.run(producer["id"]))["state"] == "review_pending"

    def pass_response(_body, _ordinal):
        return {
            "content": json.dumps(
                {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
            )
        }

    with offline_loopback_gateway(store, pass_response) as (endpoint, _requests):
        gateway = ProviderGateway(
            replace(endpoint.config, id="mock-provider", supports_json_schema=True),
            credential_resolver=lambda _: "offline-fixture",
            budget_ledger=ModelCallBudgetLedger(
                store.path, token_limit=500000, concurrency_limit=1
            ),
        )
        manager.providers = ProviderPool({"mock-provider": gateway})
        assert (
            asyncio.run(IndependentReviewService(manager).review(producer["id"]))["verdict"]
            == "PASS"
        )
    NodeVerifier(store).verify(quest_id, "task_1")
    assert manager.get(producer["id"])["state"] == "succeeded"
    model.responses.extend(
        [
            tool("worker_read_completed_delivery", {"node_key": "task_1"}, 3),
            tool(
                "file_write",
                {"path": f"work/{quest_id}/review.json", "content": '{"checked":42}'},
                4,
            ),
            response(delivery("Checked SOURCE_FACT_42 against the predecessor")),
        ]
    )
    manager.providers = ProviderPool({"mock-provider": model})
    reader = manager.start(quest_id, plan["id"], "task_2", team_digest=team, member_index=1)
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    with store.connection() as c:
        call = c.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND tool_name='worker_read_completed_delivery'",
            (reader["id"],),
        ).fetchone()
        result = json.loads(call["result_json"])
        assert call["state"] == "succeeded"
        assert result["delivery"]["summary"].startswith("SOURCE_FACT_42")
        assert result["producer_run_id"] == producer["id"] and result["truncated"] is False
        assert call["receipt_id"] is None
