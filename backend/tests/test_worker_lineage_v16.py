"""Real local receipts, restart and completion across approved remaining DAGs.

The scripted protocol is offline and is never reported as a real model result.
"""

import asyncio
import json
import sqlite3

import pytest
from test_receipt_read_repair_phase2 import dependent
from test_worker_intelligence_phase2 import delivery, response, tool

from forge_core.domain import DomainError
from forge_core.evidence import CompletionGate, NodeVerifier
from forge_core.governance import GovernanceService
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.worker_runtime import _read_action_receipt


def replan(tmp_path):
    store, manager, model, quest, old, _, producer, stale, receipts, _ = dependent(tmp_path)
    with store.connection() as connection:
        original = dict(
            connection.execute("SELECT * FROM worker_runs WHERE id=?", (producer["id"],)).fetchone()
        )
        graph = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE id=?", (old["id"],)
            ).fetchone()[0]
        )
        allocation = connection.execute(
            "SELECT tokens_allocated FROM worker_budget_ledger WHERE scope='quest'"
        ).fetchone()[0]
    graph["nodes"][1]["objective"] = (
        "Read the preserved predecessor receipt and deliver an updated summary."
    )
    service = QuestGraphService(store)
    pending = service.propose_remaining(
        quest.id, graph, expected_version=store.get_quest(quest.id)["version"]
    )
    assert store.get_quest(quest.id)["plan_revision"] == 1
    service.approve(
        quest.id,
        pending["digest"],
        pending["plan_revision"],
        expected_version=store.get_quest(quest.id)["version"],
        actor="local_user",
    )
    assert manager.get(stale["id"])["state"] == "cancelled"
    assert manager.get(producer["id"]) == original
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT tokens_allocated FROM worker_budget_ledger WHERE scope='quest'"
            ).fetchone()[0]
            == allocation
            == 70000
        )
    # Reopening the actual workspace retains the original run and lineage.
    manager.store = store = Store(store.path, workspace=Workspace.from_path(tmp_path))
    teams = TeamCompositionService(store)
    manager.team_authority = teams
    team = teams.propose(
        quest.id,
        [
            {
                "id": "reviewer",
                "task_node_key": "reader",
                "tools": ["worker_read_action_receipt", "file_read", "file_write"],
                "input_tokens": 27000,
                "output_tokens": 3000,
                "max_steps": 8,
                "max_tool_calls": 6,
                "max_duration_seconds": 120,
            }
        ],
        {
            "max_members": 1,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 30000,
            "max_total_tool_calls": 6,
            "allowed_tools": ["worker_read_action_receipt", "file_read", "file_write"],
        },
        strategy="greedy",
        expected_version=store.get_quest(quest.id)["version"],
    )
    teams.approve(
        quest.id,
        pending["plan_revision"],
        team["proposal_digest"],
        expected_version=store.get_quest(quest.id)["version"],
        actor="local_user",
    )
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    reader = manager.start(
        quest.id, pending["id"], "reader", team_digest=team["proposal_digest"], member_index=0
    )
    return store, manager, model, quest, pending, producer, reader, receipts, original


def test_remaining_worker_reads_original_receipt_and_completes_after_reopen(tmp_path):
    store, manager, model, quest, plan, producer, reader, receipts, original = replan(tmp_path)
    model.responses.extend(
        [
            tool("worker_read_action_receipt", {"receipt_id": receipts["file_write"]}),
            tool(
                "file_write", {"path": "work/review.json", "content": '{"checked":true}'}, ordinal=2
            ),
            response(delivery("Verified the preserved original receipt.")),
        ]
    )
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    first = model.requests[3]
    task = json.loads(
        next(
            item["content"]
            for item in first.messages
            if item["role"] == "user" and "upstream_receipts" in item["content"]
        )
    )
    assert receipts["file_write"] in {
        item["receipt_id"] for item in task["upstream_receipts"]["items"]
    }
    assert NodeVerifier(store).verify(quest.id, "reader")["status"] == "completed"
    assert manager.get(producer["id"]) == original
    with store.unit_of_work() as work:
        CompletionGate(store).check(work, work.get_quest(quest.id))
        result = _read_action_receipt(
            store, work.connection, reader["id"], {"receipt_id": receipts["file_write"]}
        )
        assert result["plan_revision"] == 1 and result["producer_run_id"] == producer["id"]
        assert (
            work.connection.execute(
                "SELECT tokens_allocated FROM worker_budget_ledger WHERE scope='quest'"
            ).fetchone()[0]
            == 100000
        )
        assert work.connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 3
        assert work.connection.execute("SELECT count(*) FROM quest_plans").fetchone()[0] == 2
        assert plan["plan_revision"] == 2


@pytest.mark.parametrize("mutation", ["goal", "inherited_worker", "source_contract"])
def test_remaining_receipt_reader_rejects_changed_lineage_or_original_evidence(tmp_path, mutation):
    store, _manager, _, quest, plan, producer, reader, receipts, _ = replan(tmp_path)
    with store.unit_of_work() as work:
        c = work.connection
        if mutation == "goal":
            c.execute("UPDATE quests SET goal='different task' WHERE id=?", (quest.id,))
        elif mutation == "inherited_worker":
            c.execute(
                "UPDATE task_nodes SET worker_id=? WHERE plan_id=? AND node_key='producer'",
                (reader["worker_id"], plan["id"]),
            )
        else:
            c.execute("UPDATE quest_plans SET graph_json='{}' WHERE id=?", (producer["plan_id"],))
    with store.connection() as connection:
        connection.execute("BEGIN")
        with pytest.raises(DomainError):
            _read_action_receipt(
                store, connection, reader["id"], {"receipt_id": receipts["file_write"]}
            )


def test_original_receipts_remain_immutable_after_replanning(tmp_path):
    store, manager, _, _, _, _, reader, receipts, _ = replan(tmp_path)
    with (
        pytest.raises(sqlite3.IntegrityError, match="immutable receipt"),
        store.unit_of_work() as work,
    ):
        work.connection.execute(
            "UPDATE action_receipts SET result_json='{}' WHERE id=?", (receipts["file_write"],)
        )
    manager._claim(reader["id"])
    with store.connection() as connection:
        connection.execute("BEGIN")
        result = _read_action_receipt(
            store, connection, reader["id"], {"receipt_id": receipts["file_write"]}
        )
        assert json.loads(result["result_json"])["sha256"]
