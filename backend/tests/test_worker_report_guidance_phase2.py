"""Offline writing guidance with real approvals, local artifacts and receipts."""

import asyncio
import hashlib
import json

import pytest
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response, tool

from forge_core.domain import ModelProfile, ProtocolSupport, Quest
from forge_core.governance import GovernanceService
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def approved_report(
    tmp_path, *, criterion, files=None, successor=True, objective=None, source="source"
):
    files = files or {}
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    (tmp_path / "work").mkdir()
    (tmp_path / "work/input.txt").write_bytes(source.encode("utf-8"))
    objective = (
        objective or "Provide cited research findings, limitations and downstream-required fields."
    )
    quest = Quest(title="Report guidance fixture", goal=objective)
    store.save_quest(quest)
    nodes = [
        {
            "key": "research",
            "objective": objective,
            "success_criteria": [criterion],
            "budget_tokens": 60000,
            "allowed_tools": ["file_read", "file_write"],
        }
    ]
    if successor:
        nodes.append(
            {
                "key": "consumer",
                "objective": "Independently inspect the complete predecessor result.",
                "success_criteria": ["delivery_review"],
                "budget_tokens": 60000,
                "allowed_tools": ["file_read"],
            }
        )
    graphs = QuestGraphService(store)
    plan = graphs.propose(
        quest.id,
        {
            "nodes": nodes,
            "edges": [{"source": "research", "target": "consumer", "condition": "success"}]
            if successor
            else [],
            "max_total_tokens": 60000 * len(nodes),
        },
        expected_version=0,
    )
    graphs.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    store.save_model(
        ModelProfile(
            id="protocol",
            provider="protocol",
            model_id="fixture",
            display_name="Offline report fixture",
            context_window=64000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    roles = [
        {
            "id": "coding" if index == 0 else "reviewer",
            "task_node_key": node["key"],
            "tools": node["allowed_tools"],
            "input_tokens": 30000,
            "output_tokens": 3000,
            "max_steps": 12,
            "max_tool_calls": 5,
            "max_duration_seconds": 120,
        }
        for index, node in enumerate(nodes)
    ]
    teams = TeamCompositionService(store)
    team = teams.propose(
        quest.id,
        roles,
        {
            "max_members": len(nodes),
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 33000 * len(nodes),
            "max_total_tool_calls": 5 * len(nodes),
            "allowed_tools": ["file_read", "file_write"],
        },
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user")
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    model = ProtocolModel(
        [tool("file_read", {"path": "work/input.txt"})]
        + [
            tool("file_write", {"path": path, "content": content}, index + 2)
            for index, (path, content) in enumerate(files.items())
        ]
        + [response(delivery())]
    )
    manager = WorkerManager(
        store, ProviderPool({"protocol": model}), ToolEngine(store), team_authority=teams
    )
    worker = manager.start(
        quest.id, plan["id"], "research", team_digest=team["proposal_digest"], member_index=0
    )
    return store, manager, model, worker, plan


@pytest.mark.parametrize(
    "criterion,successor,expected",
    [
        ("json_review:work/findings.json", True, True),
        ("json_review:work/findings.json", False, False),
        ("delivery_review", True, True),
        ("json_review:@delivery", True, True),
    ],
)
def test_guidance_targets_reviewed_json_deliveries_with_approved_successors(
    tmp_path, criterion, successor, expected
):
    store, manager, model, worker, plan = approved_report(
        tmp_path, criterion=criterion, successor=successor
    )
    result = asyncio.run(manager.run(worker["id"]))
    instruction = model.requests[0].messages[0]["content"]
    assert ("For compressible intermediate JSON" in instruction) is expected
    assert result["state"] == "review_pending" and result["repairs"] == 0
    with store.connection() as connection:
        capsule = json.loads(
            connection.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
        )
        approved = connection.execute("SELECT digest FROM quest_plans").fetchone()[0]
    assert capsule["success_criteria"] == [criterion]
    assert capsule["constraints"]["token_limit"] == worker["token_limit"]
    assert approved == plan["digest"]


def test_complete_code_and_original_receipt_content_survive_guidance_and_reopen(tmp_path):
    source = 'original source "evidence" 中文\n' * 350
    code = "# Required complete implementation\n" + "# preserve every requested line\n" * 350
    code += "def result():\n    return 42\n"
    findings = json.dumps(
        {
            "conclusion": "Return 42 from the complete assigned implementation.",
            "source_path": "work/input.txt",
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "limitations": ["No execution result is claimed."],
            "downstream_fields": {"implementation": "work/complete.py", "expected_result": 42},
        }
    )
    store, manager, model, worker, _plan = approved_report(
        tmp_path,
        criterion="json_review:work/findings.json",
        files={"work/complete.py": code, "work/findings.json": findings},
        source=source,
        objective="Deliver the complete required code in full and a concise findings JSON for the next node.",
    )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    instruction = model.requests[0].messages[0]["content"]
    assert "never a size limit or permission to omit required material" in instruction
    assert "complete code, source reproductions" in instruction
    assert "downstream-required field once" in instruction
    assert (
        len(code.encode()) > 6144 and (tmp_path / "work/complete.py").read_bytes() == code.encode()
    )
    assert (tmp_path / "work/findings.json").read_bytes() == findings.encode()
    reopened = Store(store.path, workspace=store.workspace)
    with reopened.connection() as connection:
        persisted = json.loads(
            connection.execute(
                "SELECT message_json FROM worker_messages WHERE run_id=? AND ordinal=0",
                (worker["id"],),
            ).fetchone()[0]
        )
        receipts = [
            (row["tool_name"], json.loads(row["result_json"]))
            for row in connection.execute(
                "SELECT i.tool_name,r.result_json FROM action_receipts r "
                "JOIN action_intents i ON i.id=r.intent_id ORDER BY i.rowid"
            )
        ]
    assert persisted["content"] == instruction
    assert receipts[0][1]["content"] == source
    assert receipts[1][1]["sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert receipts[2][1]["sha256"] == hashlib.sha256(findings.encode()).hexdigest()
    assert manager.get(worker["id"])["tool_spent"] == 3


def test_final_report_with_a_downstream_auditor_keeps_every_required_field(tmp_path):
    report = json.dumps(
        {"required_records": [{"id": index, "finding": "complete" * 8} for index in range(120)]}
    )
    _store, manager, model, worker, _plan = approved_report(
        tmp_path,
        criterion="json_review:work/final-report.json",
        files={"work/final-report.json": report},
        objective="Deliver the complete final report with all required records for a downstream auditor.",
    )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    instruction = model.requests[0].messages[0]["content"]
    assert "final reports and required detailed schemas must be delivered in full" in instruction
    assert len(report.encode()) > 6144
    assert (tmp_path / "work/final-report.json").read_bytes() == report.encode()
    assert (
        len(json.loads((tmp_path / "work/final-report.json").read_bytes())["required_records"])
        == 120
    )
