"""Native repair stays bounded and cannot restart a rejected semantic cycle."""

import asyncio
import hashlib
import importlib
import json
import secrets

import pytest
from planner_capacity_fixtures import (
    NativePlannerMock,
    configure_planner_capacity,
    semantic_capacity_graph,
)
from test_core_measured_serial_v17 import NativeFramingMock
from test_event_dispatcher import drain_dispatcher
from test_planner_protocol_v23 import graph
from test_planner_protocol_v26 import v26_node
from test_planner_router_input_v18 import Captured, NativeCapture, route_original_scenario
from test_planner_source_preview_v22 import setup

from forge_core.capacity_preflight import CapacityPreflightError
from forge_core.composer_request import measure
from forge_core.domain import ForgeEvent, Quest
from forge_core.quest_graph import InvalidGraphProposal, QuestGraphService


def test_native_repair_preserves_full_original_task_without_echoing_discarded_proposal(tmp_path):
    store, quest, original, _ = route_original_scenario(tmp_path, 3)
    rejected = json.dumps({"discarded_invalid_padding": "x" * 30000})

    class CaptureLargeRejection(NativeCapture):
        async def generate(self, request):
            native = self._payload(request, False)
            self.requests.append(request)
            self.native_sizes.append(len(json.dumps(native, ensure_ascii=False).encode()) + 64)
            if len(self.requests) == 1:
                return type("InvalidResponse", (), {"text": rejected, "usage": {}})()
            raise Captured

    provider = CaptureLargeRejection()
    with pytest.raises(Captured):
        asyncio.run(
            QuestGraphService(store, composer_capacity_measurer=measure).generate(
                quest["id"],
                expected_version=quest["version"],
                provider=provider,
                provider_id="p",
                model_id="native",
                context_limit=65536,
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
                semantic_protocol_version="v28",
            )
        )
    assert len(provider.requests) == 2
    initial, repair = provider.requests
    assert repair.messages[:2] == initial.messages
    assert json.loads(repair.messages[1]["content"])["original_request"]["content"] == original
    assert all(message["role"] != "assistant" for message in repair.messages)
    feedback = json.loads(repair.messages[-1]["content"])
    assert feedback["rejected_proposal_error"]
    assert feedback["rejected_proposal_identity"] == {
        "response_sha256": hashlib.sha256(rejected.encode()).hexdigest(),
        "response_bytes": len(rejected.encode()),
    }
    assert provider.native_sizes[1] + repair.max_output_tokens <= 65536
    # The old complete assistant echo would exceed this unchanged native context.
    assert provider.native_sizes[1] + len(rejected.encode()) + repair.max_output_tokens > 65536
    assert QuestGraphService(store).history(quest["id"]) == []


def test_oversize_repair_is_terminal_and_never_replays_initial_request(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("FORGE_DB_PATH", raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="Bounded planner", goal="Keep the full task and existing limits")
    main.store.save_quest(quest)

    class Proposal(NativePlannerMock):
        calls = 0

        async def generate(self, request):
            self.calls += 1
            return type(
                "Response", (), {"text": json.dumps(semantic_capacity_graph("a", "work"))}
            )()

    provider = Proposal()
    configure_planner_capacity(main, provider)
    main.router.model_id = "mock"

    def rejected(*_args, **_kwargs):
        raise CapacityPreflightError("Complete request cannot fit", {"details": "x" * 60000})

    monkeypatch.setattr(main.quest_graph, "_size_core_resources", rejected)
    main.store.append_event(ForgeEvent(event_type="quest.proposed", quest_id=quest.id, payload={}))
    drain_dispatcher(main.dispatcher)
    assert not asyncio.run(main.process_pending_plan())
    assert provider.calls == 1
    notice = main.dispatcher.inbox("planner")[0]
    assert notice["processed_at"] is None and notice["notice_dead_at"] is not None
    assert notice["notice_attempts"] == 1
    with main.store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_next_attempt=0 WHERE consumer_id='planner'"
        )
    assert not asyncio.run(main.process_pending_plan())
    assert provider.calls == 1
    assert main.quest_graph.history(quest.id) == []


def test_capacity_feedback_preserves_action_and_output_bounds_and_reports_native_excess(tmp_path):
    store, quest, _inventory = setup(tmp_path)
    # This probe targets context/review rejection, with the declared large-output
    # tier supported so model output eligibility cannot reject it first.
    store.save_model(store.list_models()[0].model_copy(update={"max_output_tokens": 8192}))
    selected = graph(
        v26_node(
            "complete-artifact",
            expected="work/result.json",
            tools=["file_write", "file_revise"],
            kind="json_review",
            action="bounded_test_receipts_4096",
        )
    )
    selected["nodes"][0]["output_capacity"] = {
        "profile": "custom",
        "max_artifact_bytes": 32768,
        "max_delivery_bytes": 1536,
    }
    provider = NativeFramingMock(selected)
    with pytest.raises(InvalidGraphProposal):
        asyncio.run(
            QuestGraphService(store).generate(
                quest.id,
                expected_version=0,
                provider=provider,
                provider_id="p",
                model_id="big",
                context_limit=65536,
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
                semantic_protocol_version="v26",
            )
        )
    assert len(provider.requests) == 2
    feedback = json.loads(provider.requests[1].messages[-1]["content"])
    candidate = feedback["capacity_diagnostics"]["rejected_candidates"][0]
    assert candidate["suggested_tight_bounds"] == candidate["declared_bounds"]
    assert candidate["declared_bounds"]["max_source_receipt_bytes"] >= 4096 + 1024
    assert candidate["native_context_excess_bytes"]["correction"] == max(
        0,
        candidate["correction_input_bound"]
        + candidate["output_bound"]
        - candidate["model_context_limit"],
    )
    assert candidate["native_context_excess_bytes"]["correction"] > 0


def test_complete_analysis_profile_gets_measured_response_space_and_exact_composer_binding(tmp_path):
    from test_composer_resources_v21 import graph_fixture, semantic_team

    from forge_core.composer_resources import SemanticTeamWire

    store, quest, _inventory = setup(tmp_path)
    selected = graph(v26_node("analysis", output="inline_6144", tools=["file_read"]))
    provider = NativeFramingMock(selected)

    def measure_future(*args):
        return measure(
            *args,
            model_id="big",
            context_limit=65536,
            capabilities={"json_schema": "test_fixture"},
            operator_max_concurrency=1,
        )

    plan = asyncio.run(
        QuestGraphService(
            store, capacity_providers={"p": provider}, composer_capacity_measurer=measure_future
        ).generate(
            quest.id,
            expected_version=0,
            provider=provider,
            provider_id="p",
            model_id="big",
            context_limit=65536,
            capabilities={"json_schema": True},
            capability_sources={"json_schema": "test_fixture"},
            require_capacity_contract=True,
            resource_allocation_mode="core_measured",
            semantic_protocol_version="v28",
        )
    )
    assert plan["nodes"][0]["capacity_bounds"]["max_delivery_bytes"] == 6144
    measured = plan["capacity_contract"]["nodes"]["analysis"]
    assert measured["output_bound"] == 4096
    envelope = measured["review_envelope"]
    assert envelope["correction_calls"] == 2
    assert envelope["max_review_input_tokens"] + envelope["verdict_tokens"] <= 65536
    assert envelope["correction_input_tokens"] + measured["output_bound"] <= 65536
    roles, _limits, _strategy = SemanticTeamWire.model_validate(semantic_team(plan)).inputs(
        plan, operator_max_concurrency=1
    )
    assert roles[0].output_tokens == 4096
    assert roles[0].input_tokens + roles[0].output_tokens == plan["nodes"][0]["budget_tokens"]
    # Existing approved observations retain their original response contract.
    approved = graph_fixture()
    old_roles, _, _ = SemanticTeamWire.model_validate(semantic_team(approved)).inputs(
        approved, operator_max_concurrency=1
    )
    assert old_roles[0].output_tokens == 2048
