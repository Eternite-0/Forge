"""Offline integration of real Core reads with compaction and read-only audit."""

import asyncio
import hashlib
import json

import pytest
from test_acceptance_checks_phase2 import module, publish
from test_core_source_prefetch_v23 import entries, prepared

from forge_core.context_service import ContextService
from forge_core.domain import DomainError
from forge_core.worker_prefetch import acquire, canonical, source_message


def acquired(tmp_path, monkeypatch, *, mode="mock"):
    store, manager, model, worker = prepared(tmp_path, monkeypatch, mode=mode)
    token = manager._claim(worker["id"])
    asyncio.run(acquire(manager, worker["id"], token))
    return store, manager, model, worker


def test_compaction_keeps_complete_verified_core_source_and_archived_readback(
    tmp_path, monkeypatch
):
    store, _manager, model, worker = acquired(tmp_path, monkeypatch)
    expected = source_message(entries(store, worker))
    with store.unit_of_work() as work:
        first = work.connection.execute(
            "SELECT max(ordinal)+1 FROM worker_messages WHERE run_id=?", (worker["id"],)
        ).fetchone()[0]
        for index in range(60):
            message = {"role": "user", "content": f"Offline history {index}: " + "x" * 1000}
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,'user',?)",
                (worker["id"], first + index, canonical(message)),
            )
    service = ContextService(store)
    complete = service.build_worker_context(worker["id"], input_budget=200000)
    assert expected in complete
    fixed = sum(len(canonical(item).encode()) for item in complete[:3])
    compacted = service.build_worker_context(
        worker["id"], input_budget=fixed + 14000, source_readback_available=True
    )
    assert expected in compacted
    assert len(canonical(compacted).encode()) <= fixed + 14000
    with store.connection() as connection:
        archived = connection.execute(
            "SELECT source_id FROM context_archive_sources WHERE scope='worker' "
            "AND scope_id=? ORDER BY cast(source_id AS INTEGER)",
            (worker["id"],),
        ).fetchall()
        source_ordinal = connection.execute(
            "SELECT message_ordinal FROM worker_core_source_reads WHERE run_id=?", (worker["id"],)
        ).fetchone()[0]
    assert archived
    assert str(source_ordinal) not in {row[0] for row in archived}
    restored = service.read_source("worker", worker["id"], archived[0][0])
    assert json.loads(restored["message_json"])["content"].startswith("Offline history ")
    again = service.build_worker_context(worker["id"], input_budget=fixed + 14000)
    assert expected in again
    assert not model.requests


@pytest.mark.parametrize("fault", ["body", "role", "missing"])
def test_context_rejects_changed_core_source_instead_of_pinning_a_prefix(
    tmp_path, monkeypatch, fault
):
    store, _manager, _model, worker = acquired(tmp_path, monkeypatch)
    with store.unit_of_work() as work:
        ordinal = work.connection.execute(
            "SELECT message_ordinal FROM worker_core_source_reads WHERE run_id=?", (worker["id"],)
        ).fetchone()[0]
        if fault == "missing":
            work.connection.execute(
                "DELETE FROM worker_messages WHERE run_id=? AND ordinal=?", (worker["id"], ordinal)
            )
        elif fault == "role":
            work.connection.execute(
                "UPDATE worker_messages SET role='assistant' WHERE run_id=? AND ordinal=?",
                (worker["id"], ordinal),
            )
        else:
            forged = {"role": "user", "content": "Core declared source reads: forged data"}
            work.connection.execute(
                "UPDATE worker_messages SET message_json=? WHERE run_id=? AND ordinal=?",
                (canonical(forged), worker["id"], ordinal),
            )
    with pytest.raises(DomainError):
        ContextService(store).build_worker_context(worker["id"], input_budget=65536)


@pytest.mark.parametrize("tamper", [False, True])
def test_acceptance_revalidates_core_receipts_without_inventing_model_tool_calls(
    tmp_path, monkeypatch, tamper
):
    # Explicitly scripted live-mode protocol data exercises gates; no Provider is called.
    store, manager, model, worker = prepared(tmp_path, monkeypatch, mode="live")
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert len(model.requests) == 1
    if tamper:
        with store.unit_of_work() as work:
            ordinal = work.connection.execute(
                "SELECT message_ordinal FROM worker_core_source_reads WHERE run_id=?",
                (worker["id"],),
            ).fetchone()[0]
            work.connection.execute(
                "UPDATE worker_messages SET message_json='{}' WHERE run_id=? AND ordinal=?",
                (worker["id"], ordinal),
            )
    publish(store, tmp_path)
    database = tmp_path / "data/forge.sqlite3"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    inspection = module("live_e2e").inspect_execution(tmp_path)
    assert inspection["tool_calls"] == 1
    assert inspection["tools"][0]["origin"] == "core_prefetch"
    assert inspection["model_calls"] == 0
    report = module("acceptance_checks").evaluate_scenario(
        {**module("acceptance_scenarios").describe_scenario(3), "input_manifest": []},
        inspection,
        tmp_path,
    )
    expected = "failed" if tamper else "passed"
    assert report["checks"]["receipt_integrity"]["status"] == expected
    assert report["checks"]["core_context_read_integrity"]["status"] == expected
    assert not report["passed"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
