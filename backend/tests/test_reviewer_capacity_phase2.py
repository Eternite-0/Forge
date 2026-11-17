"""Offline capacity diagnostics with actual approvals, receipts and revisions."""

import asyncio
import hashlib
import json

import pytest
import test_worker_report_guidance_phase2 as reports
from test_worker_intelligence_phase2 import delivery, response, tool

from forge_core.governance import GovernanceService
from forge_core.independent_review import (
    IndependentReviewService,
    _hash,
    _json,
    _packet,
    review_pass_matches,
)
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager

PASS = {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}


def pending(
    tmp_path,
    monkeypatch,
    *,
    successor=True,
    downstream_writer=True,
    revise=True,
    source_bytes=23400,
    source_reads=1,
    distinct_source_reads=False,
    explanation_repeats=2000,
    context_window=64000,
    raw_oversize=True,
):
    propose_plan = reports.QuestGraphService.propose
    propose_team = reports.TeamCompositionService.propose
    model_profile = reports.ModelProfile
    input_tokens = min(56000, context_window - 4000)

    def plan(self, quest_id, proposal, **kwargs):
        if revise:
            proposal["nodes"][0]["allowed_tools"].append("file_revise")
        if successor and downstream_writer:
            proposal["nodes"][1].update(
                objective="Author the final integrated findings artifact using the reviewed research.",
                expected_artifact="work/final.json",
                success_criteria=["json_review:work/final.json"],
                allowed_tools=["file_read", "file_write"],
            )
        return propose_plan(self, quest_id, proposal, **kwargs)

    def team(self, quest_id, roles, limits, **kwargs):
        for role in roles:
            role["input_tokens"] = input_tokens
        if revise:
            if "file_revise" not in roles[0]["tools"]:
                roles[0]["tools"].append("file_revise")
            limits["allowed_tools"].append("file_revise")
        limits["max_total_tokens"] = (input_tokens + 3000) * len(roles)
        return propose_team(self, quest_id, roles, limits, **kwargs)

    monkeypatch.setattr(reports.QuestGraphService, "propose", plan)
    monkeypatch.setattr(reports.TeamCompositionService, "propose", team)
    monkeypatch.setattr(
        reports,
        "ModelProfile",
        lambda **fields: model_profile(**{**fields, "context_window": context_window}),
    )
    source = ("evidence " * ((source_bytes + 8) // 9))[:source_bytes]
    content = {
        "finding": "A precise evidenced finding required by the next node.",
        "citation": {
            "source_path": "work/input.txt",
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        },
        "explanation": "Repeated explanation. " * explanation_repeats,
    }
    store, manager, model, worker, _ = reports.approved_report(
        tmp_path,
        criterion="json_review:work/findings.json",
        files={"work/findings.json": _json(content)},
        successor=successor,
        source=source,
    )
    reads = []
    for index in range(source_reads - 1):
        path = "work/input.txt"
        if distinct_source_reads:
            path = f"work/input-{index}.txt"
            prefix = f"Distinct immutable source {index}: "
            (tmp_path / path).write_text(prefix + source[len(prefix) :], encoding="utf-8")
        reads.append(tool("file_read", {"path": path}, ordinal=20 + index))
    model.responses[1:1] = reads
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        packet = _packet(store, connection, row)
        if raw_oversize:
            assert len(_json(packet).encode("utf-8")) > 65536
    return store, manager, model, worker, packet, content


def events(store, kind):
    return [
        json.loads(row["payload"])
        for row in store.list_events(limit=1000)
        if row["event_type"] == kind
    ]


def test_capacity_feedback_preserves_rejected_packet_and_never_claims_model_review(
    tmp_path, monkeypatch
):
    store, manager, model, worker, packet, _ = pending(tmp_path, monkeypatch)
    before = manager.get(worker["id"])
    original = _json(packet)
    service = IndependentReviewService(manager)
    result = asyncio.run(service.review(worker["id"]))
    after = manager.get(worker["id"])
    assert result["verdict"] == "NEEDS_REVISION" and result["revision_applied"]
    assert result["review_context"] == "deterministic_review_capacity"
    assert result["validation_source"] == "core_review_capacity"
    assert result["packet_sha256"] == _hash(packet) and _json(packet) == original
    assert result["capacity_diagnostics"]["raw_packet_bytes"] == len(original.encode()) > 65536
    assert result["capacity_diagnostics"]["raw_packet_cap"] is None
    assert "call_id" not in result and "provider_id" not in result
    assert len(model.requests) == 3 and not events(store, "review.started")
    assert after["state"] == "queued" and after["repairs"] == 1
    for key in ("steps", "tool_spent", "token_spent", "token_limit", "tool_limit", "step_limit"):
        assert after[key] == before[key]
    assert "Preserve ALL assigned requirements" in result["revision_goals"][0]
    assert f"raw packet {len(original.encode())} bytes" in result["revision_goals"][0]
    with store.connection() as connection:
        assert service._rounds(connection, worker["id"]) == 1
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
        assert not review_pass_matches(store, connection, after, "json_review:work/findings.json")


def test_compact_complete_owned_revision_still_requires_fresh_model_review(tmp_path, monkeypatch):
    store, manager, model, worker, packet, content = pending(tmp_path, monkeypatch)
    service = IndependentReviewService(manager)
    assert asyncio.run(service.review(worker["id"]))["revision_applied"]
    artifact = packet["artifacts"][0]
    compact = {**content, "explanation": "Repeated explanation."}
    model.responses.extend(
        [
            tool(
                "file_revise",
                {
                    "path": "work/findings.json",
                    "prior_receipt_id": artifact["receipt_id"],
                    "expected_sha256": artifact["sha256"],
                    "content": _json(compact),
                },
                ordinal=10,
            ),
            response(delivery("Complete compact findings, with all original evidence.")),
            response(PASS),
        ]
    )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert len(model.requests) == 5 and len(events(store, "review.result")) == 1
    result = asyncio.run(service.review(worker["id"]))
    assert result["verdict"] == "PASS" and result["review_context"] == "independent_same_model"
    assert result["execution_mode"] == "mock" and len(model.requests) == 6
    request = model.requests[-1]
    assert request.tools is None and request.budget_context.purpose == "independent_reviewer"
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        current = _packet(store, connection, row)
        assert current["artifacts"][0]["content"] == compact
        assert current["receipts"][0] == packet["receipts"][0]
        assert result["packet_sha256"] == _hash(current) != _hash(packet)
        history = [
            json.loads(item[0])["content"]
            for item in connection.execute(
                "SELECT parameters_json FROM action_intents WHERE tool_name IN ('file_write','file_revise') ORDER BY rowid"
            )
        ]
        assert history == [_json(content), _json(compact)]
        assert service._rounds(connection, worker["id"]) == 2


def test_immutable_receipts_exceeding_actual_context_cannot_get_capacity_revision(
    tmp_path, monkeypatch
):
    store, manager, model, worker, packet, _ = pending(
        tmp_path, monkeypatch, source_reads=3, distinct_source_reads=True
    )
    immutable_sources = [
        item["output"]["content"] for item in packet["receipts"] if item["tool_name"] == "file_read"
    ]
    assert len(set(immutable_sources)) == 3
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["verdict"] == "BLOCKED" and not result["revision_applied"]
    assert result["capacity_diagnostics"]["fixed_input_floor"] > 64000
    assert result["packet_sha256"] == _hash(packet)
    assert len(model.requests) == 5 and not events(store, "worker.revision_requested")


def test_repeated_immutable_sources_keep_all_receipts_but_share_wire_text(tmp_path, monkeypatch):
    store, manager, model, worker, packet, _ = pending(tmp_path, monkeypatch, source_reads=3)
    immutable_receipts = [item for item in packet["receipts"] if item["tool_name"] == "file_read"]
    assert len(immutable_receipts) == 3 and len({item["id"] for item in immutable_receipts}) == 3
    assert len({item["output"]["content"] for item in immutable_receipts}) == 1
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["verdict"] == "NEEDS_REVISION" and result["revision_applied"]
    assert result["capacity_diagnostics"]["fixed_input_floor"] < 64000
    assert result["packet_sha256"] == _hash(packet)
    assert len(model.requests) == 5 and not events(store, "review.started")


def test_context_only_overflow_uses_same_bounded_core_diagnostic(tmp_path, monkeypatch):
    store, manager, model, worker, packet, _ = pending(
        tmp_path,
        monkeypatch,
        explanation_repeats=1200,
        context_window=50000,
        raw_oversize=False,
    )
    assert len(_json(packet).encode()) < 65536
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["verdict"] == "NEEDS_REVISION" and result["revision_applied"]
    assert result["error_code"] == "review_context_limit"
    assert result["capacity_diagnostics"]["native_input_reservation"] > 50000
    assert len(model.requests) == 3 and not events(store, "review.started")


@pytest.mark.parametrize(
    "block",
    [
        "final",
        "final_auditor",
        "no_revise",
        "rounds",
        "repairs",
        "steps",
        "tokens",
        "tools",
        "grant",
        "unknown",
    ],
)
def test_capacity_diagnostic_stays_blocked_without_original_authority_or_allowance(
    tmp_path, monkeypatch, block
):
    store, manager, model, worker, _, _ = pending(
        tmp_path,
        monkeypatch,
        successor=block != "final",
        downstream_writer=block != "final_auditor",
        revise=block != "no_revise",
    )
    if block == "grant":
        GovernanceService(store).revoke_workspace(worker["quest_id"], "work", "local_user")
    with store.unit_of_work() as work:
        if block in {"repairs", "steps", "tokens", "tools"}:
            assignment = {
                "repairs": "repairs=2",
                "steps": "steps=step_limit-2",
                "tokens": "token_spent=token_limit-1",
                "tools": "tool_spent=tool_limit",
            }[block]
            work.connection.execute(
                f"UPDATE worker_runs SET {assignment} WHERE id=?", (worker["id"],)
            )
        elif block == "unknown":
            work.connection.execute(
                "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,"
                "worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at) "
                "VALUES ('unknown-capacity','unknown-model','worker',?,?,?,'protocol','fixture',1,1,'unknown',0)",
                (store.workspace.id, worker["quest_id"], worker["id"]),
            )
    service = IndependentReviewService(manager, max_rounds=1 if block == "rounds" else 2)
    result = asyncio.run(service.review(worker["id"]))
    assert result["verdict"] == "BLOCKED" and not result["revision_applied"]
    assert manager.get(worker["id"])["state"] == "review_pending"
    assert len(model.requests) == 3 and not events(store, "review.started")
    assert not asyncio.run(service.tick())


@pytest.mark.parametrize("mutation", [None, "grant", "source", "unknown", "tokens"])
def test_capacity_feedback_crash_recovery_revalidates_binding_and_requeues_once(
    tmp_path, monkeypatch, mutation
):
    store, manager, model, worker, _, _ = pending(tmp_path, monkeypatch)

    def stopped(*_):
        raise RuntimeError("Crash after committed capacity diagnostic")

    manager.request_revision = stopped
    with pytest.raises(RuntimeError, match="committed capacity"):
        asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    if mutation == "grant":
        GovernanceService(store).revoke_workspace(worker["quest_id"], "work", "local_user")
    elif mutation == "source":
        (tmp_path / "work/findings.json").write_text('{"changed":true}', encoding="utf-8")
    elif mutation in {"unknown", "tokens"}:
        with store.unit_of_work() as work:
            if mutation == "tokens":
                work.connection.execute(
                    "UPDATE worker_runs SET token_spent=token_limit WHERE id=?", (worker["id"],)
                )
            else:
                work.connection.execute(
                    "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,"
                    "worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at) "
                    "VALUES ('unknown-recovery','unknown-model','worker',?,?,?,'protocol','fixture',1,1,'unknown',0)",
                    (store.workspace.id, worker["quest_id"], worker["id"]),
                )
    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    service = IndependentReviewService(resumed)
    assert asyncio.run(service.tick())
    assert resumed.get(worker["id"])["state"] == (
        "queued" if mutation is None else "review_pending"
    )
    assert not asyncio.run(service.tick())
    assert len(events(reopened, "worker.revision_requested")) == (1 if mutation is None else 0)
    assert len(events(reopened, "review.result")) == 1 and len(model.requests) == 3
