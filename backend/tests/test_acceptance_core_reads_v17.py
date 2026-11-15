"""Acceptance compatibility over native local Core reads, never live model evidence."""

import asyncio
import hashlib
import json

import pytest
from test_acceptance_checks_phase2 import module, publish
from test_predecessor_delivery_v17 import prepared as completed_prepared
from test_receipt_read_repair_phase2 import Protocol, dependent
from test_worker_intelligence_phase2 import delivery, response, tool
from test_worker_request_framing_v17 import native_archive_worker

from forge_core.evidence import NodeVerifier


def evaluated(store, root):
    publish(store, root)
    database = root / "data/forge.sqlite3"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    report = module("acceptance_checks").evaluate_scenario(
        {**module("acceptance_scenarios").describe_scenario(3), "input_manifest": []}, {}, root
    )
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    return report


@pytest.mark.parametrize("tamper", [False, True])
def test_evaluator_revalidates_complete_delivery_instead_of_demanding_action_receipt(
    tmp_path, tamper
):
    store, manager, model, quest, _plan, producer, reader, _expected = completed_prepared(tmp_path)
    model.responses.extend(
        [
            tool("worker_read_completed_delivery", {"node_key": "producer"}),
            tool("file_write", {"path": "work/review.json", "content": '{"checked":true}'}, 2),
            response(delivery("Read the full original predecessor")),
        ]
    )
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    NodeVerifier(store).verify(quest.id, "reader")
    if tamper:
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE events SET payload=json_set(payload,'$.claim','{}') "
                "WHERE event_type='worker.result' AND worker_id=?",
                (producer["worker_id"],),
            )
    report = evaluated(store, tmp_path)
    expected = "failed" if tamper else "passed"
    assert report["checks"]["receipt_integrity"]["status"] == expected
    core = report["checks"]["core_context_read_integrity"]
    assert core["status"] == expected
    if not tamper:
        assert len(core["evidence"]["verified"]) == 1
        assert core["evidence"]["verified"][0]["tool_name"] == "worker_read_completed_delivery"
    # Core reads do not satisfy seeded file source, required final artifact or
    # independently reviewed final-report requirements of strict L3.
    assert report["checks"]["artifact_hashes"]["status"] != "passed"
    assert not report["passed"]


def test_evaluator_accepts_verified_receipt_read_without_counting_it_as_file_source(tmp_path):
    store, manager, model, quest, _, _, _, reader, receipts, _ = dependent(tmp_path)
    model.responses.extend(
        [
            tool("worker_read_action_receipt", {"receipt_id": receipts["file_write"]}),
            tool("file_write", {"path": "work/review.json", "content": '{"checked":true}'}, 2),
            response(delivery()),
        ]
    )
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    NodeVerifier(store).verify(quest.id, "reader")
    report = evaluated(store, tmp_path)
    assert report["checks"]["receipt_integrity"]["status"] == "passed"
    core = report["checks"]["core_context_read_integrity"]
    assert core["status"] == "passed"
    assert core["evidence"]["verified"][0]["tool_name"] == "worker_read_action_receipt"
    assert report["checks"]["input_sources_read"]["status"] != "passed"
    assert not report["passed"]


def test_evaluator_accepts_actual_archive_source_and_rejects_changed_source_hash(tmp_path):
    # This explicitly scripted adapter's live tag exercises the durable live
    # provenance gate. It performs no HTTP and is not real acceptance evidence.
    model = Protocol(
        [
            response(
                "",
                calls=[
                    {
                        "id": f"source-{n}",
                        "name": "file_read",
                        "arguments": {"path": f"work/source-{n}.txt"},
                    }
                    for n in (1, 2)
                ],
            ),
            tool("worker_read_context_source", {"ordinal": 3, "max_chars": 2048}),
            response(delivery()),
        ]
    )
    store, manager, worker = native_archive_worker(tmp_path, model, depth=0)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    report = evaluated(store, tmp_path)
    assert report["checks"]["receipt_integrity"]["status"] == "passed"
    core = report["checks"]["core_context_read_integrity"]
    assert core["status"] == "passed"
    assert core["evidence"]["verified"][0]["tool_name"] == "worker_read_context_source"
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT call_id,result_json FROM worker_tool_calls WHERE run_id=? AND tool_name=?",
            (worker["id"], "worker_read_context_source"),
        ).fetchone()
        result = json.loads(row["result_json"])
        result["source_sha256"] = "0" * 64
        work.connection.execute(
            "UPDATE worker_tool_calls SET result_json=? WHERE call_id=?",
            (json.dumps(result), row["call_id"]),
        )
    report = evaluated(store, tmp_path)
    assert report["checks"]["receipt_integrity"]["status"] == "failed"
    assert report["checks"]["core_context_read_integrity"]["status"] == "failed"
