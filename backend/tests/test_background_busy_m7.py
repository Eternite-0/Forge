"""Transient provider-slot pressure must not exhaust semantic notice retries."""

import asyncio
import importlib
import json
import secrets

import httpx
import pytest

from forge_core.dispatcher import Dispatcher
from forge_core.domain import ForgeEvent, Quest
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.runtime import ModelCallContext, ModelRequest
from forge_core.settings import Workspace
from forge_core.store import Store


def test_router_notice_busy_does_not_consume_failure_budget(tmp_path, monkeypatch):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    event = ForgeEvent(event_type="approval.requested", payload={})
    store.append_event(event)
    dispatcher = Dispatcher(store)
    for _ in range(10):
        dispatcher.tick()
        if dispatcher.inbox("router"):
            break
    router = RouterService(store, TeamComposer(ModelRegistry(store)), model_id="mock")

    async def busy(*args):
        raise ProviderError("concurrency_unavailable")

    monkeypatch.setattr(router, "_handle_notice", busy)
    for _ in range(6):
        assert not asyncio.run(router.handle_pending_events(dispatcher))
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE event_inbox SET notice_next_attempt=0 WHERE event_id=?", (event.id,)
            )
    row = dispatcher.inbox("router")[0]
    assert row["notice_attempts"] == 0 and row["notice_dead_at"] is None
    assert row["notice_error"] == "resource_busy"


def test_router_notice_waits_for_real_gateway_slot_longer_than_two_seconds(tmp_path):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    event = ForgeEvent(event_type="approval.requested", payload={})
    store.append_event(event)
    dispatcher = Dispatcher(store)
    for _ in range(10):
        dispatcher.tick()
        if dispatcher.inbox("router"):
            break
    entered = None

    async def handler(request):
        payload = json.loads(request.content)
        if payload["model"] == "hold":
            entered.set()
            await asyncio.sleep(2.5)
        response = '{"action":"silent","reply":null}' if payload["model"] == "notice" else "ok"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": response}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    ledger = ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1)
    gateway = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_json_schema=True,
            concurrency=2,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(handler),
        budget_ledger=ledger,
    )
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=gateway,
        provider_id="p",
        model_id="notice",
    )

    async def exercise():
        nonlocal entered
        entered = asyncio.Event()
        hold = asyncio.create_task(
            gateway.generate(
                ModelRequest(
                    model_id="hold",
                    messages=[{"role": "user", "content": "hold"}],
                    max_output_tokens=20,
                    budget_context=ModelCallContext("hold", "test", store.workspace.id),
                )
            )
        )
        await entered.wait()
        assert not await router.handle_pending_events(dispatcher)
        row = dispatcher.inbox("router")[0]
        assert row["notice_attempts"] == 0 and row["notice_error"] == "resource_busy"
        await hold
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE event_inbox SET notice_next_attempt=0 WHERE event_id=?", (event.id,)
            )
        assert await router.handle_pending_events(dispatcher)

    asyncio.run(exercise())
    assert dispatcher.inbox("router")[0]["processed_at"] is not None
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 2


@pytest.mark.parametrize("consumer", ["planner", "composer"])
def test_background_planner_and_composer_busy_do_not_consume_retries(
    tmp_path, monkeypatch, consumer
):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("FORGE_DB_PATH", raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="background", goal="test")
    main.store.save_quest(quest)
    if consumer == "planner":
        event = ForgeEvent(event_type="quest.proposed", quest_id=quest.id, payload={})
        main.store.append_event(event)
    else:
        plan = main.quest_graph.propose(
            quest.id,
            {
                "nodes": [
                    {
                        "key": "n",
                        "objective": "work",
                        "success_criteria": ["done"],
                        "budget_tokens": 10000,
                    }
                ],
                "edges": [],
                "max_total_tokens": 10000,
            },
            expected_version=0,
        )
        main.quest_graph.approve(
            quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
        )
    for _ in range(20):
        main.dispatcher.tick()
        if main.dispatcher.inbox(consumer):
            break

    class Busy:
        execution_mode = "mock"

        async def generate(self, request):
            raise ProviderError("concurrency_unavailable")

    main.router.provider = Busy()
    main.router.model_id = "mock"
    operation = main.process_pending_plan if consumer == "planner" else main.process_pending_team
    for _ in range(6):
        assert not asyncio.run(operation())
        with main.store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE event_inbox SET notice_next_attempt=0 WHERE consumer_id=?", (consumer,)
            )
    row = next(
        item for item in main.dispatcher.inbox(consumer) if item["notice_error"] == "resource_busy"
    )
    assert row["notice_attempts"] == 0 and row["notice_dead_at"] is None
