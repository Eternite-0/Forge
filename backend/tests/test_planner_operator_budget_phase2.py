"""Trusted Planner proposal ceilings with scripted responses and real persistence."""

import asyncio
import json

import pytest
from test_router_intelligence_phase2 import ScriptedMock, wire_graph

from forge_core.domain import DomainError, Quest
from forge_core.graph_domain import GraphWire
from forge_core.quest_graph import QuestGraphService
from forge_core.store import Store


def prepared(tmp_path):
    store = Store(tmp_path / "planner-cap.sqlite3")
    quest = Quest(title="Bounded plan", goal="Inspect the approved input within its allowance")
    store.save_quest(quest)
    return store, quest, QuestGraphService(store)


def proposal(tokens):
    graph = wire_graph()
    graph["max_total_tokens"] = tokens
    graph["nodes"][0]["budget_tokens"] = tokens
    return graph


def generate(service, quest, provider, **kwargs):
    return asyncio.run(
        service.generate(
            quest.id,
            expected_version=service.store.get_quest(quest.id)["version"],
            provider=provider,
            model_id="explicit-mock",
            provider_id="explicit-mock-provider",
            capabilities={"json_schema": True},
            capability_sources={"json_schema": "mock"},
            **kwargs,
        )
    )


def test_over_cap_proposal_is_repaired_once_before_any_plan_is_persisted(tmp_path):
    store, quest, service = prepared(tmp_path)
    provider = ScriptedMock(proposal(5001), proposal(5000))
    plan = generate(service, quest, provider, operator_max_total_tokens=5000)
    assert len(provider.requests) == 2
    for request in provider.requests:
        limits = json.loads(request.messages[1]["content"])["limits"]
        assert limits["max_total_tokens"] == limits["node_budget_tokens_max"] == 5000
    feedback = json.loads(provider.requests[1].messages[-1]["content"])
    assert feedback["rejected_proposal_error"] == ["Planner token budget exceeds operator limit"]
    assert plan["max_total_tokens"] == plan["nodes"][0]["budget_tokens"] == 5000
    assert len(service.history(quest.id)) == 1
    assert store.get_quest(quest.id)["version"] == 1
    assert store.list_workers(quest.id) == []


def test_repeated_over_cap_proposals_stop_after_two_calls_without_persistence(tmp_path):
    store, quest, service = prepared(tmp_path)
    before = store.get_quest(quest.id)
    provider = ScriptedMock(proposal(5001))
    with pytest.raises(DomainError, match="Invalid graph proposal"):
        generate(service, quest, provider, operator_max_total_tokens=5000)
    assert len(provider.requests) == 2
    assert service.history(quest.id) == []
    assert store.get_quest(quest.id) == before
    assert store.list_events() == [] and store.list_workers(quest.id) == []
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM task_nodes").fetchone()[0] == 0


@pytest.mark.parametrize(
    "operator_cap,effective", [(None, 1000000), (1, 1), (630000, 630000), (2000000, 1000000)]
)
def test_explicit_cap_is_visible_and_cannot_expand_default_schema_ceiling(
    tmp_path, operator_cap, effective
):
    _store, quest, service = prepared(tmp_path)
    provider = ScriptedMock(proposal(effective))
    plan = generate(service, quest, provider, operator_max_total_tokens=operator_cap)
    limits = json.loads(provider.requests[0].messages[1]["content"])["limits"]
    assert limits["max_total_tokens"] == limits["node_budget_tokens_max"] == effective
    assert plan["max_total_tokens"] == effective and len(provider.requests) == 1


def test_omitted_operator_cap_preserves_default_planning_contract(tmp_path):
    _store, quest, service = prepared(tmp_path)
    provider = ScriptedMock(proposal(1000000))
    plan = generate(service, quest, provider)
    limits = json.loads(provider.requests[0].messages[1]["content"])["limits"]
    assert limits["max_total_tokens"] == limits["node_budget_tokens_max"] == 1000000
    assert plan["max_total_tokens"] == 1000000 and len(provider.requests) == 1


@pytest.mark.parametrize("cap", [True, False, 0, -1, 1.0, "5000", [], {}])
def test_invalid_trusted_cap_rejects_before_provider_call_or_persistence(tmp_path, cap):
    store, quest, service = prepared(tmp_path)
    before = store.get_quest(quest.id)
    provider = ScriptedMock(proposal(1000))
    with pytest.raises(ValueError, match="Operator max total tokens must be a positive integer"):
        generate(service, quest, provider, operator_max_total_tokens=cap)
    assert provider.requests == [] and service.history(quest.id) == []
    assert store.get_quest(quest.id) == before and store.list_events() == []


def test_generated_cap_does_not_rewrite_manual_or_previously_approved_plan(tmp_path):
    store, quest, service = prepared(tmp_path)
    manual = service.propose(
        quest.id,
        GraphWire.model_validate(proposal(900000)).proposal().model_dump(),
        expected_version=0,
    )
    service.approve(
        quest.id, manual["digest"], manual["revision"], expected_version=1, actor="local_user"
    )
    before = service.get(quest.id)
    history = service.history(quest.id)
    other = Quest(title="New plan", goal="A separate bounded plan")
    store.save_quest(other)
    generated = generate(
        service, other, ScriptedMock(proposal(1000)), operator_max_total_tokens=1000
    )
    assert generated["max_total_tokens"] == 1000
    assert service.get(quest.id) == before and service.history(quest.id) == history
    assert before["max_total_tokens"] == 900000 and before["status"] == "approved"
