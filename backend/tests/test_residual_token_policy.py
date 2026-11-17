"""Generated allocation estimates must not stop authorized expert continuation.

Real admitted runs, scripted models, governed file writes and persisted restart
state. No external provider traffic and no mock claim of independent completion.
"""

import asyncio
import json

import pytest
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_superagent_swarm import stopped_help
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response, tool

from forge_core.domain import DomainError
from forge_core.provider_pool import ProviderPool
from forge_core.store import Store
from forge_core.superagent_swarm import reassign_expert
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def latest_team(store):
    with store.connection() as connection:
        return connection.execute(
            "SELECT proposal_digest FROM team_proposals WHERE status='approved'"
        ).fetchone()[0]


def two_expert_requests(delegated, monkeypatch, *, enforced=False):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", str(enforced).lower())
    store, quest_id, plan, teams, service, manager, first, event = stopped_help(delegated)
    assert reassign_expert(service, event["id"])
    model = ProtocolModel(
        [
            tool(
                "worker_request_help",
                {
                    "role": "coding",
                    "reason": "Implement the remaining result within the original scope",
                },
            )
        ]
    )
    manager.providers = ProviderPool({"mock-provider": model})
    second = manager.start(
        quest_id,
        plan["id"],
        "task",
        team_digest=latest_team(store),
        member_index=0,
    )
    assert asyncio.run(manager.run(second["id"]))["state"] == "blocked"
    with store.connection() as connection:
        event_id = connection.execute(
            "SELECT id FROM events WHERE event_type='worker.help_requested' ORDER BY seq DESC LIMIT 1"
        ).fetchone()[0]
        policy = json.loads(
            connection.execute("SELECT policy_json FROM delegation_mandates").fetchone()[0]
        )
        assert policy["max_tokens"] == 1_000_000  # Core-generated fixed estimate, not user input.
        assert tuple(
            connection.execute(
                "SELECT count(*),sum(token_limit),sum(tool_limit) FROM worker_runs"
            ).fetchone()
        ) == (2, 1_000_000, 48)
    return store, quest_id, plan, teams, service, manager, first, second, event_id


def test_generated_token_estimate_does_not_block_real_write_after_restart(delegated, monkeypatch):
    store, quest_id, plan, _, service, manager, first, second, event_id = two_expert_requests(
        delegated, monkeypatch
    )
    with store.connection() as connection:
        original_policy = connection.execute(
            "SELECT policy_json FROM delegation_mandates"
        ).fetchone()[0]
        original_graph = connection.execute("SELECT graph_json FROM quest_plans").fetchone()[0]
        grants = [tuple(row) for row in connection.execute("SELECT * FROM task_grants")]
        old_usage = [
            (r["id"], r["token_spent"], r["tool_spent"])
            for r in connection.execute("SELECT * FROM worker_runs")
        ]
    assert reassign_expert(service, event_id)
    third = manager.start(
        quest_id, plan["id"], "task", team_digest=latest_team(store), member_index=0
    )
    manager.control(third["id"], "pause")
    with store.connection() as connection:
        allocation = [
            tuple(row) for row in connection.execute("SELECT * FROM worker_budget_ledger")
        ]
        assert tuple(
            connection.execute(
                "SELECT count(*),sum(token_limit),sum(tool_limit) FROM worker_runs"
            ).fetchone()
        ) == (3, 1_500_000, 64)
    reopened = Store(store.path, workspace=store.workspace)
    path = f"work/{quest_id}/continued.json"
    model = ProtocolModel(
        [
            tool("file_write", {"path": path, "content": '{"continued":true}'}),
            response(
                delivery("Result written within the original authorized scope; review pending.")
            ),
        ]
    )
    resumed = WorkerManager(
        reopened,
        ProviderPool({"mock-provider": model}),
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    assert resumed.get(third["id"])["state"] == "paused"
    assert asyncio.run(resumed.run(third["id"]))["state"] == "paused"
    assert not model.requests
    resumed.control(third["id"], "resume")
    result = asyncio.run(resumed.run(third["id"]))
    assert result["state"] == "review_pending" and len(model.requests) == 2
    assert (reopened.workspace.root / path).read_text() == '{"continued":true}'
    assert result["output_limit"] == third["output_limit"]
    assert result["model_context_window"] == third["model_context_window"]
    assert result["deadline"] == third["deadline"]
    with reopened.connection() as connection:
        assert (
            connection.execute("SELECT policy_json FROM delegation_mandates").fetchone()[0]
            == original_policy
        )
        assert (
            connection.execute("SELECT graph_json FROM quest_plans").fetchone()[0] == original_graph
        )
        assert [tuple(row) for row in connection.execute("SELECT * FROM task_grants")] == grants
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM worker_budget_ledger")
        ] == allocation
        for identifier, tokens, tools in old_usage:
            row = connection.execute(
                "SELECT * FROM worker_runs WHERE id=?", (identifier,)
            ).fetchone()
            assert (row["token_spent"], row["tool_spent"]) == (tokens, tools)
            assert row["state"] == "cancelled"
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert first["quest_id"] == second["quest_id"] == third["quest_id"]


def test_operator_enabled_cumulative_cap_still_denies_third_allocation(delegated, monkeypatch):
    store, _, _, _, service, _, _, second, event_id = two_expert_requests(
        delegated, monkeypatch, enforced=True
    )
    prior_team = latest_team(store)
    with pytest.raises(DomainError, match="residual mandate allocation"):
        reassign_expert(service, event_id)
    assert latest_team(store) == prior_team
    with store.connection() as connection:
        # Proposal recording is allowed; approval and new execution are not.
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT state FROM worker_runs WHERE id=?", (second["id"],)
            ).fetchone()[0]
            == "blocked"
        )


@pytest.mark.parametrize("guard", ["cancel", "revoked", "unknown", "plan"])
def test_optional_token_policy_does_not_override_halt_or_authority(delegated, monkeypatch, guard):
    store, quest_id, _, _, service, manager, _, second, event_id = two_expert_requests(
        delegated, monkeypatch
    )
    if guard == "cancel":
        manager.control(second["id"], "cancel")
    else:
        with store.unit_of_work() as work:
            if guard == "revoked":
                work.connection.execute("UPDATE delegation_mandates SET state='revoked'")
            elif guard == "plan":
                work.connection.execute(
                    "UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (quest_id,)
                )
            else:
                work.connection.execute(
                    "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) VALUES (?,'unknown','unknown','file_write','{}','uncertain')",
                    (second["id"],),
                )
    with pytest.raises(DomainError):
        reassign_expert(service, event_id)
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM team_proposals").fetchone()[0] == 2


def test_repeated_help_cannot_expand_original_tool_allocation_or_spin(delegated, monkeypatch):
    store, quest_id, plan, _, service, manager, _, _, event_id = two_expert_requests(
        delegated, monkeypatch
    )
    assert reassign_expert(service, event_id)
    model = ProtocolModel(
        [tool("worker_request_help", {"role": "research", "reason": "Request another specialist"})]
    )
    manager.providers = ProviderPool({"mock-provider": model})
    third = manager.start(
        quest_id, plan["id"], "task", team_digest=latest_team(store), member_index=0
    )
    assert asyncio.run(manager.run(third["id"]))["state"] == "blocked"
    with store.connection() as connection:
        final_event = connection.execute(
            "SELECT id FROM events WHERE event_type='worker.help_requested' ORDER BY seq DESC LIMIT 1"
        ).fetchone()[0]
    for _ in range(2):
        with pytest.raises(DomainError, match="recovery budget is exhausted"):
            reassign_expert(service, final_event)
    assert len(model.requests) == 1
    with store.connection() as connection:
        assert tuple(
            connection.execute("SELECT count(*),sum(tool_limit) FROM worker_runs").fetchone()
        ) == (3, 64)
