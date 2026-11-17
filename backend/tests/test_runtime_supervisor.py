"""Direct lifecycle contracts without Provider calls or background DB work."""

import asyncio

import pytest

from forge_core.runtime_supervisor import RuntimeSupervisor


class WatchdogStub:
    def __init__(self):
        self.beats = []
        self.fail_on = None

    def heartbeat(self, name, **kwargs):
        if name == self.fail_on:
            raise RuntimeError("heartbeat unavailable")
        self.beats.append((name, kwargs))

    def tick(self):
        pass

    def supervise(self, loops, restart):
        for name, task in list(loops.items()):
            if task.done():
                loops[name] = restart(name)


def test_start_shutdown_and_live_operation_not_duplicated():
    async def scenario():
        entered = asyncio.Event()
        finalized, shutdowns = [], []

        async def operation():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalized.append(True)

        async def shutdown():
            assert all(task.done() for task in supervisor.tasks)
            shutdowns.append(True)

        supervisor = RuntimeSupervisor(
            {"worker": operation}, watchdog=WatchdogStub(), health={}, shutdown=shutdown
        )
        async with supervisor.running():
            await asyncio.wait_for(entered.wait(), 1)
            with pytest.raises(RuntimeError, match="still running"):
                supervisor._restart("worker")
        assert finalized == [True]
        assert shutdowns == [True]
        assert supervisor.stopping
        with pytest.raises(RuntimeError, match="twice"):
            async with supervisor.running():
                pass
        with pytest.raises(RuntimeError, match="stopping"):
            supervisor._restart("worker")

    asyncio.run(scenario())


def test_failure_recovers_without_logging_sensitive_exception(caplog):
    async def scenario():
        recovered = asyncio.Event()
        calls = 0
        health = {}

        async def operation():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("SECRET_PROVIDER_RESPONSE")
            recovered.set()

        async def shutdown():
            pass

        supervisor = RuntimeSupervisor(
            {"router": operation}, watchdog=WatchdogStub(), health=health, shutdown=shutdown
        )
        async with supervisor.running():
            await asyncio.wait_for(recovered.wait(), 2)
            assert calls == 2
            assert health["router"] == "ok"
        assert "SECRET_PROVIDER_RESPONSE" not in caplog.text
        assert "Background router failed; retrying" in caplog.text

    asyncio.run(scenario())


def test_disabled_operation_still_heartbeats_and_can_be_enabled():
    async def scenario():
        enabled = False
        called = asyncio.Event()
        watchdog = WatchdogStub()

        async def operation():
            called.set()

        async def shutdown():
            pass

        supervisor = RuntimeSupervisor(
            {"planner": operation},
            watchdog=watchdog,
            health={},
            shutdown=shutdown,
            enabled=lambda name: enabled,
        )
        async with supervisor.running():
            await asyncio.sleep(0)
            assert not called.is_set()
            assert ("planner", {"timeout_seconds": 600}) in watchdog.beats
            enabled = True
            await asyncio.wait_for(called.wait(), 1)

    asyncio.run(scenario())


def test_partial_start_failure_cancels_started_tasks_and_runs_cleanup():
    async def scenario():
        watchdog = WatchdogStub()
        watchdog.fail_on = "composer"
        shutdowns = []

        async def operation():
            await asyncio.Event().wait()

        async def shutdown():
            shutdowns.append(True)

        supervisor = RuntimeSupervisor(
            {"planner": operation, "composer": operation},
            watchdog=watchdog,
            health={},
            shutdown=shutdown,
        )
        with pytest.raises(RuntimeError, match="heartbeat unavailable"):
            async with supervisor.running():
                pytest.fail("partial start must not yield")
        assert len(supervisor.tasks) == 1
        assert all(task.done() for task in supervisor.tasks)
        assert shutdowns == [True]

    asyncio.run(scenario())


def test_watchdog_replacement_is_owned_and_cleaned_up():
    async def scenario():
        calls = 0
        entered = asyncio.Event()

        async def operation():
            nonlocal calls
            calls += 1
            entered.set()
            await asyncio.Event().wait()

        async def shutdown():
            pass

        supervisor = RuntimeSupervisor(
            {"worker": operation}, watchdog=WatchdogStub(), health={}, shutdown=shutdown
        )
        async with supervisor.running():
            await asyncio.wait_for(entered.wait(), 1)
            old = supervisor.loops["worker"]
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)
            await supervisor._watchdog_tick()
            async with asyncio.timeout(2):
                while calls < 2:
                    await asyncio.sleep(0)
            assert supervisor.loops["worker"] is not old
            assert old in supervisor.tasks
            assert calls == 2
        assert all(task.done() for task in supervisor.tasks)

    asyncio.run(scenario())


def test_body_failure_still_shuts_down():
    async def scenario():
        shutdowns = []

        async def operation():
            await asyncio.Event().wait()

        async def shutdown():
            shutdowns.append(True)

        supervisor = RuntimeSupervisor(
            {"worker": operation}, watchdog=WatchdogStub(), health={}, shutdown=shutdown
        )
        with pytest.raises(ValueError, match="application startup failed"):
            async with supervisor.running():
                raise ValueError("application startup failed")
        assert shutdowns == [True]
        assert all(task.done() for task in supervisor.tasks)

    asyncio.run(scenario())


def test_lifespan_cancellation_cleans_up_before_propagating():
    async def scenario():
        entered = asyncio.Event()
        shutdowns = []

        async def operation():
            entered.set()
            await asyncio.Event().wait()

        async def shutdown():
            shutdowns.append(True)

        supervisor = RuntimeSupervisor(
            {"worker": operation}, watchdog=WatchdogStub(), health={}, shutdown=shutdown
        )

        async def application():
            async with supervisor.running():
                await asyncio.Event().wait()

        owner = asyncio.create_task(application())
        await asyncio.wait_for(entered.wait(), 1)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert shutdowns == [True]
        assert all(task.done() for task in supervisor.tasks)

    asyncio.run(scenario())
