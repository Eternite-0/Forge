"""Restart gate fixtures exercise no Provider calls or original experiment data."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from test_verify_superagent_capability import driver


@pytest.fixture
def boundary_database(tmp_path):
    database = tmp_path / "boundary.sqlite3"
    with sqlite3.connect(database) as c:
        c.executescript("""
            CREATE TABLE model_budget_attempts(state TEXT);
            CREATE TABLE worker_model_reservations(state TEXT);
            CREATE TABLE action_intents(state TEXT);
            CREATE TABLE worker_tool_calls(state TEXT);
            CREATE TABLE worker_runs(state TEXT,lease_token TEXT);
            CREATE TABLE node_verification_attempts(state TEXT);
            CREATE TABLE quests(id TEXT,status TEXT,conversation_id TEXT,plan_revision INTEGER);
            CREATE TABLE quest_plans(id TEXT,quest_id TEXT,revision INTEGER,status TEXT);
            CREATE TABLE task_nodes(plan_id TEXT,node_key TEXT,status TEXT,worker_id TEXT);
            CREATE TABLE messages(role TEXT,metadata TEXT,conversation_id TEXT);
            CREATE TABLE events(id TEXT,event_type TEXT,quest_id TEXT,payload TEXT,conversation_id TEXT);
            INSERT INTO quests VALUES ('L2','running','main',1),('L3','running','main',1);
            INSERT INTO quest_plans VALUES ('plan-L2','L2',1,'approved');
            INSERT INTO task_nodes VALUES
                ('plan-L2','task_1','succeeded','specialist_1'),
                ('plan-L2','task_2','succeeded','specialist_2'),
                ('plan-L2','task_3','proposed',NULL);
            INSERT INTO worker_runs VALUES ('succeeded',NULL),('succeeded',NULL),('blocked',NULL);
            INSERT INTO model_budget_attempts VALUES ('settled');
            INSERT INTO worker_tool_calls VALUES ('pending');
            INSERT INTO events VALUES
                ('done','quest.status_changed','L2','{"to":"completed"}','main'),
                ('claim','worker.result','L2','{}','main'),
                ('other','quest.completed','L3','{}','main');
        """)
    return database


def complete(database):
    with sqlite3.connect(database) as c:
        c.execute("UPDATE quests SET status='completed' WHERE id='L2'")
        c.execute("UPDATE task_nodes SET status='succeeded',worker_id='integrator' WHERE node_key='task_3'")


def notice(database, event_id="done", *, role="assistant", source="router_notice",
           conversation="main", batch=None):
    metadata = {"source": source, "event_id": event_id}
    if batch is not None:
        metadata["source_event_ids"] = batch
    with sqlite3.connect(database) as c:
        c.execute("INSERT INTO messages VALUES (?,?,?)", (role, json.dumps(metadata), conversation))


def safe(database):
    return driver.restart_boundary(database, required_completed_quest_ids=("L2",))


def test_restart_rejects_exact_specialists_finished_undispatched_integrator_gap(boundary_database):
    # Original gate passed this gap: no existing Worker is running, but the DAG
    # still requires its unassigned Integrator. The L3 R2 wait is retained.
    assert driver.restart_boundary(boundary_database)
    assert not safe(boundary_database)
    with sqlite3.connect(boundary_database) as c:
        c.execute("UPDATE quests SET status='completed' WHERE id='L2'")
    notice(boundary_database)
    assert not safe(boundary_database)


@pytest.mark.parametrize("status", ["proposed", "queued", "running", "review_pending", "blocked"])
def test_completed_label_and_notice_cannot_hide_unfinished_current_dag(boundary_database, status):
    complete(boundary_database)
    notice(boundary_database)
    with sqlite3.connect(boundary_database) as c:
        c.execute("UPDATE task_nodes SET status=? WHERE node_key='task_3'", (status,))
    assert not safe(boundary_database)


@pytest.mark.parametrize("notice_arguments", [
    None,
    {"event_id": "claim"},
    {"event_id": "other"},
    {"role": "user"},
    {"source": "worker"},
    {"conversation": "other"},
])
def test_restart_requires_real_completed_notice_in_original_router(boundary_database, notice_arguments):
    complete(boundary_database)
    if notice_arguments is not None:
        notice(boundary_database, **notice_arguments)
    assert not safe(boundary_database)


def test_completed_current_dag_reviewed_notice_and_l3_r2_wait_is_safe(boundary_database):
    complete(boundary_database)
    notice(boundary_database, "claim", batch=["claim", "done"])
    # Historical revisions are not the current DAG; legitimate failed/skipped
    # recovery branches use the same terminal states as Core CompletionGate.
    with sqlite3.connect(boundary_database) as c:
        c.execute("INSERT INTO task_nodes VALUES ('old-plan','old','proposed',NULL)")
        c.execute("UPDATE task_nodes SET status='skipped' WHERE node_key='task_1'")
        c.execute("UPDATE task_nodes SET status='failed' WHERE node_key='task_2'")
    assert safe(boundary_database)
    with sqlite3.connect(boundary_database) as c:
        c.execute("UPDATE quest_plans SET revision=0")
    assert not safe(boundary_database)


@pytest.mark.parametrize("confirmation", [None, False, 1, "true"])
def test_unconfirmed_stop_never_reads_stopped_database_compacts_or_restarts(monkeypatch, confirmation):
    actions = []
    report = {"checks": {}}
    stopped = {"graceful_confirmed": confirmation, "outcome": "unconfirmed"}
    core = SimpleNamespace(stop=lambda: stopped)
    monkeypatch.setattr(driver, "restart_boundary", lambda *_args, **_kwargs: actions.append("read"))
    with pytest.raises(driver.Failure, match="graceful_stop_unconfirmed_no_replay"):
        driver.stop_for_restart(core, "unused", report, required_completed_quest_ids=("L2",))
        actions.extend(["compact", "restart"])
    assert actions == []
    assert report["restart_core_stop"] == stopped
    assert report["checks"]["restart_graceful_stop_confirmed"] is False


@pytest.mark.parametrize("still_safe", [False, True])
def test_confirmed_stop_rechecks_identical_required_completed_quest(monkeypatch, still_safe):
    actions = []
    report = {"checks": {}}

    def boundary(database, *, required_completed_quest_ids):
        actions.append((database, required_completed_quest_ids))
        return still_safe

    monkeypatch.setattr(driver, "restart_boundary", boundary)
    core = SimpleNamespace(stop=lambda: {"graceful_confirmed": True})
    if still_safe:
        driver.stop_for_restart(core, "same-database", report,
                                required_completed_quest_ids=("L2",))
    else:
        with pytest.raises(driver.Failure, match="restart_boundary_changed_no_replay"):
            driver.stop_for_restart(core, "same-database", report,
                                    required_completed_quest_ids=("L2",))
    assert actions == [("same-database", ("L2",))]
    assert report["checks"]["restart_graceful_stop_confirmed"] is True


@pytest.mark.parametrize("original_failure", [None, "original_failure"])
def test_final_unconfirmed_stop_fails_closed_and_keeps_original_failure(tmp_path, original_failure):
    stopped = {"graceful_confirmed": False, "outcome": "already_stopped_unconfirmed"}
    report = {"checks": {}, "status": "passed"}
    if original_failure:
        report.update(status="failed", failure_code=original_failure)
    driver.finalize_observations(
        SimpleNamespace(stop=lambda: stopped), SimpleNamespace(shutdown=lambda **_kwargs: None),
        tmp_path / "absent.sqlite3", report, task_count=2,
    )
    assert report["status"] == "failed"
    assert report["failure_code"] == (
        original_failure or "final_graceful_stop_unconfirmed_no_replay"
    )
    assert report["final_core_stop"] == stopped
    assert report["checks"]["final_graceful_stop_confirmed"] is False


@pytest.mark.parametrize("tamper", [None, "missing", "id", "parameter_digest", "plan_revision"])
def test_stopped_r2_approval_must_keep_exact_pending_identity(monkeypatch, tamper):
    held = [{"id": "approval", "quest_id": "L3", "parameter_digest": "digest", "plan_revision": 1}]
    actual = [dict(held[0])]
    if tamper == "missing":
        actual = []
    elif tamper is not None:
        actual[0][tamper] = "changed"
    observed = []

    def pending(database, workspace, quest_ids):
        observed.append((database, workspace, quest_ids))
        return actual

    monkeypatch.setattr(driver, "restart_boundary", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(driver, "approved_python_requests", pending)
    core = SimpleNamespace(stop=lambda: {"graceful_confirmed": True})
    report = {"checks": {}}
    if tamper is None:
        driver.stop_for_restart(
            core, "database", report, required_completed_quest_ids=("L2",),
            workspace="workspace", held_python_approvals=held,
        )
    else:
        with pytest.raises(driver.Failure, match="restart_approval_boundary_changed_no_replay"):
            driver.stop_for_restart(
                core, "database", report, required_completed_quest_ids=("L2",),
                workspace="workspace", held_python_approvals=held,
            )
    assert observed == [("database", "workspace", {"L3"})]
    assert report["checks"]["restart_exact_pending_approvals_preserved"] is (tamper is None)
