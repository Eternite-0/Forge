import asyncio

import pytest

from forge_core.benchmark import BenchmarkRunner
from forge_core.store import Store


def test_benchmark_runs_fixed_tasks_and_persists_mock_provenance(tmp_path):
    class MockGateway:
        execution_mode = "mock"

        async def generate(self, request):
            from types import SimpleNamespace

            return SimpleNamespace(text="4", provider_id="mock", model_id="m")

    store = Store(tmp_path / "benchmark.sqlite3")
    result = asyncio.run(BenchmarkRunner(store).run(MockGateway(), "m", mode="mock"))
    assert result["mode"] == "mock"
    assert result["results"] and result["results"][0]["passed"]
    with store.connection() as connection:
        row = connection.execute(
            "SELECT * FROM benchmark_runs WHERE id=?", (result["id"],)
        ).fetchone()
    assert row["mode"] == "mock"
    assert sum(item["passed"] for item in result["results"]) == 1
    assert len(result["results"]) == 3


def test_mock_gateway_cannot_label_results_live(tmp_path):
    class Gateway:
        execution_mode = "mock"

    with pytest.raises(ValueError, match="provenance"):
        asyncio.run(
            BenchmarkRunner(Store(tmp_path / "mode.sqlite3")).run(Gateway(), "m", mode="live")
        )


def test_benchmark_admits_each_call_through_gateway(tmp_path):
    import httpx

    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway

    store = Store(tmp_path / "benchmark-gateway.sqlite3")
    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)
    config = ProviderConfig(
        id="test",
        kind="openai_compatible",
        base_url="https://example.invalid/v1",
        credential_ref="FORGE_PROVIDER_KEY_TEST",
    )

    def handle(request):
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": "4"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            },
        )

    gateway = ProviderGateway(
        config,
        credential_resolver=lambda _: "placeholder",
        transport=httpx.MockTransport(handle),
        budget_ledger=ledger,
    )
    result = asyncio.run(BenchmarkRunner(store).run(gateway, "m", mode="mock"))
    assert len(result["results"]) == 3
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT purpose,workspace_id FROM model_budget_attempts"
        ).fetchall()
    assert len(rows) == 3
    assert all(
        (row["purpose"], row["workspace_id"]) == ("benchmark", store.workspace.id) for row in rows
    )
