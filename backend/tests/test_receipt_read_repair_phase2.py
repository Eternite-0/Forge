"""Offline scripted protocols, real governed files and durable no-effect proofs.

The scripted model uses the live execution tag solely to exercise the existing
receipt gate; these tests make no real Provider or model-quality claim.
"""

import asyncio
import json

import pytest
from test_worker_intelligence_phase2 import delivery, response, tool

from forge_core.domain import DomainError, ModelProfile, ProtocolSupport, Quest
from forge_core.evidence import NodeVerifier, _verify_worker_context_call
from forge_core.governance import GovernanceService
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_recovery import settled_preexecution_rejection, unresolved_calls
from forge_core.worker_runtime import WorkerManager, _json, _read_action_receipt


class Protocol:
    execution_mode = "live"

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    async def generate(self, request):
        self.requests.append(request)
        assert self.responses, "Unexpected offline model call"
        return self.responses.pop(0)


def dependent(tmp_path, *, large_source=False, reader_repairs=2):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    (tmp_path / "work").mkdir()
    source = {"finding": "Verified source", "notes": "UNTRUSTED_SOURCE_SENTINEL" + "x" * 9000}
    original = _json(source) if large_source else '{"finding":"Verified source"}'
    (tmp_path / "work/input.json").write_text(original, encoding="utf-8")
    quest = Quest(title="Receipt repair fixture", goal="Inspect the reviewed predecessor artifact.")
    store.save_quest(quest)
    producer_tools = ["file_read", "file_write"]
    reader_tools = ["worker_read_action_receipt", "file_read", "file_write"]
    nodes = [
        {
            "key": key,
            "objective": objective,
            "success_criteria": [f"json_valid:work/{artifact}.json"],
            "budget_tokens": 50000,
            "allowed_tools": tools,
        }
        for key, objective, artifact, tools in (
            (
                "producer",
                "Inspect original source and write its JSON artifact.",
                "source",
                producer_tools,
            ),
            (
                "reader",
                "Read the predecessor artifact and write an evidenced summary.",
                "review",
                reader_tools,
            ),
        )
    ]
    graph = QuestGraphService(store)
    plan = graph.propose(
        quest.id,
        {
            "nodes": nodes,
            "edges": [{"source": "producer", "target": "reader", "condition": "success"}],
            "max_total_tokens": 100000,
        },
        expected_version=0,
    )
    graph.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    store.save_model(
        ModelProfile(
            id="protocol",
            provider="protocol",
            model_id="fixture",
            display_name="Offline receipt repair fixture",
            context_window=64000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    teams = TeamCompositionService(store)
    team = teams.propose(
        quest.id,
        [
            {
                "id": "coding" if index == 0 else "reviewer",
                "task_node_key": node["key"],
                "tools": node["allowed_tools"],
                "input_tokens": 32000,
                "output_tokens": 3000,
                "max_steps": 8,
                "max_tool_calls": 6,
                "max_duration_seconds": 120,
            }
            for index, node in enumerate(nodes)
        ],
        {
            "max_members": 2,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 70000,
            "max_total_tool_calls": 12,
            "allowed_tools": reader_tools,
        },
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user")
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    model = Protocol(
        [
            tool("file_read", {"path": "work/input.json"}),
            tool("file_write", {"path": "work/source.json", "content": original}, ordinal=2),
            response(delivery()),
        ]
    )
    manager = WorkerManager(
        store,
        ProviderPool({"protocol": model}),
        ToolEngine(store),
        team_authority=teams,
        max_repairs=reader_repairs,
    )
    producer = manager.start(
        quest.id, plan["id"], "producer", team_digest=team["proposal_digest"], member_index=0
    )
    assert asyncio.run(manager.run(producer["id"]))["state"] == "review_pending"
    NodeVerifier(store).verify(quest.id, "producer")
    with store.connection() as connection:
        receipts = {
            row["tool_name"]: row["id"]
            for row in connection.execute(
                "SELECT r.id,i.tool_name FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE i.run_id=?",
                (producer["id"],),
            )
        }
    reader = manager.start(
        quest.id, plan["id"], "reader", team_digest=team["proposal_digest"], member_index=1
    )
    return store, manager, model, quest, plan, team, producer, reader, receipts, original


def calls(store, run_id):
    with store.connection() as connection:
        return connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? ORDER BY rowid", (run_id,)
        ).fetchall()


def test_plan_id_mistake_repairs_to_new_real_receipt_call_and_passes_actual_gate(tmp_path):
    store, manager, model, quest, plan, _, _, reader, receipts, _ = dependent(tmp_path)
    before = manager.get(reader["id"])
    model.responses.extend(
        [
            tool("worker_read_action_receipt", {"receipt_id": plan["id"]}),
            tool("worker_read_action_receipt", {"receipt_id": receipts["file_write"]}, ordinal=2),
            tool(
                "file_write", {"path": "work/review.json", "content": '{"checked":true}'}, ordinal=3
            ),
            response(delivery("The real predecessor receipt was inspected.")),
        ]
    )
    result = asyncio.run(manager.run(reader["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert result["steps"] == 4 and result["token_spent"] == 160 and result["tool_spent"] == 3
    for key in ("token_limit", "tool_limit", "step_limit", "allowed_tools_json"):
        assert result[key] == before[key]
    rejected, corrected, written = calls(store, reader["id"])
    assert [item["state"] for item in (rejected, corrected, written)] == [
        "failed",
        "succeeded",
        "succeeded",
    ]
    assert rejected["receipt_id"] is None and corrected["receipt_id"] is None
    assert rejected["model_call_id"] != corrected["model_call_id"]
    with store.connection() as connection:
        connection.execute("BEGIN")
        assert settled_preexecution_rejection(connection, reader["id"], rejected)
        assert _verify_worker_context_call(store, connection, reader["id"], corrected)
        assert not unresolved_calls(connection, reader["id"])
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM action_intents WHERE run_id=?", (reader["id"],)
            ).fetchone()[0]
            == 1
        )
        assert not connection.execute(
            "SELECT 1 FROM action_intents WHERE call_id=?", (rejected["call_id"],)
        ).fetchone()
    feedback = json.loads(model.requests[4].messages[-1]["content"])
    assert feedback["state"] == "rejected_before_execution"
    assert feedback["repair_request"]["reason_code"]
    assert NodeVerifier(store).verify(quest.id, "reader")["status"] == "completed"


def test_large_receipt_rejection_uses_short_artifact_receipt_and_governed_full_file(tmp_path):
    store, manager, model, quest, _, _, _, reader, receipts, original = dependent(
        tmp_path, large_source=True
    )
    model.responses.extend(
        [
            tool("worker_read_action_receipt", {"receipt_id": receipts["file_read"]}),
            tool("worker_read_action_receipt", {"receipt_id": receipts["file_write"]}, ordinal=2),
            tool("file_read", {"path": "work/source.json"}, ordinal=3),
            tool(
                "file_write", {"path": "work/review.json", "content": '{"checked":true}'}, ordinal=4
            ),
            response(delivery()),
        ]
    )
    result = asyncio.run(manager.run(reader["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    first_request = model.requests[3]
    task = next(
        json.loads(item["content"])
        for item in first_request.messages
        if item["role"] == "user" and "upstream_receipts" in item.get("content", "")
    )
    references = {item["receipt_id"]: item for item in task["upstream_receipts"]["items"]}
    assert references[receipts["file_read"]]["result_bytes"] > 8192
    assert references[receipts["file_read"]]["readback_available"] is False
    assert references[receipts["file_write"]]["readback_available"] is True
    feedback = model.requests[4].messages[-1]["content"]
    assert "UNTRUSTED_SOURCE_SENTINEL" not in feedback and len(feedback.encode()) < 2048
    assert json.loads(feedback)["state"] == "rejected_before_execution"
    assert json.loads(model.requests[6].messages[-1]["content"])["content"] == original
    with store.connection() as connection:
        connection.execute("BEGIN")
        rejected = calls(store, reader["id"])[0]
        assert settled_preexecution_rejection(connection, reader["id"], rejected)
        assert not connection.execute(
            "SELECT 1 FROM action_intents WHERE call_id=?", (rejected["call_id"],)
        ).fetchone()
    assert (tmp_path / "work/source.json").read_text(encoding="utf-8") == original
    assert NodeVerifier(store).verify(quest.id, "reader")["status"] == "completed"


def test_repeated_receipt_errors_stop_at_original_bound_without_reader_actions(tmp_path):
    store, manager, model, _, plan, _, _, reader, _, _ = dependent(tmp_path)
    model.responses.extend(
        tool("worker_read_action_receipt", {"receipt_id": plan["id"]}, ordinal=index)
        for index in range(1, 4)
    )
    result = asyncio.run(manager.run(reader["id"]))
    assert result["state"] == "blocked" and result["repairs"] == 2
    assert result["steps"] == 3 and result["token_spent"] == 120 and result["tool_spent"] == 3
    assert len(model.requests) == 6
    rows = calls(store, reader["id"])
    assert len(rows) == 3 and all(item["state"] == "failed" for item in rows)
    with store.connection() as connection:
        assert all(
            settled_preexecution_rejection(connection, reader["id"], item) for item in rows[:2]
        )
        assert not settled_preexecution_rejection(connection, reader["id"], rows[-1])
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM action_intents WHERE run_id=?", (reader["id"],)
            ).fetchone()[0]
            == 0
        )


def test_receipt_repair_does_not_allow_reusing_failed_model_call_id(tmp_path):
    store, manager, model, _, plan, _, _, reader, receipts, _ = dependent(tmp_path)
    model.responses.extend(
        [
            tool("worker_read_action_receipt", {"receipt_id": plan["id"]}),
            tool("worker_read_action_receipt", {"receipt_id": receipts["file_write"]}),
        ]
    )
    result = asyncio.run(manager.run(reader["id"]))
    assert result["state"] == "blocked" and result["repairs"] == 1
    assert len(calls(store, reader["id"])) == 1 and result["tool_spent"] == 1
    blocked = [
        json.loads(event["payload"])
        for event in store.list_events(limit=1000)
        if event["event_type"] == "worker.blocked"
    ]
    assert blocked[-1]["reason_code"] == "duplicate_tool_call"


@pytest.mark.parametrize("change", ["arguments", "no_effect", "tool_reply"])
def test_failed_receipt_read_requires_untampered_core_no_effect_proof(tmp_path, change):
    store, manager, model, quest, plan, _, _, reader, _, _ = dependent(tmp_path)
    model.responses.extend(
        [
            tool("worker_read_action_receipt", {"receipt_id": plan["id"]}),
            tool("file_write", {"path": "work/review.json", "content": "{}"}, ordinal=2),
            response(delivery()),
        ]
    )
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    rejected = calls(store, reader["id"])[0]
    with store.unit_of_work() as work:
        assert settled_preexecution_rejection(work.connection, reader["id"], rejected)
        if change == "arguments":
            work.connection.execute(
                "UPDATE worker_tool_calls SET arguments_json=? WHERE run_id=? AND call_id=?",
                (_json({"receipt_id": "changed"}), reader["id"], rejected["call_id"]),
            )
        elif change == "no_effect":
            work.connection.execute(
                "UPDATE events SET payload=json_set(payload,'$.no_effect',0) "
                "WHERE event_type='worker.progress' AND json_extract(payload,'$.failed_call_id')=?",
                (rejected["call_id"],),
            )
        else:
            work.connection.execute(
                "UPDATE worker_messages SET message_json='{}' WHERE run_id=? AND role='tool'",
                (reader["id"],),
            )
    with store.connection() as connection:
        rejected = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND call_id=?",
            (reader["id"], rejected["call_id"]),
        ).fetchone()
        assert not settled_preexecution_rejection(connection, reader["id"], rejected)
    with pytest.raises(DomainError):
        NodeVerifier(store).verify(quest.id, "reader")


@pytest.mark.parametrize("change", ["nonedge", "wrong_quest", "revoked_team"])
def test_local_receipt_repair_never_grants_cross_scope_or_revoked_access(tmp_path, change):
    store, manager, model, quest, plan, team, _, reader, receipts, _ = dependent(tmp_path)
    other = Quest(title="Other", goal="Unrelated Quest")
    store.save_quest(other)
    with store.connection() as connection:
        connection.execute("BEGIN")
        if change == "nonedge":
            connection.execute(
                "UPDATE task_edges SET condition='failure' WHERE plan_id=?", (plan["id"],)
            )
        elif change == "wrong_quest":
            connection.execute(
                "UPDATE worker_runs SET quest_id=? WHERE id=?", (other.id, reader["id"])
            )
        else:
            connection.execute(
                "UPDATE team_proposals SET status='revoked' WHERE proposal_digest=?",
                (team["proposal_digest"],),
            )
        with pytest.raises(DomainError):
            _read_action_receipt(
                store, connection, reader["id"], {"receipt_id": receipts["file_write"]}
            )
        connection.rollback()
    # After a harmless bad-ID diagnostic, changing authority still prevents
    # the corrected real-receipt call from reading bytes.
    model.responses.extend(
        [
            tool("worker_read_action_receipt", {"receipt_id": plan["id"]}),
            tool("worker_read_action_receipt", {"receipt_id": receipts["file_write"]}, ordinal=2),
        ]
    )
    original_generate = model.generate

    async def revoke_after_diagnostic(request):
        if len(model.requests) == 4:
            with store.unit_of_work() as work:
                if change == "nonedge":
                    work.connection.execute(
                        "UPDATE task_edges SET condition='failure' WHERE plan_id=?", (plan["id"],)
                    )
                elif change == "wrong_quest":
                    work.connection.execute(
                        "UPDATE worker_runs SET quest_id=? WHERE id=?", (other.id, reader["id"])
                    )
                else:
                    work.connection.execute(
                        "UPDATE team_proposals SET status='revoked' WHERE proposal_digest=?",
                        (team["proposal_digest"],),
                    )
        return await original_generate(request)

    model.generate = revoke_after_diagnostic
    try:
        asyncio.run(manager.run(reader["id"]))
    except DomainError:
        pass
    assert not any(item["state"] == "succeeded" for item in calls(store, reader["id"]))
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM action_intents WHERE run_id=?", (reader["id"],)
            ).fetchone()[0]
            == 0
        )
    assert store.get_quest(quest.id)["status"] != "completed"
