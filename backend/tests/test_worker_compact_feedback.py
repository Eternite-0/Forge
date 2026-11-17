"""Small real Gateway context guard; synthetic responses, real governed tools."""

import asyncio
import json
from dataclasses import replace
from pathlib import PureWindowsPath

import pytest
import test_worker_intelligence_phase2 as fixture
from test_model_compaction_v29 import http_response, mock_gateway

from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderError

REFERENCE_ROOT = "/synthetic/worker-context-fixture"


def native_size(body):
    return len(json.dumps(body, ensure_ascii=False).encode()) + 64 + body["max_tokens"]


def reference_paths(value, root):
    """Only substitute fixture-root spelling, including JSON inside message text."""
    if isinstance(value, dict):
        return {key: reference_paths(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [reference_paths(item, root) for item in value]
    if isinstance(value, str):
        for spelling in sorted({str(root), root.as_posix()}, key=len, reverse=True):
            value = value.replace(json.dumps(spelling)[1:-1], REFERENCE_ROOT)
            value = value.replace(spelling, REFERENCE_ROOT)
    return value


def test_reference_projection_accounts_for_windows_json_path_escaping():
    root = PureWindowsPath(r"C:\Users\runner\very-long-checkout\fixture")
    body = {
        "max_tokens": 1500,
        "messages": [{"content": json.dumps({"path": str(root / "result.json")})}],
        "path": str(root / "result.json"),
        "other": "Keep source hashes, receipt identifiers, and contracts unchanged.",
    }
    projected = reference_paths(body, root)
    assert projected["path"] == REFERENCE_ROOT + r"\result.json"
    assert json.loads(projected["messages"][0]["content"])["path"] == projected["path"]
    assert projected["other"] == body["other"]
    assert native_size(body) > native_size(projected)


def test_fixture_bound_wrong_path_repair_then_write_read_keeps_native_guard(tmp_path, monkeypatch):
    # Calibrate only filesystem spelling overhead, not model/history growth.
    # Both roots have equal length; identifiers in this fixture are fixed-width.
    calibration = tmp_path / "cal"
    execution = tmp_path / "run"
    calibration.mkdir()
    execution.mkdir()
    with monkeypatch.context() as patch:
        bodies = run_fixture(calibration, patch, 65536)
    overhead = max(
        max(0, native_size(body) - native_size(reference_paths(body, calibration)))
        for body in bodies
    )
    bound = 8192 + overhead
    with monkeypatch.context() as patch:
        bodies = run_fixture(execution, patch, bound)
    # The 8192 demonstration is explicitly for this synthetic reference root,
    # not a claim that arbitrary real filesystem paths fit an 8K model.
    assert max(native_size(reference_paths(body, execution)) for body in bodies) <= 8192
    assert max(native_size(body) for body in bodies) <= bound


def run_fixture(tmp_path, monkeypatch, context_limit):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    quest_type = fixture.Quest
    profile_type = fixture.ModelProfile
    goal = 'Write work/result.json containing exactly {"ok":true}, then read it back with file_read and report the verified contents. Use only these two tools.'
    monkeypatch.setattr(fixture, "Quest", lambda **kw: quest_type(**{**kw, "goal": goal}))
    monkeypatch.setattr(
        fixture,
        "ModelProfile",
        lambda **kw: profile_type(
            **{**kw, "context_window": context_limit, "max_output_tokens": 1500}
        ),
    )
    propose = fixture.TeamCompositionService.propose

    def small_role(self, quest_id, roles, limits, **kwargs):
        roles = [{**role, "input_tokens": 5500, "output_tokens": 1500} for role in roles]
        return propose(self, quest_id, roles, limits, **kwargs)

    monkeypatch.setattr(fixture.TeamCompositionService, "propose", small_role)
    store, manager, _, worker = fixture.prepared(
        tmp_path, [], tools=["file_write", "file_read"], criteria=["verified"]
    )
    payloads = []

    def handler(request):
        body = json.loads(request.content)
        payloads.append(body)
        # ProviderGateway's unchanged serialized-byte guard has already admitted this request.
        assert native_size(body) <= context_limit
        step = len(payloads)
        if step <= 3:
            args = {"path": "result.json" if step == 1 else "work/result.json"}
            if step <= 2:
                args["content"] = '{"ok":true}'
            return http_response(
                None,
                finish="tool_calls",
                tool_calls=[
                    {
                        "id": f"step-{step}",
                        "type": "function",
                        "function": {
                            "name": "file_write" if step <= 2 else "file_read",
                            "arguments": json.dumps(args),
                        },
                    }
                ],
            )
        assert step == 4
        observed = [m for m in body["messages"] if m["role"] == "tool"][-1]
        assert json.loads(observed["content"])["content"] == '{"ok":true}'
        return http_response(
            json.dumps(fixture.delivery("File written and read back; review pending."))
        )

    gateway = mock_gateway(store, handler)
    requests = []
    generate = gateway.generate

    async def capture(request):
        requests.append(request)
        return await generate(request)

    gateway.generate = capture
    manager.providers = ProviderPool({"protocol": gateway})
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending", result
    assert len(payloads) == 4
    assert (tmp_path / "work/result.json").read_bytes() == b'{"ok":true}'
    assert result["repairs"] == 1 and result["tool_spent"] == 3
    # Re-measure the complete provider-native payload with actual paths/IDs.
    # The unchanged guard must reject even one byte below the required bound.
    for request in requests:
        body = gateway._payload(request, False)
        required = (
            len(json.dumps(body, ensure_ascii=False).encode()) + 64 + request.max_output_tokens
        )
        assert request.context_limit == context_limit and request.max_output_tokens == 1500
        assert required <= context_limit
        with pytest.raises(ProviderError) as rejected:
            gateway._payload(replace(request, context_limit=required - 1), False)
        assert rejected.value.code == "context_exceeded"
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 2
        assert (
            c.execute(
                "SELECT count(*) FROM action_intents WHERE tool_name='file_write'"
            ).fetchone()[0]
            == 1
        )

    return payloads
