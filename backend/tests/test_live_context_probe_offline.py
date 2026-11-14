"""Exercise the opt-in probe offline; this test makes no network calls."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from forge_core.providers import ProviderGateway
from forge_core.runtime import ModelResponse


@pytest.mark.parametrize("invalid", [False, True])
def test_probe_fixtures_restart_and_report_with_mock_gateway(tmp_path, monkeypatch, invalid):
    source = Path(__file__).with_name("live_context_probe.py")
    spec = importlib.util.spec_from_file_location("forge_context_probe_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(tmp_path / "backend" / "tests" / "live_context_probe.py")
    settings = {
        "FORGE_LIVE_CONTEXT_PROBE": "1",
        "FORGE_PROVIDER_ID": "p",
        "FORGE_PROVIDER_KIND": "openai_compatible",
        "FORGE_PROVIDER_BASE_URL": "https://example.invalid/v1",
        "FORGE_PROVIDER_CREDENTIAL_REF": "FORGE_PROVIDER_KEY_TEST",
        "FORGE_PROVIDER_KEY_TEST": "fixture-only",
        "FORGE_ROUTER_MODEL_ID": "m",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    if invalid:
        monkeypatch.setenv("FORGE_CONTEXT_PROBE_TOKEN_LIMIT", "75000")
    else:
        monkeypatch.delenv("FORGE_CONTEXT_PROBE_TOKEN_LIMIT", raising=False)

    class MockGateway:
        def __init__(self, config, *, budget_ledger):
            self.ledger = budget_ledger
            self.gateway = ProviderGateway(config, budget_ledger=budget_ledger)

        def _payload(self, request, streaming):
            return self.gateway._payload(request, streaming)

        async def generate(self, request):
            context = json.loads(request.messages[1]["content"])
            target = next(q for q in context["quests"] if q["title"] == "北辰代理算法实验")
            attempt = self.ledger.admit(
                request.budget_context,
                "p",
                "m",
                input_tokens=len(
                    json.dumps(self.gateway._payload(request, False), ensure_ascii=False).encode(
                        "utf-8"
                    )
                )
                + 64,
                output_tokens=request.max_output_tokens,
                remote_timeout=30,
            )
            self.ledger.settle(attempt, {"input_tokens": 50, "output_tokens": 10})
            decision = {
                "intent": "bad-intent" if invalid else "continue_quest",
                "reply": "fixture-only" if invalid else "继续",
                "title": None,
                "goal": None,
                "quest_id": target["id"],
                "candidates": [target["id"]],
            }
            return ModelResponse(
                text=json.dumps(decision),
                tool_calls=[],
                usage={"input_tokens": 50, "output_tokens": 10},
                finish_reason="stop",
            )

    monkeypatch.setattr(module, "ProviderGateway", MockGateway)
    if invalid:
        with pytest.raises(SystemExit) as exc:
            asyncio.run(module.main())
        assert exc.value.code == 1
    else:
        asyncio.run(module.main())
    reports = list((tmp_path / ".local" / "phase1" / "milestone6").glob("*/report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert (
        report["status"] == ("failed" if invalid else "passed") and report["constraints_preserved"]
    )
    assert report["fixture_constraints"] == 60 and report["fixture_cancelled_quests"] == 105
    assert len(report["attempts"]) == 1 and report["attempts"][0]["state"] == "settled"
    assert report["responses"][0]["finish_reason"] == "stop"
    assert report["responses"][0]["tool_names"] == []
    assert report["responses"][0]["input_serialized_bytes"] > 0
    assert report["token_limit"] == (75000 if invalid else 30000)
    if invalid:
        assert report["domain_error"] == "Invalid Router model decision"
        assert report["schema_validation"][0]["type"] == "literal_error"
        assert report["model_decision"]["reply"] == "[REDACTED]"
    assert "fixture-only" not in reports[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("limit", ["0", "100001", "1.0", " 75000", "-1", "true", "９"])
def test_probe_rejects_invalid_token_limit_before_creating_workspace(tmp_path, monkeypatch, limit):
    source = Path(__file__).with_name("live_context_probe.py")
    spec = importlib.util.spec_from_file_location("forge_context_probe_invalid", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(tmp_path / "backend" / "tests" / "live_context_probe.py")
    for key, value in {
        "FORGE_LIVE_CONTEXT_PROBE": "1",
        "FORGE_PROVIDER_ID": "p",
        "FORGE_PROVIDER_KIND": "openai_compatible",
        "FORGE_PROVIDER_BASE_URL": "https://example.invalid/v1",
        "FORGE_PROVIDER_CREDENTIAL_REF": "FORGE_PROVIDER_KEY_TEST",
        "FORGE_PROVIDER_KEY_TEST": "fixture-only",
        "FORGE_ROUTER_MODEL_ID": "m",
        "FORGE_CONTEXT_PROBE_TOKEN_LIMIT": limit,
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit, match="must be an integer"):
        asyncio.run(module.main())
    assert not (tmp_path / ".local").exists()


def test_probe_records_request_bytes_when_second_call_is_denied():
    from forge_core.model_call_budget import BudgetExceeded
    from forge_core.runtime import ModelRequest

    source = Path(__file__).with_name("live_context_probe.py")
    spec = importlib.util.spec_from_file_location("forge_context_probe_bounded", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Gateway:
        def __init__(self):
            self.calls = 0

        def _payload(self, request, streaming):
            return {"messages": request.messages, "stream": streaming}

        async def generate(self, request):
            self.calls += 1
            if self.calls == 2:
                raise BudgetExceeded("denied")
            return ModelResponse(
                text="", tool_calls=[{"name": "list_quests"}], usage={}, finish_reason="tool_calls"
            )

    async def exercise():
        provider = module.TwoCalls(Gateway())
        request = ModelRequest(model_id="m", messages=[{"role": "user", "content": "中文"}])
        await provider.generate(request)
        with pytest.raises(BudgetExceeded):
            await provider.generate(request)
        with pytest.raises(RuntimeError, match="probe_call_limit"):
            await provider.generate(request)
        assert provider.calls == 2
        assert provider.responses == [
            {
                "input_serialized_bytes": len(
                    json.dumps(
                        provider.gateway._payload(request, False), ensure_ascii=False
                    ).encode("utf-8")
                ),
                "finish_reason": "tool_calls",
                "tool_names": ["list_quests"],
            },
            {
                "input_serialized_bytes": len(
                    json.dumps(
                        provider.gateway._payload(request, False), ensure_ascii=False
                    ).encode("utf-8")
                ),
                "finish_reason": None,
                "tool_names": [],
            },
        ]

    asyncio.run(exercise())
