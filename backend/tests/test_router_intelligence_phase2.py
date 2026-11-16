"""Mock decision/planning regressions; no external models or execution claims."""

import asyncio
import copy
import hashlib
import json
import sqlite3

import pytest

from forge_core.constraints import ConstraintService
from forge_core.context_service import ContextService
from forge_core.domain import DomainError, Quest
from forge_core.quest_graph import QuestGraphService
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.store import Store


class ScriptedMock:
    execution_mode = "mock"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        value = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if callable(value):
            value = value(request)
        if not isinstance(value, dict):
            return value
        return type(
            "MockResponse", (), {"text": json.dumps(value), "usage": {}, "tool_calls": []}
        )()


def decision(intent="chat", **updates):
    result = {
        "intent": intent,
        "reply": "A bounded response.",
        "title": "Decoder analysis" if intent == "create_quest" else None,
        "goal": "Find three decoder issues" if intent == "create_quest" else None,
        "quest_id": None,
        "candidates": [],
    }
    result.update(updates)
    return result


def router(store, provider):
    return RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=provider, model_id="explicit-mock"
    )


def wire_graph(count=1):
    return {
        "nodes": [
            {
                "key": f"n{index}",
                "objective": f"Inspect bounded input {index}",
                "success_criteria": ["delivery_review"],
                "budget_tokens": 1000,
                "required_capabilities": [{"name": "coding", "minimum": 40.0}],
                "expected_artifact": None,
                "max_attempts": 1,
                "allowed_tools": ["directory_list", "file_read"],
            }
            for index in range(count)
        ],
        "edges": [],
        "max_total_tokens": 10000,
        "max_cost_microunits": None,
        "cost_currency": None,
        "max_duration_seconds": 120,
    }


def generate(store, quest, provider):
    return asyncio.run(
        QuestGraphService(store).generate(
            quest.id,
            expected_version=store.get_quest(quest.id)["version"],
            provider=provider,
            model_id="explicit-mock",
            provider_id="explicit-mock-provider",
            capabilities={"json_schema": True},
            capability_sources={"json_schema": "mock"},
        )
    )


def test_simple_explanation_stays_in_single_chat_without_quest_or_workers(tmp_path):
    store = Store(tmp_path / "chat.sqlite3")
    identity = store.ensure_router()["router_session_id"]
    provider = ScriptedMock(decision(task_kind="chat"))
    response = asyncio.run(
        router(store, provider).handle_user_message(
            "Explain FIFO in one sentence", request_id="simple"
        )
    )
    assert response["intent"] == "chat" and response["quest_id"] is None
    assert len(provider.requests) == 1
    assert store.list_quests() == []
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM workers").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1
    assert store.ensure_router()["router_session_id"] == identity
    assert {message["conversation_id"] for message in store.list_messages()} == {"main"}


def test_router_repairs_invalid_decision_once_and_commits_only_valid_turn(tmp_path):
    store = Store(tmp_path / "repair.sqlite3")
    provider = ScriptedMock({"intent": "create_quest", "invented_execution": True}, decision())
    response = asyncio.run(
        router(store, provider).handle_user_message("hello", request_id="repair")
    )
    assert response["intent"] == "chat"
    assert len(provider.requests) == 2 and len(store.list_messages()) == 2
    correction = provider.requests[1]
    assert correction.budget_context.purpose == "router_reflect"
    assert not correction.tools
    assert "rejected_decision_error" in correction.messages[-1]["content"]
    assert correction.response_format["json_schema"]["strict"] is True
    assert store.list_quests() == []


def test_router_invalid_repair_is_bounded_and_has_no_turn_side_effects(tmp_path):
    store = Store(tmp_path / "failed-repair.sqlite3")
    provider = ScriptedMock({"intent": "create_quest", "invented_execution": True})
    before = store.ensure_router()
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(router(store, provider).handle_user_message("do it", request_id="invalid"))
    assert len(provider.requests) == 2
    assert store.list_messages() == [] and store.list_quests() == []
    assert [event["event_type"] for event in store.list_events()] == [
        "router.decision_rejected", "router.decision_rejected"
    ]
    assert store.ensure_router() == before
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM constraint_records").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM router_turns").fetchone()[0] == 0


def test_router_keeps_literal_constraints_distinct_from_pending_model_extracts(tmp_path):
    store = Store(tmp_path / "extracted.sqlite3")
    request = "Analyze this decoder. Do not delete raw-results.csv"
    provider = ScriptedMock(
        decision("create_quest", constraints=["Preserve all existing measurements"])
    )
    response = asyncio.run(
        router(store, provider).handle_user_message(request, request_id="create")
    )
    user = next(item for item in store.list_messages() if item["role"] == "user")
    records = ConstraintService(store).context(quest_id=response["quest_id"])["items"]
    assert {item["confirmation_status"] for item in records} == {"explicit_user", "pending_model"}
    assert all(item["source_refs"] == [f"message:{user['id']}"] for item in records)
    assert all(item["related_quest_ids"] == [response["quest_id"]] for item in records)
    assert all(item["status"] == "hypothesis" for item in records)


def test_constraint_and_quest_records_roll_back_if_final_router_turn_insert_fails(tmp_path):
    store = Store(tmp_path / "rollback.sqlite3")
    with store.unit_of_work() as work:
        work.connection.execute(
            "CREATE TRIGGER reject_turn BEFORE INSERT ON router_turns BEGIN SELECT RAISE(ABORT,'turn failure'); END"
        )
    provider = ScriptedMock(decision("create_quest", constraints=["preserve sources"]))
    with pytest.raises(sqlite3.IntegrityError, match="turn failure"):
        asyncio.run(
            router(store, provider).handle_user_message(
                "Do not delete sources", request_id="rollback"
            )
        )
    assert store.list_messages() == [] and store.list_quests() == [] and store.list_events() == []
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM constraint_records").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM constraint_message_index").fetchone()[0] == 0
        )


def test_planner_receives_original_request_and_constraint_provenance(tmp_path):
    store = Store(tmp_path / "original.sqlite3")
    text = "Analyze decoder overflow; no writes. Do not delete raw-results.csv"
    provider = ScriptedMock(decision("create_quest", goal="Summarized goal"))
    response = asyncio.run(router(store, provider).handle_user_message(text, request_id="original"))
    quest = store.get_quest(response["quest_id"])
    metadata = json.loads(quest["metadata"])
    assert metadata["original_request_sha256"] == hashlib.sha256(text.encode()).hexdigest()

    def inspect(request):
        data = json.loads(request.messages[1]["content"])
        assert data["goal"] == "Summarized goal"
        assert data["original_request"]["id"] == metadata["original_request_message_id"]
        assert data["original_request"]["content"] == text
        assert any(
            metadata["original_request_message_id"] in item["source_refs"][0]
            for item in data["user_constraints"]["items"]
        )
        return wire_graph()

    planner = ScriptedMock(inspect)
    plan = generate(store, Quest(id=quest["id"], title=quest["title"], goal=quest["goal"]), planner)
    assert plan["status"] == "proposed" and len(planner.requests) == 1
    assert store.get_quest(quest["id"])["status"] == "awaiting_approval"
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0


def updated_quest(store):
    original = "Analyze the decoder using read-only tools."
    created = asyncio.run(
        router(store, ScriptedMock(decision("create_quest"))).handle_user_message(
            original, request_id="original"
        )
    )
    update = "Focus on overflow and retain raw-results.csv."
    changed = asyncio.run(
        router(
            store,
            ScriptedMock(
                decision(
                    "update_quest",
                    goal="Inspect overflow and preserve original measurements",
                    quest_id=created["quest_id"],
                    candidates=[created["quest_id"]],
                )
            ),
        ).handle_user_message(update, request_id="goal-update")
    )
    assert changed["intent"] == "update_quest"
    return store.get_quest(created["quest_id"]), original, update


def test_planner_checks_original_and_updated_request_after_compaction_and_restart(tmp_path):
    store = Store(tmp_path / "updated-source.sqlite3")
    quest, original, update = updated_quest(store)
    metadata = json.loads(quest["metadata"])
    assert metadata["original_request_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert metadata["goal_update_sha256"] == hashlib.sha256(update.encode()).hexdigest()
    assert metadata["original_request_message_id"] != metadata["goal_update_message_id"]
    assert quest["plan_revision"] == 1
    assert (
        len(
            [
                event
                for event in store.list_events()
                if event["event_type"] == "quest.proposal_updated"
            ]
        )
        == 1
    )
    ContextService(store).compact_router(keep_recent=0)
    reopened = Store(store.path, workspace=store.workspace)

    def inspect(request):
        data = json.loads(request.messages[1]["content"])
        assert data["original_request"] == {
            "id": metadata["original_request_message_id"],
            "content": original,
        }
        assert data["updated_request"] == {
            "id": metadata["goal_update_message_id"],
            "content": update,
        }
        assert data["goal"] == "Inspect overflow and preserve original measurements"
        return wire_graph()

    provider = ScriptedMock(inspect)
    result = generate(
        reopened, Quest(id=quest["id"], title=quest["title"], goal=quest["goal"]), provider
    )
    assert result["status"] == "proposed" and len(provider.requests) == 1


@pytest.mark.parametrize("source", ["original", "goal_update"])
@pytest.mark.parametrize("corruption", ["changed", "missing", "not_user"])
def test_updated_goal_cannot_bypass_either_original_source_check(tmp_path, source, corruption):
    store = Store(tmp_path / "tampered-update.sqlite3")
    quest, _original, _update = updated_quest(store)
    metadata = json.loads(quest["metadata"])
    source_id = (
        metadata[f"{source}_request_message_id"]
        if source == "original"
        else metadata["goal_update_message_id"]
    )
    with store.unit_of_work() as work:
        if corruption == "changed":
            work.connection.execute(
                "UPDATE messages SET content='replacement' WHERE id=?", (source_id,)
            )
        elif corruption == "not_user":
            work.connection.execute("UPDATE messages SET role='assistant' WHERE id=?", (source_id,))
        else:
            work.connection.execute("DELETE FROM messages WHERE id=?", (source_id,))
    provider = ScriptedMock(wire_graph())
    expected = "Original Quest request" if source == "original" else "Updated Quest request"
    with pytest.raises(DomainError, match=expected):
        generate(store, Quest(id=quest["id"], title=quest["title"], goal=quest["goal"]), provider)
    assert not provider.requests
    assert QuestGraphService(store).history(quest["id"]) == []


@pytest.mark.parametrize("corruption", ["changed", "missing"])
def test_planner_refuses_corrupt_original_request_before_model_call(tmp_path, corruption):
    store = Store(tmp_path / "corrupt.sqlite3")
    response = asyncio.run(
        router(store, ScriptedMock(decision("create_quest"))).handle_user_message(
            "Analyze exact original source", request_id="original"
        )
    )
    quest = store.get_quest(response["quest_id"])
    source = json.loads(quest["metadata"])["original_request_message_id"]
    with store.unit_of_work() as work:
        if corruption == "changed":
            work.connection.execute(
                "UPDATE messages SET content='replacement' WHERE id=?", (source,)
            )
        else:
            work.connection.execute("DELETE FROM messages WHERE id=?", (source,))
    planner = ScriptedMock(wire_graph())
    with pytest.raises(DomainError, match="Original Quest request"):
        generate(store, Quest(id=quest["id"], title=quest["title"], goal=quest["goal"]), planner)
    assert not planner.requests


@pytest.mark.parametrize("invalid_field", ["tool", "unavailable_tool", "capability", "criterion"])
def test_planner_repairs_invalid_contract_names_once(tmp_path, invalid_field):
    store = Store(tmp_path / "plan-repair.sqlite3")
    quest = Quest(title="Analyze", goal="Inspect a decoder")
    store.save_quest(quest)
    invalid = copy.deepcopy(wire_graph())
    if invalid_field == "tool":
        invalid["nodes"][0]["allowed_tools"] = ["execute_arbitrary_shell"]
    elif invalid_field == "unavailable_tool":
        invalid["nodes"][0]["allowed_tools"] = ["browser_get"]
    elif invalid_field == "criterion":
        invalid["nodes"][0]["success_criteria"] = ["delivery_review: check all cited sources"]
    else:
        invalid["nodes"][0]["required_capabilities"] = [
            {"name": "invented_capability", "minimum": 90.0}
        ]
    provider = ScriptedMock(invalid, wire_graph())
    plan = generate(store, quest, provider)
    assert len(provider.requests) == 2
    assert "rejected_proposal_error" in provider.requests[1].messages[-1]["content"]
    assert plan["nodes"][0]["allowed_tools"] == ["directory_list", "file_read"]
    assert plan["nodes"][0]["required_capabilities"] == {"coding": 40.0}
    assert len(QuestGraphService(store).history(quest.id)) == 1


def test_planner_invalid_tools_after_repair_leave_no_plan_or_worker(tmp_path):
    store = Store(tmp_path / "failed-plan-repair.sqlite3")
    quest = Quest(title="Inspect", goal="Inspect safely")
    store.save_quest(quest)
    invalid = wire_graph()
    invalid["nodes"][0]["allowed_tools"] = ["execute_arbitrary_shell"]
    provider = ScriptedMock(invalid)
    with pytest.raises(DomainError, match="Invalid graph proposal"):
        generate(store, quest, provider)
    assert len(provider.requests) == 2
    assert QuestGraphService(store).history(quest.id) == []
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0


@pytest.mark.parametrize(
    "criterion",
    [
        "json_review:work/report.json",
        "json_valid:work/report.json",
        "semantic_numeric:scheduler-v1:work/result.py",
    ],
)
def test_planner_repairs_artifact_criteria_without_own_write_permission(tmp_path, criterion):
    store = Store(tmp_path / "artifact-plan.sqlite3")
    quest = Quest(title="Artifact task", goal="Create a governed artifact")
    store.save_quest(quest)
    invalid = wire_graph()
    invalid["nodes"][0]["success_criteria"] = [criterion]
    valid = copy.deepcopy(invalid)
    valid["nodes"][0]["allowed_tools"] += ["file_write", "file_revise"]
    provider = ScriptedMock(invalid, valid)
    plan = generate(store, quest, provider)
    assert len(provider.requests) == 2
    assert (
        "Artifact criteria require file_write on the same node"
        in provider.requests[1].messages[-1]["content"]
    )
    assert plan["nodes"][0]["success_criteria"] == [criterion]
    assert "file_write" in plan["nodes"][0]["allowed_tools"]
    assert len(QuestGraphService(store).history(quest.id)) == 1


def test_planner_repeated_unwritable_artifact_leaves_no_authorized_plan(tmp_path):
    store = Store(tmp_path / "unwritable.sqlite3")
    quest = Quest(title="Artifact", goal="Create a report")
    store.save_quest(quest)
    invalid = wire_graph()
    invalid["nodes"][0]["success_criteria"] = ["json_review:work/report.json"]
    provider = ScriptedMock(invalid)
    with pytest.raises(DomainError, match="Invalid graph proposal"):
        generate(store, quest, provider)
    assert len(provider.requests) == 2
    assert QuestGraphService(store).history(quest.id) == []
    assert store.get_quest(quest.id)["version"] == 0


def test_autonomous_planner_rejects_nine_nodes_after_one_repair(tmp_path):
    store = Store(tmp_path / "too-many.sqlite3")
    quest = Quest(title="Bounded plan", goal="Do bounded work")
    store.save_quest(quest)
    provider = ScriptedMock(wire_graph(9))
    with pytest.raises(DomainError, match="Invalid graph proposal"):
        generate(store, quest, provider)
    assert len(provider.requests) == 2
    assert QuestGraphService(store).history(quest.id) == []
    assert store.get_quest(quest.id)["version"] == 0 and store.list_events() == []


def test_same_router_identity_and_original_source_survive_compaction_and_restart(tmp_path):
    store = Store(tmp_path / "restart.sqlite3")
    text = "Investigate decoder boundaries. Do not delete raw-results.csv"
    created = asyncio.run(
        router(store, ScriptedMock(decision("create_quest"))).handle_user_message(
            text, request_id="start"
        )
    )
    source_id = json.loads(store.get_quest(created["quest_id"])["metadata"])[
        "original_request_message_id"
    ]
    identity = store.ensure_router()["router_session_id"]
    ContextService(store).compact_router(keep_recent=0)
    recovered = Store(store.path, workspace=store.workspace)

    def request_source(_request):
        return type(
            "MockResponse",
            (),
            {
                "text": "",
                "usage": {},
                "tool_calls": [
                    {
                        "id": "original",
                        "name": "read_context_source",
                        "arguments": {"source_id": source_id},
                    }
                ],
            },
        )()

    def inspect_source(request):
        source = json.loads(request.messages[-1]["content"])
        assert source["source_id"] == source_id and source["content"] == text
        assert source["source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
        return decision(
            "continue_quest", quest_id=created["quest_id"], candidates=[created["quest_id"]]
        )

    result = asyncio.run(
        router(recovered, ScriptedMock(request_source, inspect_source)).handle_user_message(
            f"Continue {created['quest_id']}", request_id="resume"
        )
    )
    assert result["quest_id"] == created["quest_id"] and result["intent"] == "continue_quest"
    assert recovered.ensure_router()["router_session_id"] == identity
    assert len(recovered.list_quests()) == 1
    assert {item["conversation_id"] for item in recovered.list_messages()} == {"main"}
