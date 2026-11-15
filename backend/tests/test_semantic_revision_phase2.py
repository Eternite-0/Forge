"""Scripted model protocol; actual governed disk actions and fixed numeric subprocesses."""

import asyncio
import hashlib
import json

import pytest
from test_file_revise_phase2 import references
from test_semantic_suite_m7 import CORRECT
from test_worker_intelligence_phase2 import ProtocolModel, delivery, prepared, response, tool

from forge_core.domain import DomainError, ForgeEvent
from forge_core.evidence import NodeVerifier
from forge_core.governance import GovernanceService
from forge_core.independent_review import IndependentReviewService
from forge_core.semantic_evidence import SemanticVerifier
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine, verify_file_revision_chain
from forge_core.worker_runtime import WorkerManager


def pending(tmp_path, monkeypatch, *, tools=None, max_repairs=2):
    # This marker exercises the live evidence gate; no Provider/network is used.
    monkeypatch.setattr(ProtocolModel, "execution_mode", "live")
    store, manager, model, worker = prepared(
        tmp_path,
        [],
        tools=tools or ["file_write", "file_revise"],
        criteria=["semantic_numeric:scheduler-v1:work/scheduler.py"],
        max_repairs=max_repairs,
    )
    requests = []

    async def script(request):
        requests.append(request)
        if len(requests) == 1:
            return tool("file_write", {"path": "work/scheduler.py", "content": "result = 0"})
        if len(requests) == 3:
            assert any("revision_request" in item.get("content", "") for item in request.messages)
            prior = references(request)[0]
            return tool(
                "file_revise",
                {
                    "path": "work/scheduler.py",
                    "prior_receipt_id": prior["receipt_id"],
                    "expected_sha256": prior["sha256"],
                    "content": CORRECT,
                },
                ordinal=2,
            )
        return response(delivery())

    model.generate = script
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    return store, manager, worker, requests


def feedback(store):
    with store.connection() as connection:
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM events WHERE event_type='review.result' ORDER BY seq"
            )
        ]


def assert_blocked(store, manager, worker):
    assert manager.get(worker["id"])["state"] == "review_pending"
    with store.connection() as connection:
        assert connection.execute("SELECT status FROM quests").fetchone()[0] == "running"
        assert (
            connection.execute("SELECT state FROM node_verification_attempts").fetchone()[0]
            == "blocked"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='worker.revision_requested'"
            ).fetchone()[0]
            == 0
        )


def test_real_semantic_failure_feedback_repairs_same_owned_artifact_and_reverifies(
    tmp_path, monkeypatch
):
    store, manager, worker, requests = pending(tmp_path, monkeypatch)
    verifier = NodeVerifier(store, revision_handler=manager.request_revision)
    assert verifier.tick()
    assert manager.get(worker["id"])["state"] == "queued"
    first = feedback(store)
    assert len(first) == 1
    review = first[0]
    assert review["verdict"] == "NEEDS_REVISION"
    assert review["review_context"] == "deterministic_semantic_validation"
    assert review["revision_available"] is True
    assert review["run_id"] == worker["id"]
    assert 1 <= len(review["failed_cases"]) <= 10
    assert all(len(item["actual_excerpt"]) <= 160 for item in review["failed_cases"])
    assert len(requests) == 2
    with store.connection() as connection:
        claim = connection.execute("SELECT * FROM node_verification_attempts").fetchone()
        assert (
            claim["state"] == "pending" and claim["claim_token"] is None and claim["attempts"] == 0
        )
        semantic = connection.execute("SELECT * FROM semantic_test_runs").fetchone()
        report = json.loads(semantic["report_json"])
        assert not report["passed"] and semantic["exit_code"] == 0
        assert review["semantic_run_id"] == semantic["id"]
        assert review["receipt_id"] == semantic["receipt_id"]
        assert review["source_sha256"] == hashlib.sha256(b"result = 0").hexdigest()
        assert (
            review["report_sha256"] == hashlib.sha256(semantic["report_json"].encode()).hexdigest()
        )
        assert review["failed_cases"][0]["case_id"] == next(
            item["case_id"] for item in report["cases"] if not item["passed"]
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM evidence WHERE kind IN ('test','review')"
            ).fetchone()[0]
            == 0
        )
    revised = asyncio.run(manager.run(worker["id"]))
    assert revised["state"] == "review_pending" and revised["repairs"] == 1
    assert revised["steps"] == 4 and revised["token_spent"] == 160 and revised["tool_spent"] == 2
    assert verifier.tick()
    assert manager.get(worker["id"])["state"] == "succeeded"
    with store.connection() as connection:
        assert connection.execute("SELECT status FROM quests").fetchone()[0] == "completed"
        runs = connection.execute("SELECT * FROM semantic_test_runs ORDER BY rowid").fetchall()
        assert [json.loads(row["report_json"])["passed"] for row in runs] == [False, True]
        chain = verify_file_revision_chain(connection, worker["id"], runs[-1]["receipt_id"])
        assert len(chain["revision_receipt_ids"]) == 2
        assert chain["sha256"] == hashlib.sha256(CORRECT.encode()).hexdigest()
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='review.started'"
            ).fetchone()[0]
            == 0
        )
    assert feedback(store) == first


@pytest.mark.parametrize(
    "failure",
    [
        "callback_missing",
        "tool_permission",
        "repairs",
        "steps",
        "tokens",
        "tools",
        "deadline",
        "grant",
    ],
)
def test_failed_suite_cannot_request_unauthorized_or_exhausted_revision(
    tmp_path, monkeypatch, failure
):
    store, manager, worker, requests = pending(
        tmp_path, monkeypatch, tools=["file_write"] if failure == "tool_permission" else None
    )
    if failure in {"repairs", "steps", "tokens", "tools", "deadline"}:
        assignments = {
            "repairs": "repairs=2",
            "steps": "steps=step_limit",
            "tokens": "token_spent=token_limit",
            "tools": "tool_spent=tool_limit",
            "deadline": "deadline=0",
        }
        with store.unit_of_work() as work:
            work.connection.execute(
                f"UPDATE worker_runs SET {assignments[failure]} WHERE id=?", (worker["id"],)
            )
    if failure == "grant":
        GovernanceService(store).revoke_workspace(worker["quest_id"], "work", "local_user")
    handler = None if failure == "callback_missing" else manager.request_revision
    assert NodeVerifier(store, revision_handler=handler).tick()
    assert_blocked(store, manager, worker)
    assert len(requests) == 2
    result = feedback(store)
    assert len(result) == 1 and result[0]["verdict"] == "NEEDS_REVISION"
    assert result[0]["revision_available"] is False


@pytest.mark.parametrize("failure", ["raises", "noop"])
def test_callback_failure_or_fake_success_stays_blocked(tmp_path, monkeypatch, failure):
    store, manager, worker, _ = pending(tmp_path, monkeypatch)

    def callback(*_args):
        if failure == "raises":
            raise RuntimeError("test-only callback failure")
        return {"state": "queued"}

    verifier = NodeVerifier(store, revision_handler=callback)
    assert verifier.tick()
    assert_blocked(store, manager, worker)
    assert not verifier.tick()
    assert len(feedback(store)) == 1


@pytest.mark.parametrize("change", ["result", "claim", "source"])
def test_subprocess_completion_does_not_authorize_stale_feedback(tmp_path, monkeypatch, change):
    store, manager, worker, _ = pending(tmp_path, monkeypatch)
    actual = SemanticVerifier.verify

    def race(verifier, *args, **kwargs):
        result = actual(verifier, *args, **kwargs)
        with store.unit_of_work() as work:
            if change == "result":
                work.append_event(
                    ForgeEvent(
                        event_type="worker.result",
                        quest_id=worker["quest_id"],
                        worker_id=worker["worker_id"],
                        payload={"run_id": worker["id"]},
                    )
                )
            elif change == "claim":
                work.connection.execute("UPDATE node_verification_attempts SET next_attempt=0")
            else:
                (tmp_path / "work/scheduler.py").write_text("result = 1", encoding="utf-8")
        return result

    monkeypatch.setattr(SemanticVerifier, "verify", race)
    assert NodeVerifier(store, revision_handler=manager.request_revision).tick()
    assert_blocked(store, manager, worker)
    assert not feedback(store)


def test_no_feedback_without_actual_failed_subprocess(tmp_path, monkeypatch):
    store, manager, worker, _ = pending(tmp_path, monkeypatch)

    def unavailable(*_args, **_kwargs):
        raise DomainError("test-only subprocess unavailable")

    monkeypatch.setattr(SemanticVerifier, "verify", unavailable)
    assert NodeVerifier(store, revision_handler=manager.request_revision).tick()
    assert_blocked(store, manager, worker)
    assert not feedback(store)


@pytest.mark.parametrize(
    "action", ["pending_tool", "uncertain_tool", "reserved_model", "unknown_model"]
)
def test_unsettled_actions_do_not_authorize_semantic_revision(tmp_path, monkeypatch, action):
    store, manager, worker, _ = pending(tmp_path, monkeypatch)
    with store.unit_of_work() as work:
        if action.endswith("tool"):
            work.connection.execute(
                "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) "
                "VALUES (?,?,?,'file_revise','{}',?)",
                (
                    worker["id"],
                    "unsettled-tool",
                    "unsettled-model",
                    "pending" if action == "pending_tool" else "uncertain",
                ),
            )
        else:
            work.connection.execute(
                "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,"
                "worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at) "
                "VALUES ('unsettled-attempt','unsettled-model','worker',?,?,?,'protocol','fixture',1,1,?,0)",
                (
                    store.workspace.id,
                    worker["quest_id"],
                    worker["id"],
                    "reserved" if action == "reserved_model" else "unknown",
                ),
            )
    assert NodeVerifier(store, revision_handler=manager.request_revision).tick()
    assert_blocked(store, manager, worker)
    assert feedback(store)[0]["revision_available"] is False


def test_actual_manager_repair_bound_is_respected_before_callback(tmp_path, monkeypatch):
    store, manager, worker, _ = pending(tmp_path, monkeypatch, max_repairs=0)
    assert NodeVerifier(store, revision_handler=manager.request_revision).tick()
    assert_blocked(store, manager, worker)
    assert feedback(store)[0]["revision_available"] is False


def test_callback_exception_after_commit_fences_queued_revision(tmp_path, monkeypatch):
    store, manager, worker, _ = pending(tmp_path, monkeypatch)

    def callback(*args):
        manager.request_revision(*args)
        raise RuntimeError("test-only lost callback return")

    assert NodeVerifier(store, revision_handler=callback).tick()
    assert manager.get(worker["id"])["state"] == "blocked"
    with store.connection() as connection:
        assert connection.execute("SELECT status FROM quests").fetchone()[0] == "running"
        assert connection.execute("SELECT status FROM task_nodes").fetchone()[0] == "blocked"
        assert (
            connection.execute("SELECT state FROM node_verification_attempts").fetchone()[0]
            == "blocked"
        )
    assert len(feedback(store)) == 1


def restart_manager(tmp_path, manager):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    return store, WorkerManager(
        store,
        manager.providers,
        ToolEngine(store),
        team_authority=TeamCompositionService(store),
        max_repairs=manager.max_repairs,
    )


def crash_before_callback(store):
    def crash(*_args):
        raise KeyboardInterrupt("test-only process loss before callback")

    with pytest.raises(KeyboardInterrupt):
        NodeVerifier(store, revision_handler=crash).tick()
    assert feedback(store)[0]["revision_available"] is True


def test_semantic_feedback_crash_recovers_once_without_model_review(tmp_path, monkeypatch):
    store, manager, worker, requests = pending(tmp_path, monkeypatch)
    crash_before_callback(store)
    reopened, recovered = restart_manager(tmp_path, manager)
    reviewer = IndependentReviewService(recovered)
    assert asyncio.run(reviewer.tick())
    assert recovered.get(worker["id"])["state"] == "queued"
    assert not asyncio.run(reviewer.tick())
    assert len(requests) == 2
    with reopened.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='worker.revision_requested'"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='review.started'"
            ).fetchone()[0]
            == 0
        )
        claim = connection.execute("SELECT * FROM node_verification_attempts").fetchone()
        assert claim["state"] == "pending" and claim["claim_token"] is None
    assert asyncio.run(recovered.run(worker["id"]))["state"] == "review_pending"
    assert NodeVerifier(reopened, revision_handler=recovered.request_revision).tick()
    assert recovered.get(worker["id"])["state"] == "succeeded"
    assert len(requests) == 4


@pytest.mark.parametrize(
    "change",
    [
        "result",
        "uncertain_tool",
        "unknown_model",
        "tokens",
        "tools",
        "grant",
        "source",
        "no_permission",
    ],
)
def test_semantic_crash_recovery_rechecks_current_admission(tmp_path, monkeypatch, change):
    store, manager, worker, requests = pending(
        tmp_path, monkeypatch, tools=["file_write"] if change == "no_permission" else None
    )
    if change == "no_permission":
        assert NodeVerifier(store, revision_handler=manager.request_revision).tick()
    else:
        crash_before_callback(store)
    with store.unit_of_work() as work:
        if change == "result":
            work.append_event(
                ForgeEvent(
                    event_type="worker.result",
                    quest_id=worker["quest_id"],
                    worker_id=worker["worker_id"],
                    payload={"run_id": worker["id"]},
                )
            )
        elif change == "uncertain_tool":
            work.connection.execute(
                "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) "
                "VALUES (?,'uncertain-after-crash','uncertain-model','file_revise','{}','uncertain')",
                (worker["id"],),
            )
        elif change == "unknown_model":
            work.connection.execute(
                "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,"
                "worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at) "
                "VALUES ('unknown-after-crash','unknown-model','worker',?,?,?,'protocol','fixture',1,1,'unknown',0)",
                (store.workspace.id, worker["quest_id"], worker["id"]),
            )
        elif change in {"tokens", "tools"}:
            column = "token" if change == "tokens" else "tool"
            work.connection.execute(
                f"UPDATE worker_runs SET {column}_spent={column}_limit WHERE id=?",
                (worker["id"],),
            )
        elif change == "source":
            (tmp_path / "work/scheduler.py").write_text("result = 1", encoding="utf-8")
    if change == "grant":
        GovernanceService(store).revoke_workspace(worker["quest_id"], "work", "local_user")
    reopened, recovered = restart_manager(tmp_path, manager)
    reviewer = IndependentReviewService(recovered)
    assert asyncio.run(reviewer.tick()) is (change != "result")
    assert not asyncio.run(reviewer.tick())
    assert recovered.get(worker["id"])["state"] == "review_pending"
    assert len(requests) == 2
    with reopened.connection() as connection:
        assert connection.execute("SELECT status FROM quests").fetchone()[0] == "running"
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='worker.revision_requested'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='review.started'"
            ).fetchone()[0]
            == 0
        )
