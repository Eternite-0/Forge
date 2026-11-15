"""Read-only driver observation must distinguish terminal and active planning."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def driver():
    path = Path(__file__).resolve().parents[1] / "scripts/phase2/live_e2e.py"
    spec = importlib.util.spec_from_file_location("driver_terminal_planning_v19", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def database(tmp_path, rows):
    target = tmp_path / "data/forge.sqlite3"
    target.parent.mkdir()
    with sqlite3.connect(target) as connection:
        connection.execute(
            "CREATE TABLE event_inbox(event_id TEXT,consumer_id TEXT,quest_id TEXT,"
            "processed_at TEXT,notice_attempts INTEGER,notice_error TEXT,notice_dead_at REAL)"
        )
        connection.executemany("INSERT INTO event_inbox VALUES(?,?,?,?,?,?,?)", rows)
    return target


def test_absent_database_is_not_created(tmp_path, driver):
    assert driver.terminal_planning_failure(tmp_path, "q") is None
    assert not (tmp_path / "data").exists()


def test_active_notice_prevents_early_failure_even_with_old_terminal_notice(tmp_path, driver):
    target = database(
        tmp_path,
        [
            ("old", "planner", "q", None, 1, "planning_failed:InvalidGraphProposal", 1.0),
            ("current", "planner", "q", None, 1, None, None),
        ],
    )
    original = target.read_bytes()
    assert driver.terminal_planning_failure(tmp_path, "q") is None
    assert target.read_bytes() == original


def test_terminal_failure_keeps_exact_attempts_and_other_quests_cannot_block(tmp_path, driver):
    target = database(
        tmp_path,
        [
            ("rejected", "planner", "q", None, 1, "planning_failed:InvalidGraphProposal", 1.0),
            ("unrelated", "planner", "other", None, 1, None, None),
            ("already_done", "planner", "q", "done", 1, None, None),
        ],
    )
    original = target.read_bytes()
    result = driver.terminal_planning_failure(tmp_path, "q")
    assert result["status"] == "failed"
    assert result["reason"] == "all_planner_notices_terminal"
    assert result["read_only_changes"] == 0
    assert len(result["notices"]) == 1
    assert result["notices"][0]["notice_attempts"] == 1
    assert target.read_bytes() == original


def test_other_dead_letter_errors_do_not_claim_terminal_planning_rejection(tmp_path, driver):
    database(tmp_path, [("transport", "planner", "q", None, 5, "resource_busy", 1.0)])
    assert driver.terminal_planning_failure(tmp_path, "q") is None
