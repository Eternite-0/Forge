"""Optional cumulative limits never disable native context, counts or accounting."""

import asyncio
import copy
import json
import sqlite3

import pytest
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_worker_intelligence_phase2 import ProtocolModel, delivery, prepared

from forge_core.domain import DomainError
from forge_core.evidence import CompletionGate, NodeVerifier
from forge_core.independent_review import IndependentReviewService, _review_reservation
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelRequest
from forge_core.team_composition import TeamCompositionService
from forge_core.worker_envelope import cycle_allowance
from forge_core.worker_runtime import WorkerManager


def tiny_declared_allocations(tmp_path, monkeypatch):
    """Approve small numeric estimates before starting; never rewrite approved contracts."""
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    propose_plan = QuestGraphService.propose
    propose_team = TeamCompositionService.propose
    initialize = WorkerManager.__init__

    def plan(self, quest_id, graph, **kwargs):
        graph = copy.deepcopy(graph)
        graph["max_total_tokens"] = 32
        graph["nodes"][0]["budget_tokens"] = 32
        return propose_plan(self, quest_id, graph, **kwargs)

    def team(self, quest_id, roles, limits, **kwargs):
        roles = copy.deepcopy(roles)
        for role in roles:
            role.update(input_tokens=0, output_tokens=64)
        return propose_team(self, quest_id, roles, {**limits, "max_total_tokens": 64}, **kwargs)

    def manager(self, *args, **kwargs):
        initialize(self, *args, **{**kwargs, "global_token_limit": 1})

    monkeypatch.setattr(QuestGraphService, "propose", plan)
    monkeypatch.setattr(TeamCompositionService, "propose", team)
    monkeypatch.setattr(WorkerManager, "__init__", manager)
    # Select the protocol fixture's mode before the approved Worker is created.
    monkeypatch.setattr(ProtocolModel, "execution_mode", "live")
    return prepared(tmp_path, [], tools=["file_read"])


@pytest.mark.parametrize("revision", [False, True])
def test_unbounded_native_http_crosses_global_quest_team_and_worker_estimates_with_real_settlement(
    tmp_path, monkeypatch, revision
):
    store, manager, scripted, worker = tiny_declared_allocations(tmp_path, monkeypatch)

    def responder(body, ordinal):
        assert body["max_tokens"] == 64
        assert len(json.dumps(body, ensure_ascii=False).encode()) + 64 + 64 <= 64000
        if ordinal == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "native-read",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "work/input.txt"}),
                        },
                    }
                ],
            }
        if ordinal == 2 or (revision and ordinal == 4):
            return {"content": json.dumps(delivery("Complete original source analysis."))}
        assert ordinal in ({3, 5} if revision else {3}) and not body.get("tools")
        needs_revision = revision and ordinal == 3
        return {
            "content": json.dumps(
                {
                    "verdict": "NEEDS_REVISION" if needs_revision else "PASS",
                    "findings": ["Clarify the existing source citation."] if needs_revision else [],
                    "revision_goals": ["State the complete verified source reference."]
                    if needs_revision
                    else [],
                    "confidence": 0.8,
                }
            )
        }

    with offline_loopback_gateway(store, responder) as (gateway, requests):
        gateway._budget_ledger = ModelCallBudgetLedger(
            store.path, token_limit=1, concurrency_limit=1
        )
        manager.providers = ProviderPool({"protocol": gateway})
        result = asyncio.run(manager.run(worker["id"]))
        assert (
            result["state"] == "review_pending"
            and result["token_spent"] == 80 > result["token_limit"]
        )
        assert result["output_limit"] == 64 and result["steps"] == 2 and result["tool_spent"] == 1
        review = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
        if revision:
            assert review["verdict"] == "NEEDS_REVISION" and review["revision_applied"]
            assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
            review = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
        assert review["verdict"] == "PASS"
        assert NodeVerifier(store).verify(worker["quest_id"], "n")["status"] == "completed"
        expected_calls = 5 if revision else 3
        assert len(requests) == expected_calls and not scripted.requests
        with store.connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            CompletionGate(store).check(
                type("ReadWork", (), {"connection": connection})(),
                connection.execute(
                    "SELECT * FROM quests WHERE id=?", (worker["quest_id"],)
                ).fetchone(),
            )
            assert (
                connection.execute("SELECT token_spent FROM worker_runs").fetchone()[0]
                == 40 * expected_calls
            )
            attempts = connection.execute(
                "SELECT state,input_charged,output_charged FROM model_budget_attempts"
            ).fetchall()
            assert len(attempts) == expected_calls and all(
                row["state"] == "settled" for row in attempts
            )
            assert (
                sum(row["input_charged"] + row["output_charged"] for row in attempts)
                == 40 * expected_calls
            )
            assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
            assert connection.total_changes == 0


def test_unbounded_allocation_still_enforces_tool_quota_and_tracks_actual_allocation(monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE worker_budget_ledger(scope TEXT,scope_id TEXT,token_limit INTEGER,tool_limit INTEGER,tokens_allocated INTEGER DEFAULT 0,tools_allocated INTEGER DEFAULT 0,UNIQUE(scope,scope_id))"
    )
    WorkerManager._reserve(connection, "global", "g", 1, 100, 1, 1)
    assert (
        connection.execute("SELECT tokens_allocated FROM worker_budget_ledger").fetchone()[0] == 100
    )
    with pytest.raises(DomainError, match="Worker budget exhausted"):
        WorkerManager._reserve(connection, "global", "g", 1, 100, 1, 1)


def test_unbounded_review_reservation_retains_output_and_context_limits(monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    row = {"token_limit": 1, "token_spent": 500, "model_context_window": 5000}
    request = ModelRequest(
        provider_id="p",
        model_id="m",
        messages=[{"role": "user", "content": "evidence"}],
        max_output_tokens=64,
    )
    assert _review_reservation(object(), request, row, 0) > row["token_limit"]
    assert request.max_output_tokens == 64 and request.context_limit == 5000
    row["model_context_window"] = 10
    with pytest.raises(DomainError):
        _review_reservation(object(), request, row, 0)


def test_unbounded_correction_allowance_still_consumes_finite_durable_call_count(monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        "CREATE TABLE events(seq INTEGER PRIMARY KEY,event_type TEXT,payload TEXT,created_at TEXT); CREATE TABLE worker_model_reservations(run_id TEXT,created_at TEXT,state TEXT);"
    )
    envelope = {
        "version": "worker-capacity-v1",
        "max_source_bytes": 1000,
        "max_artifact_bytes": 1000,
        "max_delivery_bytes": 1000,
        "correction_input_tokens": 4000,
        "correction_output_tokens": 64,
        "correction_calls": 1,
    }
    run = {"id": "r", "token_limit": 1, "token_spent": 500}
    assert cycle_allowance(connection, run, envelope)["current_request_allowance"] is None
    connection.execute(
        "INSERT INTO events VALUES (1,'worker.progress',?,'2030')",
        (json.dumps({"run_id": "r", "phase": "self_repair", "correction_reservation_cursor": 0}),),
    )
    connection.execute("INSERT INTO worker_model_reservations VALUES ('r','2030','unknown')")
    with pytest.raises(DomainError, match="correction call count exhausted"):
        cycle_allowance(connection, run, envelope)


def test_unbounded_unknown_gateway_attempt_stays_unsettled(tmp_path, monkeypatch):
    from test_worker_intelligence_phase2 import (
        test_outer_worker_timeout_keeps_actual_gateway_attempt_unknown as verify_unknown_attempt,
    )

    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    verify_unknown_attempt(tmp_path, monkeypatch)


@pytest.mark.parametrize("invalid", ["expired", "competing_owner", "paused", "plan_changed"])
def test_unbounded_token_policy_cannot_renew_invalid_worker_lease(tmp_path, monkeypatch, invalid):
    from test_worker_intelligence_phase2 import (
        test_worker_lease_renewal_never_revives_stale_or_unauthorized_claim as verify_invalid_lease,
    )

    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    verify_invalid_lease(tmp_path, invalid)


def test_unbounded_token_policy_keeps_original_task_deadline(tmp_path, monkeypatch):
    from test_worker_intelligence_phase2 import (
        test_worker_lease_renewal_stays_inside_original_task_deadline as verify_deadline,
    )

    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    verify_deadline(tmp_path)
