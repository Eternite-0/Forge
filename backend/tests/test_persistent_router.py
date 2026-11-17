import asyncio
import hashlib
import json
import sqlite3

import pytest

from forge_core.conversation_read import read_committed_turn
from forge_core.domain import ConversationMessage, DomainError, ForgeEvent, Quest
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.store import Store


class MockProvider:
    """Explicit mock; no remote model or worker runs in these tests."""

    execution_mode = "mock"

    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return type("Response", (), {"text": json.dumps(self.decision), "usage": {}})()


def test_router_informs_tool_limits_and_last_round_requires_final_decision(tmp_path):
    _store, _, router = make_router(tmp_path, {})

    class Provider:
        execution_mode = "mock"

        def __init__(self):
            self.requests = []

        async def generate(self, request):
            self.requests.append(request)
            assert request.context_limit == router.context_limit
            assert "4" in request.messages[0]["content"]
            if len(self.requests) < 4:
                assert request.tools
                return type(
                    "Response",
                    (),
                    {
                        "text": "",
                        "tool_calls": [
                            {"id": f"t{len(self.requests)}", "name": "list_quests", "arguments": {}}
                        ],
                    },
                )()
            assert not request.tools
            assert "final" in request.messages[-1]["content"].lower()
            return type(
                "Response",
                (),
                {
                    "text": json.dumps(
                        {
                            "intent": "chat",
                            "reply": "ok",
                            "title": None,
                            "goal": None,
                            "quest_id": None,
                            "candidates": [],
                        }
                    ),
                    "usage": {},
                },
            )()

    provider = Provider()
    router.provider = provider
    result = asyncio.run(router.handle_user_message("hello", request_id="tool-limits"))
    assert result["intent"] == "chat"
    assert len(provider.requests) == 4


def test_router_still_rejects_five_tool_calls(tmp_path):
    store, _, router = make_router(tmp_path, {})

    class Provider:
        execution_mode = "mock"

        async def generate(self, request):
            return type(
                "Response",
                (),
                {
                    "text": "",
                    "tool_calls": [
                        {"id": str(i), "name": "list_quests", "arguments": {}} for i in range(5)
                    ],
                },
            )()

    router.provider = Provider()
    with pytest.raises(DomainError, match="Too many Router tool calls"):
        asyncio.run(router.handle_user_message("hello", request_id="tool-overflow"))
    assert store.list_messages() == []


def test_router_rejects_tools_returned_on_final_round(tmp_path):
    store, _, router = make_router(tmp_path, {})

    class Provider:
        execution_mode = "mock"

        async def generate(self, request):
            return type(
                "Response",
                (),
                {"text": "", "tool_calls": [{"id": "t", "name": "list_quests", "arguments": {}}]},
            )()

    router.provider = Provider()
    with pytest.raises(DomainError, match="final round cannot call tools"):
        asyncio.run(router.handle_user_message("hello", request_id="final-tool"))
    assert store.list_messages() == []


def make_router(tmp_path, decision):
    store = Store(tmp_path / "router.sqlite3")
    provider = MockProvider(decision)
    return (
        store,
        provider,
        RouterService(
            store, TeamComposer(ModelRegistry(store)), provider=provider, model_id="mock"
        ),
    )


def test_model_decision_and_request_idempotency(tmp_path):
    decision = {
        "intent": "create_quest",
        "reply": "已登记",
        "title": "研究",
        "goal": "研究",
        "quest_id": None,
        "candidates": [],
    }
    store, provider, router = make_router(tmp_path, decision)
    assert read_committed_turn(store, "request-1") == {"state": "unknown"}
    first = asyncio.run(router.handle_user_message("研究", request_id="request-1"))
    recovered = read_committed_turn(Store(path=store.path, workspace=store.workspace), "request-1")
    assert recovered == {
        "state": "committed",
        "user_message": first["user_message"],
        "assistant_message": first["assistant_message"],
    }
    assert provider.calls == 1
    second = asyncio.run(router.handle_user_message("研究", request_id="request-1"))
    assert first["intent"] == "create_quest"
    assert second["intent"] == "create_quest"
    assert provider.calls == 1
    assert len(store.list_quests()) == 1
    assert first["quest_id"] == store.list_quests()[0]["id"]
    assert second == first
    assert len(store.list_messages()) == 2
    state = store.ensure_router()
    capsule = json.loads(state["capsule"])
    assert capsule["quest_id"] == store.list_quests()[0]["id"]
    assert capsule["goal"] == "研究"
    assert state["capsule_version"] == 1


def test_ambiguous_reference_never_picks_latest(tmp_path):
    decision = {
        "intent": "continue_quest",
        "reply": "继续",
        "title": None,
        "goal": None,
        "quest_id": None,
        "candidates": [],
    }
    store, _, router = make_router(tmp_path, decision)
    store.save_quest(Quest(title="甲", goal="甲"))
    store.save_quest(Quest(title="乙", goal="乙"))
    result = asyncio.run(router.handle_user_message("继续", request_id="ambiguous"))
    assert result["intent"] == "ambiguous"
    assert result["quest_id"] is None
    assert len(store.list_quests()) == 2


def test_invalid_model_output_is_atomic(tmp_path):
    store, _, router = make_router(tmp_path, {"intent": "create_quest", "passed": True})
    before = store.ensure_router()
    with pytest.raises(DomainError):
        asyncio.run(router.handle_user_message("do it", request_id="invalid"))
    assert store.list_messages() == []
    assert store.list_quests() == []
    assert store.ensure_router() == before
    # Rejected attempts may leave audit diagnostics, never a committed turn,
    # task or scheduling event. Diagnostics expose fixed codes and hashes.
    events = store.list_events()
    assert len(events) == 2
    assert {item["event_type"] for item in events} == {"router.decision_rejected"}
    diagnostics = [json.loads(item["payload"]) for item in events]
    assert [item["stage"] for item in diagnostics] == ["initial", "repair"]
    assert all(
        set(item) == {"stage", "response_sha256", "errors", "response_shape", "execution_mode"}
        for item in diagnostics
    )
    assert all(len(item["response_sha256"]) == 64 for item in diagnostics)
    assert "do it" not in json.dumps(events) and "passed" not in json.dumps(events)


def test_chat_with_help_does_not_create_quest(tmp_path):
    decision = {
        "intent": "chat",
        "reply": "你好",
        "title": None,
        "goal": None,
        "quest_id": None,
        "candidates": [],
    }
    store, _, router = make_router(tmp_path, decision)
    result = asyncio.run(router.handle_user_message("帮我解释你好", request_id="chat"))
    assert result["intent"] == "chat"
    assert result["quest_id"] is None
    assert store.list_quests() == []


def test_explicit_quest_conflict_or_unknown_never_selects_other(tmp_path):
    store, provider, router = make_router(tmp_path, {})
    first, second = Quest(title="旧", goal="旧"), Quest(title="新", goal="新")
    store.save_quest(first)
    store.save_quest(second)
    provider.decision = {
        "intent": "continue_quest",
        "reply": "继续",
        "title": None,
        "goal": None,
        "quest_id": second.id,
        "candidates": [second.id],
    }
    result = asyncio.run(router.handle_user_message(f"继续 {first.id}", request_id="wrong"))
    assert result["intent"] == "ambiguous"
    result = asyncio.run(router.handle_user_message("继续 quest_unknown", request_id="unknown"))
    assert result["intent"] == "ambiguous"
    provider.decision.update(quest_id=first.id, candidates=[first.id])
    result = asyncio.run(router.handle_user_message(f"继续 {first.id}", request_id="old"))
    assert result["intent"] == "continue_quest"
    assert result["quest_id"] == first.id


def test_approval_response_does_not_decide(tmp_path):
    store, provider, router = make_router(tmp_path, {})
    quest = Quest(title="审批", goal="审批")
    store.save_quest(quest)
    provider.decision = {
        "intent": "approval_response",
        "reply": "已批准",
        "title": None,
        "goal": None,
        "quest_id": quest.id,
        "candidates": [quest.id],
    }
    result = asyncio.run(router.handle_user_message("同意", request_id="approval"))
    assert result["intent"] == "approval_response"
    assert result["quest_id"] == quest.id
    assert "不会代替用户审批" in result["assistant_message"]["content"]
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_create_returns_its_own_quest_when_another_is_added_concurrently(tmp_path):
    store, _, router = make_router(tmp_path, {})
    other = Quest(title="并发新增", goal="独立目标")

    class ConcurrentProvider:
        execution_mode = "mock"
        calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                store.save_quest(other)
            return type(
                "Response",
                (),
                {
                    "text": json.dumps(
                        {
                            "intent": "create_quest",
                            "reply": "ok",
                            "title": "本轮",
                            "goal": "本轮目标",
                            "quest_id": None,
                            "candidates": [],
                        }
                    ),
                    "usage": {},
                },
            )()

    router.provider = ConcurrentProvider()
    result = asyncio.run(router.handle_user_message("新任务", request_id="concurrent-create"))
    assert result["quest_id"] != other.id
    assert store.get_quest(result["quest_id"])["goal"] == "本轮目标"
    assert (
        asyncio.run(router.handle_user_message("新任务", request_id="concurrent-create")) == result
    )


def test_turn_insert_failure_rolls_back_quest_and_messages(tmp_path):
    store, _, router = make_router(
        tmp_path,
        {
            "intent": "create_quest",
            "reply": "ok",
            "title": "目标",
            "goal": "目标",
            "quest_id": None,
            "candidates": [],
        },
    )
    with store.unit_of_work() as work:
        work.connection.execute(
            "CREATE TRIGGER reject_turn BEFORE INSERT ON router_turns BEGIN SELECT RAISE(ABORT, 'turn failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="turn failure"):
        asyncio.run(router.handle_user_message("创建", request_id="fail-turn"))
    assert store.list_quests() == []
    assert store.list_messages() == []
    assert store.list_events() == []


def test_long_history_is_trimmed_and_unseen_event_cursor_does_not_advance(tmp_path):
    decision = {
        "intent": "chat",
        "reply": "收到",
        "title": None,
        "goal": None,
        "quest_id": None,
        "candidates": [],
    }
    store, _, router = make_router(tmp_path, decision)
    router.context_limit = 14000
    router.output_reserve = 512
    for _ in range(20):
        store.add_message(ConversationMessage(role="user", content="x" * 3000))
    store.append_event(ForgeEvent(event_type="large", payload={"data": "x" * 20000}))
    result = asyncio.run(router.handle_user_message("继续聊天", request_id="long"))
    assert result["intent"] == "chat"
    assert result["assistant_message"]["metadata"]["context_budget"]["history_included"] < 20
    assert store.ensure_router()["event_cursor"] == 0


def test_concurrent_quest_change_retries_before_update(tmp_path):
    store, _, router = make_router(tmp_path, {})
    quest = Quest(title="并发", goal="原始")
    store.save_quest(quest)

    class ChangingProvider:
        execution_mode = "mock"
        calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                with store.unit_of_work() as work:
                    work.connection.execute(
                        "UPDATE quests SET goal='外部更新',version=version+1,plan_revision=plan_revision+1 WHERE id=?",
                        (quest.id,),
                    )
            decision = {
                "intent": "update_quest",
                "reply": "更新",
                "title": None,
                "goal": "新目标",
                "quest_id": quest.id,
                "candidates": [quest.id],
            }
            return type(
                "Response", (), {"text": json.dumps(decision), "tool_calls": [], "usage": {}}
            )()

    provider = ChangingProvider()
    router.provider = provider
    result = asyncio.run(router.handle_user_message(f"更新 {quest.id}", request_id="race"))
    assert result["quest_id"] == quest.id
    assert provider.calls == 2
    assert store.get_quest(quest.id)["goal"] == "新目标"
    assert store.get_quest(quest.id)["plan_revision"] == 2


def test_global_status_returns_no_quest_binding(tmp_path):
    store, _, router = make_router(
        tmp_path,
        {
            "intent": "status_query",
            "reply": "status",
            "title": None,
            "goal": None,
            "quest_id": None,
            "candidates": [],
        },
    )
    store.save_quest(Quest(title="one", goal="one"))
    result = asyncio.run(router.handle_user_message("status", request_id="global-status"))
    assert result["intent"] == "status_query"
    assert result["quest_id"] is None


def test_legacy_turn_retry_does_not_invent_quest_binding(tmp_path):
    store, provider, router = make_router(tmp_path, {})
    previous = {"intent": "create_quest", "user_message": {}, "assistant_message": {}}
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO router_turns VALUES (?,?,?,?,?)",
            (
                "legacy",
                "main",
                hashlib.sha256(b"old").hexdigest(),
                json.dumps(previous),
                "2026-01-01",
            ),
        )
    assert asyncio.run(router.handle_user_message("old", request_id="legacy")) == previous
    assert provider.calls == 0


def test_router_read_tool_and_forbidden_tool(tmp_path):
    store, _, router = make_router(tmp_path, {})

    class ToolProvider:
        execution_mode = "mock"
        calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                return type(
                    "Response",
                    (),
                    {
                        "text": "",
                        "tool_calls": [{"id": "call1", "name": "list_quests", "arguments": {}}],
                    },
                )()
            assert request.messages[-1]["role"] == "tool"
            decision = {
                "intent": "chat",
                "reply": "已查看",
                "title": None,
                "goal": None,
                "quest_id": None,
                "candidates": [],
            }
            return type("Response", (), {"text": json.dumps(decision), "tool_calls": []})()

    provider = ToolProvider()
    router.provider = provider
    assert asyncio.run(router.handle_user_message("查询", request_id="tool"))["intent"] == "chat"
    assert provider.calls == 2

    class ForbiddenProvider:
        execution_mode = "mock"

        async def generate(self, request):
            return type(
                "Response",
                (),
                {"text": "", "tool_calls": [{"id": "bad", "name": "approve", "arguments": {}}]},
            )()

    router.provider = ForbiddenProvider()
    with pytest.raises(DomainError):
        asyncio.run(router.handle_user_message("批准", request_id="forbidden"))
    assert len(store.list_messages()) == 2


def test_router_gateway_structured_output_over_http_transport(tmp_path):
    import httpx

    from forge_core.providers import ProviderGateway
    from forge_core.providers.config import ProviderConfig

    decision = {
        "intent": "chat",
        "reply": "真实网关协议",
        "title": None,
        "goal": None,
        "quest_id": None,
        "candidates": [],
    }

    sizes = []

    def handle(request):
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["tools"][0]["function"]["name"] == "list_quests"
        assert any(
            message["role"] == "system" and "Schema:" in message["content"]
            for message in payload["messages"]
        )
        sizes.append(len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 64)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(decision)}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    config = ProviderConfig(
        id="test",
        kind="openai_compatible",
        base_url="https://example.test/v1",
        credential_ref="FORGE_PROVIDER_KEY_TEST",
        supports_json_schema=True,
        supports_tools=True,
    )
    store = Store(tmp_path / "gateway.sqlite3")
    from forge_core.model_call_budget import ModelCallBudgetLedger

    ledger = ModelCallBudgetLedger(
        store.path, token_limit=100000, concurrency_limit=1, remote_max_seconds=60
    )
    gateway = ProviderGateway(
        config,
        credential_resolver=lambda _: "placeholder",
        transport=httpx.MockTransport(handle),
        budget_ledger=ledger,
    )
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=gateway,
        model_id="model",
        provider_id="test",
    )
    result = asyncio.run(router.handle_user_message("你好", request_id="gateway"))
    with store.connection() as connection:
        row = connection.execute(
            "SELECT purpose,workspace_id,input_reserved FROM model_budget_attempts"
        ).fetchone()
        assert (row["purpose"], row["workspace_id"]) == ("router", store.workspace.id)
        assert row["input_reserved"] == sizes[0]
        assert row["input_reserved"] + router.output_reserve <= router.context_limit
    assert result["assistant_message"]["content"] == "真实网关协议"


def test_live_model_requires_declared_capabilities(tmp_path):
    from forge_core.domain import ModelProfile, ProtocolSupport

    store, provider, router = make_router(
        tmp_path,
        {
            "intent": "chat",
            "reply": "收到",
            "title": None,
            "goal": None,
            "quest_id": None,
            "candidates": [],
        },
    )
    provider.execution_mode = "live"
    router.provider_id = "test"
    with pytest.raises(DomainError, match="capability"):
        asyncio.run(router.handle_user_message("你好", request_id="unknown-capability"))
    assert store.list_messages() == []
    profile = ModelProfile(
        id="mock",
        provider="test",
        display_name="test",
        tool_calling_support=ProtocolSupport(supported=True, source="user_override"),
        structured_output_support=ProtocolSupport(supported=True, source="user_override"),
    )
    store.save_model(profile)
    result = asyncio.run(router.handle_user_message("你好", request_id="known-capability"))
    assert result["assistant_message"]["metadata"]["execution_mode"] == "live"


def test_oversized_tool_result_fails_before_second_call_without_writes(tmp_path):
    store, _, router = make_router(tmp_path, {})
    store.append_event(ForgeEvent(event_type="large", payload={"data": "x" * 30000}))

    class Provider:
        execution_mode = "mock"
        calls = 0

        async def generate(self, request):
            self.calls += 1
            return type(
                "Response",
                (),
                {
                    "text": "",
                    "tool_calls": [{"id": "call", "name": "list_events", "arguments": {}}],
                },
            )()

    provider = Provider()
    router.provider = provider
    router.context_limit = 14000
    router.output_reserve = 512
    with pytest.raises(DomainError, match="budget"):
        asyncio.run(router.handle_user_message("查询", request_id="large-tool"))
    assert provider.calls == 1
    assert store.list_messages() == []
    assert store.ensure_router()["event_cursor"] == 0


def test_model_lookup_uses_provider_and_correct_context_window(tmp_path):
    from forge_core.domain import ModelProfile, ProtocolSupport

    store, provider, router = make_router(
        tmp_path,
        {
            "intent": "chat",
            "reply": "收到",
            "title": None,
            "goal": None,
            "quest_id": None,
            "candidates": [],
        },
    )
    provider.execution_mode = "live"
    router.provider_id = "selected"
    router.model_id = "same-model"
    support = ProtocolSupport(supported=True, source="user_override")
    store.save_model(
        ModelProfile(
            id="a",
            provider="other",
            model_id="same-model",
            display_name="other",
            context_window=100,
            tool_calling_support=support,
            structured_output_support=support,
        )
    )
    store.save_model(
        ModelProfile(
            id="b",
            provider="selected",
            model_id="same-model",
            display_name="selected",
            context_window=16000,
            tool_calling_support=support,
            structured_output_support=support,
        )
    )
    result = asyncio.run(router.handle_user_message("你好", request_id="provider-match"))
    assert result["assistant_message"]["metadata"]["context_budget"]["effective_limit"] == 16000


def test_over_hundred_quests_explicit_old_id_remains_resolvable(tmp_path):
    store, provider, router = make_router(tmp_path, {})
    first = Quest(title="old" + "x" * 1000, goal="x" * 1000)
    store.save_quest(first)
    for index in range(105):
        store.save_quest(Quest(title=f"{index}" + "x" * 1000, goal="x" * 1000))
    provider.decision = {
        "intent": "continue_quest",
        "reply": "继续",
        "title": None,
        "goal": None,
        "quest_id": first.id,
        "candidates": [first.id],
    }
    result = asyncio.run(router.handle_user_message(f"继续 {first.id}", request_id="old-quest"))
    assert result["intent"] == "continue_quest"
    provider.decision = {
        "intent": "chat",
        "reply": "你好",
        "title": None,
        "goal": None,
        "quest_id": None,
        "candidates": [],
    }
    assert (
        asyncio.run(router.handle_user_message("你好", request_id="long-quests-chat"))["intent"]
        == "chat"
    )
