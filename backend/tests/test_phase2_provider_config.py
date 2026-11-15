"""Offline Provider timing/configuration regressions; no real model requests."""

import asyncio
import json
import sqlite3

import httpx
import pytest

from forge_core.model_budget_schema import migrate_model_budget
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.runtime import ModelCallContext, ModelRequest


@pytest.fixture
def provider_environment(monkeypatch):
    values = {
        "FORGE_PROVIDER_ID": "mock-provider",
        "FORGE_PROVIDER_KIND": "openai_compatible",
        "FORGE_PROVIDER_BASE_URL": "https://example.invalid/v1",
        "FORGE_PROVIDER_CREDENTIAL_REF": "FORGE_PROVIDER_KEY_TEST",
        "FORGE_PROVIDER_SUPPORTS_TOOLS": "false",
        "FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA": "false",
        "FORGE_PROVIDER_ALLOW_INSECURE_LOOPBACK": "false",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in (
        "FORGE_PROVIDER_TIMEOUT_SECONDS",
        "FORGE_PROVIDER_MAX_RETRIES",
        "FORGE_PROVIDER_REASONING_EFFORT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_provider_environment_retains_bounded_timing_defaults(provider_environment):
    config = ProviderConfig.from_env()
    assert config.timeout_seconds == 30 and config.max_retries == 1
    assert config.credential_ref == "FORGE_PROVIDER_KEY_TEST"
    assert config.reasoning_effort is None


@pytest.mark.parametrize("timeout,retries", [("0.001", "0"), ("120", "0"), ("600", "3")])
def test_provider_environment_accepts_timing_boundaries(
    provider_environment, monkeypatch, timeout, retries
):
    monkeypatch.setenv("FORGE_PROVIDER_TIMEOUT_SECONDS", timeout)
    monkeypatch.setenv("FORGE_PROVIDER_MAX_RETRIES", retries)
    config = ProviderConfig.from_env()
    assert config.timeout_seconds == float(timeout)
    assert config.max_retries == int(retries)


@pytest.mark.parametrize(
    "value", ["", "0", "-1", "600.001", "nan", "inf", "-inf", "true", "not-a-number"]
)
def test_provider_environment_rejects_invalid_timeouts(provider_environment, monkeypatch, value):
    monkeypatch.setenv("FORGE_PROVIDER_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError):
        ProviderConfig.from_env()


@pytest.mark.parametrize("value", ["", "-1", "4", "1.5", "nan", "true"])
def test_provider_environment_rejects_invalid_retry_limits(
    provider_environment, monkeypatch, value
):
    monkeypatch.setenv("FORGE_PROVIDER_MAX_RETRIES", value)
    with pytest.raises(ValueError):
        ProviderConfig.from_env()


@pytest.mark.parametrize("request_timeout", [None, 1.0])
def test_request_timeout_applies_to_http_client_and_outer_deadline(tmp_path, request_timeout):
    path = tmp_path / "mock-budget.sqlite3"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    ledger = ModelCallBudgetLedger(path, token_limit=10_000, concurrency_limit=2)
    config = ProviderConfig(
        id="mock-provider",
        kind="openai_compatible",
        base_url="https://example.invalid/v1",
        credential_ref="FORGE_PROVIDER_KEY_TEST",
        timeout_seconds=0.05,
        max_retries=0,
    )
    observed = []

    async def handler(request):
        # MockTransport does not enforce HTTP timeouts itself. Check the actual
        # client extension as well as the Gateway's cancellable wall deadline.
        observed.append(request.extensions["timeout"])
        await asyncio.sleep(0.15)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 2},
            },
        )

    gateway = ProviderGateway(
        config,
        credential_resolver=lambda _: "dummy-secret",
        transport=httpx.MockTransport(handler),
        budget_ledger=ledger,
    )
    request = ModelRequest(
        model_id="explicit-mock",
        messages=[{"role": "user", "content": "short timing regression"}],
        max_output_tokens=64,
        timeout_seconds=request_timeout,
        budget_context=ModelCallContext("timing-call", "protocol-test", "isolated-mock"),
    )
    if request_timeout is None:
        with pytest.raises(ProviderError) as error:
            asyncio.run(gateway.generate(request))
        assert error.value.code == "timeout"
    else:
        result = asyncio.run(gateway.generate(request))
        assert result.text == "ok" and result.usage["output_tokens"] == 2
    expected = request_timeout or config.timeout_seconds
    assert observed == [{name: expected for name in ("connect", "read", "write", "pool")}]
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT state,input_charged,output_charged FROM model_budget_attempts"
        ).fetchall()
    assert rows == ([("reserved", None, None)] if request_timeout is None else [("settled", 12, 2)])


@pytest.mark.parametrize("effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"])
@pytest.mark.parametrize("kind", ["openai_compatible", "deepseek"])
def test_explicit_reasoning_effort_environment_and_pool_share_validation(
    provider_environment, monkeypatch, effort, kind
):
    monkeypatch.setenv("FORGE_PROVIDER_KIND", kind)
    monkeypatch.setenv("FORGE_PROVIDER_REASONING_EFFORT", effort)
    config = ProviderConfig.from_env()
    assert config.reasoning_effort == effort
    monkeypatch.setenv(
        "FORGE_PROVIDERS_JSON",
        json.dumps(
            [
                {
                    "id": "mock-pool",
                    "kind": kind,
                    "base_url": "https://example.invalid/v1",
                    "credential_ref": "FORGE_PROVIDER_KEY_TEST",
                    "reasoning_effort": effort,
                }
            ]
        ),
    )
    monkeypatch.setenv("FORGE_PRICE_BOUNDS_JSON", "[]")
    pool, _ = ProviderPool.from_environment()
    assert pool.get("mock-pool").config.reasoning_effort == effort


@pytest.mark.parametrize("effort", ["", "auto", "NONE", "none ", True, 1, [], {}])
def test_reasoning_effort_rejects_invalid_pool_values(monkeypatch, effort):
    data = {
        "id": "mock-pool",
        "kind": "openai_compatible",
        "base_url": "https://example.invalid/v1",
        "credential_ref": "FORGE_PROVIDER_KEY_TEST",
        "reasoning_effort": effort,
    }
    with pytest.raises(ValueError):
        ProviderConfig(**data)
    monkeypatch.setenv("FORGE_PROVIDERS_JSON", json.dumps([data]))
    monkeypatch.setenv("FORGE_PRICE_BOUNDS_JSON", "[]")
    with pytest.raises(ValueError, match="Invalid provider pool configuration"):
        ProviderPool.from_environment()


def test_reasoning_effort_rejects_anthropic_environment_and_pool(provider_environment, monkeypatch):
    monkeypatch.setenv("FORGE_PROVIDER_KIND", "anthropic")
    monkeypatch.setenv("FORGE_PROVIDER_REASONING_EFFORT", "none")
    with pytest.raises(ValueError, match="reasoning effort"):
        ProviderConfig.from_env()
    monkeypatch.setenv(
        "FORGE_PROVIDERS_JSON",
        json.dumps(
            [
                {
                    "id": "mock-pool",
                    "kind": "anthropic",
                    "base_url": "https://example.invalid/v1",
                    "credential_ref": "FORGE_PROVIDER_KEY_TEST",
                    "reasoning_effort": "none",
                }
            ]
        ),
    )
    monkeypatch.setenv("FORGE_PRICE_BOUNDS_JSON", "[]")
    with pytest.raises(ValueError):
        ProviderPool.from_environment()


@pytest.mark.parametrize("effort", [None, "none", "low"])
@pytest.mark.parametrize("kind", ["openai_compatible", "deepseek"])
@pytest.mark.parametrize("streaming", [False, True])
def test_actual_gateway_reasoning_effort_wire_is_opt_in_for_generate_and_stream(
    tmp_path, effort, kind, streaming
):
    path = tmp_path / "mock-budget.sqlite3"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    config = ProviderConfig(
        id="mock-provider",
        kind=kind,
        base_url="https://example.invalid/v1",
        credential_ref="FORGE_PROVIDER_KEY_TEST",
        reasoning_effort=effort,
    )
    observed = []

    def handler(request):
        payload = json.loads(request.content)
        observed.append(payload)
        assert payload["stream"] is streaming
        assert "fixture-credential" not in request.content.decode()
        if effort is None:
            assert "reasoning_effort" not in payload
            assert set(payload) == {"model", "messages", "max_tokens", "stream"} | (
                {"stream_options"} if streaming else set()
            )
        else:
            assert payload["reasoning_effort"] == effort
        usage = {"prompt_tokens": 12, "completion_tokens": 2}
        if streaming:
            chunks = [
                {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage},
            ]
            wire = (
                "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            )
            return httpx.Response(
                200, content=wire.encode(), headers={"Content-Type": "text/event-stream"}
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": usage,
            },
        )

    gateway = ProviderGateway(
        config,
        credential_resolver=lambda _: "fixture-credential",
        transport=httpx.MockTransport(handler),
        budget_ledger=ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1),
    )
    request = ModelRequest(
        model_id="mock-model",
        messages=[{"role": "user", "content": "wire regression"}],
        max_output_tokens=64,
        budget_context=ModelCallContext("wire", "protocol-test", "isolated-mock"),
    )

    async def consume():
        if streaming:
            events = [event async for event in gateway.stream(request)]
            return events[-1].response
        return await gateway.generate(request)

    result = asyncio.run(consume())
    assert result.text == "ok" and result.usage["output_tokens"] == 2
    assert len(observed) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT state,input_charged,output_charged FROM model_budget_attempts"
        ).fetchone() == ("settled", 12, 2)
