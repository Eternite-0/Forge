"""Approved unverified source paths require a new actual governed observation."""

import asyncio
import copy
import json

import pytest
from test_core_source_prefetch_v23 import prepared
from test_worker_intelligence_phase2 import delivery, response, tool

from forge_core.domain import DomainError
from forge_core.hypothesis_sources import (
    validate_graph,
    validate_member,
    validate_paths,
    validate_quest_hypotheses,
)
from forge_core.planner_input_roundtrip import action_completion_stages


def graph():
    return {
        "nodes": [{"key": "n", "allowed_tools": ["file_read"], "required_input_paths": []}],
        "capacity_contract": {
            "input_acquisition_policy": "core-declared-source-prefetch-v1",
            "hypothesis_read_paths": {"n": ["inputs/recalled.json"]},
            "action_evidence_allowances": {"n": 2048},
            "input_inventory": [],
        },
    }


@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", "C:/root", "a\\b", "a//b", "a/./b", "a:b", "a\n", "a.", "a "]
)
def test_nonexact_or_escaping_hypothesis_path_rejected(path):
    with pytest.raises(DomainError):
        validate_paths([path])


@pytest.mark.parametrize(
    "fault",
    ["missing_node", "extra_node", "zero_allowance", "no_tool", "verified", "duplicate", "legacy"],
)
def test_unapproved_or_unfunded_hypothesis_scope_is_rejected(fault):
    value = graph()
    contract = value["capacity_contract"]
    if fault == "missing_node":
        contract["hypothesis_read_paths"] = {}
    elif fault == "extra_node":
        contract["hypothesis_read_paths"]["other"] = []
    elif fault == "zero_allowance":
        contract["action_evidence_allowances"]["n"] = 0
    elif fault == "no_tool":
        value["nodes"][0]["allowed_tools"] = []
    elif fault == "verified":
        contract["input_inventory"] = [{"path": "inputs/recalled.json"}]
    elif fault == "duplicate":
        contract["hypothesis_read_paths"]["n"] *= 2
    else:
        contract["input_acquisition_policy"] = "native-input-roundtrip-v1"
    with pytest.raises(DomainError):
        validate_graph(value)


def test_member_cannot_gain_or_change_explicit_hypothesis_paths():
    value = graph()
    member = {"task_node_key": "n", "hypothesis_read_paths": ["inputs/recalled.json"]}
    validate_graph(value)
    validate_member(value, member)
    changed = copy.deepcopy(member)
    changed["hypothesis_read_paths"] = ["inputs/different.json"]
    with pytest.raises(DomainError):
        validate_member(value, changed)
    value["capacity_contract"].pop("hypothesis_read_paths")
    with pytest.raises(DomainError):
        validate_member(value, member)
    validate_member(value, {"task_node_key": "n"})


def test_unmentioned_path_cannot_be_smuggled_into_a_different_quest_goal(tmp_path, monkeypatch):
    store, _manager, _model, worker = prepared(
        tmp_path, monkeypatch, hypothesis_paths=["inputs/source.txt"]
    )
    with store.connection() as connection:
        with pytest.raises(DomainError, match="current Quest goal"):
            validate_quest_hypotheses(connection, worker["quest_id"], graph())
        assert connection.total_changes == 0


def test_hypothesis_capacity_prices_actual_followup_without_reading_file(tmp_path):
    value = graph()
    stages = action_completion_stages(value["nodes"][0], value, tmp_path)
    assert len(stages) == 1
    stage = stages[0]
    assert stage["after_tool"] == "file_read"
    assert stage["future_body_and_receipt_growth"] == 2 * 2048 + 1024
    assert json.loads(stage["extra_messages"][0]["tool_calls"][0]["function"]["arguments"]) == {
        "path": "inputs/recalled.json"
    }
    assert json.loads(stage["extra_messages"][1]["content"]) == {"content": "", "sha256": "0" * 64}
    assert not list(tmp_path.iterdir())


def test_approved_hypothesis_is_not_prefetched_or_verified_until_actual_model_read(
    tmp_path, monkeypatch
):
    store, manager, model, worker = prepared(
        tmp_path,
        monkeypatch,
        [tool("file_read", {"path": "inputs/source.txt"}), response(delivery())],
        hypothesis_paths=["inputs/source.txt"],
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending"
    assert result["steps"] == 2 and result["tool_spent"] == 1 and result["repairs"] == 0
    assert len(model.requests) == 2
    assert "hypothesis_read_paths" in model.requests[0].messages[0]["content"]
    assert "Original 原始 source" not in json.dumps(model.requests[0].messages, ensure_ascii=False)
    assert "Original 原始 source" in json.dumps(model.requests[1].messages, ensure_ascii=False)
    with store.connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM worker_core_source_reads").fetchone()[0] == 0
        )
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM worker_tool_calls WHERE state='succeeded'"
            ).fetchone()[0]
            == 1
        )
        capsule = json.loads(
            connection.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
        )
        assert capsule["verified_input_inventory"] == []
        assert capsule["required_input_paths"] == []
        assert capsule["hypothesis_read_paths"] == ["inputs/source.txt"]


def test_capsule_cannot_substitute_another_unverified_source(tmp_path, monkeypatch):
    store, manager, model, worker = prepared(
        tmp_path, monkeypatch, hypothesis_paths=["inputs/source.txt"]
    )
    with store.unit_of_work() as work:
        capsule = json.loads(
            work.connection.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
        )
        capsule["hypothesis_read_paths"] = ["inputs/unapproved.txt"]
        work.connection.execute("UPDATE worker_capsules SET capsule_json=?", (json.dumps(capsule),))
    with pytest.raises(DomainError, match="review contract differs"):
        manager._claim(worker["id"])
    assert not model.requests
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0


@pytest.mark.parametrize("fault", ["undeclared_path", "oversized_output"])
def test_hypothesis_read_keeps_exact_path_and_complete_source_byte_cap(
    tmp_path, monkeypatch, fault
):
    requested = "inputs/unapproved.txt" if fault == "undeclared_path" else "inputs/source.txt"
    store, manager, _model, worker = prepared(
        tmp_path,
        monkeypatch,
        [tool("file_read", {"path": requested}), response(delivery())],
        hypothesis_paths=["inputs/source.txt"],
        source="x" * 4096 if fault == "oversized_output" else "Original source",
    )
    (tmp_path / "inputs/unapproved.txt").write_text("not approved", encoding="utf-8")
    result = asyncio.run(manager.run(worker["id"]))
    assert result["repairs"] == 1 and result["tool_spent"] == 0
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0
        reason = json.loads(
            connection.execute(
                "SELECT payload FROM events WHERE event_type='worker.progress' AND json_extract(payload,'$.phase')='self_repair'"
            ).fetchone()[0]
        )["reason_code"]
        assert reason == "review_envelope_tool_batch"
