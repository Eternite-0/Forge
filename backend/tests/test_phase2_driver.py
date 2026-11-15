"""Offline acceptance-driver checks against real temporary SQLite archives."""

import asyncio
import importlib.util
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.context_service import ContextService
from forge_core.domain import ConversationMessage, DomainError, ForgeEvent
from forge_core.independent_review import IndependentReviewService
from forge_core.settings import Workspace
from forge_core.store import Store


@pytest.fixture
def driver():
    path = Path(__file__).resolve().parents[1] / "scripts/phase2/live_e2e.py"
    spec = importlib.util.spec_from_file_location("forge_phase2_driver_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inspection_missing_database_does_not_create_or_claim_a_run(tmp_path, driver):
    report = driver.inspect_execution(tmp_path)
    assert report["final_status"] == "startup_failed"
    assert not (tmp_path / "data/forge.sqlite3").exists()


def test_inspection_reads_durable_budget_values_and_keeps_unknown_usage_visible(tmp_path, driver):
    store = Store(workspace=Workspace.from_path(tmp_path))
    with store.unit_of_work() as work:
        work.connection.executemany(
            """INSERT INTO model_budget_attempts (
            attempt_id,call_id,purpose,workspace_id,provider_id,model_id,
            input_reserved,output_reserved,state,created_at,input_charged,output_charged)
            VALUES (?,?, 'worker',?,'fixture-provider','fixture-model',100,20,?,0,?,?)""",
            [
                ("attempt-settled", "call-settled", store.workspace.id, "settled", 12, 3),
                ("attempt-unknown", "call-unknown", store.workspace.id, "reserved", None, None),
            ],
        )
    report = driver.inspect_execution(tmp_path)
    assert report["model_calls"] == 2
    assert report["token_usage"]["input"] == 12 and report["token_usage"]["output"] == 3
    assert report["token_usage"]["unknown_attempts"] == 1
    assert report["final_status"] == "no_quest" and report["conversation_count"] == 1
    assert report["cost"] is None
    assert report["artifact_hashes"] == [] and report["workers"] == []


def test_inspection_counts_independent_review_rework_as_retry(tmp_path, driver):
    store = Store(workspace=Workspace.from_path(tmp_path))
    with store.unit_of_work() as work:
        work.append_event(ForgeEvent(event_type="worker.revision_requested", payload={}))
        work.append_event(ForgeEvent(event_type="worker.revision_requested", payload={}))
    report = driver.inspect_execution(tmp_path)
    assert report["retry_breakdown"]["review_revision_requests"] == 2
    assert report["retry_count"] == 2


def test_review_revision_does_not_also_count_as_worker_self_repair(tmp_path, driver):
    verdict = {
        "verdict": "NEEDS_REVISION",
        "findings": ["Clarify the cited finding."],
        "revision_goals": ["Clarify the cited finding using the existing source."],
        "confidence": 0.8,
    }
    store, manager, _model, run = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery()), response(verdict)],
    )
    asyncio.run(manager.run(run["id"]))
    asyncio.run(IndependentReviewService(manager).review(run["id"]))
    assert manager.get(run["id"])["repairs"] == 1
    (tmp_path / "data").mkdir()
    with store.connection() as source, sqlite3.connect(tmp_path / "data/forge.sqlite3") as target:
        source.backup(target)
    report = driver.inspect_execution(tmp_path)
    assert report["retry_breakdown"]["worker_self_repairs"] == 0
    assert report["retry_breakdown"]["review_revision_requests"] == 1
    assert report["retry_count"] == 1
    assert report["retry_accounting"] == "disjoint_event_counts_v2"


@pytest.mark.parametrize("scenario_enabled", [False, True])
def test_driver_inspection_failure_remains_explicit_failure(
    tmp_path, driver, monkeypatch, scenario_enabled
):
    import sqlite3

    class OfflineCore:
        def __init__(self, workspace, **kwargs):
            self.workspace = workspace
            self.limits = kwargs

        def start(self):
            return self

        def stop(self):
            return None

        def tail_log(self, _limit):
            return ""

        def env(self):
            return {
                "FORGE_REVIEW_MAX_ROUNDS": "2",
                "FORGE_WORKER_LEASE_SECONDS": "60",
                "FORGE_PROVIDER_TIMEOUT_SECONDS": "30",
                "FORGE_PROVIDER_MAX_RETRIES": "0",
                "FORGE_MODEL_TOKEN_LIMIT": str(self.limits["token_limit"]),
                "FORGE_MODEL_CONCURRENCY_LIMIT": str(self.limits["concurrency"]),
            }

        def call(self, method, path, payload=None, **_kwargs):
            if path == "/api/conversation":
                return {"body": {"router": {"router_session_id": "offline-router"}}}
            return {"status": 400, "body": {}}

    def unavailable(_workspace):
        raise sqlite3.DatabaseError("offline diagnostic fixture")

    monkeypatch.setenv("FORGE_PROVIDER_KEY_LIVE_TEST", "offline-fixture-not-a-credential")
    monkeypatch.setattr(driver, "REPO", tmp_path)
    monkeypatch.setattr(driver, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(driver, "LiveCore", OfflineCore)
    monkeypatch.setattr(driver, "inspect_execution", unavailable)
    monkeypatch.setattr(
        driver,
        "seed_scenario",
        lambda *_args: {
            "prompt": "offline fixture",
            "limits": {"max_total_tokens": 700, "max_concurrency": 1},
        },
    )
    record = driver.drive(
        SimpleNamespace(
            level="L1",
            scenario=scenario_enabled,
            prompt=None,
            token_limit=1000,
            concurrency=2,
            extra_env=None,
        )
    )
    assert record["inspection_error"] == "DatabaseError"
    assert "execution_inspection_failed" in record["failures"]
    assert record["runtime_settings"]["model_token_limit"] == (700 if scenario_enabled else 1000)
    assert record["runtime_settings"]["model_concurrency_limit"] == (1 if scenario_enabled else 2)


def test_stopped_core_compaction_rebuilds_recent_messages_and_really_reads_source(
    tmp_path, driver, monkeypatch
):
    store = Store(workspace=Workspace.from_path(tmp_path))
    source = ConversationMessage(role="user", content="Do not delete original-results.csv")
    store.add_message(source)
    store.add_message(ConversationMessage(role="assistant", content="Task registered"))
    checked = []
    read_source = ContextService.read_source

    def observe(self, scope, scope_id, source_id):
        checked.append((scope, scope_id, source_id))
        return read_source(self, scope, scope_id, source_id)

    monkeypatch.setattr(ContextService, "read_source", observe)
    report = driver.compact_stopped_core(SimpleNamespace(workspace=tmp_path))
    assert report["archive_created"] is True and report["rebuilt_messages"] == 1
    assert report["source_readback_available"] is True
    assert ("router", "main", source.id) in checked
    assert len(store.list_messages()) == 2


def test_stopped_core_compaction_does_not_mark_noop_as_verified_archive(tmp_path, driver):
    Store(workspace=Workspace.from_path(tmp_path))
    report = driver.compact_stopped_core(SimpleNamespace(workspace=tmp_path))
    assert report["archive_created"] is False
    assert report["source_readback_available"] is False
    assert report["rebuilt_messages"] == 0


def test_stopped_core_compaction_source_verification_failure_is_visible(
    tmp_path, driver, monkeypatch
):
    store = Store(workspace=Workspace.from_path(tmp_path))
    store.add_message(ConversationMessage(role="user", content="Keep the original evidence"))
    store.add_message(ConversationMessage(role="assistant", content="Registered"))

    def reject(*_args, **_kwargs):
        raise DomainError("injected source integrity failure")

    monkeypatch.setattr(ContextService, "read_source", reject)
    with pytest.raises(DomainError, match="integrity"):
        driver.compact_stopped_core(SimpleNamespace(workspace=tmp_path))


@pytest.mark.parametrize(
    "condition,independent,running,expected",
    [
        ("success", False, 0, True),
        ("failure", False, 0, False),
        ("success", True, 0, False),
        ("success", False, 1, False),
    ],
)
def test_blocked_frontier_stops_only_when_no_recovery_branch_can_run(
    driver, condition, independent, running, expected
):
    snapshot = {
        "nodes": [
            {"node_key": "source", "status": "review_pending"},
            {"node_key": "analysis", "status": "proposed"},
            {"node_key": "report", "status": "proposed"},
        ],
        "edges": [
            {"source": "source", "target": "analysis", "condition": condition},
            {"source": "analysis", "target": "report", "condition": "success"},
        ],
        "terminal_review_blocks": ["source"],
        "running_workers": running,
    }
    if independent:
        snapshot["nodes"].append({"node_key": "independent", "status": "proposed"})
    assert driver.remaining_execution_blocked(snapshot) is expected


@pytest.mark.parametrize("matching_result", [True, False])
@pytest.mark.parametrize("review_event_type", ["review.blocked", "review.revision_blocked"])
def test_restart_snapshot_requires_real_completed_node_and_retains_receipts_and_usage(
    tmp_path, driver, matching_result, review_event_type
):
    import asyncio
    import sqlite3

    store, manager, _model, worker = prepared(
        tmp_path, [tool("file_read", {"path": "work/input.txt"}), response(delivery())]
    )
    asyncio.run(manager.run(worker["id"]))
    with store.connection() as connection:
        current_result = connection.execute(
            "SELECT id FROM events WHERE event_type='worker.result' ORDER BY seq DESC LIMIT 1"
        ).fetchone()[0]
    payload = {
        "run_id": worker["id"],
        "result_event_id": current_result if matching_result else "stale-result",
    }
    if review_event_type == "review.revision_blocked":
        feedback = ForgeEvent(
            event_type="review.result",
            quest_id=worker["quest_id"],
            worker_id=worker["worker_id"],
            payload={**payload, "verdict": "NEEDS_REVISION"},
        )
        store.append_event(feedback)
        payload = {"run_id": worker["id"], "review_event_id": feedback.id}
    store.append_event(
        ForgeEvent(
            event_type=review_event_type,
            quest_id=worker["quest_id"],
            worker_id=worker["worker_id"],
            payload=payload,
        )
    )
    (tmp_path / "data").mkdir()
    with (
        sqlite3.connect(store.path) as source,
        sqlite3.connect(tmp_path / "data/forge.sqlite3") as destination,
    ):
        source.backup(destination)
    before = driver.continuity_snapshot(tmp_path, worker["quest_id"])
    assert before["terminal_review_blocks"] == (["n"] if matching_result else [])
    assert before["receipts"] and before["worker_counters"][0]["token_spent"] > 0
    assert driver.compare_continuity(before, before)["completed_nodes_retained"] is False
    # Explicit fixture state: this test verifies continuity observation, not
    # semantic acceptance (which has its own live/Mock boundary tests).
    with sqlite3.connect(tmp_path / "data/forge.sqlite3") as connection:
        connection.execute("UPDATE task_nodes SET status='succeeded'")
    before = driver.continuity_snapshot(tmp_path, worker["quest_id"])
    assert all(driver.compare_continuity(before, before).values())
    after = {**before, "receipts": [], "worker_counters": []}
    compared = driver.compare_continuity(before, after)
    assert compared["receipts_retained"] is False
    assert compared["worker_budget_counters_retained"] is False
