"""Programmatic driver regressions; no Provider requests or model-quality claims."""

import asyncio
import hashlib
import importlib.util
import json
import sqlite3
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_worker_intelligence_phase2 import prepared, tool

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_superagent_capability.py"
SPEC = importlib.util.spec_from_file_location("verify_superagent_capability_test", SCRIPT)
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


@pytest.fixture
def pending_python(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["python_unittest"])
    directory = tmp_path / "work" / worker["quest_id"]
    directory.mkdir()
    sources = {"candidate.py": b"# Candidate fixture\n", "test_candidate.py": b"# Test fixture\n"}
    for name, data in sources.items():
        (directory / name).write_bytes(data)
    parameters = {
        "path": f"work/{worker['quest_id']}",
        "manifest": {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()},
        "test_module": "test_candidate",
    }
    model.responses.append(tool("python_unittest", parameters))
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["awaiting_authorization"] == 1
    # Construct the driver's live-metadata admission condition after the Mock
    # protocol has produced the actual Core approval checkpoint. No live call
    # or live evidence is claimed by this fixture.
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET execution_mode='live'")
    approval = driver.rows(store.path, "SELECT * FROM approvals")[0]
    return store, worker, approval, parameters


def test_pending_r2_source_is_reserved_worker_call_before_any_action_intent(pending_python):
    store, worker, approval, _ = pending_python
    assert not driver.rows(store.path, "SELECT id FROM action_intents")
    accepted = driver.approved_python_requests(
        store.path, store.workspace.root, {worker["quest_id"]}
    )
    assert [item["id"] for item in accepted] == [approval["id"]]
    assert approval["decided_by"] is approval["consumed_at"] is None
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='running',awaiting_authorization=0")
        work.connection.execute("UPDATE worker_tool_calls SET state='running'")
    assert (
        driver.approved_python_requests(store.path, store.workspace.root, {worker["quest_id"]})
        == []
    )


@pytest.mark.parametrize(
    "module,extra_file,accepted",
    [
        ("test_spec_coverage", False, True),
        ("test_spec_coverage", True, False),
        ("candidate", False, False),
        ("test_../escape", False, False),
        ("test_" + "x" * 65, False, False),
    ],
)
def test_python_approval_accepts_exact_additional_test_module_only(
    pending_python, module, extra_file, accepted
):
    store, worker, approval, parameters = pending_python
    directory = store.workspace.root / parameters["path"]
    # Invalid names are rejected before source reads. Only create normal files.
    source = b"# Additional independent test module\n"
    if "/" not in module:
        (directory / (module + ".py")).write_bytes(source)
    parameters = {
        **parameters,
        "test_module": module,
        "manifest": {
            "candidate.py": hashlib.sha256((directory / "candidate.py").read_bytes()).hexdigest(),
            module + ".py": hashlib.sha256(source).hexdigest(),
        },
    }
    if extra_file:
        parameters["manifest"]["test_candidate.py"] = hashlib.sha256(
            (directory / "test_candidate.py").read_bytes()
        ).hexdigest()
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_tool_calls SET arguments_json=?", (json.dumps(parameters),)
        )
        work.connection.execute(
            "UPDATE approvals SET parameter_digest=?", (driver.parameter_digest(parameters),)
        )
    if accepted:
        result = driver.approved_python_requests(
            store.path, store.workspace.root, {worker["quest_id"]}
        )
        assert [item["id"] for item in result] == [approval["id"]]
    else:
        with pytest.raises(driver.Failure, match="manifest_out_of_scope"):
            driver.approved_python_requests(
                store.path, store.workspace.root, {worker["quest_id"]}
            )
    assert not driver.rows(store.path, "SELECT id FROM action_intents")


@pytest.mark.parametrize(
    "tamper", ["digest", "parameter_path", "scope", "revision", "policy", "source"]
)
def test_exact_python_approval_rejects_stale_or_rebound_source(pending_python, tamper):
    store, worker, approval, parameters = pending_python
    with store.unit_of_work() as work:
        c = work.connection
        if tamper == "digest":
            c.execute("UPDATE approvals SET parameter_digest=?", ("0" * 64,))
        elif tamper == "parameter_path":
            rebound = {**parameters, "path": "work/other_task"}
            c.execute("UPDATE worker_tool_calls SET arguments_json=?", (json.dumps(rebound),))
            c.execute(
                "UPDATE approvals SET parameter_digest=?", (driver.parameter_digest(rebound),)
            )
        elif tamper == "scope":
            c.execute(
                "UPDATE approvals SET resource_scope=?", (str(store.workspace.root / "work"),)
            )
        elif tamper == "revision":
            c.execute("UPDATE approvals SET plan_revision=plan_revision+1")
        elif tamper == "policy":
            c.execute("UPDATE approvals SET policy_version='unknown'")
        else:
            (store.workspace.root / parameters["path"] / "candidate.py").write_bytes(b"changed")
    with pytest.raises((driver.Failure, ValueError)):
        driver.approved_python_requests(store.path, store.workspace.root, {worker["quest_id"]})
    assert driver.rows(
        store.path, "SELECT status,decided_by FROM approvals WHERE id=?", (approval["id"],)
    ) == [{"status": "PENDING", "decided_by": None}]


def test_restart_boundary_rejects_unknown_effect_and_block_reason_comes_from_event(pending_python):
    store, worker, _approval, _ = pending_python
    assert driver.restart_boundary(store.path)
    observations = driver.blocked_observations(store.path)
    assert observations == [
        {
            "run_id": worker["id"],
            "state": "blocked",
            "awaiting_authorization": 1,
            "reason": "approval_required",
        }
    ]
    with store.unit_of_work() as work:
        work.connection.execute(
            """INSERT INTO action_intents
            SELECT 'inflight','unknown-call',quest_id,worker_id,id,plan_id,plan_revision,
            'browser_get','{}','fixture','http://example.invalid','R2',NULL,execution_mode,
            'executing','fixture','fixture' FROM worker_runs WHERE id=?""",
            (worker["id"],),
        )
    assert not driver.restart_boundary(store.path)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE action_intents SET state='uncertain' WHERE id='inflight'")
    assert not driver.restart_boundary(store.path)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE action_intents SET state='failed' WHERE id='inflight'")
    assert driver.restart_boundary(store.path)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='queued'")
    assert not driver.restart_boundary(store.path)


def test_finally_preserves_first_failure_completed_checks_and_actual_team_condition(
    pending_python, monkeypatch
):
    store, worker, _approval, _ = pending_python
    private_objective = "PRIVATE OBJECTIVE MUST NOT BE PUBLISHED"
    metadata = {
        "execution_team": {
            "strategy": "single",
            "assignments": [{"role": "research", "objective": private_objective, "depends_on": []}],
        }
    }
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET metadata=?,status='completed' WHERE id=?",
            (json.dumps(metadata), worker["quest_id"]),
        )
    observation = driver.task_team_observation(store.path, worker["quest_id"])
    assert private_objective not in json.dumps(observation)
    assert driver.strategy_observed(observation, "single")
    assert not driver.strategy_observed(observation, "fixed")
    assert not driver.strategy_observed(observation, "adaptive")
    fixed = deepcopy(observation)
    fixed.update(
        execution_team={
            "strategy": "sequential",
            "assignments": [
                {"role": "research", "depends_on": []},
                {"role": "architect", "depends_on": [0]},
            ],
        },
        selected_strategy="sequential",
        actual_worker_count=2,
        actual_roles=["research_1", "architect_2"],
        actual_dependencies=[{"source": "task_1", "target": "task_2", "condition": "success"}],
    )
    assert driver.strategy_observed(fixed, "fixed")
    assert driver.strategy_observed(fixed, "adaptive")
    fixed["actual_worker_count"] = 1
    assert not driver.strategy_observed(fixed, "fixed")
    calls = []

    def fail_stop():
        raise RuntimeError("Private teardown content")

    def fail_metrics(_database):
        raise ValueError("Private diagnostic content")

    monkeypatch.setattr(driver.metrics, "read_metrics", fail_metrics)
    monkeypatch.setattr(
        driver.scenarios,
        "verify_artifact",
        lambda *args: {"passed": True, "execution_mode": "programmatic_independent"},
    )
    monkeypatch.setattr(driver.baseline, "snapshot", lambda _db: {"calls": [{"state": "settled"}]})
    report = {
        "status": "failed",
        "failure_code": "original_live_failure",
        "checks": {},
        "tasks": {},
    }
    driver.finalize_observations(
        SimpleNamespace(stop=fail_stop),
        SimpleNamespace(shutdown=lambda **kwargs: calls.append(kwargs)),
        store.path,
        report,
        task_count=3,
        observe_tasks=lambda: driver.collect_task_results(
            store.path,
            store.workspace.root,
            {"L1": worker["quest_id"]},
            {"L1": {"source_sha256": "fixture"}},
            report,
            "adaptive",
        ),
    )
    assert report["failure_code"] == "original_live_failure" and report["status"] == "failed"
    assert report["checks"]["artifact_L1"]
    assert report["tasks"]["L1"]["independent_check"]["passed"]
    assert calls == [{"wait": False, "cancel_futures": True}]
    assert [item["stage"] for item in report["diagnostic_errors"]] == ["core_stop", "metrics"]
    assert "Private" not in json.dumps(report)


def sqlite_error(code, name):
    exc = sqlite3.OperationalError("Private SQL/path text must not be published")
    exc.sqlite_errorcode, exc.sqlite_errorname = code, name
    return exc


def test_read_only_retry_preserves_explicit_busy_ioerr_codes_and_stops_at_three(monkeypatch):
    observations, attempts, waits = [], [], []
    token = driver._READ_OBSERVATIONS.set(observations)
    monkeypatch.setattr(driver.time, "sleep", waits.append)
    errors = [
        sqlite_error(sqlite3.SQLITE_BUSY, "SQLITE_BUSY"),
        sqlite_error(522, "SQLITE_IOERR_SHORT_READ"),
    ]

    def read():
        attempts.append(1)
        if errors:
            raise errors.pop(0)
        return {"settled": True}

    try:
        assert driver.read_only(read, stage="fixture_read") == {"settled": True}
        assert len(attempts) == 3 and waits == [0.05, 0.1]
        assert [
            (item["attempt"], item["sqlite_errorcode"], item["will_retry"]) for item in observations
        ] == [(1, 5, True), (2, 522, True)]
        final = sqlite_error(sqlite3.SQLITE_IOERR, "SQLITE_IOERR")
        attempts.clear()

        def always_fail():
            attempts.append(1)
            raise final

        with pytest.raises(sqlite3.OperationalError) as rejected:
            driver.read_only(always_fail, stage="fixture_final_read")
        assert rejected.value is final and len(attempts) == 3
        assert observations[-1]["attempt"] == 3 and not observations[-1]["will_retry"]
        assert "Private" not in json.dumps(observations)
    finally:
        driver._READ_OBSERVATIONS.reset(token)


@pytest.mark.parametrize(
    "error",
    [
        sqlite_error(sqlite3.SQLITE_ERROR, "SQLITE_ERROR"),
        sqlite3.OperationalError("no explicit code"),
        ValueError("not SQLite"),
    ],
)
def test_read_only_does_not_retry_unclassified_or_nontransient_failures(monkeypatch, error):
    attempts, waits = [], []
    monkeypatch.setattr(driver.time, "sleep", waits.append)

    def read():
        attempts.append(1)
        raise error

    with pytest.raises(type(error)):
        driver.read_only(read, stage="not_retryable")
    assert len(attempts) == 1 and not waits


def test_final_snapshot_retry_diagnostics_keep_original_failure_and_remaining_tasks(
    tmp_path, monkeypatch
):
    database = tmp_path / "existing.sqlite3"
    database.touch()
    observations, attempts, artifact_attempts = [], [], []
    token = driver._READ_OBSERVATIONS.set(observations)
    monkeypatch.setattr(driver.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(driver, "rows", lambda *args: [{"id": "q1"}, {"id": "q2"}])

    def teams(_database, quest_id):
        if quest_id == "q1":
            raise sqlite_error(sqlite3.SQLITE_ERROR, "SQLITE_ERROR")
        return {
            "actual_worker_count": 2,
            "actual_roles": ["research", "architect"],
            "execution_team": None,
        }

    def artifact(_workspace, quest_id, _level):
        artifact_attempts.append(quest_id)
        if quest_id == "q1":
            raise ValueError("private artifact text")
        return {"passed": True, "execution_mode": "programmatic_independent"}

    def final_snapshot(_database):
        attempts.append(1)
        raise sqlite_error(sqlite3.SQLITE_IOERR, "SQLITE_IOERR")

    monkeypatch.setattr(driver, "task_team_observation", teams)
    monkeypatch.setattr(driver.scenarios, "verify_artifact", artifact)
    monkeypatch.setattr(driver.metrics, "read_metrics", lambda _database: {"model_calls": 24})
    monkeypatch.setattr(driver.baseline, "snapshot", final_snapshot)
    report = {"status": "failed", "failure_code": "original_failure", "checks": {}, "tasks": {}}
    try:
        driver.finalize_observations(
            SimpleNamespace(stop=lambda: None),
            SimpleNamespace(shutdown=lambda **kwargs: None),
            database,
            report,
            task_count=3,
            observe_tasks=lambda: driver.collect_task_results(
                database,
                tmp_path,
                {"L1": "q1", "L3": "q2"},
                {level: {"source_sha256": "fixture"} for level in ["L1", "L3"]},
                report,
                "adaptive",
            ),
        )
    finally:
        driver._READ_OBSERVATIONS.reset(token)
    assert report["failure_code"] == "original_failure" and report["status"] == "failed"
    assert report["metrics"]["model_calls"] == 24
    assert report["tasks"]["L3"]["independent_check"]["passed"] and report["checks"]["artifact_L3"]
    assert len(attempts) == 3 and len(observations) == 3
    assert [item["stage"] for item in report["diagnostic_errors"]] == [
        "task_team_observation",
        "task_artifact_observation",
        "final_snapshot",
    ]
    assert report["diagnostic_errors"][-1]["sqlite_errorcode"] == sqlite3.SQLITE_IOERR
    assert "private" not in json.dumps(report).lower()
    driver.collect_task_results(
        database,
        tmp_path,
        {"L1": "q1", "L3": "q2"},
        {level: {"source_sha256": "fixture"} for level in ["L1", "L3"]},
        report,
        "adaptive",
    )
    assert artifact_attempts == ["q1", "q2"]


def test_final_budget_and_source_checks_include_calls_settled_during_shutdown(tmp_path, monkeypatch):
    database = tmp_path / "database.sqlite3"
    database.touch()
    observed = []
    monkeypatch.setattr(driver.metrics, "read_metrics", lambda _db: {"model_calls": 2})
    final_state = {"calls": [{"state": "settled"}, {"state": "settled"}]}
    monkeypatch.setattr(driver, "snapshot", lambda _db: final_state)
    monkeypatch.setattr(driver, "source_digest", lambda: "changed_after_recap")

    def final_budget(db, state, count):
        observed.append((db, state, count))
        raise driver.Failure("per_task_budget_ceiling")

    monkeypatch.setattr(driver, "check_budget", final_budget)
    report = {
        "status": "failed",
        "failure_code": "original_failure",
        "source_sha256": "frozen",
        "checks": {},
    }
    driver.finalize_observations(
        SimpleNamespace(stop=lambda: None),
        SimpleNamespace(shutdown=lambda **_kwargs: None),
        database,
        report,
        task_count=3,
    )
    assert observed == [(database.resolve(), final_state, 3)]
    assert report["failure_code"] == "original_failure"
    assert not report["checks"]["final_per_task_budget"]
    assert not report["checks"]["source_unchanged_after_drain"]
    assert report["checks"]["final_calls_settled"]
    assert report["diagnostic_errors"] == [
        {"stage": "final_budget", "exception_type": "Failure"}
    ]


def test_router_requests_submit_in_order_without_waiting_for_background_workers():
    order = []

    def submit(level):
        order.append("submit_" + level)
        return SimpleNamespace(result=lambda: order.append("router_response_" + level))

    driver.submit_in_order(["L1", "L2"], submit)
    assert order == ["submit_L1", "router_response_L1", "submit_L2", "router_response_L2"]
    order.clear()

    def rejected(level):
        order.append(level)
        return SimpleNamespace(
            result=lambda: (_ for _ in ()).throw(
                driver.CoreAPIError(409, {"detail": "Invalid Router model decision"})
            )
        )

    with pytest.raises(driver.CoreAPIError):
        driver.submit_in_order(["L1", "L2"], rejected)
    assert order == ["L1"]


def test_core_api_error_keeps_only_controlled_detail_code_and_hash():
    class Core:
        def call(self, *_args, **_kwargs):
            return {
                "status": 409,
                "body": {"code": "HTTP_409", "detail": "Invalid Router model decision"},
            }

    with pytest.raises(driver.CoreAPIError) as caught:
        driver.core_api(Core(), "POST", "/api/conversation/message", {"content": "unused"})
    detail = driver.error_metadata(caught.value)
    assert str(caught.value) == "core_api_status_409"
    assert detail["http_status"] == 409 and detail["detail_code"] == "invalid_router_model_decision"
    assert len(detail["detail_sha256"]) == 64
    private = driver.error_metadata(
        driver.CoreAPIError(409, {"detail": "Private Provider content / secret"})
    )
    assert private["detail_code"] == "unclassified_core_detail"
    assert "Private" not in json.dumps(private) and "secret" not in json.dumps(private)


def shutdown_counts(*, reserved=0, unknown=0):
    return {
        "model_calls": 1,
        "provider_reserved": reserved,
        "provider_unknown": unknown,
        "worker_model_reserved": 0,
        "tool_inflight": 0,
        "tool_unknown": 0,
        "worker_tool_running": 0,
        "worker_tool_unknown": 0,
        "active_workers": 0,
    }


def test_failure_shutdown_waits_for_running_future_and_ledger_before_stop(tmp_path, monkeypatch):
    database = tmp_path / "database.sqlite3"
    database.touch()
    clock, order = [0.0], []
    future = Future()
    future.set_running_or_notify_cancel()

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] >= 1 and not future.done():
            future.set_result({"accepted": True})

    monkeypatch.setattr(driver.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(driver.time, "sleep", sleep)
    monkeypatch.setattr(
        driver,
        "shutdown_snapshot",
        lambda *_args, **_kwargs: shutdown_counts(reserved=int(clock[0] < 3)),
    )
    monkeypatch.setattr(driver.metrics, "read_metrics", lambda *_args: {"model_calls": 1})
    monkeypatch.setattr(driver, "snapshot", lambda *_args: {"calls": [{"state": "settled"}]})
    report = {"status": "failed", "failure_code": "original_http_409", "checks": {}}
    core = SimpleNamespace(
        process=SimpleNamespace(poll=lambda: None), stop=lambda: order.append(("stop", clock[0]))
    )
    driver.finalize_observations(
        core,
        SimpleNamespace(shutdown=lambda **_kwargs: order.append(("executor", clock[0]))),
        database,
        report,
        task_count=3,
        futures={"L1": future},
    )
    assert report["failure_code"] == "original_http_409" and report["status"] == "failed"
    assert report["shutdown_drain"]["outcome"] == "drained"
    assert order[0][0] == "stop" and order[0][1] >= 5
    assert future.result() == {"accepted": True}


@pytest.mark.parametrize("mode", ["reserved", "unknown", "read_unavailable"])
def test_shutdown_drain_is_bounded_and_never_replays_unknown(tmp_path, monkeypatch, mode):
    database = tmp_path / "database.sqlite3"
    database.touch()
    clock, reads = [0.0], []
    future = Future()
    future.set_running_or_notify_cancel()
    if mode != "reserved":
        future.set_exception(driver.CoreAPIError(409, {"detail": "Invalid Router model decision"}))
    monkeypatch.setattr(driver.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        driver.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def read(*_args, **_kwargs):
        reads.append(1)
        if mode == "read_unavailable":
            raise sqlite_error(sqlite3.SQLITE_IOERR, "SQLITE_IOERR")
        return shutdown_counts(reserved=int(mode == "reserved"), unknown=int(mode == "unknown"))

    monkeypatch.setattr(driver, "shutdown_snapshot", read)
    report = {"status": "failed", "failure_code": "first_failure"}
    driver.drain_before_stop(
        SimpleNamespace(process=SimpleNamespace(poll=lambda: None)),
        database,
        {"L1": future},
        report,
    )
    drain = report["shutdown_drain"]
    assert drain["elapsed_seconds"] <= 130 and report["failure_code"] == "first_failure"
    assert report["status"] == "failed"
    if mode == "unknown":
        assert drain["outcome"] == "drained_with_unknown_preserved_no_replay"
        assert drain["last_accounting"]["provider_unknown"] == 1
    elif mode == "read_unavailable":
        assert drain["outcome"] == "drain_read_unavailable_no_replay" and len(reads) == 1
    else:
        assert drain["outcome"] == "drain_deadline_no_replay" and not future.done()
