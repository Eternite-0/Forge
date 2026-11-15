"""Whole native delivery reads remain recoverable across bounded compaction."""

import asyncio
import json

import pytest
from test_predecessor_delivery_v17 import TOOL_NAME, prepared
from test_worker_intelligence_phase2 import delivery, response, tool

from forge_core.context_service import ContextService, _settled_receipted_exchange
from forge_core.domain import DomainError
from forge_core.settings import Workspace
from forge_core.store import Store


def test_complete_delivery_exchange_archives_and_reads_original_after_restart(tmp_path):
    store, manager, model, _, _, _, reader, expected = prepared(
        tmp_path,
        summary="Complete sourced analysis " + "中" * 380,
        reader_criteria=["delivery_review"],
        reader_write=False,
    )
    model.responses.extend(
        [tool(TOOL_NAME, {"node_key": "producer"}, ordinal=index) for index in range(1, 6)]
        + [response(delivery("Assessed all complete source claims."))]
    )
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        connection.execute("BEGIN")
        rows = connection.execute(
            "SELECT * FROM worker_messages WHERE run_id=? AND ordinal IN (6,7) ORDER BY ordinal",
            (reader["id"],),
        ).fetchall()
        assert _settled_receipted_exchange(store, connection, reader["id"], rows)
        original = {
            row["ordinal"]: row["message_json"]
            for row in connection.execute(
                "SELECT ordinal,message_json FROM worker_messages WHERE run_id=?",
                (reader["id"],),
            )
        }
        before_receipts = connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0]
    service = ContextService(store)
    compact = service.build_worker_context(
        reader["id"], input_budget=8000, source_readback_available=True
    )
    assert len(json.dumps(compact, ensure_ascii=False).encode("utf-8")) <= 8000
    reopened = ContextService(Store(store.path, workspace=Workspace.from_path(tmp_path)))
    with store.connection() as connection:
        archived = [
            row[0]
            for row in connection.execute(
                "SELECT source_id FROM context_archive_sources WHERE scope='worker' AND scope_id=?",
                (reader["id"],),
            )
        ]
        assert archived
        assert (
            connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0]
            == before_receipts
        )
    recovered = []
    for source_id in archived:
        row = reopened.read_source("worker", reader["id"], source_id)
        assert row["message_json"] == original[int(source_id)]
        message = json.loads(row["message_json"])
        if message.get("role") == "tool":
            recovered.append(json.loads(message["content"])["delivery"])
    assert recovered and all(item == expected for item in recovered)


def test_unbound_completed_delivery_tail_cannot_be_treated_as_settled(tmp_path):
    store, manager, model, _, _, _, reader, _ = prepared(
        tmp_path, reader_criteria=["delivery_review"], reader_write=False
    )
    model.responses.extend(
        [tool(TOOL_NAME, {"node_key": "producer"}), response(delivery("Assessed."))]
    )
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    with store.unit_of_work() as work:
        c = work.connection
        c.execute("UPDATE worker_tool_calls SET result_json='{}' WHERE run_id=?", (reader["id"],))
        rows = c.execute(
            "SELECT * FROM worker_messages WHERE run_id=? AND ordinal IN (2,3) ORDER BY ordinal",
            (reader["id"],),
        ).fetchall()
        assert not _settled_receipted_exchange(store, c, reader["id"], rows)
        from forge_core.independent_review import _packet

        run = c.execute("SELECT * FROM worker_runs WHERE id=?", (reader["id"],)).fetchone()
        with pytest.raises(DomainError):
            _packet(store, c, run)
