"""Native HTTP/Gateway serial integration; scripted local server, no real model."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_serial_capacity_v17 import serial_team
from test_worker_intelligence_phase2 import delivery

from forge_core.domain import DomainError
from forge_core.evidence import CompletionGate, NodeVerifier
from forge_core.governance import GovernanceService
from forge_core.independent_review import IndependentReviewService
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_serial_runtime_rejects_global_cost_fallback_before_http(tmp_path):
    store, quest, plan, team, _roles, _limits, _teams, _ledger = serial_team(tmp_path)

    def unexpected(_body, _ordinal):
        raise AssertionError("Cost-unsupported serial admission must not reach HTTP")

    with offline_loopback_gateway(store, unexpected) as (gateway, requests):
        gateway.config = replace(gateway.config, id="p")
        gateway._budget_ledger = ModelCallBudgetLedger(
            store.path, token_limit=180000, concurrency_limit=1
        )
        providers = ProviderPool({"p": gateway})
        manager = WorkerManager(
            store,
            providers,
            ToolEngine(store),
            team_authority=TeamCompositionService(
                store, capacity_providers=providers, budget_ledger=gateway._budget_ledger
            ),
            global_token_limit=180000,
            global_cost_limits={"USD": 100000},
        )
        with pytest.raises(DomainError, match="cost-capped"):
            manager.start(
                quest.id,
                plan["id"],
                "n0",
                team_digest=team["proposal_digest"],
                member_index=0,
            )
        assert not requests
        with store.connection() as connection:
            assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0
            assert (
                connection.execute("SELECT count(*) FROM model_budget_attempts").fetchone()[0] == 0
            )
            assert (
                connection.execute("SELECT count(*) FROM worker_budget_ledger").fetchone()[0] == 0
            )


@pytest.mark.parametrize("tamper", [False, True])
def test_native_serial_two_nodes_reopen_keep_one_pool_and_original_cap(tmp_path, tamper):
    store, quest, plan, team, _roles, _limits, _teams, _ledger = serial_team(tmp_path)
    GovernanceService(store).grant_workspace(quest.id, "inputs", "local_user")
    first_delivery = delivery("Complete original producer findings and source reference.")
    first_delivery["findings"] = [
        "中文 source is retained whole; no model result substitutes evidence."
    ]

    def responder(body, ordinal):
        if ordinal in {1, 4}:
            name = "file_read" if ordinal == 1 else "worker_read_completed_delivery"
            arguments = {"path": "inputs/unicode.json"} if ordinal == 1 else {"node_key": "n0"}
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": f"native-{ordinal}",
                        "type": "function",
                        "function": {"name": name, "arguments": canonical(arguments)},
                    }
                ],
            }
        if ordinal in {2, 5}:
            return {
                "content": canonical(
                    first_delivery
                    if ordinal == 2
                    else delivery("Independently checked predecessor findings.")
                )
            }
        assert ordinal in {3, 6} and not body.get("tools")
        return {
            "content": canonical(
                {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
            )
        }

    with offline_loopback_gateway(store, responder) as (gateway, requests):
        # Reuse the native local HTTP server without overriding execution mode
        # or manufacturing attempts/settlements. Match the approved fixture ID.
        gateway.config = replace(gateway.config, id="p")
        gateway._budget_ledger = ModelCallBudgetLedger(
            store.path, token_limit=180000, concurrency_limit=1
        )
        assert gateway.execution_mode == "live"
        providers = ProviderPool({"p": gateway})
        teams = TeamCompositionService(
            store, capacity_providers=providers, budget_ledger=gateway._budget_ledger
        )
        manager = WorkerManager(
            store, providers, ToolEngine(store), team_authority=teams, global_token_limit=180000
        )
        first = manager.start(
            quest.id, plan["id"], "n0", team_digest=team["proposal_digest"], member_index=0
        )
        assert first["token_limit"] == 125000
        with pytest.raises(DomainError):
            manager.start(
                quest.id, plan["id"], "reader", team_digest=team["proposal_digest"], member_index=1
            )
        if tamper:
            with store.unit_of_work() as work:
                row = work.connection.execute(
                    "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (first["id"],)
                ).fetchone()
                capsule = json.loads(row[0])
                capsule["serial_start_allowance"]["execution_token_limit"] -= 1
                work.connection.execute(
                    "UPDATE worker_capsules SET capsule_json=? WHERE run_id=?",
                    (canonical(capsule), first["id"]),
                )
            with pytest.raises(DomainError):
                asyncio.run(manager.run(first["id"]))
            assert requests == []
            return
        assert asyncio.run(manager.run(first["id"]))["state"] == "review_pending"
        assert (
            asyncio.run(IndependentReviewService(manager).review(first["id"]))["verdict"] == "PASS"
        )
        NodeVerifier(store).verify(quest.id, "n0")
        assert manager.get(first["id"])["state"] == "succeeded"
        with store.connection() as connection:
            original_pools = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM worker_budget_ledger WHERE scope='serial_reserve' ORDER BY scope_id"
                )
            ]
            assert len(original_pools) == 3
            assert all(row["tokens_allocated"] == 100000 for row in original_pools)
            original_attempts = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM model_budget_attempts ORDER BY created_at"
                )
            ]
        reopened = Store(store.path, workspace=store.workspace)
        teams = TeamCompositionService(
            reopened, capacity_providers=providers, budget_ledger=gateway._budget_ledger
        )
        resumed = WorkerManager(
            reopened,
            providers,
            ToolEngine(reopened),
            team_authority=teams,
            global_token_limit=180000,
        )
        second = resumed.start(
            quest.id, plan["id"], "reader", team_digest=team["proposal_digest"], member_index=1
        )
        assert second["token_limit"] == first["token_limit"] == 125000
        assert asyncio.run(resumed.run(second["id"]))["state"] == "review_pending"
        assert (
            asyncio.run(IndependentReviewService(resumed).review(second["id"]))["verdict"] == "PASS"
        )
        assert NodeVerifier(reopened).verify(quest.id, "reader")["status"] == "completed"
        assert len(requests) == 6
        tool_message = next(item for item in requests[4]["messages"] if item["role"] == "tool")
        assert json.loads(tool_message["content"])["delivery"] == first_delivery
        with reopened.connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            attempts = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM model_budget_attempts ORDER BY created_at"
                )
            ]
            assert attempts[:3] == original_attempts
            assert len(attempts) == 6 and all(row["state"] == "settled" for row in attempts)
            charged = sum(row["input_charged"] + row["output_charged"] for row in attempts)
            assert charged == 240 and charged < gateway._budget_ledger.token_limit == 180000
            assert all(
                row["input_charged"] is not None and row["output_charged"] is not None
                for row in attempts
            )
            pools = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM worker_budget_ledger WHERE scope='serial_reserve' ORDER BY scope_id"
                )
            ]
            assert pools == original_pools
            allocations = connection.execute(
                "SELECT * FROM worker_budget_ledger WHERE scope IN ('global','quest','team')"
            ).fetchall()
            assert len(allocations) == 3
            assert all(
                row["tokens_allocated"] == 150000 <= row["token_limit"] <= 180000
                for row in allocations
            )
            runs = connection.execute("SELECT * FROM worker_runs ORDER BY created_at").fetchall()
            assert len(runs) == 2 and sum(row["token_spent"] for row in runs) == 240
            assert all(
                row["state"] == "succeeded" and row["execution_mode"] == "live" for row in runs
            )
            work = type("ReadWork", (), {"connection": connection})()
            CompletionGate(reopened).check(
                work, connection.execute("SELECT * FROM quests WHERE id=?", (quest.id,)).fetchone()
            )
            assert connection.total_changes == 0
