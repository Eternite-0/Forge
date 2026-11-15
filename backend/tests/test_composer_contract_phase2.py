"""Explicit schema selection and exact role IDs; scripted Composer, no Provider calls."""

import asyncio
import copy
import json

import jsonschema
import pytest
from pydantic import ValidationError
from test_composer_repair_phase2 import ComposerMock, notice_row, team_wire

from forge_core.team_composition import PRESET_ROLES, RoleSpec, TeamWire
from forge_core.worker_schema import STRICT_DELIVERY_OUTPUT_SCHEMA

pytest_plugins = ["test_composer_repair_phase2"]


def dynamic_wire(role_id="report"):
    proposal = team_wire()
    proposal["roles"][0].update(id=role_id, dynamic=True)
    return proposal


@pytest.mark.parametrize(
    "role_id", ["research", "math", "architecture", "coding", "experiment", "report", "review"]
)
def test_explicit_null_selects_core_schema_without_aliasing_or_changing_scope(role_id):
    proposal = dynamic_wire(role_id)
    jsonschema.validate(proposal, TeamWire.model_json_schema())
    roles, limits, strategy = TeamWire.model_validate(proposal).inputs()
    role = roles[0]
    assert role.id == role_id and role.dynamic is True
    assert role.output_schema == STRICT_DELIVERY_OUTPUT_SCHEMA
    assert role.responsibility == proposal["roles"][0]["responsibility"]
    assert role.input_contract == proposal["roles"][0]["input_contract"]
    assert role.acceptance_criteria == ["delivery_review"]
    assert role.tools == [] and limits.allowed_tools == [] and strategy == "greedy"
    role.output_schema["properties"].clear()
    assert STRICT_DELIVERY_OUTPUT_SCHEMA["properties"]


@pytest.mark.parametrize(
    "missing",
    [
        "responsibility",
        "input_contract",
        "acceptance_criteria",
        "schema_omitted",
        "schema_blank",
        "schema_json_null",
        "schema_empty_object",
    ],
)
def test_explicit_schema_choice_does_not_fill_missing_dynamic_contract(missing):
    proposal = dynamic_wire()
    role = proposal["roles"][0]
    if missing == "schema_omitted":
        del role["output_schema_json"]
    elif missing == "schema_blank":
        role["output_schema_json"] = " "
    elif missing == "schema_json_null":
        role["output_schema_json"] = "null"
    elif missing == "schema_empty_object":
        role["output_schema_json"] = "{}"
    else:
        role[missing] = " " if missing == "responsibility" else []
    with pytest.raises((ValidationError, ValueError)):
        TeamWire.model_validate(proposal).inputs()


@pytest.mark.parametrize("role_id", ["architecture", "report", "review"])
def test_nonpreset_names_are_not_implicitly_aliased(role_id):
    assert role_id not in PRESET_ROLES
    proposal = dynamic_wire(role_id)
    proposal["roles"][0]["dynamic"] = False
    with pytest.raises(ValidationError, match="Unknown role requires a dynamic proposal"):
        TeamWire.model_validate(proposal).inputs()


def test_direct_dynamic_role_still_requires_explicit_output_contract():
    role = dynamic_wire()["roles"][0]
    role.pop("output_schema_json")
    role["required_capabilities"] = {"coding": 70.0}
    with pytest.raises(ValidationError, match="Dynamic role requires a complete contract"):
        RoleSpec.model_validate(role)


def test_strict_wire_requires_explicit_schema_field_while_local_preset_omission_is_compatible():
    proposal = team_wire()
    del proposal["roles"][0]["output_schema_json"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(proposal, TeamWire.model_json_schema())
    roles, _, _ = TeamWire.model_validate(proposal).inputs()
    assert roles[0].id == "coding" and roles[0].output_schema is None


def test_actual_composer_persists_explicit_dynamic_core_contract_without_starting_worker(
    composer_stage,
):
    main, quest, notice = composer_stage
    proposal = dynamic_wire()
    mock = ComposerMock(proposal)
    main.router.provider = mock
    assert asyncio.run(main.process_pending_team())
    assert len(mock.requests) == 1
    with main.store.connection() as connection:
        row = connection.execute(
            "SELECT * FROM team_proposals WHERE quest_id=?", (quest.id,)
        ).fetchone()
    member = json.loads(row["body_json"])["members"][0]
    assert row["status"] == "proposed" and member["dynamic"]
    assert (
        member["role_id"] == "report" and member["output_schema"] == STRICT_DELIVERY_OUTPUT_SCHEMA
    )
    assert member["input_contract"] == proposal["roles"][0]["input_contract"]
    assert member["tool_scope"] == [] and main.store.list_workers(quest.id) == []
    assert notice_row(main, notice["event_id"])["processed_at"] is not None


def test_composer_requires_model_to_repair_missing_dynamic_input_contract(composer_stage):
    main, quest, _ = composer_stage
    valid = dynamic_wire()
    invalid = copy.deepcopy(valid)
    invalid["roles"][0]["input_contract"] = []
    mock = ComposerMock(invalid, valid)
    main.router.provider = mock
    assert asyncio.run(main.process_pending_team())
    assert len(mock.requests) == 2
    feedback = json.loads(mock.requests[1].messages[-1]["content"])
    assert "Dynamic role requires a complete contract" in str(feedback["rejected_proposal_error"])
    with main.store.connection() as connection:
        rows = connection.execute(
            "SELECT body_json FROM team_proposals WHERE quest_id=?", (quest.id,)
        ).fetchall()
    assert len(rows) == 1
    assert (
        json.loads(rows[0][0])["members"][0]["input_contract"]
        == valid["roles"][0]["input_contract"]
    )
