"""Behavioral integration of the Super-agent loop, real SQLite and explicit Mock models."""

import asyncio
import hashlib
import importlib
import json
import secrets
import time
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_superagent_events import worker

from forge_core.dispatcher import Dispatcher
from forge_core.domain import DomainError, ModelProfile, ProtocolSupport, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.runtime import ModelResponse
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.superagent_delegation import SuperagentDelegation
from forge_core.superagent_events import TriggerService
from forge_core.superagent_loop import process_triggers
from forge_core.superagent_sources import LocalTriggerSources
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


class MockProvider:
    """In-process scripted model; no external API or task quality is implied."""

    execution_mode = "mock"

    def __init__(self, respond):
        self.respond = respond
        self.requests = []

    async def generate(self, request):
        self.requests.append(deepcopy(request))
        return self.respond(request, len(self.requests))


def response(value):
    return ModelResponse(
        text=json.dumps(value),
        tool_calls=[],
        usage={"input_tokens": 20, "output_tokens": 10},
        finish_reason="stop",
    )


def event_decision(trigger, action="dispatch", *, quest_id=None):
    return {
        "trigger_id": trigger["id"],
        "action": action,
        "title": "Independent work" if action == "dispatch" else None,
        "goal": "Handle the complete referenced event" if action == "dispatch" else None,
        "quest_id": quest_id,
    }


def conversational_decision(intent="create_quest", *, quest_id=None):
    return {
        "intent": intent,
        "reply": "I will handle this request.",
        "title": "Independent work" if intent == "create_quest" else None,
        "goal": "Complete the original request" if intent == "create_quest" else None,
        "quest_id": quest_id,
        "candidates": [quest_id] if quest_id else [],
        "task_kind": "long_research",
    }


@pytest.fixture
def rig(tmp_path):
    store = Store(tmp_path / "data" / "forge.sqlite3", workspace=Workspace.from_path(tmp_path))
    store.save_model(
        ModelProfile(
            id="mock",
            display_name="Explicit Mock model",
            provider="mock-provider",
            model_id="mock-model",
            enabled=True,
            context_window=131072,
            max_output_tokens=8192,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
            structured_output_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        model_id="mock-model",
        provider_id="mock-provider",
        context_limit=131072,
        autonomous=True,
    )
    events = TriggerService(store)
    events.register_source("external", kind="webhook")
    return store, router, events


def test_semantic_batch_dispatches_independent_work_and_ignores_noise(rig):
    store, router, events = rig
    a = events.receive("external", "a", "new-work", {"text": "Prepare a result"})
    b = events.receive("external", "b", "noise", {"text": "Thank you"})
    provider = MockProvider(
        lambda request, _: response(
            {
                "decisions": [
                    event_decision(a),
                    event_decision(b, "ignore"),
                ]
            }
        )
    )
    router.provider = provider
    identity = store.ensure_router()["router_session_id"]
    assert asyncio.run(process_triggers(router)) == 2
    assert len(provider.requests) == 1
    assert events.pending() == []
    assert events.read_trigger(b["id"])["status"] == "ignored"
    quests = store.list_quests()
    assert len(quests) == 1
    with store.connection() as c:
        mandate = c.execute("SELECT * FROM delegation_mandates").fetchone()
        assert mandate["quest_id"] == quests[0]["id"] and mandate["source_trigger_id"] == a["id"]
        assert c.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert store.ensure_router()["router_session_id"] == identity
    assert store.list_messages() == []
    assert asyncio.run(process_triggers(router)) == 0
    assert len(provider.requests) == 1


def test_semantic_attach_reuses_hidden_worker_then_bypasses_router(rig):
    store, router, events = rig
    run = worker(store)
    initial = events.receive("external", "1", "new-channel", {"text": "Continue existing work"})
    provider = MockProvider(
        lambda request, _: response(
            {
                "decisions": [event_decision(initial, "attach", quest_id=run["quest_id"])],
            }
        )
    )
    router.provider = provider
    assert asyncio.run(process_triggers(router)) == 1
    followup = events.receive("external", "2", "new-channel", {"text": "Apply this refinement"})
    assert followup["status"] == "passed_through"
    assert asyncio.run(process_triggers(router)) == 0
    assert len(provider.requests) == 1
    with store.connection() as c:
        items = c.execute(
            "SELECT event_id FROM worker_inbox WHERE run_id=?", (run["id"],)
        ).fetchall()
        assert {row["event_id"] for row in items} == {initial["event_id"], followup["event_id"]}
    assert len(store.list_quests()) == 1


@pytest.mark.parametrize("fault", ["duplicate", "missing", "wrong_id"])
def test_semantic_invalid_batch_never_partially_creates_tasks(rig, fault):
    store, router, events = rig
    a = events.receive("external", "a", "a", {})
    b = events.receive("external", "b", "b", {})
    decisions = [event_decision(a), event_decision(b)]
    if fault == "duplicate":
        decisions.append(event_decision(a))
    elif fault == "missing":
        decisions.pop()
    else:
        decisions[1]["trigger_id"] = "trigger_unknown"
    router.provider = MockProvider(lambda request, _: response({"decisions": decisions}))
    with pytest.raises(DomainError, match="match"):
        asyncio.run(process_triggers(router))
    assert store.list_quests() == [] and store.list_messages() == []
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM delegation_mandates").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='router.trigger_decided'"
            ).fetchone()[0]
            == 0
        )


def test_semantic_inflight_subscription_invalidates_cold_start_decision(rig):
    store, router, events = rig
    initial = events.receive("external", "1", "topic", {})
    existing = worker(store)

    def subscribe_during_inference(request, _):
        events.subscribe(initial["thread_id"], existing["id"])
        return response({"decisions": [event_decision(initial)]})

    router.provider = MockProvider(subscribe_during_inference)
    with pytest.raises(DomainError):
        asyncio.run(process_triggers(router))
    assert len(store.list_quests()) == 1
    assert events.read_trigger(initial["id"])["status"] == "passed_through"
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM delegation_mandates").fetchone()[0] == 0


def test_semantic_main_router_never_claims_foreign_conversation_and_rejects_foreign_attach(rig):
    store, router, events = rig
    with store.unit_of_work() as work:
        work.connection.execute("INSERT INTO conversations VALUES ('foreign','now')")
        other_quest = Quest(title="Foreign", goal="Private")
        work.save_quest(other_quest, conversation_id="foreign")
    events.register_source("foreign", kind="message", conversation_id="foreign")
    foreign = events.receive(
        "foreign", "1", "topic", {"text": "private"}, conversation_id="foreign"
    )
    main = events.receive("external", "1", "topic", {"text": "malicious attach"})

    def attach_elsewhere(request, _):
        presented = json.loads(request.messages[1]["content"])["events"]
        assert [item["id"] for item in presented] == [main["id"]]
        return response({"decisions": [event_decision(main, "attach", quest_id=other_quest.id)]})

    router.provider = MockProvider(attach_elsewhere)
    with pytest.raises(DomainError, match="unavailable"):
        asyncio.run(process_triggers(router))
    assert events.read_trigger(foreign["id"])["status"] == "pending"
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT lease_token FROM superagent_triggers WHERE id=?", (foreign["id"],)
            ).fetchone()[0]
            is None
        )
        assert c.execute("SELECT COUNT(*) FROM superagent_subscriptions").fetchone()[0] == 0


def test_router_reads_spilled_trigger_through_actual_model_tool_round(rig):
    store, router, events = rig
    marker = "complete-tail-requirement"
    trigger = events.receive("external", "1", "long", {"text": "x" * 3000 + marker})

    def read_then_decide(request, count):
        if count == 1:
            assert marker not in request.messages[1]["content"]
            assert "read_trigger" in {tool["name"] for tool in request.tools}
            return ModelResponse(
                text="",
                tool_calls=[
                    {
                        "id": "read-original",
                        "name": "read_trigger",
                        "arguments": {
                            "trigger_id": trigger["id"],
                            "offset_chars": 2900,
                            "max_chars": 1024,
                        },
                    }
                ],
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        tool = request.messages[-1]
        assert tool["role"] == "tool" and tool["tool_call_id"] == "read-original"
        assert marker in json.loads(tool["content"])["text"]
        return response({"decisions": [event_decision(trigger)]})

    provider = router.provider = MockProvider(read_then_decide)
    assert asyncio.run(process_triggers(router)) == 1
    assert len(provider.requests) == 2
    assert store.list_quests()[0]["metadata"]


def test_failed_inference_can_resume_after_lease_expiry_without_duplicate_task(rig, monkeypatch):
    store, router, events = rig
    trigger = events.receive("external", "1", "topic", {"text": "do work"})
    now = [100.0]
    service = TriggerService(store, clock=lambda: now[0])
    from forge_core import superagent_loop

    monkeypatch.setattr(superagent_loop, "TriggerService", lambda _: service)

    def failing_then_successful(request, count):
        if count == 1:
            raise DomainError("Explicit Mock network failure")
        return response({"decisions": [event_decision(trigger)]})

    provider = router.provider = MockProvider(failing_then_successful)
    identity = store.ensure_router()["router_session_id"]
    with pytest.raises(DomainError, match="Mock network"):
        asyncio.run(process_triggers(router))
    assert store.list_quests() == []
    assert asyncio.run(process_triggers(router)) == 0
    assert len(provider.requests) == 1
    now[0] += 121
    assert asyncio.run(process_triggers(router)) == 1
    assert asyncio.run(process_triggers(router)) == 0
    assert len(store.list_quests()) == 1
    assert store.ensure_router()["router_session_id"] == identity
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM delegation_mandates").fetchone()[0] == 1


def test_semantic_trigger_source_reaches_worker_prompt_and_actual_scoped_read(rig):
    store, router, events = rig
    trigger = events.receive("external", "1", "source", {"text": "x" * 2200 + "source-tail"})
    router.provider = MockProvider(
        lambda request, _: response({"decisions": [event_decision(trigger)]})
    )
    assert asyncio.run(process_triggers(router)) == 1
    quest = store.list_quests()[0]
    graph, teams = QuestGraphService(store), TeamCompositionService(store)
    assert SuperagentDelegation(store, graph, teams).prepare(quest["id"])

    def read_source(request, count):
        if count == 1:
            assert trigger["id"] in "\n".join(str(item.get("content")) for item in request.messages)
            return ModelResponse(
                text="",
                tool_calls=[
                    {
                        "id": "read-event",
                        "name": "read_trigger",
                        "arguments": {
                            "trigger_id": trigger["id"],
                            "offset_chars": 0,
                            "max_chars": 4096,
                        },
                    }
                ],
                usage={"input_tokens": 20, "output_tokens": 10},
            )
        assert request.messages[-1]["role"] == "tool"
        assert "source-tail" in json.loads(request.messages[-1]["content"])["text"]
        return response(
            {
                "summary": "Mock environmental source read",
                "findings": [],
                "decisions": [],
                "artifacts": [],
                "evidence_references": [],
                "unresolved_issues": [],
                "confidence": 0.5,
                "next_actions": [],
            }
        )

    model = MockProvider(read_source)
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    plan = graph.get(quest["id"])
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest["id"],)).fetchone()
    session = manager.start(
        quest["id"], plan["id"], "task", team_digest=team["proposal_digest"], member_index=0
    )
    assert asyncio.run(manager.run(session["id"]))["state"] == "review_pending"
    assert len(model.requests) == 2
    assert store.list_messages() == []


def test_user_conversation_autonomously_admits_worker_reads_sources_and_relays_only_via_router(rig):
    store, router, events = rig

    def user_and_notice(request, _):
        schema_name = request.response_format["json_schema"]["name"]
        if schema_name == "router_notice":
            return response(
                {"action": "update_user", "reply": "The background result is ready for review."}
            )
        return response(conversational_decision())

    router.provider = MockProvider(user_and_notice)
    initial = asyncio.run(
        router.handle_user_message("Please explain the private source.", request_id="new-task")
    )
    quest_id = initial["quest_id"]
    metadata = json.loads(store.get_quest(quest_id)["metadata"])
    graph, teams = QuestGraphService(store), TeamCompositionService(store)
    assert SuperagentDelegation(store, graph, teams).prepare(quest_id)
    observed = []

    def execute_and_read(request, count):
        observed.append(deepcopy(request.messages))
        if count == 1:
            prompt = "\n".join(str(message.get("content", "")) for message in request.messages)
            assert "Please explain the private source." in prompt
            assert metadata["trigger_id"] in prompt
            assert "read_trigger" in {tool["name"] for tool in request.tools}
            return ModelResponse(
                text="",
                tool_calls=[
                    {
                        "id": "read-task",
                        "name": "read_trigger",
                        "arguments": {"trigger_id": metadata["trigger_id"]},
                    }
                ],
                usage={"input_tokens": 20, "output_tokens": 10},
            )
        assert request.messages[-1]["role"] == "tool"
        if count == 2:
            assert "Please explain the private source." in json.loads(request.messages[-1]["content"])["text"]
            # Reading the original task does not consume a later Thread request.
            assert followup["id"] in json.dumps(request.messages)
            return ModelResponse(text="", tool_calls=[{
                "id": "read-addition", "name": "read_trigger",
                "arguments": {"trigger_id": followup["id"]},
            }], usage={"input_tokens": 20, "output_tokens": 10})
        assert count == 3
        assert "Keep the analysis concise" in json.loads(request.messages[-1]["content"])["text"]
        return response(
            {
                "summary": "Mock source analysis completed",
                "findings": ["Scripted fixture"],
                "decisions": [],
                "artifacts": [],
                "evidence_references": [],
                "unresolved_issues": [],
                "confidence": 0.7,
                "next_actions": [],
            }
        )

    model = MockProvider(execute_and_read)
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)).fetchone()
    session = manager.start(
        quest_id, plan["id"], "task", team_digest=team["proposal_digest"], member_index=0
    )
    assert len(store.list_messages()) == 2
    followup = events.receive(
        "conversation",
        "later",
        quest_id,
        {"content": "Keep the analysis concise"},
        actor="local_user",
        addressed=True,
    )
    assert followup["status"] == "passed_through"
    result = asyncio.run(manager.run(session["id"]))
    assert result["state"] == "review_pending"
    assert len(model.requests) == 3
    assert len(store.list_messages()) == 2
    dispatcher = Dispatcher(store)
    for _ in range(12):
        dispatcher.tick()
    for _ in range(12):
        if any(message["metadata"].find("router_notice") >= 0 for message in store.list_messages()):
            break
        asyncio.run(router.handle_pending_events(dispatcher))
    messages = store.list_messages()
    assert any(
        json.loads(message["metadata"]).get("source") == "router_notice" for message in messages
    )
    assert {message["conversation_id"] for message in messages} == {"main"}
    assert all(item["hidden"] for item in store.list_workers())
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM superagent_subscriptions WHERE run_id=?", (session["id"],)
            ).fetchone()[0]
            == 1
        )
        assert c.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_actual_api_lifespan_polls_persistent_sources_and_records_component_health(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
        "FORGE_PROVIDERS_JSON",
        "FORGE_PRICE_BOUNDS_JSON",
        "FORGE_ROUTER_MODEL_ID",
        "FORGE_ROUTER_CONTEXT_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    events = TriggerService(main.store)
    events.register_source("test-clock", kind="timer", mode="ignore")
    events.register_source("test-files", kind="file", mode="ignore")
    path = tmp_path / "watched.txt"
    path.write_text("before", encoding="utf-8")
    sources = LocalTriggerSources(main.store)
    sources.schedule("test-clock", "timer", {"text": "tick"}, due_at=time.time() - 1)
    sources.watch_file("test-files", "watched.txt")
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        path.write_text("after", encoding="utf-8")
        deadline = time.monotonic() + 6
        count = 0
        expected_heartbeats = {"sources", "delegation", "watchdog", "router", "worker"}
        while time.monotonic() < deadline:
            with main.store.connection() as c:
                count = c.execute("SELECT COUNT(*) FROM superagent_triggers").fetchone()[0]
            heartbeats = {item["component"]: item for item in main.app.state.watchdog.snapshot()}
            if (
                count == 2
                and expected_heartbeats <= heartbeats.keys()
                and main.app.state.background_health.get("sources") == "ok"
            ):
                break
            time.sleep(0.02)
        assert count == 2
        assert main.app.state.background_health["sources"] == "ok"
        heartbeats = {item["component"]: item for item in main.app.state.watchdog.snapshot()}
        assert expected_heartbeats <= heartbeats.keys()
        assert heartbeats["sources"]["state"] == "ok"
    assert all(task.done() for task in main.app.state.background_tasks)
    assert not main.app.state.worker_scheduler.tasks


def test_router_dispatches_multiple_independent_workers_without_replacing_original_source(rig):
    store, router, _events = rig
    assignments = [
        {"objective": "Inspect the implementation", "role": "coding"},
        {"objective": "Verify the derivation", "role": "math"},
    ]
    decision = conversational_decision()
    decision["independent_dispatches"] = assignments
    model = router.provider = MockProvider(lambda request, _: response(decision))
    original = "Review the implementation and the independent derivation. Preserve every input."
    result = asyncio.run(router.handle_user_message(original, request_id="parallel"))
    quest = store.get_quest(result["quest_id"])
    metadata = json.loads(quest["metadata"])
    assert metadata["independent_dispatches"] == assignments
    schema = model.requests[0].response_format["json_schema"]["schema"]
    assert "independent_dispatches" in schema["properties"]
    assert "IndependentDispatch" in schema["$defs"]
    assert "independent_dispatches" in model.requests[0].messages[0]["content"]
    with store.connection() as c:
        mandate = c.execute("SELECT * FROM delegation_mandates").fetchone()
        source = c.execute(
            "SELECT * FROM messages WHERE id=?", (mandate["source_message_id"],)
        ).fetchone()
    assert source["content"] == original
    assert mandate["source_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    graph, teams = QuestGraphService(store), TeamCompositionService(store)
    assert SuperagentDelegation(store, graph, teams).prepare(quest["id"])
    assert [node["objective"] for node in graph.get(quest["id"])["nodes"]] == [
        assignment["objective"] for assignment in assignments
    ]


def test_legacy_router_retains_single_task_semantics_and_its_original_schema(rig):
    store, router, _events = rig
    router.autonomous = False
    decision = conversational_decision()
    decision["independent_dispatches"] = [{"objective": "Independent analysis", "role": "research"}]
    model = router.provider = MockProvider(lambda request, _: response(decision))
    result = asyncio.run(router.handle_user_message("Inspect the inputs", request_id="legacy"))
    metadata = json.loads(store.get_quest(result["quest_id"])["metadata"])
    assert "independent_dispatches" not in metadata
    schema = model.requests[0].response_format["json_schema"]["schema"]
    assert "independent_dispatches" not in schema["properties"]
    assert "$defs" not in schema
    assert "independent_dispatches" not in model.requests[0].messages[0]["content"]
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM delegation_mandates").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM quest_plans").fetchone()[0] == 0


def test_compact_router_keeps_six_field_schema_and_defaults_to_one_worker(rig):
    store, router, _events = rig
    router.context_limit = 32768
    model = router.provider = MockProvider(lambda request, _: response(conversational_decision()))
    result = asyncio.run(router.handle_user_message("Inspect the inputs", request_id="compact"))
    schema = model.requests[0].response_format["json_schema"]["schema"]
    assert set(schema["properties"]) == {
        "intent",
        "reply",
        "title",
        "goal",
        "quest_id",
        "candidates",
    }
    assert "$defs" not in schema
    assert "independent_dispatches" not in model.requests[0].messages[0]["content"]
    graph, teams = QuestGraphService(store), TeamCompositionService(store)
    assert SuperagentDelegation(store, graph, teams).prepare(result["quest_id"])
    assert len(graph.get(result["quest_id"])["nodes"]) == 1


@pytest.mark.parametrize(
    "assignments",
    [
        [{"objective": "Task", "role": "research"}] * 5,
        [{"objective": "  ", "role": "research"}],
        [{"objective": "Task", "role": "arbitrary"}],
    ],
)
def test_router_invalid_dispatches_have_no_partial_user_turn(rig, assignments):
    store, router, _events = rig
    decision = conversational_decision()
    decision["independent_dispatches"] = assignments
    router.provider = MockProvider(lambda request, _: response(decision))
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(router.handle_user_message("Do the work", request_id="invalid-workers"))
    assert len(router.provider.requests) == 2
    assert store.list_messages() == [] and store.list_quests() == []
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM delegation_mandates").fetchone()[0] == 0


def test_trigger_dispatch_carries_independent_assignments_into_internal_contracts(rig):
    store, router, events = rig
    trigger = events.receive(
        "external", "1", "new-task", {"text": "Analyze two independent sources"}
    )
    decision = event_decision(trigger)
    decision["independent_dispatches"] = [
        {"objective": "Inspect source one", "role": "research"},
        {"objective": "Inspect source two", "role": "research"},
    ]
    router.provider = MockProvider(lambda request, _: response({"decisions": [decision]}))
    assert asyncio.run(process_triggers(router)) == 1
    quest = store.list_quests()[0]
    assert (
        json.loads(quest["metadata"])["independent_dispatches"]
        == decision["independent_dispatches"]
    )
    graph, teams = QuestGraphService(store), TeamCompositionService(store)
    assert SuperagentDelegation(store, graph, teams).prepare(quest["id"])
    assert len(graph.get(quest["id"])["nodes"]) == 2
    with store.connection() as c:
        mandate = c.execute("SELECT * FROM delegation_mandates").fetchone()
        assert mandate["source_trigger_id"] == trigger["id"]
        assert mandate["source_sha256"] == trigger["payload_sha256"]


def test_compact_trigger_schema_omits_optional_assignment_definitions(rig):
    _store, router, events = rig
    router.context_limit = 32768
    trigger = events.receive("external", "1", "small-window", {})
    model = router.provider = MockProvider(
        lambda request, _: response({"decisions": [event_decision(trigger)]})
    )
    assert asyncio.run(process_triggers(router)) == 1
    schema = model.requests[0].response_format["json_schema"]["schema"]
    assert set(schema["$defs"]) == {"TriggerDecision"}
    assert "independent_dispatches" not in schema["$defs"]["TriggerDecision"]["properties"]
    assert "independent_dispatches" not in model.requests[0].messages[0]["content"]


@pytest.mark.parametrize("action", ["ignore", "attach"])
def test_trigger_cannot_assign_new_workers_when_it_does_not_dispatch(rig, action):
    store, router, events = rig
    trigger = events.receive("external", "1", "bad-workers", {})
    decision = event_decision(trigger, action)
    decision["independent_dispatches"] = [{"objective": "Do another task", "role": "coding"}]
    router.provider = MockProvider(lambda request, _: response({"decisions": [decision]}))
    with pytest.raises(ValidationError, match="require a new task"):
        asyncio.run(process_triggers(router))
    assert store.list_quests() == []
    assert events.read_trigger(trigger["id"])["status"] == "pending"


def test_watchdog_loop_survives_heartbeat_storage_failures_with_backoff(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
        "FORGE_PROVIDERS_JSON",
        "FORGE_PRICE_BOUNDS_JSON",
        "FORGE_ROUTER_MODEL_ID",
        "FORGE_ROUTER_CONTEXT_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    original = main.Watchdog.heartbeat
    attempts = []

    def transient_failure(self, component, **kwargs):
        if component == "watchdog":
            attempts.append(time.monotonic())
            if len(attempts) <= 4:
                raise OSError("private storage diagnostic must not be logged")
        return original(self, component, **kwargs)

    monkeypatch.setattr(main.Watchdog, "heartbeat", transient_failure)
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if len(attempts) >= 5 and main.app.state.background_health.get("watchdog") == "ok":
                break
            time.sleep(0.02)
        assert len(attempts) >= 5
        assert main.app.state.background_health["watchdog"] == "ok"
        assert not main.app.state.background_tasks[-1].done()
        assert attempts[2] - attempts[0] >= 0.8
        assert attempts[4] - attempts[2] >= 1.8
        assert any(
            row["component"] == "watchdog" and row["state"] == "ok"
            for row in main.app.state.watchdog.snapshot()
        )
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("Background watchdog failed; retrying") == 2
    assert not any("private storage diagnostic" in message for message in messages)
    assert all(task.done() for task in main.app.state.background_tasks)
