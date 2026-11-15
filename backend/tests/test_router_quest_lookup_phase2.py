"""Quest ambiguity depends on bounded identity recall, separate from source facts."""

import asyncio

import pytest
from test_router_intelligence_phase2 import ScriptedMock, decision

from forge_core.context_service import ContextService
from forge_core.domain import ConversationMessage, Quest
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.store import Store


def service(store, quest):
    return RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=ScriptedMock(decision("continue_quest", quest_id=quest.id, candidates=[quest.id])),
        model_id="explicit-mock",
        context_limit=65536,
    )


def snapshot(router, query):
    router._sync_memory()
    with router.store.connection() as connection:
        return router._snapshot(connection, query)


def test_many_constraint_sources_do_not_hide_one_matching_quest(tmp_path):
    store = Store(tmp_path / "constraint-hits.sqlite3")
    target = Quest(title="Nebula decoder", goal="Inspect the original code")
    store.save_quest(target)
    for index in range(60):
        store.add_message(
            ConversationMessage(
                role="user", content=f"Nebula constraint {index}: Do not delete measurements."
            )
        )
    router = service(store, target)
    observed = snapshot(router, "Continue Nebula constraints")
    assert observed["memory"]["omitted"]
    assert not observed["quest_lookup"]["omitted"]
    assert observed["quest_lookup"]["cursor"] is None
    assert observed["recalled_ids"] == [target.id]
    result = asyncio.run(
        router.handle_user_message("Continue Nebula constraints", request_id="one-identity")
    )
    assert result["intent"] == "continue_quest" and result["quest_id"] == target.id


def test_two_matching_quest_identities_still_require_clarification(tmp_path):
    store = Store(tmp_path / "two-identities.sqlite3")
    first = Quest(title="Nebula first", goal="Inspect decoder")
    second = Quest(title="Nebula second", goal="Inspect encoder")
    store.save_quest(first)
    store.save_quest(second)
    router = service(store, first)
    observed = snapshot(router, "Continue Nebula")
    assert set(observed["recalled_ids"]) == {first.id, second.id}
    result = asyncio.run(router.handle_user_message("Continue Nebula", request_id="two-identities"))
    assert result["intent"] == "ambiguous" and result["quest_id"] is None


def test_bounded_quest_results_preserve_omission_and_ambiguity(tmp_path):
    store = Store(tmp_path / "many-identities.sqlite3")
    targets = [
        Quest(title=f"Nebula candidate {index}", goal="Inspect decoder") for index in range(101)
    ]
    for quest in targets:
        store.save_quest(quest)
    router = service(store, targets[0])
    observed = snapshot(router, "Continue Nebula")
    assert len(observed["quest_lookup"]["items"]) == 100
    assert observed["quest_lookup"]["omitted"] and observed["quest_lookup"]["cursor"]
    result = asyncio.run(
        router.handle_user_message("Continue Nebula", request_id="bounded-identities")
    )
    assert result["intent"] == "ambiguous"


@pytest.mark.parametrize("explicit", [False, True])
def test_prefilter_candidate_cap_stays_unresolved_unless_user_names_exact_id(tmp_path, explicit):
    store = Store(tmp_path / "hidden-identity.sqlite3")
    target = Quest(title="Nebula target", goal="Inspect decoder")
    store.save_quest(target)
    for index in range(520):
        store.add_message(ConversationMessage(role="user", content=f"Nebula memo {index}"))
    router = service(store, target)
    query = f"Continue Nebula {target.id}" if explicit else "Continue Nebula"
    observed = snapshot(router, query)
    # Candidate collection is capped before source-type filtering. The genuine
    # Quest sorts behind the message sources and must not be silently ruled out.
    assert observed["quest_lookup"]["items"] == []
    assert observed["quest_lookup"]["omitted"] and observed["quest_lookup"]["cursor"]
    ContextService(store).compact_router(keep_recent=0)
    result = asyncio.run(router.handle_user_message(query, request_id="hidden-identity"))
    assert result["intent"] == ("continue_quest" if explicit else "ambiguous")
