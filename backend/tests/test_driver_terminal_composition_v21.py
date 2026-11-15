"""A stopped team proposal is a failure; active notices keep the driver waiting."""

import importlib.util
from pathlib import Path

import pytest
from test_driver_terminal_planning_v19 import database


@pytest.fixture
def driver():
    path = Path(__file__).resolve().parents[1] / "scripts/phase2/live_e2e.py"
    spec = importlib.util.spec_from_file_location("driver_terminal_composition_v21", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_terminal_composition_is_read_only_and_retains_attempts(tmp_path, driver):
    target = database(
        tmp_path,
        [
            ("failed", "composer", "q", None, 1, "composition_failed:InvalidTeamProposal", 1.0),
            ("planner", "planner", "q", None, 1, None, None),
            ("other", "composer", "other", None, 1, None, None),
            ("processed", "composer", "q", "done", 1, None, None),
        ],
    )
    original = target.read_bytes()
    result = driver.terminal_composition_failure(tmp_path, "q")
    assert result["status"] == "failed"
    assert result["reason"] == "all_composer_notices_terminal"
    assert result["read_only_changes"] == 0
    assert [row["event_id"] for row in result["notices"]] == ["failed"]
    assert result["notices"][0]["notice_attempts"] == 1
    assert target.read_bytes() == original


@pytest.mark.parametrize("active_error", [None, "composition_failed:retrying"])
def test_active_composer_notice_prevents_early_failure(tmp_path, driver, active_error):
    database(
        tmp_path,
        [
            ("old", "composer", "q", None, 1, "composition_failed:InvalidTeamProposal", 1.0),
            ("active", "composer", "q", None, 1, active_error, None),
        ],
    )
    assert driver.terminal_composition_failure(tmp_path, "q") is None


def test_transport_dead_letter_is_not_a_deterministic_team_rejection(tmp_path, driver):
    database(tmp_path, [("network", "composer", "q", None, 5, "resource_busy", 1.0)])
    assert driver.terminal_composition_failure(tmp_path, "q") is None


def test_missing_database_remains_absent(tmp_path, driver):
    assert driver.terminal_composition_failure(tmp_path, "q") is None
    assert not (tmp_path / "data").exists()
