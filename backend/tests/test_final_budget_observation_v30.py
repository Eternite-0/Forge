"""Offline regressions for real API budget observations on early failure paths."""

import importlib.util
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


def actual_driver():
    source_path = Path(__file__).resolve().parents[1] / "scripts/phase2/live_e2e.py"
    spec = importlib.util.spec_from_file_location("offline_v30_final_budget", source_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("early_stage", ["router", "planner", "composer"])
@pytest.mark.parametrize("budget_status", [200, 503])
def test_all_early_failures_observe_real_budget_before_stopping(
    tmp_path, monkeypatch, early_stage, budget_status
):
    driver = actual_driver()
    order = []
    observed_budget = {
        "token_budgets_enforced": False,
        "token_limit": None,
        "charged_tokens": 17,
    }

    class OfflineCore:
        def __init__(self, workspace, **_kwargs):
            self.workspace = workspace

        def env(self):
            return {
                "FORGE_ENFORCE_TOKEN_BUDGETS": "false",
                "FORGE_REVIEW_MAX_ROUNDS": "3",
                "FORGE_WORKER_LEASE_SECONDS": "150",
                "FORGE_PROVIDER_TIMEOUT_SECONDS": "120",
                "FORGE_PROVIDER_MAX_RETRIES": "0",
                "FORGE_MODEL_CONCURRENCY_LIMIT": "1",
            }

        def start(self):
            order.append("start")

        def stop(self):
            order.append("stop")

        def tail_log(self, _limit):
            return ""

        def call(self, _method, path, _payload=None, **_kwargs):
            if path == "/api/conversation":
                return {
                    "status": 200,
                    "body": {"router": {"router_session_id": "offline"}},
                }
            if path == "/api/budget":
                order.append("budget")
                return {
                    "status": budget_status,
                    "body": observed_budget
                    if budget_status == 200
                    else {"error": "offline unavailable"},
                }
            if path == "/api/conversation/message":
                return {
                    "status": 200,
                    "body": {} if early_stage == "router" else {"quest_id": "q"},
                }
            if path.endswith("/plan") and early_stage == "composer":
                return {"status": 200, "body": {"plan": {"nodes": []}}}
            return {"status": 400, "body": {}}

    monkeypatch.setenv("FORGE_PROVIDER_KEY_LIVE_TEST", "offline-not-a-key")
    monkeypatch.setattr(driver, "REPO", tmp_path)
    monkeypatch.setattr(driver, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(driver, "LiveCore", OfflineCore)
    monkeypatch.setattr(
        driver,
        "seed_scenario",
        lambda *_args, **_kwargs: {
            "prompt": "offline",
            "limits": {"max_concurrency": 1},
        },
    )
    monkeypatch.setattr(driver, "drain_model_attempts", lambda _ws: {})
    monkeypatch.setattr(driver, "inspect_execution", lambda _ws: {})
    monkeypatch.setattr(driver, "evaluate_scenario", lambda *_args, **_kwargs: {"passed": True})
    monkeypatch.setattr(driver, "wait_for", lambda read, **_kwargs: read())
    monkeypatch.setattr(driver, "terminal_planning_failure", lambda *_args: None)
    record = driver.drive(
        SimpleNamespace(
            level="L1",
            scenario=True,
            prompt=None,
            token_limit=1,
            no_token_budget=True,
            concurrency=1,
            extra_env=None,
            plan_timeout=1,
            team_timeout=1,
        )
    )
    assert order[-2:] == ["budget", "stop"]
    assert record["results"]["budget_final_response"]["status"] == budget_status
    if budget_status == 200:
        assert record["results"]["budget"] == observed_budget
    else:
        assert "budget" not in record["results"]
        assert "final_budget_api_observation_failed" in record["failures"]


def test_settlement_wait_does_not_stop_before_queued_router_notice_calls(tmp_path, monkeypatch):
    driver = actual_driver()
    database = tmp_path / "data" / "forge.sqlite3"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE model_budget_attempts(state TEXT);"
            "CREATE TABLE event_inbox(consumer_id TEXT,event_type TEXT,processed_at TEXT,notice_dead_at TEXT);"
            "INSERT INTO event_inbox VALUES ('router','worker.progress',NULL,NULL);"
            "INSERT INTO event_inbox VALUES ('router','model.call_settled',NULL,NULL);"
        )
    observed = {"now": 0.0, "ticks": 0}

    def advance(seconds):
        observed["now"] += seconds
        observed["ticks"] += 1
        with sqlite3.connect(database) as connection:
            if observed["ticks"] == 1:
                connection.execute("INSERT INTO model_budget_attempts VALUES ('reserved')")
                connection.execute(
                    "UPDATE event_inbox SET processed_at='done' WHERE event_type='worker.progress'"
                )
            elif observed["ticks"] == 2:
                connection.execute("UPDATE model_budget_attempts SET state='settled'")

    monkeypatch.setattr(
        driver,
        "time",
        SimpleNamespace(monotonic=lambda: observed["now"], sleep=advance),
    )
    result = driver.drain_model_attempts(tmp_path)
    assert result["unsettled_attempts"] == result["pending_router_notices"] == 0
    assert result["waited_seconds"] >= 1.5 and observed["ticks"] >= 6
