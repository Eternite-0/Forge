"""Router consumes actual bounded semantic selections; lexical retrieval remains."""

import asyncio
import json

from test_router_intelligence_phase2 import ScriptedMock, decision

from forge_core.domain import Quest
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.runtime import ModelResponse
from forge_core.settings import Workspace
from forge_core.store import Store


class RouterSelector:
    execution_mode = "mock"

    def __init__(self, selected, final=None):
        self.selected, self.final = selected, final or decision()
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if request.budget_context.purpose == "memory_semantic_retrieval":
            candidates = json.loads(request.messages[1]["content"])["candidates"]
            result = {"candidate_ids": [self.selected] if self.selected in candidates else []}
        else:
            result = self.final
        return ModelResponse(text=json.dumps(result), tool_calls=[], usage={}, finish_reason="mock")


def prepared(tmp_path, provider):
    store = Store(tmp_path / "router.sqlite3", workspace=Workspace.from_path(tmp_path))
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=provider,
        model_id="explicit-mock",
        context_limit=65536,
        semantic_recall=True,
    )
    return store, router


def test_router_includes_zero_overlap_semantic_memory_with_original_trust(tmp_path):
    provider = RouterSelector("placeholder")
    _, router = prepared(tmp_path, provider)
    identity = router.memory_service.remember(
        "Never disclose passwords or credentials in logs.",
        kind="decision",
        layer="L2",
        source_refs=["message:earlier"],
        status="hypothesis",
    )
    provider.selected = "memory:" + identity
    query = "How should we avoid spilling confidential material?"
    assert router.memory_service.retrieve_context(query)["items"] == []
    result = asyncio.run(router.handle_user_message(query, request_id="semantic"))
    assert result["intent"] == "chat"
    assert len(provider.requests) == 2
    actual = json.loads(provider.requests[-1].messages[1]["content"])
    assert actual["semantic_recall"]["status"] == "selected"
    item = next(item for item in actual["memory"] if item["id"] == identity)
    assert item["status"] == "hypothesis" and item["source_refs"] == ["message:earlier"]


def test_semantic_task_identity_can_reopen_an_older_quest(tmp_path):
    provider = RouterSelector("placeholder")
    store, router = prepared(tmp_path, provider)
    target = Quest(title="Nebula", goal="Construct a safe priority queue")
    store.save_quest(target)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status='completed' WHERE id=?", (target.id,))
    for index in range(3):
        store.save_quest(Quest(title=f"Recent unrelated {index}", goal="Other work"))
    router._sync_memory()
    provider.selected = "quest:" + target.id
    provider.final = decision("continue_quest", quest_id=target.id, candidates=[target.id])
    query = "Resume the starvation resistant scheduling exploration"
    with store.connection() as connection:
        assert target.id not in {row["id"] for row in router._snapshot(connection, query)["quests"]}
    result = asyncio.run(router.handle_user_message(query, request_id="old-semantic"))
    seen = json.loads(provider.requests[-1].messages[1]["content"])
    assert result["intent"] == "continue_quest" and result["quest_id"] == target.id, json.dumps(
        {
            "target": target.id,
            "semantic": seen.get("semantic_recall"),
            "quests": [item["id"] for item in seen.get("quests", [])],
            "memory": [
                {"id": item["id"], "related": item["related_quest_ids"]}
                for item in seen.get("memory", [])
            ],
        }
    )
    assert len(provider.requests) == 2


def test_legacy_router_does_not_add_semantic_provider_call(tmp_path):
    provider = ScriptedMock(decision())
    store = Store(tmp_path / "legacy.sqlite3", workspace=Workspace.from_path(tmp_path))
    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=provider, model_id="mock"
    )
    asyncio.run(router.handle_user_message("hello", request_id="legacy"))
    assert len(provider.requests) == 1


def test_multimodal_task_records_explicit_unavailable_capability_without_planning_event(tmp_path):
    provider = ScriptedMock(
        decision("create_quest", task_kind="multimodal", reply="I will inspect your video")
    )
    store = Store(tmp_path / "multimodal.sqlite3", workspace=Workspace.from_path(tmp_path))
    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=provider, model_id="mock"
    )
    result = asyncio.run(
        router.handle_user_message("Analyze the attached video", request_id="vision")
    )
    quest = store.get_quest(result["quest_id"])
    metadata = json.loads(quest["metadata"])
    assert quest["status"] == "needs_input"
    assert metadata["task_kind"] == "multimodal"
    assert metadata["capability_status"] == "unavailable"
    assert metadata["capability_reason"] == "multimodal_input_protocol_unavailable"
    with store.connection() as connection:
        types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM events WHERE quest_id=?", (quest["id"],)
            )
        ]
    assert "quest.capability_unavailable" in types and "quest.proposed" not in types
    assert "尚未实现" in result["assistant_message"]["content"]
    assert store.list_workers(quest["id"]) == []
