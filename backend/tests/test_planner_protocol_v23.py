"""Independent constructed V23 probes; no Provider calls or live acceptance."""

import asyncio
import copy
import json

import pytest
from pydantic import ValidationError
from test_core_measured_serial_v17 import NativeFramingMock
from test_planner_source_preview_v22 import dependency, setup
from test_planner_source_preview_v22 import node as old_node

from forge_core.planner_capacity_preview_v23 import preview_complete_capacity
from forge_core.planner_dataflow import DataflowGraphWire, DataflowNodeWire
from forge_core.planner_dataflow_v23 import DataflowGraphWireV23, DataflowNodeWireV23
from forge_core.quest_graph import InvalidGraphProposal, QuestGraphService


def node(*args, allowance=0, **kwargs):
    result = old_node(*args, **kwargs)
    result["capacity_bounds"]["action_evidence_allowance_bytes"] = allowance
    return result


def graph(*nodes):
    return {"allocation_mode": "core_measured", "nodes": list(nodes)}


@pytest.mark.parametrize(
    "kind",
    [
        "delivery_review",
        "json_review:@delivery",
        "json_review",
        "json_valid",
        "semantic_numeric:scheduler-v1",
    ],
)
def test_all_five_old_enum_values_compile_identically(kind):
    wire = graph(
        node(
            "old",
            expected="work/result.json",
            tools=["file_write"],
            kind=kind,
            bound=1024,
        )
    )
    options = {
        "resource_limits": {"max_total_tokens": 180000},
        "coordination_reserve_tokens": 18000,
        "verified_paths": [],
    }
    v23 = DataflowGraphWireV23.model_validate(wire).proposal_data(**options)
    assert v23.pop("capacity_contract") == {
        "input_acquisition_policy": "core-declared-source-prefetch-v1",
        "action_evidence_allowances": {"old": 0},
    }
    wire["nodes"][0]["capacity_bounds"].pop("action_evidence_allowance_bytes")
    assert v23 == DataflowGraphWire.model_validate(wire).proposal_data(**options)


@pytest.mark.parametrize("kind", ["json_review", "json_valid", "semantic_numeric:scheduler-v1"])
def test_exact_canonical_path_is_losslessly_decoded(kind):
    selected = node(
        "exact",
        expected="work/中文-result.json",
        tools=["file_write"],
        kind=kind + ":work/中文-result.json",
        bound=1024,
    )
    assert (
        DataflowNodeWireV23.model_validate(selected)
        .success_criteria[0]
        .criterion(selected["expected_artifact"])
        == selected["success_criteria"][0]["kind"]
    )
    with pytest.raises(ValidationError):
        DataflowNodeWire.model_validate(selected)


@pytest.mark.parametrize(
    "bad",
    [
        "unknown:work/result.json",
        "json_review:work/wrong.json",
        "json_review:WORK/result.json",
        "json_review:./work/result.json",
        "json_review:work\\result.json",
        "json_review:work/result.json ",
        "json_review:work/result.json:extra",
        "json_review:work/result.json\n",
    ],
)
def test_unknown_or_nonexact_path_never_gets_a_verifier(bad):
    selected = node(
        "wrong", expected="work/result.json", tools=["file_write"], kind=bad, bound=1024
    )
    with pytest.raises(ValidationError):
        DataflowNodeWireV23.model_validate(selected)


@pytest.mark.parametrize(
    "criteria",
    [
        [{"kind": "json_review"}, {"kind": "json_valid:work/result.json"}],
        [{"kind": "json_review"}, {"kind": "json_review:work/result.json"}],
        [{"kind": "json_review:work/result.json", "artifact_path": "work/result.json"}],
    ],
)
def test_mixed_or_duplicate_or_extra_path_forms_are_rejected(criteria):
    selected = node("mixed", expected="work/result.json", tools=["file_write"], bound=1024)
    selected["success_criteria"] = criteria
    with pytest.raises(ValidationError):
        DataflowNodeWireV23.model_validate(selected)


def preview(store, quest, selected, provider):
    from forge_core.capacity_preflight import discover_input_inventory

    return preview_complete_capacity(
        QuestGraphService(store),
        store.get_quest(quest.id),
        json.dumps(selected),
        discover_input_inventory(store, "inputs/unicode.json"),
        provider,
        resource_limits={
            "max_total_tokens": 630000,
            "max_cost_microunits": None,
            "cost_currency": None,
            "max_duration_seconds": None,
        },
        coordination_reserve_tokens=63000,
    )


def test_valid_nodes_get_all_raw_native_correction_metrics_while_bad_criteria_stay_unknown(
    tmp_path, monkeypatch
):
    store, quest, _inventory = setup(tmp_path)
    selected = graph(
        node(
            "producer",
            expected="work/producer.json",
            inputs=["inputs/unicode.json"],
            tools=["file_read", "file_write", "file_revise"],
            kind="json_review:work/producer.json",
            bound=32768,
        ),
        node(
            "unknown",
            predecessors=[dependency("producer", artifact=False)],
            tools=["worker_read_completed_delivery"],
            kind="PRIVATE_UNKNOWN",
            bound=1024,
        ),
    )
    original = copy.deepcopy(selected)
    provider = NativeFramingMock(selected)

    def forbidden(*args, **kwargs):
        raise AssertionError("diagnostic attempted a write or external call")

    monkeypatch.setattr(store, "unit_of_work", forbidden)
    monkeypatch.setattr(provider, "generate", forbidden)
    report = preview(store, quest, selected, provider)
    assert report["status"] == "available", report
    producer, unknown = report["nodes"]
    assert producer["status"] == "available", report
    candidate = producer["candidates"][0]
    assert candidate["raw_packet_bound"] > 65536
    assert candidate["review_input_bound"] > 65536
    assert candidate["correction_input_bound"] > 65536
    assert candidate["native_envelope_fixed_point_iterations"] >= 2
    assert set(candidate["violations"]) == {
        "review_envelope_bounds_invalid",
        "raw_review_packet_limit_exceeded",
        "selected_model_context_exceeded",
    }
    assert unknown == {
        "node": "unknown",
        "status": "unavailable",
        "reason": "invalid_criteria_node_not_measured",
    }
    assert "PRIVATE_" not in json.dumps(report)
    assert selected == original and provider.requests == []


def test_first_rejection_combines_schema_and_all_independent_capacity_measurements(
    tmp_path,
):
    store, quest, _inventory = setup(tmp_path)
    selected = graph(
        node(
            "over",
            expected="work/report.json",
            tools=["file_write"],
            kind="json_review:work/report.json",
            bound=32768,
        ),
        node("invalid", kind="PRIVATE_UNKNOWN", bound=1024),
    )
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
                output_limit=8192,
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "test_fixture"},
                require_capacity_contract=True,
                resource_allocation_mode="core_measured",
                semantic_protocol_version="v23",
            )
        )
    assert len(provider.requests) == 2
    repair = json.loads(provider.requests[1].messages[-1]["content"])
    assert repair["rejected_proposal_error"]
    full = repair["capacity_diagnostics"]["complete_capacity_preview"]
    assert full["status"] == "available", full
    assert full["nodes"][0]["candidates"][0]["correction_input_bound"] > 65536
    assert full["nodes"][1]["status"] == "unavailable"
    assert provider.requests[1].messages[-2]["content"] == json.dumps(selected)
    assert QuestGraphService(store).history(quest.id) == []


def roundtrip_plan(tmp_path, *, inputs=True, policy="native-input-roundtrip-v1", allowance=0):
    from forge_core.capacity_preflight import discover_input_inventory
    from forge_core.source_capacity import bind_source_receipt_bounds

    store, quest, _inventory = setup(tmp_path)
    selected = graph(
        node(
            "analysis",
            inputs=["inputs/unicode.json"] if inputs else [],
            tools=["file_read"],
            bound=512,
            allowance=allowance,
        )
    )
    wire = DataflowGraphWireV23.model_validate(selected)
    inventory = discover_input_inventory(store, "inputs/unicode.json")
    data = wire.proposal_data(
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
        verified_paths=[item["path"] for item in inventory],
    )
    data["capacity_contract"]["input_acquisition_policy"] = policy
    if policy == "native-input-roundtrip-v1":
        data["capacity_contract"].pop("action_evidence_allowances")
    from forge_core.constraints import execution_constraints

    with store.connection() as connection:
        data["execution_constraints"] = execution_constraints(connection, quest.id)
    bind_source_receipt_bounds(
        store,
        data,
        inventory,
        action_evidence_allowances=data["capacity_contract"].get("action_evidence_allowances"),
    )
    provider = NativeFramingMock(selected)
    QuestGraphService(store)._size_core_resources(
        store.get_quest(quest.id), data, inventory, provider
    )
    return store, quest, data, inventory, provider


def test_input_roundtrip_counts_two_full_native_requests_and_preserves_original_review_reserve(
    tmp_path,
):
    from forge_core.capacity_preflight import node_review_envelope
    from forge_core.team_composition import (
        DEFAULT_OUTPUT_SCHEMA,
        CompositionLimits,
        RoleSpec,
        TeamComposerEngine,
    )

    store, quest, data, _inventory, provider = roundtrip_plan(tmp_path)
    assert data["capacity_contract"]["input_acquisition_policy"] == "native-input-roundtrip-v1"
    node = data["nodes"][0]
    item = data["capacity_contract"]["nodes"]["analysis"]
    acquisition = item["input_acquisition"]
    assert acquisition["required"] and item["initial_model_calls"] == 2
    assert acquisition["total_bound"] == acquisition["input_bound"] + acquisition["output_bound"]
    assert acquisition["request_measurement"]["native_input_reservation"] > 0
    assert (
        item["initial_work_tokens"]
        == acquisition["total_bound"] + item["worker_input_policy"] + item["output_bound"]
    )
    assert item["minimum_node_tokens"] == item["initial_work_tokens"] + item["review_cycle_reserve"]
    assert node["budget_tokens"] + data["coordination_reserve_tokens"] <= data["max_total_tokens"]
    role = RoleSpec(
        id="research",
        task_node_key="analysis",
        input_tokens=node["budget_tokens"] - 2048,
        output_tokens=2048,
        tools=node["allowed_tools"],
        responsibility=node["objective"],
        input_contract=node["required_input_paths"],
        output_schema=DEFAULT_OUTPUT_SCHEMA,
        acceptance_criteria=node["success_criteria"],
    )
    body = TeamComposerEngine().compose(
        [role],
        store.list_models(),
        CompositionLimits(
            max_members=1,
            max_concurrency=1,
            max_depth=1,
            max_total_tokens=node["budget_tokens"],
            allowed_tools=node["allowed_tools"],
        ),
        strategy="greedy",
    )
    body["members"][0]["input_acquisition_policy"] = "native-input-roundtrip-v1"
    with store.connection() as connection:
        envelope, normal = node_review_envelope(
            store,
            connection,
            store.get_quest(quest.id),
            data,
            node,
            body["members"][0],
            provider,
            resource_sizing=True,
        )
        diagnostic_envelope, diagnostics = node_review_envelope(
            store,
            connection,
            store.get_quest(quest.id),
            data,
            node,
            body["members"][0],
            provider,
            resource_sizing=True,
            _diagnostic_only=True,
        )
    assert envelope == diagnostic_envelope
    assert normal["worker_input_policy"] == diagnostics["worker_input_bound"]
    assert normal["initial_work_tokens"] == diagnostics["initial_work_tokens"]
    assert diagnostics["violations"] == []
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 0


def test_nodes_without_declared_input_reads_have_zero_acquisition_allowance(tmp_path):
    _store, _quest, data, _inventory, _provider = roundtrip_plan(tmp_path, inputs=False)
    item = data["capacity_contract"]["nodes"]["analysis"]
    assert item["initial_model_calls"] == 1
    assert item["input_acquisition"]["total_bound"] == 0
    assert item["input_acquisition"]["request_measurement"] is None
    assert item["initial_work_tokens"] == item["worker_input_policy"] + item["output_bound"]


def test_prefetch_native_first_request_contains_complete_core_source_data_without_model_selection(
    tmp_path, monkeypatch
):
    from forge_core.worker_prefetch import POLICY, PREFIX, source_message
    from forge_core.worker_prompts import measure_initial_worker_request

    observed = []

    def capture(*args, **kwargs):
        observed.extend(kwargs.get("extra_messages", []))
        return measure_initial_worker_request(*args, **kwargs)

    monkeypatch.setattr("forge_core.worker_prompts.measure_initial_worker_request", capture)
    store, _quest, data, _inventory, _provider = roundtrip_plan(
        tmp_path, policy=POLICY, allowance=1024
    )
    item = data["capacity_contract"]["nodes"]["analysis"]
    assert item["input_acquisition"]["total_bound"] == 0
    assert item["initial_model_calls"] == 1
    assert item["initial_work_tokens"] == item["worker_input_policy"] + item["output_bound"]
    assert item["minimum_node_tokens"] == item["initial_work_tokens"] + item["review_cycle_reserve"]
    source_blocks = [message for message in observed if message["content"].startswith(PREFIX)]
    assert source_blocks and all(message["role"] == "user" for message in source_blocks)
    assert not any(message["role"] in {"assistant", "tool"} for message in observed)
    entry = json.loads(source_blocks[0]["content"][len(PREFIX) :])["sources"][0]
    source = (store.workspace.root / "inputs/unicode.json").read_text(encoding="utf-8")
    assert entry["output"]["content"] == source
    assert source_message([entry]) == source_blocks[0]
    assert entry["tool_name"] == "file_read" and len(entry["call_id"]) == 44
    with store.connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM worker_core_source_reads").fetchone()[0] == 0
        )
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("bad", [True, -1, 32769, 1.5, "1024", None])
def test_action_evidence_allowance_is_explicit_bounded_integer(bad):
    selected = node("analysis")
    selected["capacity_bounds"]["action_evidence_allowance_bytes"] = bad
    with pytest.raises(ValidationError):
        DataflowNodeWireV23.model_validate(selected)


def test_action_evidence_allowance_contributes_only_to_original_complete_source_cap(
    tmp_path,
):
    from forge_core.capacity_preflight import validate_plan_contract
    from forge_core.domain import DomainError
    from forge_core.source_capacity import (
        SOURCE_METADATA_RESERVE_BYTES,
        measure_required_sources,
    )
    from forge_core.worker_prefetch import POLICY

    store, quest, data, inventory, _provider = roundtrip_plan(
        tmp_path, policy=POLICY, allowance=2048
    )
    node = data["nodes"][0]
    measured = measure_required_sources(store, data, node, inventory)
    assert node["capacity_bounds"]["max_source_receipt_bytes"] == (
        measured.required_source_receipt_bytes + SOURCE_METADATA_RESERVE_BYTES + 2048
    )
    assert "action_evidence_allowance_bytes" not in node["capacity_bounds"]
    with store.connection() as connection:
        validate_plan_contract(store, connection, quest.id, data)
        data["capacity_contract"]["action_evidence_allowances"]["analysis"] += 1
        with pytest.raises(DomainError, match="differs from complete source bound"):
            validate_plan_contract(store, connection, quest.id, data)


@pytest.mark.parametrize("state", ["pending", "running", "uncertain", "failed"])
def test_unsettled_core_sources_prevent_remaining_plan_and_team_reassignment(tmp_path, state):
    from test_remaining_dag_v16 import prepared

    from forge_core.domain import DomainError

    store, quest, _plan, raw, _roles, _limits, teams, _manager, runs = prepared(tmp_path)
    with store.unit_of_work() as work:
        # Deliberately unsettled constructed record: never execution evidence.
        work.connection.execute(
            "INSERT INTO worker_core_source_reads "
            "(id,run_id,ordinal,tool_name,arguments_json,binding_json,binding_digest,state) "
            "VALUES (?,?,0,'file_read','{}','{}',?,?)",
            ("core_source_" + "0" * 32, runs[1]["id"], "0" * 64, state),
        )
    before = store.get_quest(quest.id)
    with pytest.raises(DomainError, match="settled|unsettled"):
        QuestGraphService(store).propose_remaining(
            quest.id, raw, expected_version=before["version"]
        )
    with store.connection() as connection, pytest.raises(DomainError, match="settled"):
        teams._reassignment_target(connection, quest.id, runs[1]["id"])
    assert store.get_quest(quest.id) == before


def test_new_source_sizing_never_turns_action_allowance_into_an_unbounded_source_cap(
    tmp_path,
):
    from forge_core.capacity_preflight import (
        CapacityPreflightError,
        discover_input_inventory,
    )
    from forge_core.source_capacity import bind_source_receipt_bounds

    store, _quest, _inventory = setup(tmp_path)
    (store.workspace.root / "inputs/unicode.json").write_text("x" * 33000, encoding="utf-8")
    selected = graph(
        node("read", inputs=["inputs/unicode.json"], tools=["file_read"], allowance=32768)
    )
    inventory = discover_input_inventory(store, "inputs/unicode.json")
    data = DataflowGraphWireV23.model_validate(selected).proposal_data(
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
        verified_paths=[item["path"] for item in inventory],
    )
    before = copy.deepcopy(data)
    with pytest.raises(CapacityPreflightError, match="exceeds 65536"):
        bind_source_receipt_bounds(
            store,
            data,
            inventory,
            action_evidence_allowances=data["capacity_contract"]["action_evidence_allowances"],
        )
    assert data == before


def test_production_background_planner_selects_prefetch_contract_with_explicit_v23_fixture(
    tmp_path, monkeypatch
):
    import importlib
    import secrets
    from dataclasses import replace

    from planner_capacity_fixtures import configure_planner_capacity
    from test_event_dispatcher import drain_dispatcher
    from test_router_intelligence_phase2 import ScriptedMock

    from forge_core.domain import ForgeEvent, Quest
    from forge_core.provider_pool import ProviderPool

    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FORGE_MODEL_TOKEN_LIMIT", "630000")
    for name in (
        "FORGE_ROUTER_MODEL_ID",
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="Explicit V23 production protocol", goal="Inspect a bounded input")
    main.store.save_quest(quest)
    selected = graph(node("analysis", bound=512, allowance=0))
    provider = ScriptedMock(selected)
    configure_planner_capacity(main, provider)
    provider.config = replace(provider.config, supports_tools=True, supports_json_schema=True)
    main.router.model_id = "mock"
    main.quest_graph.capacity_providers = ProviderPool({"planner-fixture": provider})
    original_generate = main.quest_graph.generate

    async def explicit_legacy_v23(*args, **kwargs):
        kwargs["semantic_protocol_version"] = "v23"
        return await original_generate(*args, **kwargs)

    monkeypatch.setattr(main.quest_graph, "generate", explicit_legacy_v23)
    main.store.append_event(ForgeEvent(event_type="quest.proposed", quest_id=quest.id, payload={}))
    drain_dispatcher(main.dispatcher)
    assert asyncio.run(main.process_pending_plan())
    plan = main.quest_graph.get(quest.id)
    assert plan["status"] == "proposed"
    assert (
        plan["capacity_contract"]["input_acquisition_policy"] == "core-declared-source-prefetch-v1"
    )
    assert plan["capacity_contract"]["action_evidence_allowances"] == {"analysis": 0}
    assert len(provider.requests) == 1
    with main.store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0


def test_cumulative_roundtrip_that_cannot_fit_original_ceiling_is_rejected_without_shrinking_bounds(
    tmp_path,
):
    from forge_core.capacity_preflight import CapacityPreflightError

    store, quest, data, inventory, provider = roundtrip_plan(tmp_path)
    original_bounds = copy.deepcopy(data["nodes"][0]["capacity_bounds"])
    required = data["nodes"][0]["budget_tokens"] + data["coordination_reserve_tokens"]
    data["max_total_tokens"] = required - 1
    with pytest.raises(CapacityPreflightError, match="original task ceiling") as failure:
        QuestGraphService(store)._size_core_resources(
            store.get_quest(quest.id), data, inventory, provider
        )
    assert failure.value.capacity_diagnostics["original_ceiling"] == required - 1
    assert data["nodes"][0]["capacity_bounds"] == original_bounds


def test_normal_preflight_still_rejects_a_packet_which_diagnostic_only_can_measure(
    tmp_path,
):
    from forge_core.capacity_preflight import (
        CapacityPreflightError,
        discover_input_inventory,
    )
    from forge_core.source_capacity import bind_source_receipt_bounds

    store, quest, _inventory = setup(tmp_path)
    selected = graph(
        node(
            "over",
            expected="work/over.json",
            tools=["file_write"],
            kind="json_review:work/over.json",
            bound=32768,
        )
    )
    inventory = discover_input_inventory(store, "inputs/unicode.json")
    data = DataflowGraphWireV23.model_validate(selected).proposal_data(
        resource_limits={"max_total_tokens": 630000},
        coordination_reserve_tokens=63000,
        verified_paths=[item["path"] for item in inventory],
    )
    bind_source_receipt_bounds(store, data, inventory)
    provider = NativeFramingMock(selected)
    report = preview(store, quest, selected, provider)
    assert report["nodes"][0]["candidates"][0]["raw_packet_bound"] > 65536
    with pytest.raises(
        CapacityPreflightError, match="No eligible model fits complete node capacity"
    ):
        QuestGraphService(store)._size_core_resources(
            store.get_quest(quest.id), data, inventory, provider
        )
    assert "over" not in data["capacity_contract"]["nodes"]
