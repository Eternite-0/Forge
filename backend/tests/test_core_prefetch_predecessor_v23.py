"""Offline actual successful predecessor, full Core handoff and restore fences."""

import asyncio
import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from test_core_source_prefetch_v23 import InjectedCrash, entries, prepared
from test_predecessor_delivery_v17 import prepared as original_chain
from test_review_envelope_phase2 import envelope
from test_worker_intelligence_phase2 import delivery, response

from forge_core.domain import DomainError
from forge_core.quest_graph import QuestGraphService
from forge_core.team_composition import TeamComposerEngine, TeamCompositionService
from forge_core.worker_prefetch import POLICY, acquire, completed_delivery_sources, source_message
from forge_core.workspace_backup import (
    BackupError,
    backup_workspace,
    restore_workspace,
    review_restore,
)


def prepared_chain(tmp_path, monkeypatch, *, artifact_input=False, pending_tail=False):
    """Approve the new policy before any existing fixture operation is executed."""
    original_plan = QuestGraphService.propose
    original_propose = TeamCompositionService.propose
    original_candidate = TeamComposerEngine._candidate
    original_init = TeamCompositionService.__init__

    def plan(self, quest_id, graph, **kwargs):
        graph = copy.deepcopy(graph)
        raw = (tmp_path / "work/input.json").read_bytes()
        for node in graph["nodes"]:
            node["budget_tokens"] = 170000
            node["required_input_paths"] = ["work/input.json"] if node["key"] == "producer" else []
            node["capacity_bounds"] = {
                "max_source_receipt_bytes": 8192,
                "max_artifact_bytes": 4096,
                "max_delivery_bytes": 2048,
            }
            if artifact_input and node["key"] == "reader":
                node["allowed_tools"] = ["file_read"]
                node["required_input_paths"] = ["work/source.json"]
                node["required_predecessor_deliveries"] = []
            if node["key"] == "producer":
                node["expected_artifact"] = "work/source.json"
        graph["coordination_reserve_tokens"] = 20000
        graph["max_total_tokens"] = 170000 * len(graph["nodes"]) + 20000
        graph["capacity_contract"] = {
            "version": "verified-capacity-v1",
            "input_inventory": [
                {
                    "path": "work/input.json",
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            ],
            "nodes": {},
            "input_acquisition_policy": POLICY,
            "action_evidence_allowances": {
                node["key"]: 4096 if "file_write" in node["allowed_tools"] else 0
                for node in graph["nodes"]
            },
        }
        from forge_core.source_capacity import measure_required_sources

        for node in graph["nodes"]:
            node["capacity_bounds"]["max_source_receipt_bytes"] = (
                measure_required_sources(
                    self.store, graph, node, graph["capacity_contract"]["input_inventory"]
                ).required_source_receipt_bytes
                + 1024
                + graph["capacity_contract"]["action_evidence_allowances"][node["key"]]
            )
        return original_plan(self, quest_id, graph, **kwargs)

    def init(self, *args, **kwargs):
        kwargs.setdefault("capacity_providers", SimpleNamespace(get=lambda _identifier: object()))
        original_init(self, *args, **kwargs)

    def candidate(self, *args, **kwargs):
        member = original_candidate(self, *args, **kwargs)
        member.update(input_acquisition_policy=POLICY, action_evidence_allowance_bytes=0)
        return member

    def propose(self, quest_id, roles, limits, **kwargs):
        roles = copy.deepcopy(roles)
        for role in roles:
            role["input_tokens"] = 137000
            if artifact_input and role["task_node_key"] == "reader":
                role["tools"] = ["file_read"]
        limits = {
            **limits,
            "max_total_tokens": 140000 * len(roles),
            "allowed_tools": sorted({tool for role in roles for tool in role["tools"]}),
        }
        return original_propose(self, quest_id, roles, limits, **kwargs)

    from forge_core import capacity_preflight

    env = envelope(
        max_source_bytes=8192,
        max_artifact_bytes=4096,
        max_delivery_bytes=2048,
        correction_input_tokens=16000,
    )
    monkeypatch.setattr(
        capacity_preflight,
        "node_review_envelope",
        lambda *a, **k: (
            {**env, "max_source_bytes": a[4]["capacity_bounds"]["max_source_receipt_bytes"]},
            {},
        ),
    )
    monkeypatch.setattr(QuestGraphService, "propose", plan)
    monkeypatch.setattr(TeamCompositionService, "__init__", init)
    monkeypatch.setattr(TeamCompositionService, "propose", propose)
    monkeypatch.setattr(TeamComposerEngine, "_candidate", candidate)
    return original_chain(
        tmp_path,
        reader_write=False,
        reader_criteria=["delivery_review"],
        producer_model_review=True,
        pending_tail=pending_tail,
    )


@pytest.mark.parametrize("artifact_input", [False, True])
def test_first_reader_request_gets_true_successful_predecessor_without_model_tool_fiction(
    tmp_path, monkeypatch, artifact_input
):
    store, manager, model, _quest, _plan, _producer, reader, expected = prepared_chain(
        tmp_path, monkeypatch, artifact_input=artifact_input
    )
    previous_requests = len(model.requests)
    model.responses.append(response(delivery("Compared the full independently verified source.")))
    result = asyncio.run(manager.run(reader["id"]))
    assert result["state"] == "review_pending"
    acquired = entries(store, reader)
    assert len(acquired) == 1 and result["tool_spent"] == 1 and result["steps"] == 1
    assert len(model.requests) == previous_requests + 1
    assert source_message(acquired) in list(model.requests[-1].messages)
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM worker_tool_calls WHERE run_id=?", (reader["id"],)
            ).fetchone()[0]
            == 0
        )
        connection.execute("BEGIN")
        run = connection.execute("SELECT * FROM worker_runs WHERE id=?", (reader["id"],)).fetchone()
        sources = completed_delivery_sources(store, connection, run)
    if artifact_input:
        assert (
            acquired[0]["receipt_id"]
            and acquired[0]["output"]["content"] == '{"analysis":"evidence"}'
        )
        assert sources == []
    else:
        assert acquired[0]["receipt_id"] is None
        assert sources[0]["output"]["delivery"] == expected
        assert sources[0]["output"]["execution_mode"] == "live"


def test_completed_delivery_prefetch_rechecks_true_source_pass_instead_of_trusting_saved_body(
    tmp_path, monkeypatch
):
    store, manager, model, _quest, _plan, producer, reader, _expected = prepared_chain(
        tmp_path, monkeypatch
    )
    model.responses.append(response(delivery()))
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    assert entries(store, reader)
    with store.unit_of_work() as work:
        event = work.connection.execute(
            "SELECT id,payload FROM events WHERE event_type='review.result' AND json_extract(payload,'$.run_id')=?",
            (producer["id"],),
        ).fetchone()
        payload = json.loads(event["payload"])
        payload["packet_sha256"] = "0" * 64
        work.connection.execute(
            "UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), event["id"])
        )
    with pytest.raises(DomainError):
        entries(store, reader)


@pytest.mark.parametrize("when", ["before_snapshot", "after_snapshot"])
def test_restore_fence_retains_unresolved_core_reads_even_without_native_action_intent(
    tmp_path, monkeypatch, when
):
    root = tmp_path / "workspace"
    store, manager, _model, worker = prepared(root, monkeypatch)
    (root / "data").mkdir()
    destination = root / "data/forge.sqlite3"

    def copy_database():
        with (
            closing(sqlite3.connect(store.path)) as original,
            closing(sqlite3.connect(destination)) as target,
        ):
            original.backup(target)

    copy_database()
    archive = tmp_path / "backup"
    if when == "after_snapshot":
        backup_workspace(root, archive)
    token = manager._claim(worker["id"])

    async def crash(**_kwargs):
        raise InjectedCrash

    monkeypatch.setattr(manager.tools, "execute", crash)
    with pytest.raises(InjectedCrash):
        asyncio.run(acquire(manager, worker["id"], token))
    copy_database()
    if when == "before_snapshot":
        backup_workspace(root, archive)
    restored = restore_workspace(root, archive)
    marker = json.loads((root / ".forge-restore-fence.json").read_text(encoding="utf-8"))
    with pytest.raises(BackupError, match="Unresolved"):
        review_restore(
            root,
            expected_manifest_sha256=marker["manifest_sha256"],
            expected_rollback_directory=restored["rollback_directory"],
            reason="Offline unknown Core read must remain fenced",
        )
    assert (root / ".forge-restore-fence.json").is_file()
    with closing(sqlite3.connect(root / "data/forge.sqlite3")) as connection:
        assert connection.execute("SELECT count(*) FROM restore_reviews").fetchone()[0] == 0
