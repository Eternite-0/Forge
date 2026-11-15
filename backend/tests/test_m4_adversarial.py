"""Independent real-SQLite M4 adversarial regression tests."""

import pytest

from forge_core.domain import DomainError, Quest, QuestStatus
from forge_core.quest_graph import QuestGraphService
from forge_core.quest_service import QuestService
from forge_core.settings import Workspace
from forge_core.store import Store


def test_cancelled_quest_never_has_ready_plan_or_plan_control(tmp_path):
    store = Store(tmp_path / "m4.sqlite3", workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="test", goal="test")
    store.save_quest(quest)
    graph = QuestGraphService(store)
    plan = graph.propose(
        quest.id,
        {
            "nodes": [
                {"key": "a", "objective": "work", "success_criteria": ["done"], "budget_tokens": 10}
            ],
            "edges": [],
        },
        expected_version=0,
    )
    graph.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    QuestService(store).transition(quest.id, QuestStatus.CANCELLED, expected_version=2)
    assert graph.readiness(quest.id)["a"] == "inactive"
    with pytest.raises(DomainError):
        graph.control(quest.id, "pause", expected_version=3, actor="local_user")


def test_concurrent_notice_failure_does_not_dead_letter_successful_inflight(tmp_path):
    import asyncio

    from forge_core.dispatcher import Dispatcher
    from forge_core.domain import ForgeEvent
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store = Store(tmp_path / "notice.sqlite3", workspace=Workspace.from_path(tmp_path))
    event = ForgeEvent(event_type="approval.requested", payload={"reason": "test"})
    store.append_event(event)
    dispatcher = Dispatcher(store)
    dispatcher.register("router")
    dispatcher.deliver(dispatcher.claim("router"))
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_attempts=4 WHERE event_id=?", (event.id,)
        )

    class Provider:
        execution_mode = "mock"

        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await self.release.wait()
                return type(
                    "Response", (), {"text": '{"action":"silent","reply":null}', "usage": {}}
                )()
            raise RuntimeError("temporary provider failure")

    async def race():
        provider = Provider()
        router = RouterService(
            store, TeamComposer(ModelRegistry(store)), provider=provider, model_id="mock"
        )
        successful = asyncio.create_task(router.handle_pending_events(dispatcher))
        await provider.started.wait()
        await router.handle_pending_events(dispatcher)
        provider.release.set()
        await successful

    asyncio.run(race())
    notice = dispatcher.inbox("router")[0]
    assert notice["processed_at"] is not None
    assert notice["notice_dead_at"] is None


def test_expired_notice_attempt_is_fenced_from_new_attempt(tmp_path, monkeypatch):
    import asyncio
    import time

    from forge_core.dispatcher import Dispatcher
    from forge_core.domain import ForgeEvent
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    store = Store(tmp_path / "race.sqlite3", workspace=Workspace.from_path(tmp_path))
    event = ForgeEvent(event_type="approval.requested", payload={"reason": "test"})
    store.append_event(event)
    dispatcher = Dispatcher(store, clock=lambda: now[0])
    dispatcher.register("router")
    dispatcher.deliver(dispatcher.claim("router"))
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_attempts=3 WHERE event_id=?", (event.id,)
        )

    class Provider:
        execution_mode = "mock"

        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await self.release.wait()
                raise RuntimeError("stale failure")
            await asyncio.sleep(0)
            return type("Response", (), {"text": '{"action":"silent","reply":null}', "usage": {}})()

    async def race():
        provider = Provider()
        router = RouterService(
            store, TeamComposer(ModelRegistry(store)), provider=provider, model_id="mock"
        )
        first = asyncio.create_task(router.handle_pending_events(dispatcher))
        await provider.started.wait()
        now[0] += 61
        # Let the old attempt fail while a new lease exists, before new completion.
        second = asyncio.create_task(router.handle_pending_events(dispatcher))
        provider.release.set()
        return await asyncio.gather(first, second)

    result = asyncio.run(race())
    notice = dispatcher.inbox("router")[0]
    assert any(result)
    assert notice["processed_at"] is not None
    assert notice["notice_error"] is None


def test_expired_notice_success_cannot_ack_new_inflight_attempt(tmp_path, monkeypatch):
    import asyncio
    import time

    from forge_core.dispatcher import Dispatcher
    from forge_core.domain import ForgeEvent
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    store = Store(tmp_path / "old-success.sqlite3", workspace=Workspace.from_path(tmp_path))
    event = ForgeEvent(event_type="approval.requested", payload={"reason": "test"})
    store.append_event(event)
    dispatcher = Dispatcher(store, clock=lambda: now[0])
    dispatcher.register("router")
    dispatcher.deliver(dispatcher.claim("router"))

    class Provider:
        execution_mode = "mock"

        def __init__(self):
            self.started = [asyncio.Event(), asyncio.Event()]
            self.release = [asyncio.Event(), asyncio.Event()]
            self.calls = 0

        async def generate(self, request):
            index = self.calls
            self.calls += 1
            self.started[index].set()
            await self.release[index].wait()
            return type("Response", (), {"text": '{"action":"silent","reply":null}', "usage": {}})()

    async def race():
        provider = Provider()
        router = RouterService(
            store, TeamComposer(ModelRegistry(store)), provider=provider, model_id="mock"
        )
        first = asyncio.create_task(router.handle_pending_events(dispatcher))
        await provider.started[0].wait()
        now[0] += 61
        second = asyncio.create_task(router.handle_pending_events(dispatcher))
        await provider.started[1].wait()
        provider.release[0].set()
        await first
        assert dispatcher.inbox("router")[0]["processed_at"] is None
        provider.release[1].set()
        assert await second

    asyncio.run(race())


@pytest.mark.parametrize("old_fails", [False, True])
def test_expired_planner_attempt_cannot_modify_new_attempt(tmp_path, monkeypatch, old_fails):
    import asyncio
    import importlib
    import json
    import secrets
    import time

    from forge_core.domain import ForgeEvent

    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "planner.sqlite3"))
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="q", goal="plan")
    main.store.save_quest(quest)
    event = ForgeEvent(event_type="quest.proposed", quest_id=quest.id, payload={"source": "router"})
    main.store.append_event(event)
    main.dispatcher.register("planner")
    main.dispatcher.deliver(main.dispatcher.claim("planner"))
    with main.store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_attempts=3 WHERE consumer_id='planner' AND event_id=?",
            (event.id,),
        )

    class Provider:
        execution_mode = "mock"

        def __init__(self):
            self.started = [asyncio.Event(), asyncio.Event()]
            self.release = [asyncio.Event(), asyncio.Event()]
            self.calls = 0

        async def generate(self, request):
            index = self.calls
            self.calls += 1
            self.started[index].set()
            await self.release[index].wait()
            if index == 0 and old_fails:
                raise RuntimeError("stale failure")
            graph = {
                "nodes": [
                    {
                        "key": "a",
                        "objective": "work",
                        "success_criteria": ["done"],
                        "budget_tokens": 10,
                        "required_capabilities": [],
                        "allowed_tools": [],
                        "expected_artifact": None,
                        "max_attempts": 1,
                    }
                ],
                "edges": [],
                "max_total_tokens": 1000,
                "max_cost_microunits": None,
                "max_duration_seconds": None,
                "cost_currency": None,
            }
            return type("Response", (), {"text": json.dumps(graph), "usage": {}})()

    async def race():
        provider = Provider()
        main.router.provider = provider
        main.router.model_id = "mock"
        first = asyncio.create_task(main.process_pending_plan())
        await provider.started[0].wait()
        now[0] += 61
        second = asyncio.create_task(main.process_pending_plan())
        await provider.started[1].wait()
        provider.release[0].set()
        assert not await first
        notice = main.dispatcher.inbox("planner")[0]
        assert notice["processed_at"] is None and notice["notice_dead_at"] is None
        provider.release[1].set()
        assert await second

    asyncio.run(race())
    assert main.dispatcher.inbox("planner")[0]["processed_at"] is not None
    assert main.quest_graph.get(quest.id)["status"] == "proposed"
