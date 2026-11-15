"""Native Composer contracts enforced by real local Worker admission and repair."""

import asyncio
import copy
import json
import math

import jsonschema
import pytest
from pydantic import ValidationError
from test_composer_repair_phase2 import team_wire
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response

from forge_core.domain import ModelProfile, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import (
    DEFAULT_OUTPUT_SCHEMA,
    RoleSpec,
    TeamCompositionService,
    TeamWire,
)
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager
from forge_core.worker_schema import (
    DELIVERY_FIELDS,
    DELIVERY_OUTPUT_SCHEMA,
    STRICT_DELIVERY_OUTPUT_SCHEMA,
)


def native_worker(tmp_path, responses, *, criterion="Done", contract="native", max_repairs=2):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Native delivery contract", goal="Deliver the approved bounded result")
    store.save_quest(quest)
    graphs = QuestGraphService(store)
    plan = graphs.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": quest.goal,
                    "success_criteria": [criterion],
                    "budget_tokens": 60000,
                    "allowed_tools": [],
                }
            ],
            "edges": [],
            "max_total_tokens": 60000,
        },
        expected_version=0,
    )
    graphs.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    store.save_model(
        ModelProfile(
            id="protocol",
            provider="protocol",
            model_id="fixture",
            display_name="Offline native contract fixture",
            context_window=64000,
            max_output_tokens=4000,
        )
    )
    proposal = team_wire()
    proposal["roles"][0].update(
        required_capabilities=[],
        input_tokens=30000,
        output_tokens=3000,
        max_steps=6,
        max_duration_seconds=120,
        acceptance_criteria=[criterion],
    )
    proposal["limits"]["max_total_tokens"] = 33000
    if contract == "legacy_string":
        proposal["roles"][0]["output_schema_json"] = json.dumps(DELIVERY_OUTPUT_SCHEMA)
    roles, limits, strategy = TeamWire.model_validate(proposal).inputs()
    if contract == "legacy_direct":
        roles = [
            RoleSpec(
                id="coding",
                task_node_key="n",
                input_tokens=30000,
                output_tokens=3000,
                max_steps=6,
                max_tool_calls=0,
                max_duration_seconds=120,
            )
        ]
    teams = TeamCompositionService(store)
    team = teams.propose(quest.id, roles, limits, strategy=strategy, expected_version=2)
    teams.approve(quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user")
    model = ProtocolModel(responses)
    manager = WorkerManager(
        store,
        ProviderPool({"protocol": model}),
        ToolEngine(store),
        team_authority=teams,
        max_repairs=max_repairs,
    )
    worker = manager.start(
        quest.id, plan["id"], "n", team_digest=team["proposal_digest"], member_index=0
    )
    return store, manager, model, worker, team


def test_native_schema_is_separate_from_unchanged_legacy_default():
    assert DEFAULT_OUTPUT_SCHEMA is DELIVERY_OUTPUT_SCHEMA
    assert DELIVERY_OUTPUT_SCHEMA["required"] == ["summary"]
    assert STRICT_DELIVERY_OUTPUT_SCHEMA["required"] == list(DELIVERY_FIELDS)
    assert STRICT_DELIVERY_OUTPUT_SCHEMA["properties"]["confidence"]["minimum"] == 0
    assert STRICT_DELIVERY_OUTPUT_SCHEMA["properties"]["confidence"]["maximum"] == 1
    assert "minimum" not in DELIVERY_OUTPUT_SCHEMA["properties"]["confidence"]
    assert STRICT_DELIVERY_OUTPUT_SCHEMA["properties"] is not DELIVERY_OUTPUT_SCHEMA["properties"]
    jsonschema.Draft202012Validator.check_schema(STRICT_DELIVERY_OUTPUT_SCHEMA)
    jsonschema.validate({"summary": "old contract"}, DELIVERY_OUTPUT_SCHEMA)


@pytest.mark.parametrize("field", DELIVERY_FIELDS)
def test_native_schema_requires_each_model_supplied_field_without_default_insertion(field):
    value = delivery()
    del value[field]
    before = copy.deepcopy(value)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, STRICT_DELIVERY_OUTPUT_SCHEMA)
    assert value == before and field not in value


@pytest.mark.parametrize("confidence", [0, 0.8, 1])
def test_native_schema_accepts_confidence_bounds(confidence):
    value = delivery()
    value["confidence"] = confidence
    jsonschema.validate(value, STRICT_DELIVERY_OUTPUT_SCHEMA)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, True, "0.8", None])
def test_native_schema_rejects_invalid_confidence(confidence):
    value = delivery()
    value["confidence"] = confidence
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, STRICT_DELIVERY_OUTPUT_SCHEMA)


@pytest.mark.parametrize(
    "criterion",
    ["Done", "json_review:work/report.json", "numeric_self_test:work/code.py", "delivery_review"],
)
def test_native_worker_rejects_summary_then_repairs_complete_delivery_for_any_criterion(
    tmp_path, criterion
):
    store, manager, model, worker, team = native_worker(
        tmp_path, [response({"summary": "incomplete"}), response(delivery())], criterion=criterion
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert team["members"][0]["output_schema"] == STRICT_DELIVERY_OUTPUT_SCHEMA
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert result["steps"] == 2 and result["token_spent"] == 80
    feedback = json.loads(model.requests[1].messages[-1]["content"])["repair_request"]
    assert feedback["reason_code"] == "invalid_output_contract"
    assert feedback["detail"]["schema"] == "output_schema"
    events = store.list_events(limit=100)
    accepted = [event for event in events if event["event_type"] == "worker.result"]
    assert len(accepted) == 1
    payload = json.loads(accepted[0]["payload"])
    assert json.loads(payload["claim"]) == delivery()
    assert payload["delivery_fields_supplied"] == list(DELIVERY_FIELDS)


def test_native_incomplete_output_stops_at_existing_repair_budget_without_result(tmp_path):
    store, manager, model, worker, _ = native_worker(
        tmp_path, [response({"summary": "still incomplete"})] * 3
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == 2
    assert result["steps"] == 3 and result["token_spent"] == 120
    assert len(model.requests) == 3
    assert not any(event["event_type"] == "worker.result" for event in store.list_events(limit=100))


@pytest.mark.parametrize("confidence", [-0.1, 1.1, True, float("nan"), float("inf"), -float("inf")])
def test_native_worker_repairs_out_of_range_confidence_without_delivery_review(
    tmp_path, confidence
):
    invalid = delivery()
    invalid["confidence"] = confidence
    _store, manager, model, worker, _ = native_worker(
        tmp_path, [response(invalid), response(delivery())]
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    feedback = json.loads(model.requests[1].messages[-1]["content"])["repair_request"]
    if isinstance(confidence, float) and not math.isfinite(confidence):
        assert feedback["detail"]["error"] == "ValueError"
    else:
        assert feedback["detail"]["path"] == "confidence"


@pytest.mark.parametrize("contract", ["legacy_string", "legacy_direct"])
def test_legacy_explicit_or_direct_contract_still_accepts_summary_without_repair(
    tmp_path, contract
):
    store, manager, model, worker, team = native_worker(
        tmp_path, [response({"summary": "legacy result"})], contract=contract
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert team["members"][0]["output_schema"] == DELIVERY_OUTPUT_SCHEMA
    assert result["state"] == "review_pending" and result["repairs"] == 0
    assert len(model.requests) == 1
    accepted = next(
        event for event in store.list_events(limit=100) if event["event_type"] == "worker.result"
    )
    assert json.loads(accepted["payload"])["delivery_fields_supplied"] == ["summary"]


@pytest.mark.parametrize("bound", [True, "0", None, float("nan"), float("inf")])
def test_output_numeric_bounds_reject_nonfinite_or_nonnumeric_schema(bound):
    schema = copy.deepcopy(STRICT_DELIVERY_OUTPUT_SCHEMA)
    schema["properties"]["confidence"]["minimum"] = bound
    with pytest.raises(ValidationError, match="finite numbers|not JSON compliant"):
        RoleSpec(id="coding", input_tokens=100, output_tokens=100, output_schema=schema)


def test_output_numeric_bounds_reject_inverted_range():
    schema = copy.deepcopy(STRICT_DELIVERY_OUTPUT_SCHEMA)
    schema["properties"]["confidence"].update(minimum=2, maximum=1)
    with pytest.raises(ValidationError, match="inverted"):
        RoleSpec(id="coding", input_tokens=100, output_tokens=100, output_schema=schema)
