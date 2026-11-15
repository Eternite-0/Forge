"""Router uses native model handoffs automatically and preserves the live turn."""

import asyncio
import json
from dataclasses import replace

from test_model_compaction_v29 import messages
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_router_intelligence_phase2 import decision

from forge_core.context_service import ContextService
from forge_core.domain import ModelProfile, ProtocolSupport
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.store import Store


def test_router_automatically_checkpoints_through_gateway_before_next_real_decision(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store = Store(tmp_path / "router.sqlite3")
    original = messages(store, count=60, body_chars=200)
    seen = []

    def responder(body, _ordinal):
        if not body.get("response_format"):
            assert not body.get("tools")
            packet = json.loads(body["messages"][1]["content"])
            seen.extend(item["id"] for item in packet["messages"])
            return {
                "content": "Keep the original exact sources. User work is still pending; use readback when needed."
            }
        context = next(
            json.loads(item["content"])
            for item in body["messages"]
            if item["role"] == "user" and item["content"].startswith('{"')
        )
        capsule = json.loads(context["capsule"])
        assert capsule["model_handoff"]["status"] == "unverified"
        assert "exact sources" in capsule["model_handoff"]["text"]
        assert context["input"] == "Continue the active goal and preserve my sources."
        return {"content": json.dumps(decision())}

    with offline_loopback_gateway(store, responder) as (gateway, requests):
        gateway.config = replace(gateway.config, supports_json_schema=True)
        gateway._budget_ledger = ModelCallBudgetLedger(
            store.path, token_limit=None, concurrency_limit=1
        )
        profile = ModelProfile(
            id="router-native",
            provider=gateway.config.id,
            model_id="compact-model",
            display_name="Offline native Router",
            context_window=65536,
            max_output_tokens=8192,
            tool_calling_support=ProtocolSupport(supported=True, source="fixture"),
            structured_output_support=ProtocolSupport(supported=True, source="fixture"),
        )
        store.save_model(profile)
        router = RouterService(
            store,
            TeamComposer(ModelRegistry(store)),
            provider=gateway,
            model_id=profile.model_id,
            provider_id=profile.provider,
            context_limit=65536,
            output_reserve=1024,
        )
        result = asyncio.run(
            router.handle_user_message(
                "Continue the active goal and preserve my sources.", request_id="after-auto-compact"
            )
        )
        assert result["intent"] == "chat" and len(requests) >= 2
    assert seen == [item.id for item in original[:-10]]
    for item in original[:-10]:
        assert (
            ContextService(store).read_source("router", "main", item.id)["content"] == item.content
        )
    with store.connection() as connection:
        attempts = connection.execute(
            "SELECT purpose,state,input_charged,output_charged FROM model_budget_attempts ORDER BY rowid"
        ).fetchall()
        assert attempts[0]["purpose"] == "context_compaction"
        assert attempts[-1]["purpose"] == "router"
        assert all(
            item["state"] == "settled"
            and item["input_charged"] >= 0
            and item["output_charged"] >= 0
            for item in attempts
        )
