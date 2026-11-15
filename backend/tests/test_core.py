import asyncio

import pytest
from conftest import RouterMockProvider

from forge_core.domain import DomainError, ModelProfile, QuestStatus, validate_quest_transition
from forge_core.registry import DEFAULT_ROLES, ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.store import Store


def make_store(tmp_path):
    return Store(str(tmp_path / "forge-test.sqlite3"))


def test_router_identity_survives_store_reopen(tmp_path):
    path = str(tmp_path / "persistent.sqlite3")
    first = Store(path).ensure_router()["router_session_id"]
    second = Store(path).ensure_router()["router_session_id"]
    assert first == second


def test_single_conversation_stores_messages_and_quest(tmp_path):
    store = make_store(tmp_path)
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=RouterMockProvider("create_quest"),
        model_id="mock",
    )
    result = asyncio.run(router.handle_user_message("帮我研究一个新的多模态 Agent 算法"))
    assert result["intent"] == "create_quest"
    assert len(store.list_messages()) == 2
    assert len(store.list_quests()) == 1
    assert store.list_quests()[0]["status"] == "proposed"


def test_chat_does_not_create_quest(tmp_path):
    store = make_store(tmp_path)
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=RouterMockProvider("chat"),
        model_id="mock",
    )
    result = asyncio.run(router.handle_user_message("你好，今天怎么样？"))
    assert result["intent"] == "chat"
    assert store.list_quests() == []


def test_model_registry_team_composition(tmp_path):
    store = make_store(tmp_path)
    registry = ModelRegistry(store)
    registry.upsert(
        ModelProfile(
            id="model-a",
            provider="test",
            display_name="Research Model",
            capabilities={"research": 90, "writing": 80},
        )
    )
    proposal = TeamComposer(registry).compose([DEFAULT_ROLES[0]])
    assert proposal[0]["model_candidates"][0]["model_id"] == "model-a"


def test_quest_transition_validation():
    validate_quest_transition(QuestStatus.PROPOSED, QuestStatus.AWAITING_APPROVAL)
    with pytest.raises(DomainError):
        validate_quest_transition(QuestStatus.COMPLETED, QuestStatus.RUNNING)


def test_quest_lifecycle_transition_is_persisted(tmp_path):
    from forge_core.domain import DomainError, Quest, QuestStatus
    from forge_core.quest_service import QuestService

    store = Store(str(tmp_path / "lifecycle.sqlite3"))
    quest = Quest(title="Test", goal="Test goal")
    store.save_quest(quest)
    service = QuestService(store)
    changed = service.transition(quest.id, QuestStatus.AWAITING_APPROVAL)
    assert changed["status"] == "awaiting_approval"
    with pytest.raises(DomainError):
        service.transition(quest.id, QuestStatus.COMPLETED)


def test_worker_sessions_are_hidden_by_default(tmp_path):
    from forge_core.domain import Quest, WorkerSession

    store = Store(str(tmp_path / "worker.sqlite3"))
    quest = Quest(title="Hidden worker", goal="Background work")
    store.save_quest(quest)
    worker = WorkerSession(quest_id=quest.id, role="research")
    store.save_worker(worker)
    stored = store.list_workers(quest.id)
    assert len(stored) == 1
    assert stored[0]["hidden"] == 1
