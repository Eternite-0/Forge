"""Offline HTTP mock protocol tests; these do not call a real model."""

import asyncio
import itertools
import json
import sqlite3

import httpx
import pytest

from forge_core.model_budget_schema import migrate_model_budget
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.runtime import ModelCallContext, ModelRequest


@pytest.fixture(autouse=True)
def isolated_mock_ledger(tmp_path, monkeypatch):
    path = tmp_path / "mock-budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    ledger = ModelCallBudgetLedger(
        path, token_limit=100_000_000, concurrency_limit=64, remote_max_seconds=600
    )
    monkeypatch.setattr(ProviderGateway, "_test_budget_ledger", ledger, raising=False)


def mock_ledger():
    return ProviderGateway._test_budget_ledger


def run(coro):
    return asyncio.run(coro)


def config(kind="openai_compatible", **kwargs):
    return ProviderConfig(
        id="test",
        kind=kind,
        base_url="https://example.invalid/v1",
        credential_ref="FORGE_PROVIDER_KEY_TEST",
        **kwargs,
    )


def request(**kwargs):
    return ModelRequest(
        model_id="mock-model",
        messages=[{"role": "user", "content": "hello"}],
        budget_context=ModelCallContext("mock-call", "protocol-test", "isolated-test"),
        **kwargs,
    )


def gateway(handler, kind="openai_compatible", **kwargs):
    return ProviderGateway(
        config(kind, **kwargs),
        credential_resolver=lambda _: "test-secret-value",
        transport=httpx.MockTransport(handler),
        budget_ledger=mock_ledger(),
    )


def test_openai_generate_tools_usage_and_cost():
    def handler(req):
        payload = json.loads(req.content)
        assert req.headers["authorization"] == "Bearer test-secret-value"
        assert payload["model"] == "mock-model"
        return httpx.Response(
            200,
            json={
                "id": "remote",
                "choices": [
                    {
                        "message": {
                            "content": "hello",
                            "tool_calls": [
                                {
                                    "id": "call",
                                    "type": "function",
                                    "function": {"name": "lookup", "arguments": '{"x":1}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 500,
                    "prompt_tokens_details": {"cached_tokens": 100},
                },
            },
        )

    result = run(
        gateway(
            handler, input_price_per_million=2, output_price_per_million=4, currency="USD"
        ).generate(request())
    )
    assert result.text == "hello"
    assert result.tool_calls[0]["arguments"] == {"x": 1}
    assert result.usage["input_tokens"] == 1000
    assert result.usage["cached_input_tokens"] == 100
    assert result.cost is None  # cached price unknown, never guess


def test_anthropic_wire_format_and_schema_fail_closed():
    def handler(req):
        assert req.headers["x-api-key"] == "test-secret-value"
        payload = json.loads(req.content)
        assert payload["messages"][0]["role"] == "user"
        return httpx.Response(
            200,
            json={
                "id": "msg",
                "content": [
                    {"type": "text", "text": "ok"},
                    {"type": "tool_use", "id": "tool", "name": "lookup", "input": {"x": 2}},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        )

    response = run(gateway(handler, "anthropic").generate(request()))
    assert response.tool_calls[0]["arguments"] == {"x": 2}
    with pytest.raises(ProviderError, match="unsupported"):
        run(gateway(handler, "anthropic").generate(request(response_schema={"type": "object"})))


def test_retry_redaction_and_unconfigured():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            429 if len(calls) == 1 else 200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
            headers={"Retry-After": "0"},
        )

    assert run(gateway(handler, max_retries=1).generate(request())).text == "ok"
    assert len(calls) == 2

    def denied(req):
        return httpx.Response(401, text="test-secret-value")

    with pytest.raises(ProviderError) as error:
        run(gateway(denied).generate(request()))
    assert error.value.code == "auth"
    assert "test-secret-value" not in str(error.value)
    with pytest.raises(ProviderError) as missing:
        run(ProviderGateway.unconfigured().generate(request()))
    assert missing.value.code == "configuration"


def test_stream_handles_fragmented_sse_and_truncation():
    events = [
        b'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    def handler(req):
        return httpx.Response(
            200, content=b"".join(events), headers={"Content-Type": "text/event-stream"}
        )

    async def consume(g):
        return [event async for event in g.stream(request())]

    result = run(consume(gateway(handler)))
    assert any(e.type == "text_delta" and e.text == "Hi" for e in result)
    assert result[-1].type == "completed"

    def truncated(req):
        return httpx.Response(200, content=events[0], headers={"Content-Type": "text/event-stream"})

    with pytest.raises(ProviderError) as error:
        run(consume(gateway(truncated)))
    assert error.value.code == "protocol_error"


def test_schema_deepseek_and_discovery():
    seen = []

    def handler(req):
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(
                200, json={"data": [{"id": "real-id", "capabilities": {"invented": True}}]}
            )
        body = json.loads(req.content)
        assert body["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
        )

    schema = {
        "type": "json_schema",
        "json_schema": {"name": "decision", "strict": True, "schema": {"type": "object"}},
    }
    g = gateway(handler, supports_json_schema=True)
    assert g.execution_mode == "mock"
    assert (
        run(
            g.generate(
                request(
                    response_format=schema,
                    model_capabilities={"json_schema": True},
                    model_capability_sources={"json_schema": "operator"},
                )
            )
        ).text
        == "{}"
    )
    assert run(g.discover()) == [{"id": "real-id", "provider_id": "test"}]
    with pytest.raises(ProviderError, match="unsupported"):
        run(
            gateway(handler, "deepseek", supports_json_schema=True).generate(
                request(response_format=schema)
            )
        )


def test_true_fragmented_sse_tool_arguments():
    wire = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"find","arguments":"{\\"x\\":"}}]},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]},"finish_reason":"tool_calls"}]}\n\n'
        b"data: [DONE]\n\n"
    )

    class Fragmented(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(0, len(wire), 3):
                yield wire[index : index + 3]

    async def consume():
        g = gateway(lambda req: httpx.Response(200, stream=Fragmented()))
        return [event async for event in g.stream(request())]

    result = run(consume())
    assert result[-1].response.tool_calls == [{"id": "c1", "name": "find", "arguments": {"x": 1}}]


def test_deadline_cancel_and_environment_secret_policy(monkeypatch):
    async def slow(req):
        await asyncio.sleep(10)
        return httpx.Response(200)

    g = gateway(slow, timeout_seconds=0.01)
    with pytest.raises(ProviderError) as exc:
        run(g.generate(request()))
    assert exc.value.code == "timeout"

    async def cancel():
        task = asyncio.create_task(gateway(slow).generate(request()))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(cancel())
    with pytest.raises(ValueError):
        ProviderConfig(
            id="x",
            kind="anthropic",
            base_url="https://example.invalid",
            credential_ref="FORGE_CORE_SECRET",
        )
    monkeypatch.setenv("FORGE_PROVIDER_ID", "example")
    monkeypatch.setenv("FORGE_PROVIDER_KIND", "anthropic")
    monkeypatch.setenv("FORGE_PROVIDER_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("FORGE_PROVIDER_CREDENTIAL_REF", "FORGE_PROVIDER_KEY_EXAMPLE")
    assert ProviderConfig.from_env().credential_ref == "FORGE_PROVIDER_KEY_EXAMPLE"


def test_pricing_and_anthropic_cached_usage():
    def handler(req):
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 900,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 500,
                },
            },
        )

    g = gateway(
        handler,
        "anthropic",
        input_price_per_million=2,
        cached_input_price_per_million=1,
        output_price_per_million=4,
        currency="USD",
    )
    result = run(g.generate(request()))
    assert result.usage["input_tokens"] == 1000
    assert result.cost["amount"] == pytest.approx(0.0039)
    assert g.health()["successes"] == 1


def test_anthropic_multiturn_tool_conversion():
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "find"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"x":1}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c", "content": "found"},
    ]

    def handler(req):
        body = json.loads(req.content)
        assert body["system"] == "policy"
        assert body["messages"][1]["content"] == [
            {"type": "tool_use", "id": "c", "name": "lookup", "input": {"x": 1}}
        ]
        assert body["messages"][2]["content"] == [
            {"type": "tool_result", "tool_use_id": "c", "content": "found"}
        ]
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
        )

    assert (
        run(
            gateway(handler, "anthropic").generate(
                ModelRequest(
                    model_id="m",
                    messages=messages,
                    budget_context=ModelCallContext("mock-call", "protocol-test", "isolated-test"),
                )
            )
        ).text
        == "ok"
    )


def test_timing_usage_and_deepseek_cache_validation():
    for value in (float("nan"), float("inf"), -1, "1"):
        with pytest.raises(ValueError):
            config(timeout_seconds=value)

    def handler(req):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "prompt_cache_hit_tokens": 4,
                    "prompt_cache_miss_tokens": 6,
                },
            },
        )

    result = run(gateway(handler, "deepseek").generate(request()))
    assert result.usage["cached_input_tokens"] == 4

    def invalid(req):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": -1},
            },
        )

    with pytest.raises(ProviderError) as exc:
        run(gateway(invalid).generate(request()))
    assert exc.value.code == "protocol_error"
    with pytest.raises(ProviderError) as exc:
        run(gateway(handler).generate(request(timeout_seconds=float("nan"))))
    assert exc.value.code == "configuration"


def test_stream_size_and_observer():
    seen = []

    def observer(g, model, provider, value):
        seen.append((g.execution_mode, model, provider, value))

    g = ProviderGateway(
        config(),
        credential_resolver=lambda _: "secret",
        observer=observer,
        budget_ledger=mock_ledger(),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
        ),
    )

    async def consume():
        return [event async for event in g.stream(request())]

    with pytest.raises(ProviderError) as exc:
        run(consume())
    assert exc.value.code == "protocol_error"
    assert seen[0][0] == "mock" and seen[0][3]["outcome"] == "error"


@pytest.mark.parametrize(
    ("finish", "code"), [("length", "truncated"), ("content_filter", "refused")]
)
def test_incomplete_generate_and_stream_never_complete(finish, code):
    usage = {"prompt_tokens": 4, "completion_tokens": 2}

    def handler(req):
        if json.loads(req.content)["stream"]:
            wire = f'data: {{"choices":[{{"delta":{{}},"finish_reason":"{finish}"}}],"usage":{{"prompt_tokens":4,"completion_tokens":2}}}}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, content=wire.encode())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "partial"}, "finish_reason": finish}],
                "usage": usage,
            },
        )

    g = gateway(handler)
    with pytest.raises(ProviderError) as error:
        run(g.generate(request()))
    assert error.value.code == code and error.value.usage["output_tokens"] == 2

    async def consume():
        return [event async for event in g.stream(request())]

    with pytest.raises(ProviderError) as error:
        run(consume())
    assert error.value.code == code and error.value.usage["output_tokens"] == 2


def test_duplicate_tool_ids_and_inconsistent_finish_rejected():
    def handler(req):
        call = {"id": "same", "function": {"name": "f", "arguments": "{}"}}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"tool_calls": [call, call]}, "finish_reason": "tool_calls"}
                ]
            },
        )

    with pytest.raises(ProviderError) as exc:
        run(gateway(handler).generate(request()))
    assert exc.value.code == "protocol_error"


def test_anthropic_stream_tool_input_and_usage():
    frames = [
        {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 10}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "call", "name": "lookup", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"x":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "2}"},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 3},
        },
        {"type": "message_stop"},
    ]
    wire = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames).encode()

    async def consume():
        return [
            event
            async for event in gateway(
                lambda req: httpx.Response(200, content=wire), "anthropic"
            ).stream(request())
        ]

    events = run(consume())
    assert events[-1].response.tool_calls == [
        {"id": "call", "name": "lookup", "arguments": {"x": 2}}
    ]
    assert events[-1].response.usage["output_tokens"] == 3


def test_concurrency_limit_and_rate_spacing():
    import time

    active = 0
    maximum = 0
    times = []

    async def handler(req):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        times.append(time.monotonic())
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    async def concurrent():
        g = gateway(handler, concurrency=1, min_interval_seconds=0.01)
        return await asyncio.gather(*(g.generate(request()) for _ in range(3)))

    assert len(run(concurrent())) == 3
    assert maximum == 1
    assert all(b - a >= 0.009 for a, b in itertools.pairwise(times))


def test_oversized_json_and_provider_error_redaction():
    def oversized(req):
        return httpx.Response(200, content=b"x" * (8 * 1024 * 1024 + 1))

    with pytest.raises(ProviderError) as error:
        run(gateway(oversized).generate(request()))
    assert error.value.code == "protocol_error"

    def bad(req):
        return httpx.Response(503, text="test-secret-value")

    with pytest.raises(ProviderError) as error:
        run(gateway(bad, max_retries=0).generate(request()))
    assert "test-secret-value" not in str(error.value)


def test_model_schema_capability_fail_closed():
    def handler(req):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
        )

    schema = {"type": "object"}
    g = gateway(handler, supports_json_schema=True)
    for capabilities in ({"json_schema": False}, {}, {"json_schema": None}, {"json_schema": True}):
        with pytest.raises(ProviderError, match="unsupported"):
            run(g.generate(request(response_schema=schema, model_capabilities=capabilities)))
    result = run(
        g.generate(
            request(response_schema=schema, capability_override=True, capability_source="operator")
        )
    )
    assert result.text == "{}"


def test_insecure_http_requires_explicit_numeric_loopback_opt_in():
    with pytest.raises(ValueError):
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="http://127.0.0.1:8788/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
        )
    local = ProviderConfig(
        id="p",
        kind="openai_compatible",
        base_url="http://127.0.0.1:8788/v1",
        credential_ref="FORGE_PROVIDER_KEY_TEST",
        allow_insecure_loopback=True,
    )
    assert local.base_url.startswith("http://127.0.0.1")
    for host in ("localhost", "192.168.1.1", "example.invalid"):
        with pytest.raises(ValueError):
            ProviderConfig(
                id="p",
                kind="openai_compatible",
                base_url=f"http://{host}:8788/v1",
                credential_ref="FORGE_PROVIDER_KEY_TEST",
                allow_insecure_loopback=True,
            )


def test_mock_http_observations_persist_real_outcomes(tmp_path):
    from forge_core.model_intelligence import ObservationStore
    from forge_core.store import Store

    observations = ObservationStore(Store(tmp_path / "metrics.sqlite3"))
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    g = ProviderGateway(
        config(max_retries=0),
        credential_resolver=lambda _: "mock-secret",
        transport=httpx.MockTransport(handler),
        observer=observations.record,
        budget_ledger=mock_ledger(),
    )
    assert run(g.generate(request())).text == "ok"
    with pytest.raises(ProviderError):
        run(g.generate(request()))
    summary = observations.summarize("mock-model", "test", mode="mock")
    assert summary["n_samples"] == 2
    assert summary["reliability"] == 0.5
    assert observations.summarize("mock-model", "test", mode="live") is None


def test_truncated_tool_arguments_still_report_usage():
    def handler(req):
        if json.loads(req.content)["stream"]:
            wire = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c","function":{"name":"f","arguments":"{"}}]},"finish_reason":"length"}],"usage":{"prompt_tokens":1,"completion_tokens":2}}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, content=wire)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [{"id": "c", "function": {"name": "f", "arguments": "{"}}]
                        },
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    g = gateway(handler)
    with pytest.raises(ProviderError) as error:
        run(g.generate(request()))
    assert error.value.code == "truncated" and error.value.usage["output_tokens"] == 2

    async def consume():
        return [event async for event in g.stream(request())]

    with pytest.raises(ProviderError) as error:
        run(consume())
    assert error.value.code == "truncated" and error.value.usage["output_tokens"] == 2


def test_read_failure_redacts_transport_details():
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadTimeout("test-secret-value")
            yield b""

    def handler(req):
        return httpx.Response(200, stream=Broken())

    with pytest.raises(ProviderError) as error:
        run(gateway(handler).generate(request()))
    assert error.value.code == "timeout"
    assert "test-secret-value" not in str(error.value)


@pytest.mark.parametrize(
    "options",
    [
        {"supports_tools": 1},
        {"supports_json_schema": "false"},
        {"input_price_per_million": True, "currency": "USD"},
        {"output_price_per_million": 2},
        {"input_price_per_million": 2, "currency": " "},
    ],
)
def test_config_rejects_invalid_capabilities_and_pricing(options):
    with pytest.raises(ValueError):
        config(**options)
