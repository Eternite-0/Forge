"""Recorded Router decisions replayed offline through the actual creation path."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from test_router_intelligence_phase2 import ScriptedMock, router

from forge_core.domain import DomainError, ModelProfile, Quest
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store


class Captured(Exception):
    pass


class NativeCapture(ProviderGateway):
    """Production framing and no network/model request."""

    def __init__(self, *, repair=False):
        super().__init__(
            ProviderConfig(
                id="p",
                kind="openai_compatible",
                base_url="https://example.invalid/v1",
                credential_ref="FORGE_PROVIDER_KEY_TEST",
                supports_json_schema=True,
            )
        )
        self.requests = []
        self.native_sizes = []
        self.repair = repair

    async def generate(self, request):
        native = self._payload(request, False)
        self.requests.append(request)
        self.native_sizes.append(len(json.dumps(native, ensure_ascii=False).encode()) + 64)
        if self.repair and len(self.requests) == 1:
            return type("InvalidResponse", (), {"text": "{}", "usage": {}})()
        raise Captured


def route_original_scenario(tmp_path, level):
    root = next(
        parent / "backend"
        for parent in Path(__file__).resolve().parents
        if (parent / "backend/scripts/phase2/acceptance_scenarios.py").is_file()
    )
    module_path = root / "scripts/phase2/acceptance_scenarios.py"
    spec = importlib.util.spec_from_file_location("planner_v18_scenario", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prompt = module.seed_scenario(level, tmp_path)["prompt"]
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/phase2_router_decisions_v17.json").read_text(
            encoding="utf-8"
        )
    )
    observed = fixture["cases"][level - 1]["decision"]
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    store.save_model(ModelProfile.model_validate(fixture["model_profile"]))
    result = asyncio.run(
        router(store, ScriptedMock(observed)).handle_user_message(
            prompt, request_id=f"scenario-{level}"
        )
    )
    return store, store.get_quest(result["quest_id"]), prompt, observed


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize("repair", [False, True])
def test_actual_router_quest_shape_retains_full_sources_and_fits_native_planner(
    tmp_path, level, repair
):
    store, quest, original, observed = route_original_scenario(tmp_path, level)
    provider = NativeCapture(repair=repair)
    with pytest.raises(Captured):
        asyncio.run(
            QuestGraphService(store).generate(
                quest["id"],
                expected_version=quest["version"],
                provider=provider,
                provider_id="p",
                model_id="native",
                context_limit=65536,
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                require_capacity_contract=True,
            )
        )
    assert len(provider.requests) == (2 if repair else 1)
    data = json.loads(provider.requests[0].messages[1]["content"])
    assert data["original_request"]["content"] == original
    assert data["goal"] == observed["goal"]
    assert data["user_constraints"]["items"]
    assert data["verified_input_inventory"]
    assert (
        data["execution_constraints"]["limits"]["max_total_tokens"]
        == {1: 180000, 2: 360000, 3: 630000}[level]
    )
    for request, native in zip(provider.requests, provider.native_sizes, strict=True):
        assert request.context_limit == 65536
        assert native + request.max_output_tokens <= request.context_limit
        assert request.messages[1]["content"] == provider.requests[0].messages[1]["content"]
    assert QuestGraphService(store).history(quest["id"]) == []


def test_native_planner_context_rejects_full_policy_and_schema_before_provider(
    tmp_path,
):
    store, quest, _original, _observed = route_original_scenario(tmp_path, 3)
    provider = NativeCapture()
    with pytest.raises(DomainError, match="Planner context budget exceeded"):
        asyncio.run(
            QuestGraphService(store).generate(
                quest["id"],
                expected_version=quest["version"],
                provider=provider,
                provider_id="p",
                model_id="native",
                context_limit=28000,
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                require_capacity_contract=True,
            )
        )
    assert provider.requests == []


def test_planner_content_bound_remains_finite_without_truncating_goal(tmp_path):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Oversize", goal="完整约束" * 10000)
    store.save_quest(quest)
    provider = NativeCapture()
    with pytest.raises(DomainError, match="Quest is too large to plan"):
        asyncio.run(
            QuestGraphService(store).generate(
                quest.id,
                expected_version=0,
                provider=provider,
                provider_id="p",
                model_id="native",
                context_limit=65536,
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                require_capacity_contract=True,
            )
        )
    assert provider.requests == []
    assert store.get_quest(quest.id)["goal"] == quest.goal
