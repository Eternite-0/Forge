"""Integration checks for the real application lifespan and Router event turn."""

import asyncio
import importlib
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import httpx
import pytest
from fastapi.testclient import TestClient

from forge_core.dispatcher import Dispatcher, LeaseError
from forge_core.domain import DomainError, ForgeEvent, Quest, WorkerSession
from forge_core.store import Store


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Background dispatch did not reach the expected state")


def test_lifespan_dispatches_and_resumes_notice_without_fabricating_user_message(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("FORGE_ROUTER_MODEL_ID", raising=False)
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="Background", goal="Report failure")
    main.store.save_quest(quest)
    worker = WorkerSession(quest_id=quest.id, role="research")
    main.store.save_worker(worker)
    event = ForgeEvent(
        event_type="worker.failed",
        quest_id=quest.id,
        worker_id=worker.id,
        payload={"reason": "test fixture"},
    )
    main.store.append_event(event)
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        wait_for(lambda: bool(Dispatcher(main.store).inbox("router")))
        assert main.store.list_messages() == []
    pending = Dispatcher(main.store).inbox("router")
    assert len(pending) == 1 and pending[0]["processed_at"] is None

    class MockProvider:
        execution_mode = "mock"

        async def generate(self, request):
            return type(
                "Response",
                (),
                {
                    "text": json.dumps({"action": "update_user", "reply": "后台任务遇到问题"}),
                    "usage": {},
                },
            )()

    main.router.provider = MockProvider()
    main.router.model_id = "mock"
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        wait_for(lambda: len(main.store.list_messages()) == 1)
        time.sleep(0.15)
    messages = main.store.list_messages()
    assert len(messages) == 1 and messages[0]["role"] == "assistant"
    assert messages[0]["conversation_id"] == "main"
    assert main.store.get_quest(quest.id)["status"] == "proposed"
    assert Dispatcher(main.store).inbox("router")[0]["processed_at"] is not None
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        time.sleep(0.15)
    assert len(main.store.list_messages()) == 1


def test_two_dispatchers_fence_expired_claim_and_redeliver_after_reopen(tmp_path):
    path = tmp_path / "dispatcher.sqlite3"
    first_store = Store(path)
    event = ForgeEvent(event_type="approval.requested", payload={"message": "fixture"})
    first_store.append_event(event)
    now = [100.0]
    dispatchers = [Dispatcher(Store(path), clock=lambda: now[0], lease_seconds=1) for _ in range(2)]
    dispatchers[0].register("router")
    barrier = Barrier(2)

    def claim(index):
        barrier.wait(timeout=5)
        return dispatchers[index].claim("router")

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(claim, range(2)))
    assert sum(lease is not None for lease in leases) == 1
    stale = next(lease for lease in leases if lease is not None)
    now[0] += 2
    reopened = Dispatcher(Store(path), clock=lambda: now[0], lease_seconds=1)
    fresh = reopened.claim("router")
    assert fresh and fresh.event_id == event.id and fresh.token != stale.token
    with pytest.raises(LeaseError):
        reopened.deliver(stale)
    reopened.deliver(fresh)
    assert len(reopened.inbox("router")) == 1
    assert reopened.claim("router") is None


def test_urgent_delivery_does_not_skip_earlier_cursor_gap(tmp_path):
    store = Store(tmp_path / "urgent.sqlite3")
    routine = ForgeEvent(event_type="audit.routine", payload={"message": "first"})
    urgent = ForgeEvent(event_type="approval.requested", payload={"message": "urgent"})
    store.append_event(routine)
    store.append_event(urgent)
    dispatcher = Dispatcher(store)
    dispatcher.register("router")
    urgent_lease = dispatcher.claim("router")
    assert urgent_lease.event_id == urgent.id
    dispatcher.deliver(urgent_lease)
    with store.connection() as connection:
        cursor = connection.execute(
            "SELECT cursor_seq FROM event_consumers WHERE id='router'"
        ).fetchone()[0]
    assert cursor < urgent_lease.seq
    routine_lease = dispatcher.claim("router")
    assert routine_lease.event_id == routine.id
    dispatcher.deliver(routine_lease)
    with store.connection() as connection:
        cursor = connection.execute(
            "SELECT cursor_seq FROM event_consumers WHERE id='router'"
        ).fetchone()[0]
    assert cursor == urgent_lease.seq


def test_handler_failure_rolls_back_inbox_then_poison_reaches_dead_letter(tmp_path):
    store = Store(tmp_path / "poison.sqlite3")
    store.append_event(ForgeEvent(event_type="worker.notice", payload={"message": "fixture"}))
    now = [100.0]
    dispatcher = Dispatcher(store, clock=lambda: now[0], max_attempts=2, base_backoff_seconds=1)
    dispatcher.register("router")

    def fail_after_write(connection, item):
        connection.execute("INSERT INTO models VALUES ('transient','{}','test')")
        raise RuntimeError("fixture")

    for _ in range(2):
        lease = dispatcher.claim("router")
        assert lease is not None
        with pytest.raises(RuntimeError, match="fixture"):
            dispatcher.deliver(lease, handler=fail_after_write)
        assert dispatcher.inbox("router") == []
        with store.connection() as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM models WHERE id='transient'").fetchone()[0]
                == 0
            )
        dispatcher.fail(lease, "handler_failed")
        now[0] += 2
    assert len(dispatcher.dead_letters("router")) == 1


def test_graph_planner_sends_strict_wire_schema_through_gateway(tmp_path):
    from forge_core.providers import ProviderConfig, ProviderGateway
    from forge_core.quest_graph import QuestGraphService

    store = Store(tmp_path / "graph-wire.sqlite3")
    quest = Quest(title="Fixture", goal="Produce a plan")
    store.save_quest(quest)
    seen = []

    def verify_objects(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(value.get("properties", {}))
            for child in value.values():
                verify_objects(child)
        elif isinstance(value, list):
            for child in value:
                verify_objects(child)

    def provider_handler(request):
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        verify_objects(schema)
        seen.append(schema)
        graph = {
            "nodes": [
                {
                    "key": "a",
                    "objective": "Review",
                    "success_criteria": ["Done"],
                    "budget_tokens": 100,
                    "required_capabilities": [],
                    "allowed_tools": [],
                    "expected_artifact": None,
                    "max_attempts": 1,
                }
            ],
            "edges": [],
            "max_total_tokens": 100,
            "max_cost_microunits": None,
            "max_duration_seconds": None,
            "cost_currency": None,
        }
        return httpx.Response(
            200,
            json={
                "id": "mock",
                "model": "m",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": json.dumps(graph)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    from forge_core.model_call_budget import ModelCallBudgetLedger

    gateway = ProviderGateway(
        ProviderConfig(
            id="test",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_json_schema=True,
        ),
        credential_resolver=lambda _: "fixture-secret",
        transport=httpx.MockTransport(provider_handler),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1),
    )
    result = asyncio.run(
        QuestGraphService(store).generate(
            quest.id,
            expected_version=0,
            provider=gateway,
            model_id="m",
            provider_id="test",
            capabilities={"json_schema": True},
            capability_sources={"json_schema": "user_override"},
        )
    )
    assert len(seen) == 1 and result["status"] == "proposed"
    with store.connection() as connection:
        row = connection.execute("SELECT purpose,quest_id FROM model_budget_attempts").fetchone()
        assert (row["purpose"], row["quest_id"]) == ("planner", quest.id)


def test_notice_failure_stays_pending_and_silent_decision_creates_no_message(tmp_path):
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store = Store(tmp_path / "notice.sqlite3")
    event = ForgeEvent(event_type="approval.requested", payload={"message": "fixture"})
    store.append_event(event)
    dispatcher = Dispatcher(store)
    dispatcher.tick()

    class MockProvider:
        execution_mode = "mock"
        response = '{"action":"update_user","reply":null}'

        async def generate(self, request):
            return type("Response", (), {"text": self.response, "usage": {}})()

    provider = MockProvider()
    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=provider, model_id="mock"
    )
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert dispatcher.inbox("router")[0]["processed_at"] is None
    assert store.list_messages() == []
    provider.response = '{"action":"silent","reply":null}'
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_next_attempt=0 WHERE event_id=?", (event.id,)
        )
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert dispatcher.inbox("router")[0]["processed_at"] is not None
    assert store.list_messages() == []
    assert not asyncio.run(router.handle_pending_events(dispatcher))


def test_concurrent_worker_progress_does_not_ack_unseen_event(tmp_path):
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store = Store(tmp_path / "notice-race.sqlite3")
    quest = Quest(title="Progress", goal="Track workers")
    store.save_quest(quest)
    first_worker = WorkerSession(quest_id=quest.id, role="first")
    second_worker = WorkerSession(quest_id=quest.id, role="second")
    store.save_worker(first_worker)
    store.save_worker(second_worker)
    first = ForgeEvent(
        event_type="worker.progress",
        quest_id=quest.id,
        worker_id=first_worker.id,
        payload={"message": "first"},
    )
    second = ForgeEvent(
        event_type="worker.progress",
        quest_id=quest.id,
        worker_id=second_worker.id,
        payload={"message": "second"},
    )
    store.append_event(first)
    dispatcher = Dispatcher(store)

    def delivered(event_id):
        dispatcher.tick()
        return any(item["event_id"] == event_id for item in dispatcher.inbox("router"))

    wait_for(lambda: delivered(first.id))

    class MockProvider:
        execution_mode = "mock"

        async def generate(self, request):
            store.append_event(second)
            wait_for(lambda: delivered(second.id))
            return type("Response", (), {"text": '{"action":"silent","reply":null}', "usage": {}})()

    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=MockProvider(), model_id="mock"
    )
    assert asyncio.run(router.handle_pending_events(dispatcher))
    items = {item["event_id"]: item for item in dispatcher.inbox("router")}
    assert items[first.id]["processed_at"] is not None
    assert items[second.id]["processed_at"] is None
    assert store.list_messages() == []


def test_slow_notice_model_does_not_block_delivery_of_later_critical_event(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    started = Event()

    class SlowMock:
        execution_mode = "mock"

        async def generate(self, request):
            started.set()
            await asyncio.sleep(3)
            return type("Response", (), {"text": '{"action":"silent","reply":null}', "usage": {}})()

    main.router.provider = SlowMock()
    main.router.model_id = "mock"
    main.store.append_event(
        ForgeEvent(event_type="approval.requested", payload={"message": "first"})
    )
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        assert started.wait(timeout=4)
        critical = ForgeEvent(event_type="approval.requested", payload={"message": "critical"})
        main.store.append_event(critical)
        wait_for(
            lambda: any(
                row["event_id"] == critical.id for row in Dispatcher(main.store).inbox("router")
            ),
            timeout=2,
        )


def test_notice_failures_back_off_persist_and_stop_after_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)

    class BadMock:
        execution_mode = "mock"
        calls = 0

        async def generate(self, request):
            self.calls += 1
            return type(
                "Response", (), {"text": '{"action":"update_user","reply":null}', "usage": {}}
            )()

    mock = BadMock()
    main.router.provider = mock
    main.router.model_id = "mock"
    original_handle = main.router._handle_notice

    async def delayed_handle(event_ids, notice_token):
        # Expose the claim/read window deterministically. Advancing retry time
        # during this delay would intentionally fence this execution attempt.
        await asyncio.sleep(0.08)
        return await original_handle(event_ids, notice_token)

    monkeypatch.setattr(main.router, "_handle_notice", delayed_handle)
    event = ForgeEvent(event_type="approval.requested", payload={"message": "test"})
    main.store.append_event(event)

    def notice():
        rows = Dispatcher(main.store).inbox("router")
        return next((row for row in rows if row["event_id"] == event.id), None)

    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        # Attempts count claims, not completed calls. Do not invalidate a live
        # lease when this fixture manually advances the durable retry clock.
        wait_for(
            lambda: (
                notice() is not None
                and notice()["notice_attempts"] == 1
                and notice()["notice_token"] is None
            )
        )
        assert notice()["notice_next_attempt"] > time.time()
        time.sleep(0.2)
        assert mock.calls == 1
    assert notice()["notice_attempts"] == 1
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        for attempt in range(2, 6):
            with main.store.unit_of_work() as work:
                work.connection.execute(
                    "UPDATE event_inbox SET notice_next_attempt=0 WHERE event_id=?", (event.id,)
                )
            wait_for(
                lambda attempt=attempt: (
                    notice()["notice_attempts"] == attempt and notice()["notice_token"] is None
                )
            )
        wait_for(lambda: notice()["notice_dead_at"] is not None)
        time.sleep(0.6)
        assert mock.calls == 5
    assert notice()["processed_at"] is None and main.store.list_messages() == []


def test_authenticated_graph_controls_survive_restart_without_running_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="Controlled plan", goal="Prepare only")
    main.store.save_quest(quest)
    graph = {
        "nodes": [
            {
                "key": "one",
                "objective": "Prepare",
                "success_criteria": ["Ready"],
                "budget_tokens": 100,
                "required_capabilities": [],
                "allowed_tools": [],
                "expected_artifact": None,
                "max_attempts": 1,
            }
        ],
        "edges": [],
        "max_total_tokens": 100,
        "max_cost_microunits": None,
        "max_duration_seconds": None,
        "cost_currency": None,
    }

    class MockProvider:
        execution_mode = "mock"

        async def generate(self, request):
            name = request.response_format["json_schema"]["name"]
            data = graph if name == "quest_graph" else {"action": "silent", "reply": None}
            return type("Response", (), {"text": json.dumps(data), "usage": {}})()

    main.router.provider = MockProvider()
    main.router.model_id = "mock"
    token = {"Authorization": f"Bearer {main.guard._token}"}
    path = f"/api/quests/{quest.id}/plan"
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        assert client.post(f"{path}/generate", json={"expected_version": 0}).status_code == 401
        response = client.post(f"{path}/generate", headers=token, json={"expected_version": 0})
        assert response.status_code == 200
        plan = response.json()
        assert plan["status"] == "proposed" and main.store.list_workers(quest.id) == []
        assert main.store.get_quest(quest.id)["status"] == "awaiting_approval"
        approval = {
            "digest": "0" * 64,
            "plan_revision": plan["plan_revision"],
            "expected_version": 1,
        }
        assert client.post(f"{path}/approve", headers=token, json=approval).status_code == 409
        approval["digest"] = plan["digest"]
        assert client.post(f"{path}/approve", headers=token, json=approval).status_code == 200
        assert main.store.get_quest(quest.id)["status"] == "approved"
        assert (
            client.post(
                f"{path}/control", headers=token, json={"action": "pause", "expected_version": 1}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"{path}/control", headers=token, json={"action": "pause", "expected_version": 2}
            ).json()["status"]
            == "paused"
        )
        assert main.store.get_quest(quest.id)["status"] == "paused"
        assert (
            client.post(
                f"{path}/control", headers=token, json={"action": "resume", "expected_version": 3}
            ).json()["status"]
            == "approved"
        )
        assert main.store.get_quest(quest.id)["status"] == "queued"
    importlib.reload(main)
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        token = {"Authorization": f"Bearer {main.guard._token}"}
        assert client.get(path, headers=token).json()["status"] == "approved"
        assert (
            client.post(
                f"/api/quests/{quest.id}/transition", headers=token, json={"status": "completed"}
            ).status_code
            == 409
        )
        assert client.get("/api/conversation", headers=token).json()["conversation_id"] == "main"
    assert main.store.get_quest(quest.id)["status"] == "queued"
    assert main.store.list_workers(quest.id) == []


@pytest.mark.parametrize(
    "edges",
    [
        [{"source": "a", "target": "a", "condition": "success"}],
        [{"source": "a", "target": "other-quest-node", "condition": "success"}],
    ],
)
def test_invalid_graph_never_changes_quest_or_outbox(tmp_path, edges):
    from forge_core.quest_graph import QuestGraphService

    store = Store(tmp_path / "invalid-graph.sqlite3")
    quest = Quest(title="Invalid graph", goal="Reject unsafe dependency")
    store.save_quest(quest)
    graph = {
        "nodes": [
            {"key": "a", "objective": "Do work", "success_criteria": ["Done"], "budget_tokens": 100}
        ],
        "edges": edges,
    }
    with pytest.raises(DomainError):
        QuestGraphService(store).propose(quest.id, graph, expected_version=0)
    assert store.get_quest(quest.id)["version"] == 0
    assert store.list_events() == [] and store.list_outbox() == []


def test_user_turn_automatically_plans_once_without_extra_user_message(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    graph = {
        "nodes": [
            {
                "key": "a",
                "objective": "Plan",
                "success_criteria": ["Done"],
                "budget_tokens": 100,
                "required_capabilities": [],
                "allowed_tools": [],
                "expected_artifact": None,
                "max_attempts": 1,
            }
        ],
        "edges": [],
        "max_total_tokens": 100,
        "max_cost_microunits": None,
        "max_duration_seconds": None,
        "cost_currency": None,
    }
    decision = {
        "intent": "create_quest",
        "reply": "已登记",
        "title": "目标",
        "goal": "目标",
        "quest_id": None,
        "candidates": [],
    }

    class MockProvider:
        execution_mode = "mock"
        plan_calls = 0

        async def generate(self, request):
            name = request.response_format["json_schema"]["name"]
            if name == "quest_graph":
                self.plan_calls += 1
                data = graph
            elif name == "router_notice":
                data = {"action": "silent", "reply": None}
            else:
                data = decision
            return type("Response", (), {"text": json.dumps(data), "usage": {}})()

    mock = MockProvider()
    main.router.provider = mock
    main.router.model_id = "mock"
    token = {"Authorization": f"Bearer {main.guard._token}"}
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/api/conversation/message",
            headers=token,
            json={"content": "请建立目标", "request_id": "fixture-turn"},
        )
        assert response.status_code == 200
        quest = main.store.list_quests()[0]
        path = f"/api/quests/{quest['id']}/plan"
        wait_for(lambda: client.get(path, headers=token).status_code == 200)
        assert client.get(path, headers=token).json()["status"] == "proposed"
        assert mock.plan_calls == 1
    messages = main.store.list_messages()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert main.store.list_workers(quest["id"]) == []
    importlib.reload(main)
    main.router.provider = mock
    main.router.model_id = "mock"
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        time.sleep(0.7)
    assert mock.plan_calls == 1
    assert len(main.store.list_messages()) == 2


def test_planner_cancelled_claims_survive_reload_and_stop_after_five(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="Interrupted", goal="Never fake a plan")
    main.store.save_quest(quest)
    event = ForgeEvent(event_type="quest.proposed", quest_id=quest.id, payload={})
    main.store.append_event(event)

    def delivered():
        main.dispatcher.tick()
        return any(item["event_id"] == event.id for item in main.dispatcher.inbox("planner"))

    wait_for(delivered)

    class WaitingProvider:
        execution_mode = "mock"
        calls = 0

        async def generate(self, request):
            self.calls += 1
            await asyncio.Event().wait()

    provider = WaitingProvider()

    for attempt in range(1, 6):
        main.router.provider = provider
        main.router.model_id = "mock"
        before = provider.calls

        async def cancel_next(before=before):
            task = asyncio.create_task(main.process_pending_plan())
            for _ in range(100):
                if provider.calls > before:
                    break
                await asyncio.sleep(0.01)
            assert provider.calls == before + 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(cancel_next())
        notice = main.dispatcher.inbox("planner")[0]
        assert notice["notice_attempts"] == attempt and notice["processed_at"] is None
        assert notice["notice_next_attempt"] > time.time()
        importlib.reload(main)
        assert main.dispatcher.inbox("planner")[0]["notice_attempts"] == attempt
        with main.store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE event_inbox SET notice_next_attempt=0 WHERE event_id=?", (event.id,)
            )
    assert not asyncio.run(main.process_pending_plan())
    assert main.dispatcher.inbox("planner")[0]["notice_dead_at"] is not None
    assert provider.calls == 5 and main.store.list_messages() == []
    with main.store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM quest_plans WHERE quest_id=?", (quest.id,)
            ).fetchone()[0]
            == 0
        )


def test_planner_refuses_stale_quest_after_model_call(tmp_path):
    from forge_core.quest_graph import QuestGraphService

    store = Store(tmp_path / "plan-cas.sqlite3")
    quest = Quest(title="CAS", goal="Reject stale plan")
    store.save_quest(quest)
    graph = {
        "nodes": [
            {
                "key": "a",
                "objective": "Plan",
                "success_criteria": ["Done"],
                "budget_tokens": 100,
                "required_capabilities": [],
                "allowed_tools": [],
                "expected_artifact": None,
                "max_attempts": 1,
            }
        ],
        "edges": [],
        "max_total_tokens": 100,
        "max_cost_microunits": None,
        "max_duration_seconds": None,
        "cost_currency": None,
    }

    class MockProvider:
        execution_mode = "mock"

        async def generate(self, request):
            with store.unit_of_work() as work:
                work.connection.execute(
                    "UPDATE quests SET version=version+1 WHERE id=?", (quest.id,)
                )
            return type("Response", (), {"text": json.dumps(graph), "usage": {}})()

    with pytest.raises(DomainError, match="version"):
        asyncio.run(
            QuestGraphService(store).generate(
                quest.id,
                expected_version=0,
                provider=MockProvider(),
                model_id="mock",
                provider_id=None,
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "mock"},
            )
        )
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM quest_plans").fetchone()[0] == 0
    assert store.list_events() == [] and store.list_outbox() == []
