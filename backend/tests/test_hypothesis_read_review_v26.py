"""Independent budget and actual-tool checks for approved recalled references."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from test_core_measured_serial_v17 import NativeFramingMock
from test_core_source_prefetch_v23 import prepared
from test_verified_capacity_v16 import prepared as capacity_workspace
from test_worker_intelligence_phase2 import delivery, response, tool

from forge_core.constraints import execution_constraints
from forge_core.quest_graph import QuestGraphService
from forge_core.tool_engine import ToolEngine


def test_unknown_hypothesis_admission_never_allocates_an_unbounded_file(tmp_path, monkeypatch):
    store, manager, model, worker = prepared(
        tmp_path,
        monkeypatch,
        [tool("file_read", {"path": "inputs/source.txt"}), response(delivery())],
        hypothesis_paths=["inputs/source.txt"],
        source="small",
    )
    opened = Path.open
    sizes = []
    target = tmp_path / "inputs/source.txt"
    target.write_text("x" * 70000, encoding="utf-8")

    class BoundedFile:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size=-1):
            sizes.append(size)
            assert 0 < size <= 65537
            return self.stream.read(size)

    def guarded_open(path, mode="r", *args, **kwargs):
        stream = opened(path, mode, *args, **kwargs)
        return BoundedFile(stream) if path == target and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["repairs"] == 1 and result["tool_spent"] == 0
    assert sizes == [65537]
    assert "x" * 70000 not in json.dumps(model.requests[-1].messages)
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0


@pytest.mark.parametrize("actual_text", ["newly observed after queuing", "x" * 4096])
def test_actual_hypothesis_read_rechecks_complete_receipt_cap_after_queued_file_change(
    tmp_path, monkeypatch, actual_text
):
    store, manager, model, worker = prepared(
        tmp_path,
        monkeypatch,
        [tool("file_read", {"path": "inputs/source.txt"}), response(delivery())],
        hypothesis_paths=["inputs/source.txt"],
        source="small admission observation",
    )
    perform = ToolEngine._perform
    changed = []

    def change_before_actual_read(self, intent, token, cancelled):
        if intent["tool_name"] == "file_read" and not changed:
            changed.append(True)
            (tmp_path / "inputs/source.txt").write_text(actual_text, encoding="utf-8")
        return perform(self, intent, token, cancelled)

    monkeypatch.setattr(ToolEngine, "_perform", change_before_actual_read)
    result = asyncio.run(manager.run(worker["id"]))
    assert changed
    with store.connection() as c:
        capsule = json.loads(c.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0])
        assert capsule["verified_input_inventory"] == []
        assert c.execute("SELECT count(*) FROM worker_core_source_reads").fetchone()[0] == 0
        intent = c.execute("SELECT state FROM action_intents").fetchone()
        receipt = json.loads(c.execute("SELECT result_json FROM action_receipts").fetchone()[0])
        if len(actual_text.encode()) > capsule["review_envelope"]["max_source_bytes"]:
            assert result["repairs"] == 1 and intent["state"] == "failed"
            assert actual_text not in json.dumps(receipt)
            assert actual_text not in json.dumps(model.requests[-1].messages)
        else:
            assert result["repairs"] == 0 and intent["state"] == "succeeded"
            assert receipt["content"] == actual_text
            assert receipt["sha256"] == hashlib.sha256(actual_text.encode()).hexdigest()
            assert actual_text in json.dumps(model.requests[-1].messages)


def test_normal_fixed_point_prices_empty_inventory_hypothesis_read_within_original_180k(tmp_path):
    store, quest = capacity_workspace(
        tmp_path,
        content='Apply the previous readiness rule. Task execution limits: {"max_total_tokens":180000,"max_nodes":1,"max_members":1,"max_concurrency":1,"max_duration_seconds":600}',
    )
    quest.goal = "Recover and inspect the recalled ledger at inputs/recalled.json before deciding."
    # Fixture setup before any proposal/approval, standing in for the sourced
    # Router goal. The original user message deliberately has no literal path.
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET goal=? WHERE id=?", (quest.goal, quest.id))
    with store.connection() as c:
        constraint = execution_constraints(c, quest.id)
    graph = {
        "nodes": [
            {
                "key": "read_recalled",
                "objective": "Read the recalled ledger and independently assess the prior decision rule.",
                "success_criteria": ["delivery_review"],
                "budget_tokens": 2049,
                "required_capabilities": {},
                "allowed_tools": ["file_read"],
                "expected_artifact": None,
                "max_attempts": 1,
                "required_input_paths": [],
                "required_predecessor_deliveries": [],
                "capacity_bounds": {
                    "max_source_receipt_bytes": 3074,
                    "max_artifact_bytes": 1,
                    "max_delivery_bytes": 2048,
                },
            }
        ],
        "edges": [],
        "max_total_tokens": 180000,
        "max_duration_seconds": 600,
        "coordination_reserve_tokens": 18000,
        "execution_constraints": constraint,
        "capacity_contract": {
            "version": "verified-capacity-v1",
            "input_inventory": [],
            "nodes": {},
            "input_acquisition_policy": "core-declared-source-prefetch-v1",
            "action_evidence_allowances": {"read_recalled": 2048},
            "hypothesis_read_paths": {"read_recalled": ["inputs/recalled.json"]},
        },
    }
    provider = NativeFramingMock({})
    service = QuestGraphService(store)
    observation = service._size_core_resources(store.get_quest(quest.id), graph, [], provider)
    service._capacity_preflight(store.get_quest(quest.id), graph, [], provider)
    measured = graph["capacity_contract"]["nodes"]["read_recalled"]
    assert observation["fixed_point_verified"] and 2 <= observation["iterations"] <= 8
    assert measured["initial_model_calls"] == 2
    assert len(measured["action_completion_stages"]) == 1
    assert measured["action_completion_stages"][0]["after_tool"] == "file_read"
    assert graph["nodes"][0]["budget_tokens"] + 18000 <= 180000
    assert graph["capacity_contract"]["input_inventory"] == []
    assert graph["capacity_contract"]["hypothesis_read_paths"] == {
        "read_recalled": ["inputs/recalled.json"]
    }
    assert not provider.requests and not (tmp_path / "inputs/recalled.json").exists()
    with store.connection() as c:
        assert c.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 0
