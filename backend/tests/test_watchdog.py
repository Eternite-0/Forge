"""Real SQLite recovery keeps generation fencing and unknown effects intact."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_worker_runtime import (
    MockApprovedTeam,
    MockToolEngine,
    ScriptedMock,
    graph,  # noqa: F401 - shared, real-SQLite pytest fixture
)

from forge_core.dispatcher import Dispatcher, LeaseError
from forge_core.domain import DomainError, ForgeEvent
from forge_core.provider_pool import ProviderPool
from forge_core.watchdog import Watchdog, migrate
from forge_core.worker_model_usage import reserve_worker_model_call
from forge_core.worker_runtime import WorkerManager


@pytest.fixture
def worker(graph):  # noqa: F811 - pytest resolves the imported fixture by name.
    store, quest, plan = graph
    with store.unit_of_work() as work:
        if not work.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runtime_heartbeats'"
        ).fetchone():
            migrate(work.connection)
    tool = MockToolEngine()
    model = ScriptedMock()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": model}),
        tool,
        team_authority=MockApprovedTeam(plan, tools=["read"]),
        clock=lambda: 100.0,
        lease_seconds=5,
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    old_token = manager._claim(session["id"])
    return store, manager, session, old_token, model, tool


def test_expired_owner_requeues_and_runtime_rechecks_fenced_ownership(worker):
    store, manager, session, old_token, model, _ = worker
    watch = Watchdog(store, clock=lambda: 106.0)
    report = watch.tick()
    assert report["requeued"] == [session["id"]]
    with store.connection() as connection, pytest.raises(DomainError, match="stale"):
        manager._fenced(connection, session["id"], old_token)
    # A recovered task is still subject to the existing deadline and authorization checks.
    result = asyncio.run(manager.run(session["id"]))
    assert result["state"] == "review_pending"
    assert model.calls == 2
    assert watch.tick()["requeued"] == []


def test_unknown_tool_effect_blocks_and_never_replays(worker):
    store, _, session, _, model, tool = worker
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO worker_tool_calls VALUES (?,?,?,?,?,'running',NULL,NULL)",
            (session["id"], "orphan", "model-orphan", "read", "{}"),
        )
    report = Watchdog(store, clock=lambda: 106.0).tick()
    assert report["blocked"] == [session["id"]]
    assert model.calls == 0 and tool.calls == []
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM worker_tool_calls").fetchone()[0] == "uncertain"
        )
        assert connection.execute("SELECT state FROM worker_runs").fetchone()[0] == "blocked"
    assert store.list_messages() == []


def test_unknown_provider_admission_remains_reserved(worker):
    store, _, session, _, model, _ = worker
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT * FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()
        reserve_worker_model_call(work.connection, row, "provider-orphan", 100, 10, None, [])
    assert Watchdog(store, clock=lambda: 106.0).tick()["blocked"] == [session["id"]]
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM worker_model_reservations").fetchone()[0]
            == "reserved"
        )
    assert model.calls == 0


def test_orphan_executing_intent_blocks_even_without_worker_call_checkpoint(worker):
    store, _, session, _, model, tool = worker
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT * FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()
        work.connection.execute(
            "INSERT INTO action_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "intent-orphan",
                "call-orphan",
                row["quest_id"],
                row["worker_id"],
                row["id"],
                row["plan_id"],
                row["plan_revision"],
                "file_write",
                "{}",
                "unchanged-digest",
                "project/file.txt",
                "R1",
                None,
                "mock",
                "executing",
                "2026-11-16",
                "2026-11-16",
            ),
        )
    assert Watchdog(store, clock=lambda: 106.0).tick()["blocked"] == [session["id"]]
    with store.connection() as connection:
        assert connection.execute("SELECT state FROM action_intents").fetchone()[0] == "executing"
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
    assert model.calls == 0 and tool.calls == []


def test_concurrent_watchdogs_only_requeue_one_generation(worker):
    store, _, session, _, _, _ = worker
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: Watchdog(store, clock=lambda: 106.0).tick(), range(2)))
    assert sum(session["id"] in result["requeued"] for result in results) == 1
    events = [
        event for event in store.list_events() if event["event_type"] == "runtime.worker_recovered"
    ]
    assert len(events) == 1


def test_elapsed_deadline_without_unknown_effect_fails_once(worker):
    store, _, session, _, _, _ = worker
    watchdog = Watchdog(store, clock=lambda: 200.0)
    assert watchdog.tick()["failed"] == [session["id"]]
    assert watchdog.tick()["failed"] == []
    assert (
        len([event for event in store.list_events() if event["event_type"] == "worker.failed"]) == 1
    )


def test_live_owner_and_unexpired_delivery_are_untouched(worker):
    store, _, _, _, _, _ = worker
    watchdog = Watchdog(store, clock=lambda: 101.0)
    assert watchdog.tick()["requeued"] == []
    with store.connection() as connection:
        assert connection.execute("SELECT state FROM worker_runs").fetchone()[0] == "running"


def test_invalid_execution_environment_blocks_recovery_without_creating_paths(worker):
    store, _, session, _, model, tool = worker
    environment_root = store.workspace.root / "data" / "worker-environments"
    environment_root.parent.mkdir(parents=True, exist_ok=True)
    environment_root.write_text("not a directory", encoding="utf-8")
    report = Watchdog(store, clock=lambda: 106.0).tick()
    assert report["blocked"] == [session["id"]]
    assert environment_root.read_text(encoding="utf-8") == "not a directory"
    assert model.calls == 0 and tool.calls == []


def test_expired_database_delivery_is_released_with_old_lease_fenced(worker):
    store, _, _, _, _, _ = worker
    store.append_event(ForgeEvent(event_type="probe.local"))
    now = [100.0]
    dispatcher = Dispatcher(store, clock=lambda: now[0], lease_seconds=5)
    dispatcher.register("router")
    lease = dispatcher.claim("router")
    now[0] = 106
    assert Watchdog(store, clock=lambda: now[0]).tick()["deliveries_released"] == 1
    with pytest.raises(LeaseError):
        dispatcher.deliver(lease)
    replacement = dispatcher.claim("router")
    assert replacement.attempt == lease.attempt + 1
    dispatcher.deliver(replacement)
    assert len(dispatcher.inbox("router")) == 1


def test_heartbeats_throttle_persistence_and_report_stall_once(worker):
    store, _, _, _, _, _ = worker
    now = [100.0]
    watchdog = Watchdog(store, clock=lambda: now[0], heartbeat_interval=10)
    assert watchdog.heartbeat("router", timeout_seconds=30)
    now[0] = 101
    assert not watchdog.heartbeat("router", timeout_seconds=30)
    now[0] = 131
    assert watchdog.tick()["stalled_components"] == ["router"]
    assert watchdog.tick()["stalled_components"] == []
    assert watchdog.snapshot()[0]["state"] == "stalled"
    assert watchdog.heartbeat("router", timeout_seconds=30)
    assert watchdog.snapshot()[0]["state"] == "ok"


def test_exited_loop_restarts_with_backoff_but_pending_task_is_not_duplicated(worker):
    store, _, _, _, _, _ = worker
    now = [100.0]
    watchdog = Watchdog(store, clock=lambda: now[0])
    watchdog.heartbeat("router")
    watchdog.heartbeat("dispatcher")

    async def scenario():
        async def exited():
            raise RuntimeError("remote text must never enter the event log")

        async def pending():
            await asyncio.Event().wait()

        broken, live = asyncio.create_task(exited()), asyncio.create_task(pending())
        await asyncio.sleep(0)
        tasks = {"router": broken, "dispatcher": live}
        restarted = watchdog.supervise(tasks, lambda name: asyncio.create_task(exited()))
        assert restarted == ["router"]
        assert tasks["dispatcher"] is live and not live.cancelled()
        await asyncio.sleep(0)
        assert watchdog.supervise(tasks, lambda name: pytest.fail("backoff bypassed")) == []
        now[0] = 103
        assert watchdog.supervise(tasks, lambda name: asyncio.create_task(pending())) == ["router"]
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    asyncio.run(scenario())
    assert all("remote text" not in event["payload"] for event in store.list_events())


def test_stable_replacement_regains_restart_budget(worker):
    store, _, _, _, _, _ = worker
    now = [100.0]
    watchdog = Watchdog(store, clock=lambda: now[0], heartbeat_interval=1, stable_seconds=20)
    watchdog.heartbeat("router", timeout_seconds=30)

    async def scenario():
        async def exited():
            raise RuntimeError("scripted component failure")

        async def pending():
            await asyncio.Event().wait()

        broken = asyncio.create_task(exited())
        await asyncio.sleep(0)
        tasks = {"router": broken}
        assert watchdog.supervise(tasks, lambda _: asyncio.create_task(pending()), max_restarts=1)
        now[0] = 103
        watchdog.heartbeat("router", timeout_seconds=30)
        now[0] = 114
        watchdog.heartbeat("router", timeout_seconds=30)
        assert watchdog.snapshot()[0]["restart_count"] == 1
        now[0] = 124
        watchdog.heartbeat("router", timeout_seconds=30)
        assert watchdog.snapshot()[0]["restart_count"] == 0
        assert watchdog.snapshot()[0]["next_restart_at"] == 0
        tasks["router"].cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        assert watchdog.supervise(tasks, lambda _: asyncio.create_task(pending()), max_restarts=1)
        assert watchdog.snapshot()[0]["restart_count"] == 1
        tasks["router"].cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    asyncio.run(scenario())


def test_core_downtime_and_retry_do_not_count_as_component_stability(worker):
    store, _, _, _, _, _ = worker
    now = [100.0]
    previous = Watchdog(store, clock=lambda: now[0])
    previous.heartbeat("router", timeout_seconds=30)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE runtime_heartbeats SET restart_count=4,next_restart_at=110 WHERE component='router'"
        )
    # A new host observes its own stable run before resetting durable failures.
    now[0] = 400
    watchdog = Watchdog(store, clock=lambda: now[0], heartbeat_interval=1, stable_seconds=20)
    watchdog.heartbeat("router", timeout_seconds=30)
    assert watchdog.snapshot()[0]["restart_count"] == 4
    now[0] = 415
    watchdog.heartbeat("router", state="retrying", timeout_seconds=30)
    now[0] = 420
    watchdog.heartbeat("router", timeout_seconds=30)
    now[0] = 431
    watchdog.heartbeat("router", timeout_seconds=30)
    assert watchdog.snapshot()[0]["restart_count"] == 4
    now[0] = 441
    watchdog.heartbeat("router", timeout_seconds=30)
    assert watchdog.snapshot()[0]["restart_count"] == 0


@pytest.mark.parametrize("window", [0, -1, float("nan"), float("inf"), 3601])
def test_component_stability_window_is_bounded(worker, window):
    store, _, _, _, _, _ = worker
    with pytest.raises(ValueError, match="stability window"):
        Watchdog(store, stable_seconds=window)
