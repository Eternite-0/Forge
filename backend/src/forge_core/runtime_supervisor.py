"""Own trusted Core loop tasks, never execution policy or durable work queues.

Operations retain their existing transactions, admission gates and checkpoints.
Only exited tasks can be replaced, through the durable Watchdog restart policy.
"""

import asyncio
import logging
from contextlib import asynccontextmanager


class RuntimeSupervisor:
    def __init__(self, operations, *, watchdog, health, shutdown, enabled=None):
        if not operations or "watchdog" in operations:
            raise ValueError("Expected named Core operations without watchdog")
        self.operations = dict(operations)
        self.watchdog = watchdog
        self.health = health
        self.shutdown = shutdown
        self.enabled = enabled or (lambda name: True)
        self.loops = {}
        # Stable list for application diagnostics, including replaced tasks.
        self.tasks = []
        self.stopping = False
        self.started = False

    async def _run_loop(self, name, operation):
        failures = 0
        while not self.stopping:
            try:
                if name == "watchdog" or self.enabled(name):
                    await operation()
                self.watchdog.heartbeat(name, timeout_seconds=600)
                failures = 0
                self.health[name] = "ok"
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never expose provider/tool exception text.
                failures += 1
                self.health[name] = "retrying"
                try:
                    self.watchdog.heartbeat(name, state="retrying", timeout_seconds=600)
                except Exception:  # noqa: BLE001, S110 - sanitized warning below.
                    pass
                logging.getLogger(__name__).warning("Background %s failed; retrying", name)
            await asyncio.sleep(min(0.5 * (2 ** min(failures, 6)), 30))

    def _restart(self, name):
        if self.stopping:
            raise RuntimeError("Core supervisor is stopping")
        previous = self.loops.get(name)
        if previous is not None and not previous.done():
            raise RuntimeError("Core operation is still running")
        self.watchdog.heartbeat(name, state="restarting", timeout_seconds=600)
        task = asyncio.create_task(self._run_loop(name, self.operations[name]))
        self.tasks.append(task)
        return task

    async def _watchdog_tick(self):
        await asyncio.to_thread(self.watchdog.tick)
        if not self.stopping:
            self.watchdog.supervise(self.loops, self._restart)

    @asynccontextmanager
    async def running(self):
        if self.started:
            raise RuntimeError("Core supervisor cannot be started twice")
        self.started = True
        try:
            for name in self.operations:
                self.loops[name] = self._restart(name)
            self.tasks.append(asyncio.create_task(self._run_loop("watchdog", self._watchdog_tick)))
            yield self
        finally:
            self.stopping = True
            for task in self.tasks:
                task.cancel()
            try:
                await asyncio.gather(*self.tasks, return_exceptions=True)
            finally:
                await self.shutdown()
