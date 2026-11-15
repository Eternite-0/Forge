"""Separate finite cumulative Worker budgets from each model request's window."""

import asyncio

import pytest

from forge_core.domain import DomainError, ModelProfile, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelResponse
from forge_core.store import Store
from forge_core.team_composition import (
    CompositionLimits,
    RoleSpec,
    TeamComposerEngine,
    TeamCompositionService,
)
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


@pytest.mark.parametrize("strategy", ["single_model", "greedy", "coverage", "complementarity"])
def test_cumulative_allowance_may_exceed_one_context_window(strategy):
    role = RoleSpec(id="coding", input_tokens=95000, output_tokens=1000)
    profile = ModelProfile(
        id="mock",
        provider="mock",
        model_id="mock",
        display_name="Offline model",
        context_window=64000,
        max_output_tokens=4000,
    )
    limits = CompositionLimits(
        max_members=1, max_depth=0, max_concurrency=1, max_total_tokens=96000
    )
    proposal = TeamComposerEngine().compose([role], [profile], limits, strategy=strategy)
    assert proposal["estimated_tokens"] == 96000
    assert proposal["members"][0]["model_context_window"] == 64000
    assert proposal["members"][0]["output_tokens"] == 1000
    with pytest.raises(ValueError, match="Token budget exceeded"):
        TeamComposerEngine().compose(
            [role],
            [profile],
            limits.model_copy(update={"max_total_tokens": 95999}),
            strategy=strategy,
        )


@pytest.mark.parametrize(
    "window,max_output,output",
    [(None, 4000, 1000), (64000, None, 1000), (64000, 999, 1000), (1000, 1000, 1000)],
)
def test_context_and_single_response_output_bounds_remain_required(window, max_output, output):
    role = RoleSpec(id="coding", input_tokens=95000, output_tokens=output)
    profile = ModelProfile(
        id="mock",
        provider="mock",
        model_id="mock",
        display_name="Offline model",
        context_window=window,
        max_output_tokens=max_output,
    )
    limits = CompositionLimits(
        max_members=1, max_depth=0, max_concurrency=1, max_total_tokens=96000
    )
    with pytest.raises(ValueError, match="No eligible model"):
        TeamComposerEngine().compose([role], [profile], limits, strategy="greedy")


def approved_task(tmp_path):
    store = Store(tmp_path / "budget.sqlite3")
    quest = Quest(title="Finite cumulative allowance", goal="Inspect a bounded contract")
    store.save_quest(quest)
    graphs = QuestGraphService(store)
    plan = graphs.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": "Return a short summary",
                    "success_criteria": ["manual_review"],
                    "budget_tokens": 12000,
                }
            ],
            "edges": [],
            "max_total_tokens": 20000,
        },
        expected_version=0,
    )
    graphs.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    store.save_model(
        ModelProfile(
            id="mock",
            provider="mock",
            model_id="mock",
            display_name="Offline model",
            context_window=4096,
            max_output_tokens=512,
        )
    )
    return store, quest, plan


def test_larger_cumulative_allowance_still_limits_each_native_request(tmp_path):
    store, quest, plan = approved_task(tmp_path)
    teams = TeamCompositionService(store)
    team = teams.propose(
        quest.id,
        [{"id": "coding", "task_node_key": "n", "input_tokens": 11500, "output_tokens": 500}],
        {"max_members": 1, "max_depth": 0, "max_concurrency": 1, "max_total_tokens": 12000},
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(
        quest.id,
        plan["plan_revision"],
        team["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )

    class MockProvider:
        execution_mode = "mock"

        def __init__(self):
            self.requests = []

        async def generate(self, request):
            self.requests.append(request)
            return ModelResponse(
                text='{"summary":"unverified bounded delivery"}',
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    provider = MockProvider()
    manager = WorkerManager(
        store, ProviderPool({"mock": provider}), ToolEngine(store), team_authority=teams
    )
    worker = manager.start(
        quest.id, plan["id"], "n", team_digest=team["proposal_digest"], member_index=0
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["token_limit"] == 12000 and result["model_context_window"] == 4096
    assert result["state"] == "review_pending" and result["token_spent"] == 15
    assert len(provider.requests) == 1
    assert provider.requests[0].context_limit == 4096
    assert provider.requests[0].max_output_tokens == 500


def test_cumulative_allowance_cannot_exceed_approved_node_budget(tmp_path):
    store, quest, _plan = approved_task(tmp_path)
    with pytest.raises(DomainError, match="Team token budget exceeds task budget"):
        TeamCompositionService(store).propose(
            quest.id,
            [{"id": "coding", "task_node_key": "n", "input_tokens": 12500, "output_tokens": 500}],
            {"max_members": 1, "max_depth": 0, "max_concurrency": 1, "max_total_tokens": 13000},
            strategy="greedy",
            expected_version=2,
        )
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM team_proposals").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0
