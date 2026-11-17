"""Registry context windows are capacity, never a cumulative Token-budget grant."""

import asyncio
import json

import httpx
import pytest
from test_m5_lifespan_execution import load_main
from test_superagent_loop import (
    MockProvider,
    conversational_decision,
    response,
    rig,  # noqa: F401
)

from forge_core.domain import ConversationMessage, DomainError
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderGateway


def registry_window(store, window=100000000):
    profile = store.list_models()[0].model_copy(update={"context_window": window})
    store.save_model(profile)
    return profile


def snapshot(router):
    with router.store.connection() as c:
        return router._snapshot(c)


def test_registry_window_reaches_model_request_without_premature_byte_compaction(rig, monkeypatch):  # noqa: F811
    store, router, _ = rig
    profile = registry_window(store)
    router.use_registry_context = True
    router.context_limit = 32768  # Conservative fallback is not an implicit cap.
    router.provider = MockProvider(lambda *_: response(conversational_decision("chat")))
    for _ in range(12):
        with store.unit_of_work() as work:
            work.add_message(ConversationMessage(role="user", content="historical text " * 160))

    def no_compaction(**kwargs):
        raise AssertionError("The small-window byte threshold must not apply to Registry100M")

    monkeypatch.setattr(router.context_service, "compact_router", no_compaction)
    assert router.effective_context_limit(profile) == 100000000
    result = asyncio.run(router.handle_user_message("Hello", request_id="registry-window"))
    assert result["intent"] == "chat"
    assert len(router.provider.requests) == 1
    assert router.provider.requests[0].context_limit == 100000000


def test_explicit_operator_cap_preserves_smaller_window(rig):  # noqa: F811
    store, router, _ = rig
    profile = registry_window(store)
    router.context_limit = 32768
    assert router.use_registry_context is False
    assert router.effective_context_limit(profile) == 32768
    context, _ = router._context(snapshot(router), "Hello")
    assert context["budget"]["effective_limit"] == 32768
    router.use_registry_context = True
    assert (
        router.effective_context_limit(profile.model_copy(update={"context_window": None})) == 32768
    )
    assert router.effective_context_limit(None) == 32768


def test_semantic_recall_uses_registry_window_and_accepts_large_capacity(rig):  # noqa: F811
    store, router, _ = rig
    registry_window(store)
    router.use_registry_context = True
    router.context_limit = 32768
    source = ConversationMessage(role="user", content="Retain project notes")
    with store.unit_of_work() as work:
        work.add_message(source)
    router.memory_service.remember(
        "Retain project notes", kind="fact", layer="L1", source_refs=["message:" + source.id]
    )
    provider = MockProvider(lambda *_: response({"candidate_ids": []}))
    result = asyncio.run(
        router.memory_service.retrieve_semantic_context(
            "keep notes",
            provider=provider,
            model_id=router.model_id,
            provider_id=router.provider_id,
            context_limit=router.effective_context_limit(registry_window(store)),
        )
    )
    assert result["semantic"]["status"] == "selected"
    assert provider.requests[0].context_limit == 100000000
    assert provider.requests[0].max_output_tokens == 512


def test_semantic_handle_and_model_compactor_share_registry_window(rig, monkeypatch):  # noqa: F811
    store, router, _ = rig
    registry_window(store)
    router.use_registry_context = True
    router.context_limit = 32768
    router.semantic_recall = True
    captured = []

    async def semantic(query, **kwargs):
        captured.append(("semantic", kwargs["context_limit"]))
        return {"items": [], "semantic": {"status": "empty"}}

    class Compactor:
        def __init__(self, context_service, provider, *, profile, context_limit, output_limit):
            captured.append(("compaction", context_limit))
            self.context_service = context_service

        async def compact(self, **kwargs):
            self.context_service.compact_router(**kwargs)

    monkeypatch.setattr(router.memory_service, "retrieve_semantic_context", semantic)
    monkeypatch.setattr("forge_core.model_compaction.RouterModelCompactor", Compactor)
    router.provider = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            supports_json_schema=True,
        ),
        credential_resolver=lambda _: "placeholder",
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": json.dumps(conversational_decision("chat"))},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10},
                },
            )
        ),
    )
    for index in range(51):
        with store.unit_of_work() as work:
            work.add_message(
                ConversationMessage(role="user", content="Historical turn " + str(index))
            )
    asyncio.run(router.handle_user_message("Hello", request_id="same-window"))
    assert ("compaction", 100000000) in captured
    assert ("semantic", 100000000) in captured


def test_unknown_profile_keeps_fallback_and_live_eligibility_guard(rig):  # noqa: F811
    store, router, _ = rig
    router.use_registry_context = True
    router.context_limit = 32768
    with store.unit_of_work() as work:
        work.connection.execute("DELETE FROM models")

    class UncalledLive:
        execution_mode = "live"

        async def generate(self, request):
            raise AssertionError("Unknown Registry profile must not reach a Provider")

    router.provider = UncalledLive()
    assert router.effective_context_limit(None) == 32768
    with pytest.raises(DomainError, match="capability is unknown"):
        asyncio.run(router.handle_user_message("Hello", request_id="unknown-model"))


@pytest.mark.parametrize("operator_cap,expected", [(None, 100000000), ("32768", 32768)])
def test_main_starts_without_context_env_and_settings_report_effective_registry_limit(
    tmp_path,
    monkeypatch,
    operator_cap,
    expected,
):
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("FORGE_ROUTER_MODEL_ID", "mock-model")
    if operator_cap:
        monkeypatch.setenv("FORGE_ROUTER_CONTEXT_LIMIT", operator_cap)
    else:
        monkeypatch.delenv("FORGE_ROUTER_CONTEXT_LIMIT", raising=False)
    main = load_main(tmp_path, monkeypatch)
    from forge_core.domain import ModelProfile

    main.router.provider_id = "fixture"
    main.store.save_model(
        ModelProfile(
            id="fixture",
            model_id="mock-model",
            provider="fixture",
            display_name="Declared large context fixture",
            context_window=100000000,
        )
    )
    settings = main.settings_summary()
    assert main.router.use_registry_context is (operator_cap is None)
    assert settings["context_limit"] == expected
    assert settings["context_limit_source"] == (
        "model_registry" if operator_cap is None else "operator_cap"
    )
