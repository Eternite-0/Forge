"""Persistent model-call accounting; all HTTP traffic here is MockTransport."""

import asyncio
import multiprocessing
import sqlite3

import httpx
import pytest

from forge_core.model_budget_schema import migrate_model_budget
from forge_core.model_call_budget import BudgetExceeded, ModelCallBudgetLedger
from forge_core.provider_pool import PriceBound
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.runtime import ModelCallContext, ModelRequest


@pytest.mark.parametrize("value", [-1, 1.5, float("nan"), float("inf")])
def test_invalid_legacy_opening_rolls_back_migration(tmp_path, value):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path, isolation_level=None) as connection:
        connection.execute("CREATE TABLE workspace(singleton INTEGER, id TEXT)")
        connection.execute(
            "CREATE TABLE worker_runs(id TEXT, quest_id TEXT, token_spent, cost_spent_microunits, currency TEXT)"
        )
        connection.execute("INSERT INTO worker_runs VALUES ('run','quest',?,NULL,NULL)", (value,))
        connection.execute("BEGIN")
        with pytest.raises(ValueError):
            migrate_model_budget(connection)
        connection.rollback()
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='model_budget_attempts'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_isolation_period_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        ModelCallBudgetLedger(
            tmp_path / "budget.sqlite", token_limit=10, concurrency_limit=1, isolation_seconds=value
        )


def _compete(path, start, results, identifier):
    ledger = ModelCallBudgetLedger(path, token_limit=10, concurrency_limit=2, remote_max_seconds=30)
    start.wait()
    try:
        ledger.admit(
            ModelCallContext(identifier, "router", "w"),
            "p",
            "m",
            input_tokens=4,
            output_tokens=4,
            remote_timeout=30,
        )
        results.put("admitted")
    except BudgetExceeded:
        results.put("denied")


def test_two_processes_cannot_spend_last_budget_twice(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_compete, args=(path, start, results, str(i))) for i in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    assert sorted(results.get(timeout=5) for _ in processes) == ["admitted", "denied"]


def test_reservations_survive_restart_and_limit(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    ledger = ModelCallBudgetLedger(path, token_limit=10, concurrency_limit=2, remote_max_seconds=30)
    context = ModelCallContext(call_id="a", purpose="router", workspace_id="w")
    ledger.admit(context, "p", "m", input_tokens=4, output_tokens=4, remote_timeout=30)
    ledger = ModelCallBudgetLedger(path, token_limit=10, concurrency_limit=2, remote_max_seconds=30)
    with pytest.raises(BudgetExceeded):
        ledger.admit(
            ModelCallContext(call_id="b", purpose="router", workspace_id="w"),
            "p",
            "m",
            input_tokens=2,
            output_tokens=2,
            remote_timeout=30,
        )


def test_unknown_remote_bound_requires_valid_usage_to_release_slot(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    ledger = ModelCallBudgetLedger(path, token_limit=100, concurrency_limit=1)
    call = ModelCallContext("first", "router", "w")
    first = ledger.admit(call, "p", "m", input_tokens=4, output_tokens=4, remote_timeout=30)
    with pytest.raises(BudgetExceeded):
        ledger.admit(call, "p", "m", input_tokens=4, output_tokens=4, remote_timeout=30)
    ledger.settle(first, {"input_tokens": 2, "output_tokens": 1})
    second = ledger.admit(call, "p", "m", input_tokens=4, output_tokens=4, remote_timeout=30)
    ledger.settle(second, {"input_tokens": None, "output_tokens": None})
    with pytest.raises(BudgetExceeded):
        ledger.admit(call, "p", "m", input_tokens=4, output_tokens=4, remote_timeout=30)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT recover_after FROM model_budget_attempts WHERE attempt_id=?", (second,)
        ).fetchone() == (None,)


@pytest.mark.parametrize(
    "usage", [{"input_tokens": 6, "output_tokens": 1}, {"input_tokens": 1, "output_tokens": 7}]
)
def test_usage_above_reservation_is_recorded_and_fences_future_calls(tmp_path, usage):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    ledger = ModelCallBudgetLedger(path, token_limit=1000, concurrency_limit=1)
    call = ModelCallContext("first", "router", "w")
    attempt = ledger.admit(call, "p", "m", input_tokens=4, output_tokens=4, remote_timeout=30)
    ledger.settle(attempt, usage)
    with sqlite3.connect(path) as connection:
        state, inp, out = connection.execute(
            "SELECT state,input_charged,output_charged FROM model_budget_attempts"
        ).fetchone()
        assert (state, inp, out) == (
            "overrun",
            max(4, usage["input_tokens"]),
            max(4, usage["output_tokens"]),
        )
    restarted = ModelCallBudgetLedger(path, token_limit=1000, concurrency_limit=1)
    with pytest.raises(BudgetExceeded, match="fenced"):
        restarted.admit(call, "p", "m", input_tokens=1, output_tokens=1, remote_timeout=30)


def test_gateway_without_ledger_never_posts():
    calls = []
    gateway = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
        ),
        credential_resolver=lambda _: "secret",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    request = ModelRequest(
        model_id="m",
        messages=[{"role": "user", "content": "hello"}],
        budget_context=ModelCallContext("c", "router", "w"),
    )
    with pytest.raises(ProviderError, match="budget_unconfigured"):
        asyncio.run(gateway.generate(request))
    assert calls == []


def test_two_gateways_wait_for_shared_persistent_slot(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    first_ledger = ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1)
    second_ledger = ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1)
    active = 0
    peak = 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.15)
        active -= 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    config = ProviderConfig(
        id="p",
        kind="openai_compatible",
        base_url="https://example.invalid/v1",
        credential_ref="FORGE_PROVIDER_KEY_TEST",
    )
    gateways = [
        ProviderGateway(
            config,
            credential_resolver=lambda _: "secret",
            transport=httpx.MockTransport(handler),
            budget_ledger=ledger,
        )
        for ledger in (first_ledger, second_ledger)
    ]
    requests = [
        ModelRequest(
            model_id="m",
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=10,
            budget_context=ModelCallContext(str(index), "router", "w"),
        )
        for index in range(2)
    ]

    async def run_both():
        return await asyncio.gather(
            *(
                gateway.generate(request)
                for gateway, request in zip(gateways, requests, strict=True)
            )
        )

    assert len(asyncio.run(run_both())) == 2
    assert peak == 1
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM model_budget_attempts WHERE state='settled'"
            ).fetchone()[0]
            == 2
        )


def test_gateway_reserves_each_retry_and_settles_only_valid_usage(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    ledger = ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            max_retries=1,
        ),
        credential_resolver=lambda _: "secret",
        transport=httpx.MockTransport(handler),
        budget_ledger=ledger,
    )
    request = ModelRequest(
        model_id="m",
        messages=[{"role": "user", "content": "hello"}],
        max_output_tokens=10,
        budget_context=ModelCallContext("c", "router", "w"),
    )
    asyncio.run(gateway.generate(request))
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT state,input_charged,output_charged FROM model_budget_attempts ORDER BY created_at"
        ).fetchall()
    assert rows == [("rejected", None, None), ("settled", 5, 2)]


def test_429_cooldown_is_shared_and_retry_keeps_first_reserve(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    ledger = ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1)
    peer = ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0.15"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            max_retries=1,
        ),
        credential_resolver=lambda _: "secret",
        transport=httpx.MockTransport(handler),
        budget_ledger=ledger,
    )
    request = ModelRequest(
        model_id="m",
        messages=[{"role": "user", "content": "hello"}],
        max_output_tokens=10,
        budget_context=ModelCallContext("c", "router", "w"),
    )

    async def run_retry():
        task = asyncio.create_task(gateway.generate(request))
        while peer.cooldown_remaining("p") <= 0:
            await asyncio.sleep(0.01)
        with pytest.raises(BudgetExceeded):
            peer.admit(
                ModelCallContext("peer", "router", "w"),
                "p",
                "m",
                input_tokens=1,
                output_tokens=1,
                remote_timeout=30,
            )
        await task

    asyncio.run(run_retry())
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT state FROM model_budget_attempts ORDER BY created_at"
        ).fetchall() == [("rejected",), ("settled",)]


def test_early_stream_close_retains_unknown_reservation(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    ledger = ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1)
    wire = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: [DONE]\n\n'
    gateway = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
        ),
        credential_resolver=lambda _: "secret",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=wire)),
        budget_ledger=ledger,
    )
    request = ModelRequest(
        model_id="m",
        messages=[{"role": "user", "content": "hello"}],
        max_output_tokens=10,
        budget_context=ModelCallContext("c", "router", "w"),
    )

    async def consume_one():
        stream = gateway.stream(request)
        assert (await anext(stream)).type == "text_delta"
        await stream.aclose()

    asyncio.run(consume_one())
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT state,input_charged,recover_after FROM model_budget_attempts"
        ).fetchone() == ("reserved", None, None)


def test_money_limit_requires_reconciliation_and_operator_price_bound(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    bound = PriceBound("p", "m", "USD", 1_000_000, 2_000_000, "v1", True, True)
    call = ModelCallContext("c", "router", "w")
    ledger = ModelCallBudgetLedger(
        path,
        token_limit=100,
        concurrency_limit=1,
        cost_limit=30,
        currency="USD",
        price_bounds={("p", "m"): bound},
    )
    with pytest.raises(BudgetExceeded):
        ledger.admit(call, "p", "m", input_tokens=4, output_tokens=4, remote_timeout=30)
    ledger = ModelCallBudgetLedger(
        path,
        token_limit=100,
        concurrency_limit=1,
        cost_limit=30,
        currency="USD",
        price_bounds={("p", "m"): bound},
        reconciled_historical_cost=5,
    )
    attempt = ledger.admit(call, "p", "m", input_tokens=4, output_tokens=4, remote_timeout=30)
    ledger.settle(attempt, {"input_tokens": 2, "output_tokens": 1})
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT cost_reserved,cost_charged,price_version FROM model_budget_attempts"
        ).fetchone() == (12, 4, "v1")
