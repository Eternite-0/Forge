"""Model-selected recall is concrete, source-bound, and remains a hypothesis."""

import asyncio
import copy
import json

import pytest
from test_router_intelligence_phase2 import ScriptedMock, decision, router

from forge_core.capacity_preflight import discover_input_inventory
from forge_core.constraints import ConstraintService
from forge_core.domain import DomainError
from forge_core.router import _decision_schema
from forge_core.router_domain import RouterDecision
from forge_core.router_recall import selected_requirements
from forge_core.settings import Workspace
from forge_core.store import Store

PATH = "inputs/aster-release.json"
RULE = "For Aster, shipment requires zero failed checks and at least fifteen total checks."


def prepared(tmp_path):
    store = Store(tmp_path / "recall.sqlite3", workspace=Workspace.from_path(tmp_path))
    service = router(store, ScriptedMock(decision()))
    asyncio.run(service.handle_user_message(f"Read {PATH}. {RULE}", request_id="origin"))
    origin = next(item for item in store.list_messages() if item["role"] == "user")
    identities = [
        service.memory_service.remember(
            text,
            kind="constraint",
            layer="L2",
            source_refs=["message:" + origin["id"]],
        )
        for text in [f"Aster source: {PATH}", RULE]
    ]
    return store, service, origin, identities


def test_selected_split_rule_and_path_enter_real_goal_with_original_provenance(tmp_path):
    store, service, origin, identities = prepared(tmp_path)

    def propose(request):
        context = json.loads(request.messages[1]["content"])
        assert set(identities) <= {item["id"] for item in context["memory"]}
        return decision(
            "create_quest",
            goal="Apply the prior Aster dispatch rule to the current ledger.",
            recalled_requirement_ids=identities,
        )

    service.provider = ScriptedMock(propose)
    result = asyncio.run(
        service.handle_user_message("New Aster dispatch assessment", request_id="target")
    )
    quest = store.get_quest(result["quest_id"])
    records = json.loads(quest["metadata"])["recalled_requirements"]
    assert PATH in quest["goal"] and RULE in quest["goal"]
    assert [item["memory_id"] for item in records] == identities
    assert all(item["status"] == "hypothesis" for item in records)
    assert all(item["source_refs"] == ["message:" + origin["id"]] for item in records)
    constraints = ConstraintService(store).context(quest_id=quest["id"])["items"]
    recalled = [
        item for item in constraints if item["statement"] in [f"Aster source: {PATH}", RULE]
    ]
    assert len(recalled) == 2
    assert all(item["confirmation_status"] == "pending_model" for item in recalled)
    assert all(item["source_refs"] == ["message:" + origin["id"]] for item in recalled)
    assert discover_input_inventory(store, result["user_message"]["content"]) == []
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0


def test_unselected_recall_does_not_become_a_new_requirement(tmp_path):
    store, service, _, _ = prepared(tmp_path)
    service.provider = ScriptedMock(decision("create_quest", goal="Only the new task"))
    result = asyncio.run(service.handle_user_message("New Aster task", request_id="unselected"))
    quest = store.get_quest(result["quest_id"])
    assert quest["goal"] == "Only the new task"
    assert "recalled_requirements" not in json.loads(quest["metadata"])


def test_invented_recall_is_reflected_once_without_committing_an_invalid_quest(tmp_path):
    store, service, _, _ = prepared(tmp_path)
    invalid = decision("create_quest", recalled_requirement_ids=["not-presented"])
    service.provider = ScriptedMock(invalid)
    before = len(store.list_messages())
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(service.handle_user_message("New Aster task", request_id="invented"))
    assert len(service.provider.requests) == 2
    assert len(store.list_messages()) == before and store.list_quests() == []


@pytest.mark.parametrize("mutation", ["duplicate", "conflict", "omitted", "revoked", "changed"])
def test_selected_source_must_still_be_complete_and_eligible(tmp_path, mutation):
    store, service, origin, identities = prepared(tmp_path)
    with store.connection() as connection:
        context = {
            "memory": service.memory_service.retrieve_context(
                "",
                source_refs=["memory:" + identities[0]],
                exact_source_refs=True,
                connection=connection,
            )["items"]
        }
    choice = RouterDecision.model_validate(
        decision("create_quest", recalled_requirement_ids=[identities[0]])
    )
    if mutation == "duplicate":
        choice.recalled_requirement_ids *= 2
    elif mutation in {"conflict", "omitted"}:
        context = copy.deepcopy(context)
        context["memory"][0]["conflicted" if mutation == "conflict" else "omitted"] = True
    elif mutation == "revoked":
        service.memory_service.revoke_source("message:" + origin["id"])
    else:
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE memories SET statement='changed' WHERE id=?", (identities[0],)
            )
    with store.connection() as connection, pytest.raises(ValueError):
        selected_requirements(service.memory_service, connection, choice, context)


def test_compact_router_schema_preserves_its_original_six_fields():
    assert set(_decision_schema(compact=True)["properties"]) == {
        "intent",
        "reply",
        "title",
        "goal",
        "quest_id",
        "candidates",
    }
