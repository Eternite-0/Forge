"""Programmatic Worker failure/reassignment tests with explicit Mock models."""

import asyncio
import json

import httpx
import pytest
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_worker_intelligence_phase2 import delivery

from forge_core.domain import DomainError
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.model_performance import model_health_snapshot
from forge_core.provider_pool import PriceBound, ProviderPool
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.superagent_swarm import reassign_expert
from forge_core.tool_engine import ToolEngine
from forge_core.worker_model_usage import (
    completed_gateway_rejection_proof,
    reserve_worker_model_call,
)
from forge_core.worker_runtime import WorkerManager


class SettledFailure:
    execution_mode = "mock"

    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        # The Mock adapter's explicit usage contract is settled by actual Core
        # accounting. This is an injected protocol failure, never live evidence.
        raise ProviderError("truncated", usage={"input_tokens": 25, "output_tokens": 15})


def failed_worker(delegated):
    store, quest_id, mandate, graph, teams, service = delegated
    assert service.prepare(quest_id)
    original_profile = store.list_models()[0]
    store.save_model(original_profile.model_copy(update={"id": "backup", "model_id": "backup-model"}))
    with store.connection() as connection:
        team = connection.execute("SELECT proposal_digest FROM team_proposals").fetchone()[0]
    model = SettledFailure()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": model}),
        ToolEngine(store),
        team_authority=teams,
        max_repairs=0,
    )
    plan = graph.get(quest_id)
    run = manager.start(quest_id, plan["id"], "task", team_digest=team, member_index=0)
    assert asyncio.run(manager.run(run["id"]))["state"] == "blocked"
    with store.connection() as connection:
        event = connection.execute(
            "SELECT * FROM events WHERE event_type='worker.blocked' AND worker_id=? ORDER BY seq DESC LIMIT 1",
            (run["worker_id"],),
        ).fetchone()
        assert json.loads(event["payload"])["reason_code"] == "model_error"
        assert connection.execute("SELECT state FROM worker_model_reservations").fetchone()[0] == "settled"
    return store, quest_id, mandate, service, manager, run, event, model


def snapshot(store, run_id):
    with store.connection() as connection:
        return {
            "run": dict(connection.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone()),
            "ledgers": [dict(row) for row in connection.execute("SELECT * FROM worker_budget_ledger ORDER BY scope,scope_id")],
            "reservations": [dict(row) for row in connection.execute("SELECT * FROM worker_model_reservations ORDER BY call_id")],
            "teams": connection.execute("SELECT COUNT(*) FROM team_proposals").fetchone()[0],
        }


def test_settled_provider_failure_selects_different_model_without_refunding_original_allocation(delegated):
    store, quest_id, _mandate, service, manager, run, event, model = failed_worker(delegated)
    before = snapshot(store, run["id"])
    assert reassign_expert(service, event["id"])
    after = snapshot(store, run["id"])
    assert before["ledgers"] == after["ledgers"]
    assert before["reservations"] == after["reservations"]
    assert after["run"]["token_spent"] == before["run"]["token_spent"] == 40
    assert after["run"]["tool_spent"] == before["run"]["tool_spent"] == 0
    assert after["run"]["state"] == "cancelled" and len(model.requests) == 1
    with store.connection() as connection:
        approved = connection.execute("SELECT * FROM team_proposals WHERE status='approved'").fetchone()
        member = json.loads(approved["body_json"])["members"][0]
        assert member["provider_id"] == "mock-provider" and member["model_id"] == "backup-model"
        replaced = connection.execute("SELECT payload FROM events WHERE event_type='superagent.expert_reassigned'").fetchone()
        assert json.loads(replaced[0])["kind"] == "model"
    replacement = manager.start(
        quest_id, run["plan_id"], "task", team_digest=approved["proposal_digest"], member_index=0
    )
    assert replacement["state"] == "queued" and replacement["model_id"] == "backup-model"
    with store.connection() as connection:
        assert connection.execute("SELECT SUM(tool_limit) FROM worker_runs").fetchone()[0] <= 64
        assert connection.execute("SELECT SUM(token_limit) FROM worker_runs").fetchone()[0] <= 1_000_000


@pytest.mark.parametrize("unknown", ["gateway", "tool"])
def test_provider_replacement_refuses_unknown_calls_without_changing_state(delegated, unknown):
    store, quest_id, _mandate, service, _manager, run, event, _model = failed_worker(delegated)
    with store.unit_of_work() as work:
        if unknown == "gateway":
            work.connection.execute(
                "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at) "
                "VALUES ('unknown','unknown','worker',?,?,?,'mock-provider','mock-model',10,10,'unknown',0)",
                (store.workspace.id, quest_id, run["id"]),
            )
        else:
            work.connection.execute(
                "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) "
                "VALUES (?,'uncertain','uncertain','file_write','{}','uncertain')",
                (run["id"],),
            )
    before = snapshot(store, run["id"])
    with pytest.raises(DomainError, match="Unsettled"):
        reassign_expert(service, event["id"])
    assert snapshot(store, run["id"]) == before


@pytest.mark.parametrize("cap,value", [("max_workers", 1), ("max_tools", 32)])
def test_provider_replacement_cannot_exceed_residual_original_mandate(delegated, cap, value):
    store, _quest_id, mandate, service, _manager, run, event, _model = failed_worker(delegated)
    with store.unit_of_work() as work:
        policy = json.loads(work.connection.execute("SELECT policy_json FROM delegation_mandates WHERE id=?", (mandate,)).fetchone()[0])
        policy[cap] = value
        work.connection.execute("UPDATE delegation_mandates SET policy_json=? WHERE id=?", (json.dumps(policy), mandate))
    before = snapshot(store, run["id"])
    with pytest.raises(DomainError, match="recovery budget"):
        reassign_expert(service, event["id"])
    assert snapshot(store, run["id"]) == before


def rejected_gateway_worker(delegated, *, fault="reject"):
    """Actual Gateway/ledger and scripted HTTP; no real Provider is contacted."""
    store, quest_id, _mandate, graph, teams, service = delegated
    assert service.prepare(quest_id)
    profile = store.list_models()[0]
    store.save_model(profile.model_copy(update={"id": "backup", "model_id": "backup-model"}))
    requests = []

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial rejection"
            raise httpx.ReadError("scripted interrupted response body")

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["model"] == "mock-model":
            if fault == "network":
                raise httpx.ConnectError("scripted network failure")
            if fault == "partial":
                return httpx.Response(503, stream=BrokenBody())
            return httpx.Response(503, json={"error": {"code": "scripted_unavailable"}})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(delivery("Backup inspected task"))}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 15},
            },
        )

    prices = {
        ("mock-provider", model): PriceBound("mock-provider", model, "USD", 1000, 2000, "test", True, True)
        for model in ("mock-model", "backup-model")
    }
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider", kind="openai_compatible", base_url="https://fixture.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST", supports_tools=True, max_retries=0,
        ),
        credential_resolver=lambda _: "offline-fixture",
        transport=httpx.MockTransport(handle),
        budget_ledger=ModelCallBudgetLedger(
            store.path, token_limit=1_000_000, concurrency_limit=1, call_limit=8,
            price_bounds=prices, enforce_token_budgets=True,
        ),
    )
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": gateway}), ToolEngine(store), team_authority=teams
    )
    with store.connection() as connection:
        team = connection.execute("SELECT proposal_digest FROM team_proposals").fetchone()[0]
    run = manager.start(quest_id, graph.get(quest_id)["id"], "task", team_digest=team, member_index=0)
    assert asyncio.run(manager.run(run["id"]))["state"] == "blocked"
    with store.connection() as connection:
        event = connection.execute("SELECT * FROM events WHERE event_type='worker.blocked' ORDER BY seq DESC LIMIT 1").fetchone()
        reservation = dict(connection.execute("SELECT * FROM worker_model_reservations").fetchone())
        attempt = dict(connection.execute("SELECT * FROM model_budget_attempts").fetchone())
    return store, quest_id, service, manager, run, event, reservation, attempt, requests


def test_complete_http_rejection_replaces_model_but_retains_all_unknown_usage_reservations(delegated):
    store, quest_id, service, manager, run, event, reserve, attempt, requests = rejected_gateway_worker(delegated)
    assert reserve["state"] == "reserved" and reserve["input_tokens"] is reserve["output_tokens"] is None
    assert attempt["state"] == "rejected" and attempt["input_charged"] is attempt["output_charged"] is None
    assert attempt["cost_reserved"] > 0 and attempt["cost_charged"] is None
    before = snapshot(store, run["id"])
    with store.connection() as connection:
        proof = completed_gateway_rejection_proof(connection, reserve["call_id"])
        assert proof["attempt_id"] == attempt["attempt_id"]
        health = model_health_snapshot(connection, "mock-provider", "mock-model", mode="mock")
        assert health["unknown_attempts"] == health["in_flight_calls"] == 0
        assert health["known_rejected_calls"] == health["usage_unknown_calls"] == 1
        assert connection.execute("SELECT COUNT(*) FROM worker_tool_calls").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM worker_messages WHERE role='assistant'").fetchone()[0] == 0
        rejected = json.loads(connection.execute("SELECT payload FROM events WHERE event_type='worker.model_request_rejected'").fetchone()[0])
        assert rejected["usage_status"] == "unknown" and rejected["reservation_retained"] is True
    assert reassign_expert(service, event["id"])
    after = snapshot(store, run["id"])
    assert after["ledgers"] == before["ledgers"] and after["reservations"] == before["reservations"]
    assert after["run"]["token_spent"] == before["run"]["token_spent"] == reserve["reserved_tokens"]
    with store.connection() as connection:
        approved = connection.execute("SELECT * FROM team_proposals WHERE status='approved'").fetchone()
        assert dict(connection.execute("SELECT * FROM model_budget_attempts WHERE attempt_id=?", (attempt["attempt_id"],)).fetchone()) == attempt
    replacement = manager.start(quest_id, run["plan_id"], "task", team_digest=approved["proposal_digest"], member_index=0)
    assert asyncio.run(manager.run(replacement["id"]))["state"] == "review_pending"
    assert [request["model"] for request in requests] == ["mock-model", "backup-model"]
    with store.connection() as connection:
        assert dict(connection.execute("SELECT * FROM worker_model_reservations WHERE call_id=?", (reserve["call_id"],)).fetchone()) == reserve
        assert dict(connection.execute("SELECT * FROM model_budget_attempts WHERE attempt_id=?", (attempt["attempt_id"],)).fetchone()) == attempt


@pytest.mark.parametrize("fault", ["network", "partial"])
def test_transport_or_incomplete_rejection_remains_unknown_and_cannot_replace(delegated, fault):
    store, _quest_id, service, _manager, run, event, reserve, attempt, requests = rejected_gateway_worker(delegated, fault=fault)
    assert reserve["state"] == attempt["state"] == "reserved"
    before = snapshot(store, run["id"])
    with store.connection() as connection:
        assert completed_gateway_rejection_proof(connection, reserve["call_id"]) is None
        health = model_health_snapshot(connection, "mock-provider", "mock-model", mode="mock")
        assert health["known_rejected_calls"] == 0 and health["unknown_attempts"] == 1
    with pytest.raises(DomainError, match="Unsettled"):
        reassign_expert(service, event["id"])
    assert snapshot(store, run["id"]) == before and len(requests) == 1


@pytest.mark.parametrize("tamper", ["identity", "workspace", "purpose", "compaction", "unknown", "duplicate"])
def test_rejection_proof_requires_one_exact_worker_gateway_source(delegated, tamper):
    store, _quest_id, service, _manager, run, event, reserve, _attempt, _requests = rejected_gateway_worker(delegated)
    proof_call_id = reserve["call_id"]
    with store.unit_of_work() as work:
        if tamper == "identity":
            work.connection.execute("UPDATE model_budget_attempts SET model_id='other-model'")
        elif tamper == "workspace":
            work.connection.execute("UPDATE model_budget_attempts SET workspace_id='other-workspace'")
        elif tamper == "purpose":
            work.connection.execute("UPDATE model_budget_attempts SET purpose='context_compaction'")
        elif tamper == "compaction":
            row = dict(work.connection.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone())
            row["lease_token"] = "original-compactor-owner"
            proof_call_id = "context-reservation"
            reserve_worker_model_call(
                work.connection, row, proof_call_id, 100, 10, None, [], purpose="context_compaction"
            )
        elif tamper == "unknown":
            work.connection.execute("UPDATE model_budget_attempts SET state='unknown'")
        else:
            row = dict(work.connection.execute("SELECT * FROM model_budget_attempts").fetchone())
            row["attempt_id"] = "duplicate-rejection"
            columns = list(row)
            work.connection.execute(
                "INSERT INTO model_budget_attempts (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")",
                [row[column] for column in columns],
            )
    before = snapshot(store, run["id"])
    with store.connection() as connection:
        assert completed_gateway_rejection_proof(connection, proof_call_id) is None
    with pytest.raises(DomainError, match="Unsettled"):
        reassign_expert(service, event["id"])
    assert snapshot(store, run["id"]) == before
