"""Offline acceptance-driver races; no Provider calls or quality claims."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def driver(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "superagent_acceptance_driver_test", scripts / "verify_superagent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state():
    return {
        "calls": [{"state": "settled"}],
        "events": [],
        "message_digest": "fixed",
        "router_notices": [{"processed_at": "now", "notice_dead_at": None}],
    }


def test_driver_waits_for_late_router_delivery_and_restarts_stability_window(driver):
    current = state()
    quiet, ready = driver.quiescent_since(current, True, None, now=1)
    assert not ready
    current["router_notices"].append({"processed_at": None, "notice_dead_at": None})
    quiet, ready = driver.quiescent_since(current, True, quiet, now=3)
    assert quiet is None and not ready
    current["router_notices"][-1]["processed_at"] = "processed"
    quiet, ready = driver.quiescent_since(current, True, quiet, now=4)
    assert not ready
    current["calls"].append({"state": "reserved"})
    quiet, ready = driver.quiescent_since(current, True, quiet, now=6)
    assert quiet is None and not ready
    current["calls"][-1]["state"] = "settled"
    quiet, ready = driver.quiescent_since(current, True, quiet, now=7)
    quiet, ready = driver.quiescent_since(current, True, quiet, now=9)
    assert ready


def test_driver_rejects_dead_router_delivery_and_final_shutdown_reservation(driver):
    current = state()
    current["router_notices"][0]["notice_dead_at"] = "dead"
    with pytest.raises(driver.ProbeFailure, match="router_notice_dead_lettered"):
        driver.quiescent_since(current, True, None, now=3)
    report = {"status": "passed", "checks": {"business": True}}
    current = state()
    current["calls"].append({"state": "reserved"})
    driver.final_accounting(report, current)
    assert report["status"] == "failed"
    assert report["checks"]["business"]
    assert not report["checks"]["final_provider_calls_settled"]


def test_durable_worker_intervals_prove_parallelism_between_poll_samples(driver):
    current = state()

    def event(kind, run_id, stamp):
        return {"event_type": kind, "created_at": stamp, "payload": json.dumps({"run_id": run_id})}

    current["events"] = [
        event("worker.accepted", "a", "2026-11-16T00:00:00+00:00"),
        event("worker.accepted", "b", "2026-11-16T00:00:01+00:00"),
        event("worker.result", "a", "2026-11-16T00:00:02+00:00"),
        event("worker.result", "b", "2026-11-16T00:00:03+00:00"),
    ]
    intervals, maximum = driver.running_intervals(current)
    assert len(intervals) == 2 and maximum == 2
    current["events"][2]["created_at"] = "2026-11-16T00:00:01+00:00"
    _, maximum = driver.running_intervals(current)
    assert maximum == 1


@pytest.mark.parametrize("target", ["completed", "running", "cancelled", "failed"])
def test_driver_accepts_only_source_bound_completed_status_notice(driver, target):
    current = state()
    current["events"] = [
        {
            "id": "terminal-event",
            "event_type": "quest.status_changed",
            "quest_id": "task",
            "payload": json.dumps({"to": target}),
        }
    ]
    current["messages"] = [
        {
            "metadata": json.dumps({"source": "router_notice", "event_id": "terminal-event"}),
        }
    ]
    assert driver.notices_for_tasks(current) == ({"task"} if target == "completed" else set())
    current["messages"][0]["metadata"] = json.dumps(
        {"source": "router_notice", "event_id": "unrelated"}
    )
    assert not driver.notices_for_tasks(current)


def test_reclaim_keeps_first_ownership_observation_until_explicit_terminal_fact(driver):
    current = state()

    def event(kind, run_id, second):
        return {
            "event_type": kind,
            "created_at": f"2026-11-16T00:00:{second:02d}+00:00",
            "payload": json.dumps({"run_id": run_id}),
        }

    current["events"] = [
        event("worker.accepted", "a", 0),
        event("worker.accepted", "b", 1),
        event("worker.result", "a", 3),
        event("worker.accepted", "b", 4),
        event("worker.result", "b", 5),
        event("worker.accepted", "b", 6),
        event("worker.result", "b", 7),
    ]
    intervals, maximum = driver.running_intervals(current)
    assert maximum == 2
    assert [item["started_at"] for item in intervals if item["run_id"] == "b"] == [
        "2026-11-16T00:00:01+00:00",
        "2026-11-16T00:00:06+00:00",
    ]
