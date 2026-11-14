"""Small reproducible protocol baseline; scores are not capability estimates."""

import json
import time

from .domain import new_id, now_iso
from .runtime import ModelCallContext, ModelRequest

SUITE_VERSION = "forge-smoke-v1"
TASKS = (
    ("arithmetic", "Reply with only the integer result of 2 + 2.", "4"),
    ("instruction", "Reply with only the lowercase word blue.", "blue"),
    ("copy", "Reply with only the exact text FORGE.", "FORGE"),
)


class BenchmarkRunner:
    def __init__(self, store):
        self.store = store

    async def run(self, gateway, model_id, *, mode):
        if mode not in {"mock", "live"}:
            raise ValueError("Benchmark mode must be mock or live")
        if getattr(gateway, "execution_mode", None) != mode:
            raise ValueError("Gateway execution provenance does not match benchmark mode")
        results = []
        provider_id = getattr(getattr(gateway, "config", None), "id", None)
        if provider_id is None and mode == "mock":
            provider_id = "mock"
        if not provider_id:
            raise ValueError("Gateway provider identity is required")
        for task_id, prompt, expected in TASKS:
            start = time.monotonic()
            try:
                response = await gateway.generate(
                    ModelRequest(
                        model_id=model_id,
                        messages=[{"role": "user", "content": prompt}],
                        max_output_tokens=32,
                        budget_context=ModelCallContext(
                            call_id=new_id("call"),
                            purpose="benchmark",
                            workspace_id=self.store.workspace.id,
                        ),
                    )
                )
                if not response.provider_id or (mode == "live" and response.provider_id == "mock"):
                    raise ValueError("Benchmark provider provenance does not match mode")
                if response.model_id != model_id:
                    raise ValueError("Benchmark response model differs from requested model")
                if provider_id != response.provider_id:
                    raise ValueError("Provider changed during benchmark")
                results.append(
                    {
                        "task_id": task_id,
                        "passed": response.text.strip() == expected,
                        "output": response.text,
                        "elapsed_seconds": time.monotonic() - start,
                    }
                )
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001 - record a failed task without exception text
                results.append(
                    {
                        "task_id": task_id,
                        "passed": False,
                        "error_type": type(exc).__name__,
                        "elapsed_seconds": time.monotonic() - start,
                    }
                )
        run_id = new_id("benchmark")
        created_at = now_iso()
        with self.store.unit_of_work() as work:
            work.connection.execute(
                "INSERT INTO benchmark_runs VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    model_id,
                    provider_id,
                    SUITE_VERSION,
                    mode,
                    json.dumps(results),
                    created_at,
                ),
            )
        return {
            "id": run_id,
            "model_id": model_id,
            "provider_id": provider_id,
            "suite_version": SUITE_VERSION,
            "mode": mode,
            "created_at": created_at,
            "n_samples": len(results),
            "results": results,
        }
