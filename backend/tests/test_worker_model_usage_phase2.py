"""Real Gateway accounting over offline HTTP, including ownership changes."""

import asyncio
import json

import httpx
import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response
from test_worker_runtime import MockApprovedTeam, MockToolEngine

from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import PriceBound, ProviderPool
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_model_usage import (
    reconcile_worker_model_calls,
    refund_unsent_worker_model_call,
    settle_worker_model_call,
)
from forge_core.worker_runtime import WorkerManager, WorkerScheduler

pytest_plugins = ["test_worker_runtime"]


def http_result(*, old=False):
    message = {"content": json.dumps(delivery("fresh response"))}
    if old:
        message = {
            "content": "obsolete response must never enter Worker history",
            "tool_calls": [
                {
                    "id": "obsolete-write",
                    "type": "function",
                    "function": {
                        "name": "file_write",
                        "arguments": json.dumps(
                            {"path": "work/obsolete.txt", "content": "must not exist"}
                        ),
                    },
                }
            ],
        }
    return httpx.Response(
        200,
        json={
            "choices": [{"message": message, "finish_reason": "tool_calls" if old else "stop"}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 15},
        },
    )


def gateway_for(store, handler):
    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            concurrency=2,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(handler),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=200000, concurrency_limit=2),
    )
    return gateway


def records(store):
    with store.connection() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM worker_model_reservations")]


def no_old_response(store, tmp_path):
    assert not (tmp_path / "work/obsolete.txt").exists()
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0
        assert not any(
            "obsolete response" in row[0]
            for row in connection.execute("SELECT message_json FROM worker_messages")
        )


@pytest.mark.parametrize("control", ["pause", "cancel"])
def test_settled_late_response_only_reconciles_its_exact_reserve(tmp_path, control):
    store, manager, _model, worker = prepared(tmp_path, [], tools=["file_read", "file_write"])

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handle(_request):
            entered.set()
            await release.wait()
            return http_result(old=True)

        manager.providers = ProviderPool({"protocol": gateway_for(store, handle)})
        task = asyncio.create_task(manager.run(worker["id"]))
        await entered.wait()
        reserved = manager.get(worker["id"])
        with store.connection() as connection:
            allocation = [
                tuple(row) for row in connection.execute("SELECT * FROM worker_budget_ledger")
            ]
        manager.control(worker["id"], control)
        controlled = manager.get(worker["id"])
        release.set()
        result = await task
        assert result["state"] == controlled["state"]
        assert result["generation"] == controlled["generation"]
        assert result["lease_token"] is None
        assert result["token_spent"] == 40 < reserved["token_spent"]
        assert result["steps"] == 1 and result["repairs"] == result["tool_spent"] == 0
        record = records(store)[0]
        assert record["state"] == "settled"
        assert record["reserved_tokens"] == reserved["token_spent"]
        with store.unit_of_work() as work:
            assert reconcile_worker_model_calls(work.connection, worker["id"]) == 0
            assert settle_worker_model_call(
                work.connection, record["call_id"], usage={"input_tokens": 25, "output_tokens": 15}
            )
        assert manager.get(worker["id"])["token_spent"] == 40
        with store.connection() as connection:
            assert [
                tuple(row) for row in connection.execute("SELECT * FROM worker_budget_ledger")
            ] == allocation
            assert (
                connection.execute(
                    "SELECT count(*) FROM events WHERE event_type='worker.result'"
                ).fetchone()[0]
                == 0
            )
        no_old_response(store, tmp_path)

    asyncio.run(scenario())


def test_old_settlement_never_refunds_new_owners_live_reservation(tmp_path):
    store, manager, _model, worker = prepared(tmp_path, [], tools=["file_read", "file_write"])

    async def scenario():
        entered = [asyncio.Event(), asyncio.Event()]
        releases = [asyncio.Event(), asyncio.Event()]
        seen = []

        async def handle(request):
            index = len(seen)
            seen.append(request)
            entered[index].set()
            await releases[index].wait()
            return http_result(old=index == 0)

        pool = ProviderPool({"protocol": gateway_for(store, handle)})
        manager.providers = pool
        old_task = asyncio.create_task(manager.run(worker["id"]))
        await entered[0].wait()
        old_record = records(store)[0]
        manager.control(worker["id"], "pause")
        manager.control(worker["id"], "resume")
        second = WorkerManager(
            store, pool, ToolEngine(store), team_authority=TeamCompositionService(store)
        )
        new_task = asyncio.create_task(second.run(worker["id"]))
        await entered[1].wait()
        current = manager.get(worker["id"])
        new_record = records(store)[1]
        assert (
            current["token_spent"] == old_record["reserved_tokens"] + new_record["reserved_tokens"]
        )
        releases[0].set()
        await old_task
        after = manager.get(worker["id"])
        assert after["token_spent"] == new_record["reserved_tokens"] + 40
        for key in ("generation", "lease_token", "lease_until", "state", "steps"):
            assert after[key] == current[key]
        assert records(store)[1]["state"] == "reserved"
        no_old_response(store, tmp_path)
        releases[1].set()
        result = await new_task
        assert result["state"] == "review_pending" and result["token_spent"] == 80
        assert result["steps"] == 2 and result["tool_spent"] == 0
        assert len(seen) == 2 and all(row["state"] == "settled" for row in records(store))
        no_old_response(store, tmp_path)

    asyncio.run(scenario())


def test_reopen_recovers_settled_call_without_replaying_crashed_response(tmp_path):
    store, manager, _model, worker = prepared(tmp_path, [], tools=["file_read", "file_write"])
    posts = []

    def handle(request):
        posts.append(request)
        return http_result(old=len(posts) == 1)

    gateway = gateway_for(store, handle)
    generate = gateway.generate

    async def crash(request):
        await generate(request)
        raise asyncio.CancelledError

    gateway.generate = crash
    manager.providers = ProviderPool({"protocol": gateway})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.run(worker["id"]))
    retained = manager.get(worker["id"])
    assert retained["token_spent"] > 40 and records(store)[0]["state"] == "reserved"
    no_old_response(store, tmp_path)
    manager.control(worker["id"], "pause")
    manager.control(worker["id"], "resume")
    reopened = Store(store.path, workspace=store.workspace)
    gateway.generate = generate
    second = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    assert asyncio.run(second.run(worker["id"]))["state"] == "review_pending"
    assert second.get(worker["id"])["token_spent"] == 80
    assert len(posts) == 2 and all(row["state"] == "settled" for row in records(store))
    no_old_response(store, tmp_path)


@pytest.mark.parametrize("control", ["pause", "cancel"])
@pytest.mark.parametrize("known", [False, True])
def test_tick_recovers_all_states_accounting_only_after_crash(tmp_path, control, known):
    store, manager, _model, worker = prepared(tmp_path, [], tools=["file_read", "file_write"])
    posts = []

    def handle(request):
        posts.append(request)
        return http_result(old=True)

    gateway = gateway_for(store, handle)
    generate = gateway.generate

    async def crash(request):
        await generate(request)
        raise asyncio.CancelledError

    gateway.generate = crash
    manager.providers = ProviderPool({"protocol": gateway})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.run(worker["id"]))
    manager.control(worker["id"], control)
    controlled = manager.get(worker["id"])
    assert records(store)[0]["state"] == "reserved"
    with store.unit_of_work() as work:
        if not known:
            # Synthetic unknown outcome must retain its full original reserve.
            work.connection.execute(
                "UPDATE model_budget_attempts SET state='unknown',input_charged=NULL,output_charged=NULL"
            )
        allocation = [
            tuple(row) for row in work.connection.execute("SELECT * FROM worker_budget_ledger")
        ]
        messages = [tuple(row) for row in work.connection.execute("SELECT * FROM worker_messages")]
        events = work.connection.execute("SELECT count(*) FROM events").fetchone()[0]
    reopened = Store(store.path, workspace=store.workspace)
    second = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )

    async def recover():
        scheduler = WorkerScheduler(second)
        assert await scheduler.tick() == 0
        assert await scheduler.tick() == 0
        assert not scheduler.tasks
        await scheduler.shutdown()

    asyncio.run(recover())
    expected = dict(controlled)
    if known:
        expected["token_spent"] = 40
    assert second.get(worker["id"]) == expected
    assert records(store)[0]["state"] == ("settled" if known else "reserved")
    assert len(posts) == 1
    with store.connection() as connection:
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM worker_budget_ledger")
        ] == allocation
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM worker_messages")
        ] == messages
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == events
    no_old_response(store, tmp_path)


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "reserved",
        "unknown",
        "overrun",
        "wrong_run",
        "wrong_provider",
        "null_usage",
        "wrong_call",
    ],
)
@pytest.mark.parametrize("execution_mode", ["mock", "live"])
def test_unproven_attempts_cannot_release_any_local_reservation(tmp_path, invalid, execution_mode):
    store, manager, model, worker = prepared(tmp_path, [])
    captures = []
    # This scripted adapter tests both synchronous protocol modes offline.
    model.execution_mode = execution_mode
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET execution_mode=? WHERE id=?", (execution_mode, worker["id"])
        )

    async def interrupt(request):
        captures.append(request)
        request._budget_post_started = True
        raise asyncio.CancelledError

    model.generate = interrupt
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.run(worker["id"]))
    original = manager.get(worker["id"])
    record = records(store)[0]
    if invalid != "missing":
        with store.unit_of_work() as work:
            work.connection.execute(
                "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,"
                "worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at,"
                "input_charged,output_charged) VALUES ('attempt',?,'worker',?,?,?,?,?,1000,100,?,0,?,15)",
                (
                    "another-call" if invalid == "wrong_call" else record["call_id"],
                    store.workspace.id,
                    worker["quest_id"],
                    "another-run" if invalid == "wrong_run" else worker["id"],
                    "another-provider" if invalid == "wrong_provider" else "protocol",
                    "fixture",
                    invalid if invalid in {"reserved", "unknown", "overrun"} else "settled",
                    None if invalid == "null_usage" else 25,
                ),
            )
    with store.unit_of_work() as work:
        assert not settle_worker_model_call(work.connection, record["call_id"])
        assert not settle_worker_model_call(
            work.connection,
            record["call_id"],
            usage={"input_tokens": 25, "output_tokens": 15},
            current_lease_token="wrong-owner",
        )
        if invalid not in {"missing", "wrong_call"}:
            # A current owner cannot replace an unproven Gateway attempt with
            # synchronous reported usage, in either execution protocol mode.
            assert not settle_worker_model_call(
                work.connection,
                record["call_id"],
                usage={"input_tokens": 25, "output_tokens": 15},
                current_lease_token=original["lease_token"],
            )
        assert reconcile_worker_model_calls(work.connection, worker["id"]) == 0
    assert manager.get(worker["id"])["token_spent"] == original["token_spent"]
    assert records(store)[0]["state"] == "reserved"


def test_current_synchronous_live_adapter_keeps_original_response_usage_contract(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [response(delivery())])
    # Simulate the synchronous live adapter contract; no real model is called.
    model.execution_mode = "live"
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET execution_mode='live' WHERE id=?", (worker["id"],)
        )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["token_spent"] == 40
    assert result["steps"] == 1 and records(store)[0]["state"] == "settled"
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM model_budget_attempts").fetchone()[0] == 0
    with store.unit_of_work() as work:
        assert reconcile_worker_model_calls(work.connection, worker["id"]) == 0
    assert manager.get(worker["id"])["token_spent"] == 40


def test_stale_synchronous_live_response_cannot_settle_without_gateway_proof(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_read", "file_write"])
    model.execution_mode = "live"
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET execution_mode='live' WHERE id=?", (worker["id"],)
        )

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(_request):
            entered.set()
            await release.wait()
            return response(
                "obsolete response must never enter Worker history",
                calls=[
                    {
                        "id": "obsolete-write",
                        "name": "file_write",
                        "arguments": {"path": "work/obsolete.txt", "content": "must not exist"},
                    }
                ],
            )

        model.generate = delayed
        task = asyncio.create_task(manager.run(worker["id"]))
        await entered.wait()
        reservation = records(store)[0]
        manager.control(worker["id"], "pause")
        controlled = manager.get(worker["id"])
        release.set()
        assert await task == controlled
        with store.unit_of_work() as work:
            assert not settle_worker_model_call(
                work.connection,
                reservation["call_id"],
                usage={"input_tokens": 25, "output_tokens": 15},
                current_lease_token=reservation["lease_token"],
            )
            assert reconcile_worker_model_calls(work.connection, worker["id"]) == 0
        assert manager.get(worker["id"]) == controlled
        assert records(store)[0]["state"] == "reserved"
        no_old_response(store, tmp_path)

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", [None, "input_price", "price_version", "cost_charged"])
def test_late_cost_settlement_only_releases_original_scopes_once(graph, tmp_path, mismatch):
    store, quest, plan = graph
    price = PriceBound(
        "mock-provider", "mock-model", "USD", 1_000_000, 2_000_000, "v14", True, True
    )
    bounds = {(price.provider_id, price.model_id): price}
    ledger = ModelCallBudgetLedger(
        store.path, token_limit=10000, concurrency_limit=1, price_bounds=bounds
    )

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handle(_request):
            entered.set()
            await release.wait()
            return http_result(old=True)

        gateway = ProviderGateway(
            ProviderConfig(
                id="mock-provider",
                kind="openai_compatible",
                base_url="https://example.invalid/v1",
                credential_ref="FORGE_PROVIDER_KEY_TEST",
                supports_tools=True,
            ),
            credential_resolver=lambda _: "fixture",
            transport=httpx.MockTransport(handle),
            budget_ledger=ledger,
        )
        generate = gateway.generate

        async def changed_attempt(request):
            result = await generate(request)
            if mismatch:
                # Synthetic ledger corruption exercises fail-closed accounting;
                # it is deliberately separate from the normal offline Gateway.
                with store.unit_of_work() as work:
                    work.connection.execute(
                        f"UPDATE model_budget_attempts SET {mismatch}=? WHERE call_id=?",
                        (
                            "different-version" if mismatch == "price_version" else 1,
                            request.budget_context.call_id,
                        ),
                    )
            return result

        gateway.generate = changed_attempt
        manager = WorkerManager(
            store,
            ProviderPool({"mock-provider": gateway}),
            MockToolEngine(),
            team_authority=MockApprovedTeam(plan, cost_cap=5000),
            price_bounds=bounds,
            global_cost_limits={"USD": 5000},
        )
        worker = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
        task = asyncio.create_task(manager.run(worker["id"]))
        await entered.wait()
        reservation = records(store)[0]
        reserved = manager.get(worker["id"])
        assert reserved["cost_spent_microunits"] == reservation["reserved_cost_microunits"] > 55
        assert json.loads(reservation["cost_scopes_json"]) == [
            {"scope": "quest", "scope_id": quest.id, "currency": "USD"},
            {"scope": "global", "scope_id": "global", "currency": "USD"},
        ]
        with store.unit_of_work() as work:
            allocation = [
                tuple(row) for row in work.connection.execute("SELECT * FROM worker_budget_ledger")
            ]
            # A later scope was never part of this call's local reservation.
            work.connection.execute(
                "INSERT INTO worker_cost_ledger VALUES ('quest','other-quest','USD',5000,123)"
            )
        manager.control(worker["id"], "pause")
        release.set()
        result = await task
        assert result["state"] == "paused" and result["steps"] == 1 and result["repairs"] == 0
        expected_cost = reservation["reserved_cost_microunits"] if mismatch else 55
        expected_tokens = reservation["reserved_tokens"] if mismatch else 40
        assert result["cost_spent_microunits"] == expected_cost
        assert result["token_spent"] == expected_tokens
        assert records(store)[0]["state"] == ("reserved" if mismatch else "settled")
        with store.unit_of_work() as work:
            assert reconcile_worker_model_calls(work.connection, worker["id"]) == 0
            assert settle_worker_model_call(work.connection, reservation["call_id"]) is (
                mismatch is None
            )
            costs = {
                (row["scope"], row["scope_id"]): row["reserved_microunits"]
                for row in work.connection.execute("SELECT * FROM worker_cost_ledger")
            }
            assert costs == {
                ("quest", quest.id): expected_cost,
                ("global", "global"): expected_cost,
                ("quest", "other-quest"): 123,
            }
            assert [
                tuple(row) for row in work.connection.execute("SELECT * FROM worker_budget_ledger")
            ] == allocation
        assert manager.get(worker["id"])["token_spent"] == expected_tokens
        no_old_response(store, tmp_path)

    asyncio.run(scenario())


def test_mock_current_owner_and_unsent_refund_remain_exactly_once(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [response(delivery())])
    generate = model.generate
    calls = []

    async def busy(request):
        calls.append(request)
        if len(calls) == 1:
            request._budget_post_started = False
            raise ProviderError("concurrency_unavailable")
        return await generate(request)

    model.generate = busy
    assert asyncio.run(manager.run(worker["id"]))["state"] == "queued"
    first = records(store)[0]
    assert first["state"] == "refunded"
    assert manager.get(worker["id"])["token_spent"] == manager.get(worker["id"])["steps"] == 0
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET next_attempt_at=0 WHERE id=?", (worker["id"],)
        )
        assert not refund_unsent_worker_model_call(
            work.connection, first["call_id"], first["lease_token"]
        )
        assert not settle_worker_model_call(
            work.connection, first["call_id"], usage={"input_tokens": 25, "output_tokens": 15}
        )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert manager.get(worker["id"])["token_spent"] == 40
    assert [row["state"] for row in records(store)] == ["refunded", "settled"]
