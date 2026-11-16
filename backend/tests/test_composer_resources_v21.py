"""Production semantic Composer and real native admission; scripted, no network."""

import asyncio
import copy
import json

import jsonschema
import pytest
from pydantic import ValidationError
from test_composer_repair_phase2 import ComposerMock, notice_row, team_wire
from test_core_measured_planner_v19 import configured_main, generate, semantic_wire
from test_core_measured_serial_v17 import NativeFramingMock
from test_event_dispatcher import drain_dispatcher
from test_verified_capacity_v16 import capacity_graph

from forge_core.composer_resources import (
    SemanticTeamWire,
    safe_team_rejection_diagnostics,
    uses_core_composer_resources,
)
from forge_core.domain import DomainError, ModelProfile, ProtocolSupport
from forge_core.planner_resource_policy import planned_worker_output_tokens
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderError
from forge_core.team_composition import TeamCompositionService, TeamWire
from forge_core.worker_schema import STRICT_DELIVERY_OUTPUT_SCHEMA


def semantic_team(graph):
    return {
        "roles": [
            {
                "id": "research" if index == 0 else "reviewer",
                "task_node_key": node["key"],
                "required_capabilities": [
                    {"name": name, "minimum": value}
                    for name, value in node["required_capabilities"].items()
                ],
                "tools": node["allowed_tools"],
                "dynamic": False,
                "responsibility": "Inspect approved sources",
                "input_contract": node["required_input_paths"] or ["Approved objective"],
                "output_schema_json": None,
                "acceptance_criteria": node["success_criteria"],
            }
            for index, node in enumerate(graph["nodes"])
        ],
        "strategy": "greedy",
    }


def graph_fixture(*, serial=False):
    nodes = [
        {
            "key": key,
            "budget_tokens": 150000,
            "required_capabilities": {},
            "required_input_paths": [],
            "allowed_tools": ["file_read"],
            "success_criteria": ["delivery_review"],
        }
        for key in (["a", "b"] if serial else ["a"])
    ]
    graph = {
        "nodes": nodes,
        "edges": [{"source": "a", "target": "b", "condition": "success"}] if serial else [],
        "max_total_tokens": 400000,
        "max_duration_seconds": 300,
        "coordination_reserve_tokens": 40000,
        "execution_constraints": {
            "limits": {
                "max_members": 2,
                "max_concurrency": 2,
                "max_worker_steps": 12,
                "max_worker_tool_calls": 7,
                "max_duration_seconds": 250,
            }
        },
        "capacity_contract": {
            "version": "verified-capacity-v1",
            "resource_allocation": {"mode": "core_measured"},
            "nodes": {node["key"]: {"output_bound": 2048} for node in nodes},
        },
    }
    if serial:
        graph["serial_capacity_policy"] = {
            "version": "serial-capacity-v1",
            "shared_reserve_tokens": 50000,
        }
    return graph


def test_strict_semantic_schema_has_no_numeric_resource_fields():
    schema = SemanticTeamWire.model_json_schema()
    assert set(schema["properties"]) == {"roles", "strategy"}
    properties = schema["$defs"]["SemanticTeamRoleWire"]["properties"]
    assert not {
        "input_tokens",
        "output_tokens",
        "max_steps",
        "max_tool_calls",
        "max_duration_seconds",
        "depth",
    } & set(properties)
    for item in [schema, *schema["$defs"].values()]:
        if item.get("type") == "object":
            assert set(item["required"]) == set(item["properties"])


@pytest.mark.parametrize("serial", [False, True])
def test_core_binds_exact_approved_resources_without_mutating_semantics(serial):
    graph = graph_fixture(serial=serial)
    proposal = semantic_team(graph)
    before = copy.deepcopy((graph, proposal))
    jsonschema.validate(proposal, SemanticTeamWire.model_json_schema())
    roles, limits, strategy = SemanticTeamWire.model_validate(proposal).inputs(
        graph, operator_max_concurrency=1
    )
    assert (graph, proposal) == before
    assert strategy == "greedy" and limits.max_concurrency == 1
    for role, wire in zip(roles, proposal["roles"], strict=True):
        assert role.input_tokens == 147952 and role.output_tokens == 2048
        assert role.input_tokens + role.output_tokens == 150000
        assert role.max_steps == 12 and role.max_tool_calls == 7
        assert role.tools == wire["tools"]
        assert role.acceptance_criteria == wire["acceptance_criteria"]
        assert role.input_contract == wire["input_contract"]
        assert role.output_schema == STRICT_DELIVERY_OUTPUT_SCHEMA
    assert sum(role.max_duration_seconds for role in roles) == 250
    assert limits.max_total_tokens == 150000 * len(roles) + (50000 if serial else 0)
    assert limits.max_total_tool_calls == 7 * len(roles)


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "max_steps", "depth"])
def test_semantic_model_cannot_override_core_resource_numbers(field):
    proposal = semantic_team(graph_fixture())
    proposal["roles"][0][field] = 8192
    with pytest.raises(ValidationError):
        SemanticTeamWire.model_validate(proposal)


def test_legacy_wire_preserves_model_submitted_allocations():
    proposal = team_wire()
    roles, limits, _ = TeamWire.model_validate(proposal).inputs()
    assert roles[0].input_tokens == 100 and roles[0].output_tokens == 50
    assert limits.max_total_tokens == 150
    for mode in [None, "model_declared", "core_measured_serial"]:
        graph = graph_fixture()
        graph["capacity_contract"]["resource_allocation"]["mode"] = mode
        assert not uses_core_composer_resources(graph)


def test_remaining_contract_binds_only_unfinished_nodes_without_legacy_fallback():
    graph = graph_fixture(serial=True)
    del graph["capacity_contract"]["nodes"]["a"]
    proposal = semantic_team(graph)
    proposal["roles"] = [role for role in proposal["roles"] if role["task_node_key"] == "b"]
    before = copy.deepcopy(graph)
    assert uses_core_composer_resources(graph)
    roles, limits, _ = SemanticTeamWire.model_validate(proposal).inputs(
        graph, operator_max_concurrency=2
    )
    assert [role.task_node_key for role in roles] == ["b"]
    assert roles[0].input_tokens + roles[0].output_tokens == 150000
    assert limits.max_total_tokens == 200000 and limits.max_concurrency == 1
    assert graph == before


@pytest.mark.parametrize("output", [None, True, 0, -1, 150000, 200000])
def test_invalid_approved_measurements_cannot_be_silently_reallocated(output):
    graph = graph_fixture()
    graph["capacity_contract"]["nodes"]["a"]["output_bound"] = output
    with pytest.raises(DomainError):
        SemanticTeamWire.model_validate(semantic_team(graph)).inputs(
            graph, operator_max_concurrency=1
        )


def test_safe_diagnostics_do_not_persist_exception_or_unknown_field_values():
    marker = "PRIVATE_SOURCE_AND_SECRET_MARKER"
    proposal = semantic_team(graph_fixture())
    proposal["roles"][0][marker] = marker
    with pytest.raises(ValidationError) as caught:
        SemanticTeamWire.model_validate(proposal)
    diagnostic = safe_team_rejection_diagnostics(caught.value)
    assert marker not in json.dumps(diagnostic)
    assert diagnostic["fields"] == [["roles", 0, "unrecognized_field"]]
    assert len(json.dumps(diagnostic).encode()) < 4096


def test_safe_capacity_diagnostics_retain_only_whitelisted_nonnegative_integers():
    marker = "PRIVATE_SOURCE_AND_SECRET_MARKER"
    error = DomainError(marker)
    error.capacity_diagnostics = {
        "minimum_node_tokens": 162194,
        "allocated_tokens": 153902,
        "output_bound": 8192,
        "raw_packet_bytes": True,
        "source": marker,
        marker: 1,
        "worker_input_bound": -1,
    }
    diagnostics = safe_team_rejection_diagnostics(error)
    assert diagnostics["capacity"] == {
        "minimum_node_tokens": 162194,
        "allocated_tokens": 153902,
        "output_bound": 8192,
    }
    assert marker not in json.dumps(diagnostics)


@pytest.fixture
def production_stage(tmp_path, monkeypatch):
    main, quest, _ = configured_main(tmp_path, monkeypatch)
    main.store.save_model(
        ModelProfile(
            id="big",
            provider="p",
            model_id="big",
            display_name="Offline native capacity",
            context_window=65536,
            max_output_tokens=8192,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
            structured_output_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    graph = capacity_graph()
    graph["nodes"][0]["required_input_paths"] = []
    framing = NativeFramingMock(semantic_wire(graph))
    plan = generate(main.store, quest, framing)
    main.quest_graph.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    main.team_service = TeamCompositionService(
        main.store,
        capacity_providers=ProviderPool({"p": framing}),
        operator_max_concurrency=main.native_worker_concurrency,
    )
    main.router.model_id = "big"
    main.router.provider_id = "p"
    main.router.context_limit = 65536
    drain_dispatcher(main.dispatcher)
    notice = next(
        row
        for row in main.dispatcher.inbox("composer")
        if row["quest_id"] == quest.id and row["event_type"] == "quest.plan_approved"
    )
    return main, quest, plan, notice, framing


def rejection_events(main):
    return [
        json.loads(row["payload"])
        for row in main.store.list_events(limit=100)
        if row["event_type"] == "team.proposal_rejected"
    ]


def test_production_pump_uses_semantic_wire_then_full_native_service_admission(production_stage):
    main, quest, plan, notice, framing = production_stage
    proposal = semantic_team(plan)
    main.router.provider = mock = ComposerMock(proposal)
    assert asyncio.run(main.process_pending_team())
    assert len(mock.requests) == 1
    assert set(mock.requests[0].response_format["json_schema"]["schema"]["properties"]) == {
        "roles",
        "strategy",
    }
    with main.store.connection() as connection:
        row = connection.execute(
            "SELECT * FROM team_proposals WHERE quest_id=?", (quest.id,)
        ).fetchone()
    body = json.loads(row["body_json"])
    member = body["members"][0]
    assert row["status"] == "proposed"
    assert member["output_tokens"] == planned_worker_output_tokens(plan["nodes"][0]) == 4096
    assert member["estimated_tokens"] == plan["nodes"][0]["budget_tokens"]
    assert member["capacity_preflight"]["minimum_node_tokens"] <= member["estimated_tokens"]
    assert member["output_schema"] == STRICT_DELIVERY_OUTPUT_SCHEMA
    assert len(framing.requests) == 1
    assert main.store.list_workers(quest.id) == []
    assert notice_row(main, notice["event_id"])["processed_at"] is not None


def test_two_invalid_production_responses_are_terminal_and_safe_after_one_repair(production_stage):
    main, _quest, plan, notice, _ = production_stage
    marker = "PRIVATE_SOURCE_AND_SECRET_MARKER"
    invalid = semantic_team(plan)
    invalid["roles"][0]["output_tokens"] = marker
    main.router.provider = mock = ComposerMock(invalid)
    assert not asyncio.run(main.process_pending_team())
    saved = notice_row(main, notice["event_id"])
    assert saved["notice_attempts"] == 1 and saved["notice_dead_at"] is not None
    assert saved["notice_token"] is None and saved["processed_at"] is None
    assert marker not in saved["notice_error"]
    events = rejection_events(main)
    assert len(events) == 2 and all(item["response_sha256"] for item in events)
    assert marker not in json.dumps(events)
    assert [item["model_attempt"] for item in events] == [1, 2]
    assert not asyncio.run(main.process_pending_team()) and len(mock.requests) == 2
    with main.store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM team_proposals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 0


def test_invalid_production_semantics_can_be_repaired_without_autoapproval(production_stage):
    main, quest, plan, notice, _ = production_stage
    valid = semantic_team(plan)
    invalid = copy.deepcopy(valid)
    invalid["roles"][0]["tools"] = ["file_write"]
    main.router.provider = mock = ComposerMock(invalid, valid)
    assert asyncio.run(main.process_pending_team()) and len(mock.requests) == 2
    assert len(rejection_events(main)) == 1
    assert notice_row(main, notice["event_id"])["processed_at"] is not None
    assert main.store.list_workers(quest.id) == []


def test_omitted_node_cannot_create_a_team_or_expand_duration(production_stage):
    main, quest, _plan, notice, _ = production_stage
    main.router.provider = mock = ComposerMock({"roles": [], "strategy": "greedy"})
    assert not asyncio.run(main.process_pending_team())
    assert len(mock.requests) == 2
    assert notice_row(main, notice["event_id"])["notice_dead_at"] is not None
    with main.store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM team_proposals WHERE quest_id=?", (quest.id,)
            ).fetchone()[0]
            == 0
        )


def test_rejection_event_requires_current_composer_notice_lease(production_stage):
    main, _quest, plan, notice, _ = production_stage

    class ExpiredLease(ComposerMock):
        async def generate(self, request):
            with main.store.unit_of_work() as work:
                work.connection.execute(
                    "UPDATE event_inbox SET notice_next_attempt=0 WHERE consumer_id='composer' "
                    "AND event_id=?",
                    (notice["event_id"],),
                )
            return await super().generate(request)

    proposal = semantic_team(plan)
    proposal["roles"][0]["output_tokens"] = 8192
    main.router.provider = ExpiredLease(proposal)
    assert not asyncio.run(main.process_pending_team())
    assert rejection_events(main) == []
    saved = notice_row(main, notice["event_id"])
    assert saved["processed_at"] is None and saved["notice_dead_at"] is None


@pytest.mark.parametrize("code,attempts", [("timeout", 1), ("concurrency_unavailable", 0)])
def test_production_transport_and_busy_keep_conservative_retry_accounting(
    production_stage, code, attempts
):
    main, _quest, _plan, notice, _ = production_stage

    class TransportFailure(ComposerMock):
        async def generate(self, request):
            self.requests.append(request)
            raise ProviderError(code)

    main.router.provider = mock = TransportFailure({})
    assert not asyncio.run(main.process_pending_team())
    saved = notice_row(main, notice["event_id"])
    assert saved["notice_attempts"] == attempts and saved["notice_dead_at"] is None
    assert saved["notice_token"] is None and len(mock.requests) == 1
    assert rejection_events(main) == []


def test_selected_provider_capacity_is_rechecked_after_semantic_binding(production_stage):
    main, quest, plan, _notice, _ = production_stage
    proposal = semantic_team(plan)
    proposal["roles"][0]["responsibility"] = "long role contract " * 100
    # Full original source/output bounds and budget are retained. A contract
    # exceeding funded framing is rejected by the ordinary native preflight.
    roles, limits, strategy = SemanticTeamWire.model_validate(proposal).inputs(
        plan, operator_max_concurrency=main.native_worker_concurrency
    )
    with pytest.raises(DomainError, match="complete Worker/review/correction allowance"):
        main.team_service.propose(quest.id, roles, limits, strategy=strategy, expected_version=2)
