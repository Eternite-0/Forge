"""Programmatic SQLite tests of outcome observations, not real Provider results."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from test_model_recovery_capability_sprint import keep_live_lease
from test_watchdog import worker  # noqa: F401 - shared real SQLite fixture.
from test_worker_runtime import graph  # noqa: F401 - dependency of imported fixture.

from forge_core.watchdog import Watchdog
from forge_core.worker_model_usage import reserve_worker_model_call


def checkpoint(store, run_id, index, *, content="unchanged", path="source.py"):
    output = {"content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO worker_tool_calls VALUES (?,?,?,?,?,'succeeded',?,?)",
            (
                run_id,
                f"read-{index}",
                f"model-{index}",
                "file_read",
                json.dumps({"path": path}),
                f"receipt-{index}",
                json.dumps(
                    {"receipt_id": f"receipt-{index}", "state": "succeeded", "output": output}
                ),
            ),
        )
        work.connection.execute(
            "INSERT INTO worker_messages VALUES (?,?, 'assistant',?)",
            (
                run_id,
                index + 100,
                json.dumps({"role": "assistant", "content": f"Thinking {index}"}),
            ),
        )
        work.connection.execute(
            "UPDATE worker_capsules SET revision=revision+1 WHERE run_id=?", (run_id,)
        )


def test_repeated_read_and_inference_activity_does_not_hide_unchanged_outcome(worker):  # noqa: F811
    store, _, session, _, model, tool = worker
    keep_live_lease(store, session["id"])
    now = [100.0]
    watch = Watchdog(store, clock=lambda: now[0], worker_progress_timeout=20)
    checkpoint(store, session["id"], 0)
    assert watch.tick()["stalled_workers"] == []
    for index, moment in enumerate((110, 120, 130), 1):
        now[0] = moment
        checkpoint(store, session["id"], index)
        report = watch.tick()
    assert report["stalled_workers"][0]["reason_code"] == "worker_no_productive_progress"
    assert report["stalled_workers"][0]["elapsed_seconds"] == 30
    Watchdog(store, clock=lambda: 135, worker_progress_timeout=20).tick()
    events = store.list_events()
    help_events = [event for event in events if event["event_type"] == "worker.help_requested"]
    assert len(help_events) == 1
    assert sum(event["event_type"] == "runtime.worker_progress_observed" for event in events) == 4
    assert model.calls == 0 and tool.calls == []
    with store.connection() as connection:
        row = connection.execute(
            "SELECT * FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()
        assert row["state"] == "running" and row["lease_until"] == 900


def test_new_source_content_resets_outcome_window_but_old_content_does_not(worker):  # noqa: F811
    store, _, session, _, _, _ = worker
    keep_live_lease(store, session["id"])
    now = [100.0]
    watch = Watchdog(store, clock=lambda: now[0], worker_progress_timeout=20)
    checkpoint(store, session["id"], 0)
    watch.tick()
    now[0] = 115
    checkpoint(store, session["id"], 1, content="new source revision")
    assert watch.tick()["stalled_workers"] == []
    now[0] = 130
    checkpoint(store, session["id"], 2)
    assert watch.tick()["stalled_workers"] == []
    now[0] = 136
    checkpoint(store, session["id"], 3, content="new source revision")
    assert watch.tick()["stalled_workers"][0]["reason_code"] == "worker_no_productive_progress"


def test_active_but_unknown_effect_is_only_diagnosed_and_not_replayed(worker):  # noqa: F811
    store, _, session, _, model, tool = worker
    keep_live_lease(store, session["id"])
    checkpoint(store, session["id"], 0)
    Watchdog(store, clock=lambda: 100, worker_progress_timeout=20).tick()
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT * FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()
        reserve_worker_model_call(work.connection, row, "unknown", 100, 10, None, [])
    checkpoint(store, session["id"], 1)
    report = Watchdog(store, clock=lambda: 125, worker_progress_timeout=20).tick()
    assert report["stalled_workers"][0]["recovery"] == "observe_only_unknown_effect"
    assert report["stalled_workers"][0]["reason_code"] == "worker_no_productive_progress"
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM worker_model_reservations").fetchone()[0]
            == "reserved"
        )
        assert (
            connection.execute(
                "SELECT state FROM worker_runs WHERE id=?", (session["id"],)
            ).fetchone()[0]
            == "running"
        )
    assert model.calls == 0 and tool.calls == []


def test_settled_model_calls_and_thoughts_alone_do_not_advance_outcomes(worker):  # noqa: F811
    store, _, session, _, _, _ = worker
    keep_live_lease(store, session["id"])
    Watchdog(store, clock=lambda: 100, worker_progress_timeout=20).tick()
    for index, moment in enumerate((110, 120, 125)):
        with store.unit_of_work() as work:
            row = work.connection.execute(
                "SELECT * FROM worker_runs WHERE id=?", (session["id"],)
            ).fetchone()
            reserve_worker_model_call(work.connection, row, f"settled-{index}", 100, 10, None, [])
            work.connection.execute(
                "UPDATE worker_model_reservations SET state='settled',input_tokens=10,output_tokens=5,"
                "settled_at='2026-11-16T00:00:00+00:00' WHERE call_id=?",
                (f"settled-{index}",),
            )
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?, 'assistant',?)",
                (
                    session["id"],
                    index + 100,
                    json.dumps({"role": "assistant", "content": f"Thought {index}"}),
                ),
            )
        report = Watchdog(
            store, clock=lambda moment=moment: moment, worker_progress_timeout=20
        ).tick()
    assert report["stalled_workers"][0]["reason_code"] == "worker_no_productive_progress"
    assert not report["stalled_workers"][0]["unsettled_effect_or_call"]


def test_two_watchdogs_deduplicate_active_nonproductive_frontier(worker):  # noqa: F811
    store, _, session, _, _, _ = worker
    keep_live_lease(store, session["id"])
    checkpoint(store, session["id"], 0)
    Watchdog(store, clock=lambda: 100, worker_progress_timeout=20).tick()
    checkpoint(store, session["id"], 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda _: Watchdog(store, clock=lambda: 125, worker_progress_timeout=20).tick(),
                range(2),
            )
        )
    help_events = [
        event for event in store.list_events() if event["event_type"] == "worker.help_requested"
    ]
    assert len(help_events) == 1
    assert json.loads(help_events[0]["payload"])["reason_code"] == "worker_no_productive_progress"


def test_consumed_followup_resets_outcome_window_without_treating_capsule_revision_as_output(
    worker,  # noqa: F811
):
    store, _, session, _, _, _ = worker
    keep_live_lease(store, session["id"])
    Watchdog(store, clock=lambda: 100, worker_progress_timeout=20).tick()
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_capsules SET inbox_cursor=inbox_cursor+1,revision=revision+1 WHERE run_id=?",
            (session["id"],),
        )
    assert (
        Watchdog(store, clock=lambda: 115, worker_progress_timeout=20).tick()["stalled_workers"]
        == []
    )
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_capsules SET revision=revision+1 WHERE run_id=?", (session["id"],)
        )
    assert (
        Watchdog(store, clock=lambda: 130, worker_progress_timeout=20).tick()["stalled_workers"]
        == []
    )
    report = Watchdog(store, clock=lambda: 136, worker_progress_timeout=20).tick()
    assert report["stalled_workers"][0]["reason_code"] == "worker_no_productive_progress"
