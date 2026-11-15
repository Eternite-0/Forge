"""Receipt readback is bound to approved graph, scope and durable tool calls."""

import asyncio
import json

import jsonschema
import pytest

from forge_core.domain import DomainError, ModelProfile, ProtocolSupport, Quest
from forge_core.evidence import NodeVerifier
from forge_core.governance import GovernanceService
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelResponse
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def test_approved_dependent_reads_real_receipt_and_gate_rechecks(tmp_path):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    (tmp_path / "work").mkdir()
    quest = Quest(title="receipt", goal="review source")
    store.save_quest(quest)
    graph = QuestGraphService(store)
    plan = graph.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "producer",
                    "objective": "write",
                    "success_criteria": ["json_valid:work/a.json"],
                    "budget_tokens": 16000,
                    "allowed_tools": ["file_write"],
                },
                {
                    "key": "review",
                    "objective": "review",
                    "success_criteria": ["json_valid:work/review.json"],
                    "budget_tokens": 16000,
                    "allowed_tools": ["worker_read_action_receipt", "file_write"],
                },
            ],
            "edges": [{"source": "producer", "target": "review", "condition": "success"}],
            "max_total_tokens": 32000,
        },
        expected_version=0,
    )
    graph.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    store.save_model(
        ModelProfile(
            id="fixture",
            provider="fixture",
            model_id="scripted",
            display_name="fixture",
            context_window=32000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    teams = TeamCompositionService(store)
    roles = [
        {
            "id": "coding",
            "task_node_key": "producer",
            "tools": ["file_write"],
            "input_tokens": 9000,
            "output_tokens": 3000,
            "max_steps": 4,
            "max_tool_calls": 2,
        },
        {
            "id": "reviewer",
            "task_node_key": "review",
            "tools": ["worker_read_action_receipt", "file_write"],
            "input_tokens": 9000,
            "output_tokens": 3000,
            "max_steps": 5,
            "max_tool_calls": 3,
        },
    ]
    team = teams.propose(
        quest.id,
        roles,
        {
            "max_members": 2,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 24000,
            "max_total_tool_calls": 5,
            "allowed_tools": ["file_write", "worker_read_action_receipt"],
        },
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(
        quest.id,
        plan["plan_revision"],
        team["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")

    class Model:
        execution_mode = "live"
        calls = 0
        receipt = None

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                calls = [
                    {
                        "id": "write",
                        "name": "file_write",
                        "arguments": {"path": "work/a.json", "content": "{}"},
                    }
                ]
            elif self.calls == 3:
                assert any(tool["name"] == "worker_read_action_receipt" for tool in request.tools)
                task = next(
                    json.loads(message["content"])
                    for message in request.messages
                    if message["role"] == "user" and "upstream_receipts" in message["content"]
                )
                self.receipt = task["upstream_receipts"]["items"][0]["receipt_id"]
                calls = [
                    {
                        "id": "read",
                        "name": "worker_read_action_receipt",
                        "arguments": {"receipt_id": self.receipt},
                    }
                ]
            elif self.calls == 4:
                assert self.receipt in request.messages[-1]["content"]
                calls = [
                    {
                        "id": "review-write",
                        "name": "file_write",
                        "arguments": {"path": "work/review.json", "content": "{}"},
                    }
                ]
            else:
                calls = []
            return ModelResponse(
                text="" if calls else '{"summary":"done"}',
                tool_calls=calls,
                usage={"input_tokens": 20, "output_tokens": 10},
                finish_reason="tool_calls" if calls else "stop",
            )

    model = Model()
    manager = WorkerManager(
        store, ProviderPool({"fixture": model}), ToolEngine(store), team_authority=teams
    )
    producer = manager.start(
        quest.id, plan["id"], "producer", team_digest=team["proposal_digest"], member_index=0
    )
    assert asyncio.run(manager.run(producer["id"]))["state"] == "review_pending"
    NodeVerifier(store).verify(quest.id, "producer")
    reviewer = manager.start(
        quest.id, plan["id"], "review", team_digest=team["proposal_digest"], member_index=1
    )
    assert asyncio.run(manager.run(reviewer["id"]))["state"] == "review_pending"
    assert NodeVerifier(store).verify(quest.id, "review")["status"] == "completed"
    with store.connection() as connection:
        call = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND tool_name='worker_read_action_receipt'",
            (reviewer["id"],),
        ).fetchone()
        assert call["state"] == "succeeded" and call["receipt_id"] is None
    from forge_core.evidence import _verify_worker_context_call
    from forge_core.worker_runtime import _read_action_receipt

    with store.connection() as connection:
        connection.execute("BEGIN")
        call = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND tool_name='worker_read_action_receipt'",
            (reviewer["id"],),
        ).fetchone()
        assert _verify_worker_context_call(store, connection, reviewer["id"], call)
        source = _read_action_receipt(
            store, connection, reviewer["id"], {"receipt_id": model.receipt}
        )
        assert (
            source["result_sha256"]
            and source["execution_mode"] == "live"
            and source["truncated"] is False
        )
        from forge_core.tool_domain import CONTRACTS

        contract = next(item for item in CONTRACTS if item.name == "worker_read_action_receipt")
        jsonschema.validate(source, contract.output_schema)
        with pytest.raises(DomainError):
            _read_action_receipt(store, connection, reviewer["id"], {"receipt_id": "unknown"})
        connection.execute(
            "UPDATE worker_tool_calls SET result_json='{}' WHERE run_id=? AND tool_name='worker_read_action_receipt'",
            (reviewer["id"],),
        )
        call = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND tool_name='worker_read_action_receipt'",
            (reviewer["id"],),
        ).fetchone()
        assert not _verify_worker_context_call(store, connection, reviewer["id"], call)
        connection.rollback()
    with store.connection() as connection:
        connection.execute("BEGIN")
        connection.execute(
            "UPDATE worker_messages SET message_json='{}' WHERE run_id=? AND role='tool'",
            (producer["id"],),
        )
        with pytest.raises(DomainError):
            _read_action_receipt(store, connection, reviewer["id"], {"receipt_id": model.receipt})
        connection.rollback()
    other = Quest(title="other", goal="other quest")
    store.save_quest(other)
    for sql, parameters in [
        (
            "UPDATE worker_tool_calls SET result_json='[]' WHERE run_id=?",
            (producer["id"],),
        ),
        ("UPDATE worker_runs SET member_index=999 WHERE id=?", (reviewer["id"],)),
        ("UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (quest.id,)),
        (
            "UPDATE team_proposals SET status='revoked' WHERE proposal_digest=?",
            (team["proposal_digest"],),
        ),
        ("UPDATE worker_runs SET quest_id=? WHERE id=?", (other.id, reviewer["id"])),
        ("UPDATE task_edges SET condition='failure' WHERE plan_id=?", (plan["id"],)),
    ]:
        with store.connection() as connection:
            connection.execute("BEGIN")
            connection.execute(sql, parameters)
            with pytest.raises(DomainError):
                _read_action_receipt(
                    store, connection, reviewer["id"], {"receipt_id": model.receipt}
                )
            connection.rollback()


def test_missing_receipt_rejected(tmp_path):
    from forge_core.worker_runtime import _read_action_receipt

    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    with store.unit_of_work() as work, pytest.raises(DomainError):
        _read_action_receipt(store, work.connection, "missing", {"receipt_id": "missing"})
