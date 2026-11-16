"""Optional HTTP-attempt ceiling uses the existing durable admission transaction."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from forge_core.model_budget_schema import migrate_model_budget
from forge_core.model_call_budget import BudgetExceeded, ModelCallBudgetLedger
from forge_core.runtime import ModelCallContext


def ledger(path, limit):
    return ModelCallBudgetLedger(
        path, token_limit=1000, concurrency_limit=4, call_limit=limit, remote_max_seconds=30
    )


def admit(budget, key):
    return budget.admit(
        ModelCallContext(key, "router", "workspace"),
        "provider",
        "model",
        input_tokens=4,
        output_tokens=4,
        remote_timeout=30,
    )


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
def test_invalid_call_ceiling_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="call limit"):
        ledger(tmp_path / "budget.sqlite", value)


@pytest.mark.parametrize("outcome", ["settled", "unknown", "rejected", "reserved"])
def test_attempt_ceiling_survives_settlement_and_reopening(tmp_path, outcome):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    budget = ledger(path, 1)
    attempt = admit(budget, "first")
    if outcome == "settled":
        budget.settle(attempt, {"input_tokens": 2, "output_tokens": 1})
    elif outcome == "unknown":
        budget.settle(attempt, {"input_tokens": None, "output_tokens": None})
    elif outcome == "rejected":
        budget.reject_completed(attempt)
    with pytest.raises(BudgetExceeded, match="call limit exhausted"):
        admit(ledger(path, 1), "after_restart")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 1


def test_competing_admissions_cannot_exceed_one_remaining_call(tmp_path):
    path = tmp_path / "budget.sqlite"
    with sqlite3.connect(path) as connection:
        migrate_model_budget(connection)
    barrier = Barrier(2)

    def compete(key):
        budget = ledger(path, 1)
        barrier.wait(timeout=5)
        try:
            admit(budget, key)
            return "admitted"
        except BudgetExceeded:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(compete, str(index)) for index in range(2)]
        assert sorted(item.result(timeout=10) for item in futures) == ["admitted", "denied"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 1
