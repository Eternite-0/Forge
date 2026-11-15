"""Optional cumulative limits retain exact usage, concurrency and native bounds."""

import asyncio
import json
import sqlite3

import httpx
import pytest

from forge_core.model_budget_schema import migrate_model_budget
from forge_core.model_call_budget import (
    BudgetExceeded,
    ConcurrencyUnavailable,
    ModelCallBudgetLedger,
)
from forge_core.operator_settings import OperatorConfig
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.runtime import ModelCallContext, ModelRequest
from forge_core.token_policy import token_budgets_enforced


def ledger_at(tmp_path, **kwargs):
    path = tmp_path / "usage.sqlite3"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    return ModelCallBudgetLedger(path, concurrency_limit=1, **kwargs)


def test_normal_policy_has_no_cumulative_token_limit_and_rejects_invalid_settings(monkeypatch):
    monkeypatch.delenv("FORGE_ENFORCE_TOKEN_BUDGETS")
    assert token_budgets_enforced() is False
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "arbitrary")
    with pytest.raises(ValueError, match="true or false"):
        token_budgets_enforced()


def test_no_total_limit_retains_unknown_attempts_and_concurrency_after_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    ledger = ledger_at(tmp_path, token_limit=None)
    first = ledger.admit(
        ModelCallContext("first", "router", "workspace"),
        "p",
        "m",
        input_tokens=4000,
        output_tokens=2000,
        remote_timeout=30,
    )
    restored = ModelCallBudgetLedger(ledger.path, token_limit=None, concurrency_limit=1)
    assert restored.token_limit is None and not restored.enforce_token_budgets
    with pytest.raises(ConcurrencyUnavailable):
        restored.admit(
            ModelCallContext("second", "router", "workspace"),
            "p",
            "m",
            input_tokens=4000,
            output_tokens=2000,
            remote_timeout=30,
        )
    restored.settle(first, {})
    with pytest.raises(ConcurrencyUnavailable):
        restored.admit(
            ModelCallContext("second", "router", "workspace"),
            "p",
            "m",
            input_tokens=4000,
            output_tokens=2000,
            remote_timeout=30,
        )
    restored.settle(first, {"input_tokens": 3900, "output_tokens": 1800})
    second = restored.admit(
        ModelCallContext("second", "router", "workspace"),
        "p",
        "m",
        input_tokens=4000,
        output_tokens=2000,
        remote_timeout=30,
    )
    restored.settle(second, {"input_tokens": 3800, "output_tokens": 1700})
    with sqlite3.connect(ledger.path) as connection:
        rows = connection.execute(
            "SELECT state,input_charged,output_charged FROM model_budget_attempts ORDER BY rowid"
        ).fetchall()
    assert rows == [("settled", 3900, 1800), ("settled", 3800, 1700)]


def test_disabled_policy_does_not_treat_a_configured_zero_as_a_total_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    ledger = ledger_at(tmp_path, token_limit=0, scope_limits={("workspace", "workspace"): (0, 1)})
    attempt = ledger.admit(
        ModelCallContext("first", "router", "workspace"),
        "p",
        "m",
        input_tokens=40,
        output_tokens=20,
        remote_timeout=30,
    )
    ledger.settle(attempt, {"input_tokens": 30, "output_tokens": 10})
    assert ledger.configured_token_limit == 0 and ledger.token_limit is None


def test_opt_in_total_budget_still_blocks_over_limit_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    ledger = ledger_at(tmp_path, token_limit=10)
    with pytest.raises(BudgetExceeded, match="budget exhausted"):
        ledger.admit(
            ModelCallContext("first", "router", "workspace"),
            "p",
            "m",
            input_tokens=8,
            output_tokens=3,
            remote_timeout=30,
        )


def test_native_context_limit_still_rejects_before_http_with_unbounded_total(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    ledger = ledger_at(tmp_path, token_limit=None)
    posted = []

    def respond(request):
        posted.append(request)
        return httpx.Response(200, json={})

    provider = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
        ),
        credential_resolver=lambda _: "test-only",
        transport=httpx.MockTransport(respond),
        budget_ledger=ledger,
    )
    with pytest.raises(ProviderError) as failure:
        asyncio.run(
            provider.generate(
                ModelRequest(
                    provider_id="p",
                    model_id="m",
                    messages=[{"role": "user", "content": "x" * 4096}],
                    context_limit=4096,
                    max_output_tokens=100,
                    budget_context=ModelCallContext("first", "router", "workspace"),
                )
            )
        )
    assert failure.value.code == "context_exceeded" and posted == []
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute("SELECT count(*) FROM model_budget_attempts").fetchone()[0] == 0


def test_operator_config_expresses_no_limit_without_a_large_number():
    config = OperatorConfig(
        provider=None, router_context_limit=65536, model_concurrency_limit=2, network_hosts=[]
    )
    assert config.model_token_limit is None and config.enforce_token_budgets is False
    encoded = json.loads(config.model_dump_json())
    assert encoded["model_token_limit"] is None
    with pytest.raises(ValueError, match="explicit limit"):
        OperatorConfig(**{**encoded, "enforce_token_budgets": True})
