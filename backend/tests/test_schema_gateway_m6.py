"""MockTransport verifies structured-output prompts and persistent admission."""

import asyncio
import copy
import json
import sqlite3

import httpx
import pytest

from forge_core.model_budget_schema import migrate_model_budget
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.runtime import ModelCallContext, ModelRequest


@pytest.mark.parametrize("wire_format", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_schema_instruction_matches_wire_and_is_reserved(tmp_path, wire_format, streaming):
    path = tmp_path / "schema.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    ledger = ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1)
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if streaming:
            return httpx.Response(
                200,
                content=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\ndata: [DONE]\n\n',
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    config = ProviderConfig(
        id="p",
        kind="openai_compatible",
        base_url="https://example.invalid/v1",
        credential_ref="FORGE_PROVIDER_KEY_TEST",
        supports_json_schema=True,
    )
    gateway = ProviderGateway(
        config,
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(handler),
        budget_ledger=ledger,
    )
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    messages = [{"role": "system", "content": "policy"}, {"role": "user", "content": "hello"}]
    original = copy.deepcopy(messages)
    spec = (
        {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": schema},
            }
        }
        if wire_format
        else {"response_schema": schema}
    )
    request = ModelRequest(
        model_id="m",
        messages=messages,
        max_output_tokens=20,
        model_capabilities={"json_schema": True},
        model_capability_sources={"json_schema": "operator"},
        budget_context=ModelCallContext("call", "router", "w"),
        **spec,
    )

    async def invoke():
        if streaming:
            return [event async for event in gateway.stream(request)]
        return await gateway.generate(request)

    asyncio.run(invoke())
    payload = seen[0]
    assert messages == original and request.messages == original
    assert payload["response_format"]["json_schema"]["schema"] == schema
    injected = payload["messages"][1]
    assert injected["role"] == "system"
    assert (
        json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        in injected["content"]
    )
    assert payload["messages"][0] == original[0] and payload["messages"][2] == original[1]
    with sqlite3.connect(path) as connection:
        reserved = connection.execute(
            "SELECT input_reserved FROM model_budget_attempts"
        ).fetchone()[0]
    assert reserved == len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 64


def test_schema_unknown_capability_is_rejected_before_post(tmp_path):
    path = tmp_path / "schema.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    calls = []
    gateway = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_json_schema=True,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        budget_ledger=ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1),
    )
    request = ModelRequest(
        model_id="m",
        messages=[{"role": "user", "content": "hello"}],
        response_schema={"type": "object"},
        budget_context=ModelCallContext("call", "router", "w"),
    )
    with pytest.raises(ProviderError, match="unsupported"):
        asyncio.run(gateway.generate(request))
    assert calls == []


@pytest.mark.parametrize("streaming", [False, True])
def test_final_schema_payload_context_limit_checked_before_post(tmp_path, streaming):
    path = tmp_path / "schema.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    calls = []
    gateway = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_json_schema=True,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        budget_ledger=ModelCallBudgetLedger(path, token_limit=10000, concurrency_limit=1),
    )
    request = ModelRequest(
        model_id="m",
        messages=[{"role": "user", "content": "hello"}],
        max_output_tokens=20,
        response_schema={"type": "object"},
        model_capabilities={"json_schema": True},
        model_capability_sources={"json_schema": "operator"},
        budget_context=ModelCallContext("call", "router", "w"),
    )
    wire = gateway._payload(request, streaming)
    request.context_limit = (
        len(json.dumps(wire, ensure_ascii=False).encode("utf-8"))
        + 64
        + request.max_output_tokens
        - 1
    )

    async def invoke():
        if streaming:
            return [event async for event in gateway.stream(request)]
        return await gateway.generate(request)

    with pytest.raises(ProviderError, match="context_exceeded"):
        asyncio.run(invoke())
    assert calls == []
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 0
