"""Offline Core-prefetch protocol tests; no actual Provider/network quality claims."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_review_envelope_phase2 import envelope
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response

from forge_core.domain import ConversationMessage, DomainError, ModelProfile, ProtocolSupport, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamComposerEngine, TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_prefetch import POLICY, acquire, source_message, verified_sources
from forge_core.worker_runtime import WorkerManager


def prepared(
    tmp_path,
    monkeypatch,
    responses=None,
    *,
    mode="mock",
    source="Original 原始 source\n",
    hypothesis_paths=None,
    source_wire_format=None,
):
    """Real approvals/native read fixture; capacity numbers are explicit offline test policy.

    This isolates runtime protocol behavior from the separately tested capacity
    compiler. No already approved record or historical run is rewritten.
    Return (store, manager, scripted_model, worker), without starting execution.
    `mode=live` is an offline labelled protocol fixture, never a real Provider run.
    """
    root = tmp_path.resolve()
    store = Store(root / "core.sqlite3", workspace=Workspace.from_path(root))
    (root / "inputs").mkdir()
    (root / "work").mkdir()
    (root / "inputs" / "source.txt").write_text(source, encoding="utf-8", newline="\n")
    raw = (root / "inputs" / "source.txt").read_bytes()
    inventory = [
        {"path": "inputs/source.txt", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    ]
    original = ConversationMessage(
        role="user", content="Inspect inputs/source.txt with complete citations."
    )
    store.add_message(original)
    quest = Quest(
        title="Offline Core source protocol",
        goal=original.content,
        metadata={
            "original_request_message_id": original.id,
            "original_request_sha256": hashlib.sha256(original.content.encode()).hexdigest(),
        },
    )
    store.save_quest(quest)
    graph = {
        "nodes": [
            {
                "key": "n",
                "objective": original.content,
                "success_criteria": ["delivery_review"],
                "budget_tokens": 170000,
                "allowed_tools": ["file_read"],
                "required_input_paths": ["inputs/source.txt"],
                "capacity_bounds": {
                    "max_source_receipt_bytes": 4096,
                    "max_artifact_bytes": 1,
                    "max_delivery_bytes": 2048,
                },
            }
        ],
        "edges": [],
        "max_total_tokens": 180000,
        "coordination_reserve_tokens": 10000,
        "capacity_contract": {
            "version": "verified-capacity-v1",
            "input_inventory": inventory,
            "nodes": {},
            "input_acquisition_policy": POLICY,
            "action_evidence_allowances": {"n": 0},
        },
    }
    from forge_core.source_capacity import measure_required_sources

    graph["nodes"][0]["capacity_bounds"]["max_source_receipt_bytes"] = (
        measure_required_sources(
            store, graph, graph["nodes"][0], inventory
        ).required_source_receipt_bytes
        + 1024
    )
    if hypothesis_paths is not None:
        graph["nodes"][0]["required_input_paths"] = []
        graph["nodes"][0]["capacity_bounds"]["max_source_receipt_bytes"] = 3074
        graph["capacity_contract"]["input_inventory"] = []
        graph["capacity_contract"]["hypothesis_read_paths"] = {"n": hypothesis_paths}
        graph["capacity_contract"]["action_evidence_allowances"] = {"n": 2048}
    if source_wire_format is not None:
        graph["capacity_contract"]["source_wire_format"] = source_wire_format
    graphs = QuestGraphService(store)
    plan = graphs.propose(quest.id, graph, expected_version=0)
    graphs.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    store.save_model(
        ModelProfile(
            id="protocol",
            provider="protocol",
            model_id="fixture",
            display_name="Offline protocol fixture",
            context_window=65536,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )

    class Engine(TeamComposerEngine):
        def _candidate(self, *args, **kwargs):
            member = super()._candidate(*args, **kwargs)
            member.update(
                input_acquisition_policy=POLICY,
                action_evidence_allowance_bytes=2048 if hypothesis_paths is not None else 0,
            )
            return member

    from forge_core import capacity_preflight

    env = envelope(
        max_source_bytes=4096,
        max_artifact_bytes=1,
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
    model = ProtocolModel(responses or [response(delivery())])
    model.execution_mode = mode
    providers = ProviderPool({"protocol": model})
    teams = TeamCompositionService(store, engine=Engine(), capacity_providers=providers)
    proposal = teams.propose(
        quest.id,
        [
            {
                "id": "coding",
                "task_node_key": "n",
                "tools": ["file_read"],
                "input_tokens": 137000,
                "output_tokens": 3000,
                "max_steps": 8,
                "max_tool_calls": 4,
                "max_duration_seconds": 120,
            }
        ],
        {
            "max_members": 1,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 140000,
            "max_total_tool_calls": 4,
            "allowed_tools": ["file_read"],
        },
        expected_version=2,
        strategy="greedy",
    )
    teams.approve(quest.id, 1, proposal["proposal_digest"], expected_version=2, actor="local_user")
    manager = WorkerManager(store, providers, ToolEngine(store), team_authority=teams)
    worker = manager.start(
        quest.id, plan["id"], "n", team_digest=proposal["proposal_digest"], member_index=0
    )
    return store, manager, model, worker


def entries(store, worker):
    with store.connection() as connection:
        connection.execute("BEGIN")
        run = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        return verified_sources(store, connection, run)


def test_first_request_has_complete_native_read_with_zero_model_read_selection(
    tmp_path, monkeypatch
):
    store, manager, model, worker = prepared(tmp_path, monkeypatch)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending"
    assert result["steps"] == 1 and result["tool_spent"] == 1 and result["token_spent"] == 40
    assert len(model.requests) == 1
    acquired = entries(store, worker)
    assert len(acquired) == 1 and acquired[0]["output"]["content"] == "Original 原始 source\n"
    assert source_message(acquired) in list(model.requests[0].messages)
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM worker_messages WHERE role='tool'").fetchone()[
                0
            ]
            == 0
        )
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
        assert (
            connection.execute("SELECT execution_mode FROM action_receipts").fetchone()[0] == "mock"
        )


def test_prefetch_checkpoint_is_idempotent_without_steps_models_or_duplicate_tools(
    tmp_path, monkeypatch
):
    store, manager, model, worker = prepared(tmp_path, monkeypatch)
    token = manager._claim(worker["id"])
    asyncio.run(acquire(manager, worker["id"], token))
    first = entries(store, worker)
    asyncio.run(acquire(manager, worker["id"], token))
    assert entries(store, worker) == first
    assert manager.get(worker["id"])["steps"] == 0
    assert manager.get(worker["id"])["tool_spent"] == 1 and not model.requests
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 1
        assert (
            connection.execute("SELECT count(*) FROM worker_core_source_reads").fetchone()[0] == 1
        )


@pytest.mark.parametrize("fault", ["body", "role", "missing", "input"])
def test_source_or_core_block_tamper_cannot_supply_evidence(tmp_path, monkeypatch, fault):
    store, manager, _model, worker = prepared(tmp_path, monkeypatch)
    token = manager._claim(worker["id"])
    asyncio.run(acquire(manager, worker["id"], token))
    with store.unit_of_work() as work:
        ordinal = work.connection.execute(
            "SELECT message_ordinal FROM worker_core_source_reads"
        ).fetchone()[0]
        if fault == "body":
            work.connection.execute(
                "UPDATE worker_messages SET message_json='{}' WHERE run_id=? AND ordinal=?",
                (worker["id"], ordinal),
            )
        elif fault == "role":
            work.connection.execute(
                "UPDATE worker_messages SET role='assistant' WHERE run_id=? AND ordinal=?",
                (worker["id"], ordinal),
            )
        elif fault == "missing":
            work.connection.execute(
                "DELETE FROM worker_messages WHERE run_id=? AND ordinal=?", (worker["id"], ordinal)
            )
    if fault == "input":
        (tmp_path / "inputs" / "source.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(DomainError):
        entries(store, worker)


@pytest.mark.parametrize(
    "column,value",
    [("binding_json", "{}"), ("result_json", "{}"), ("receipt_id", "fake"), ("state", "pending")],
)
def test_settled_core_binding_and_results_are_immutable(tmp_path, monkeypatch, column, value):
    store, manager, _model, worker = prepared(tmp_path, monkeypatch)
    token = manager._claim(worker["id"])
    asyncio.run(acquire(manager, worker["id"], token))
    with pytest.raises(sqlite3.IntegrityError), store.unit_of_work() as work:
        work.connection.execute(f"UPDATE worker_core_source_reads SET {column}=?", (value,))


def test_absent_native_reservation_cannot_borrow_core_origin(tmp_path, monkeypatch):
    _store, manager, _model, worker = prepared(tmp_path, monkeypatch)
    token = manager._claim(worker["id"])
    with pytest.raises(DomainError, match="not reserved"):
        manager.tools.prepare(
            call_id="core_source_" + "0" * 32,
            quest_id=worker["quest_id"],
            worker_id=worker["worker_id"],
            run_id=worker["id"],
            tool_name="file_read",
            parameters={"path": "inputs/source.txt"},
            lease_token=token,
        )


def test_changed_input_rejects_before_provider_and_does_not_gain_source_authority(
    tmp_path, monkeypatch
):
    _store, manager, model, worker = prepared(tmp_path, monkeypatch)
    (tmp_path / "inputs" / "source.txt").write_text("changed", encoding="utf-8")
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked"
    assert not model.requests and manager.get(worker["id"])["tool_spent"] == 0


class InjectedCrash(BaseException):
    """Offline crash boundary; never substituted into a Provider response."""


@pytest.mark.parametrize("settle_first", [True, False])
def test_crash_after_admission_reuses_only_a_genuinely_settled_native_receipt(
    tmp_path, monkeypatch, settle_first
):
    store, manager, model, worker = prepared(tmp_path, monkeypatch)
    token = manager._claim(worker["id"])
    execute = manager.tools.execute
    calls = []

    async def crash(**kwargs):
        calls.append(kwargs["call_id"])
        if settle_first:
            await execute(**kwargs)
        raise InjectedCrash

    monkeypatch.setattr(manager.tools, "execute", crash)
    with pytest.raises(InjectedCrash):
        asyncio.run(acquire(manager, worker["id"], token))
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET lease_until=0 WHERE id=?", (worker["id"],))
    reopened = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    restored = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    result = asyncio.run(restored.run(worker["id"]))
    assert len(calls) == 1
    assert result["tool_spent"] == 1
    with reopened.connection() as connection:
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == int(
            settle_first
        )
        assert (
            connection.execute("SELECT count(*) FROM worker_core_source_reads").fetchone()[0] == 1
        )
    if settle_first:
        assert result["state"] == "review_pending" and result["steps"] == 1
        assert len(model.requests) == 1 and len(entries(reopened, worker)) == 1
    else:
        assert result["state"] == "blocked" and result["steps"] == 0 and not model.requests


@pytest.mark.parametrize("fault", ["unknown_origin", "quota", "missing_quota", "inputs"])
def test_capsule_policy_quota_and_read_set_tamper_rejects_before_model(
    tmp_path, monkeypatch, fault
):
    store, manager, model, worker = prepared(tmp_path, monkeypatch)
    with store.unit_of_work() as work:
        capsule = json.loads(
            work.connection.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
        )
        if fault == "unknown_origin":
            capsule["input_acquisition_policy"] = "unknown-origin"
        elif fault == "quota":
            capsule["action_evidence_allowance_bytes"] = 1
        elif fault == "missing_quota":
            capsule.pop("action_evidence_allowance_bytes")
        else:
            capsule["required_input_paths"] = ["inputs/unauthorized.txt"]
        work.connection.execute("UPDATE worker_capsules SET capsule_json=?", (json.dumps(capsule),))
    with pytest.raises(DomainError):
        asyncio.run(manager.run(worker["id"]))
    assert not model.requests and manager.get(worker["id"])["tool_spent"] == 0


def test_core_admission_cannot_be_borrowed_with_changed_native_parameters(tmp_path, monkeypatch):
    store, manager, _model, worker = prepared(tmp_path, monkeypatch)
    token = manager._claim(worker["id"])

    async def inspect(**kwargs):
        with pytest.raises(DomainError, match="reservation"):
            manager.tools.prepare(**{**kwargs, "parameters": {"path": "work/user.txt"}})
        raise InjectedCrash

    monkeypatch.setattr(manager.tools, "execute", inspect)
    with pytest.raises(InjectedCrash):
        asyncio.run(acquire(manager, worker["id"], token))
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0


def test_legacy_readonly_database_without_core_table_keeps_original_provenance(tmp_path):
    from test_worker_intelligence_phase2 import prepared as legacy_prepared

    store, _manager, _model, worker = legacy_prepared(tmp_path, [response(delivery())])
    # Reconstruct the genuine pre-feature schema shape; no live or retained run is changed.
    with store.unit_of_work() as work:
        work.connection.execute("DROP TABLE worker_core_source_reads")
        work.connection.execute("UPDATE schema_meta SET version=12")
    with closing(
        sqlite3.connect(Path(store.path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        run = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        assert verified_sources(store, connection, run) == []
        assert connection.total_changes == 0
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 12


def test_new_policy_without_admission_table_cannot_become_legacy(tmp_path, monkeypatch):
    store, _manager, _model, worker = prepared(tmp_path, monkeypatch)
    with store.unit_of_work() as work:
        work.connection.execute("DROP TABLE worker_core_source_reads")
    with pytest.raises(DomainError, match="table"):
        entries(store, worker)
