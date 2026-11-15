"""Actual bounded interpretation, isolation and receipt-backed self repair."""

import asyncio
import hashlib
import json

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.code_test_harness import HarnessError, run_tests
from forge_core.domain import DomainError
from forge_core.worker_recovery import settled_read_failure, unresolved_calls


def suite(*pairs):
    return {"cases": [{"inputs": inputs, "expected": expected} for inputs, expected in pairs]}


def test_actual_pure_functions_cover_recursion_loops_and_boolean_logic():
    source = """def triangular(n):
    if n <= 0:
        return 0
    return n + triangular(n - 1)
def choose(enabled, values):
    total = 0
    for value in values:
        if enabled and value > 0:
            total = total + value
    return total
def count(n):
    result = 0
    while n > 0:
        result = result + 1
        n = n - 1
    return result
"""
    assert run_tests(source, "triangular", suite(({"n": 5}, 15), ({"n": 0}, 0)))["failed"] == 0
    assert (
        run_tests(source, "choose", suite(({"enabled": True, "values": [-2, 3, 4]}, 7)))["passed"]
        == 1
    )
    assert run_tests(source, "count", suite(({"n": 8}, 8)))["passed"] == 1


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef f(x):\n return x",
        "def f(x):\n return x.__class__",
        "def f(x):\n return open('secret').read()",
        "@print\ndef f(x):\n return x",
        "def f(x=1):\n return x",
        "def f(x):\n return [item for item in x]",
        "def f(x):\n def g(y):\n  return y\n return g(x)",
    ],
)
def test_candidate_cannot_execute_python_or_acquire_authority(source):
    try:
        result = run_tests(source, "f", suite(({"x": 1}, 1)))
    except HarnessError:
        return
    assert result["passed"] == 0 and result["failed"] == 1


@pytest.mark.parametrize(
    "source",
    [
        "def f(x):\n return f(x)",
        "def f(x):\n while x > 0:\n  x = x + 1\n return x",
        "def f(x):\n return 2 ** 1000000",
    ],
)
def test_unbounded_computation_returns_a_bounded_test_failure(source):
    result = run_tests(source, "f", suite(({"x": 1}, 1)))
    assert result["passed"] == 0 and result["failed"] == 1
    assert result["steps"] <= 50002


def test_wrong_value_and_type_are_failures_and_missing_parameters_are_rejected():
    result = run_tests("def f(x):\n return x", "f", suite(({"x": True}, 1), ({"x": 2}, 3)))
    assert result["passed"] == 0 and result["failed"] == 2
    with pytest.raises(HarnessError):
        run_tests("def f(x):\n return x", "f", suite(({}, 1)))


def test_real_subprocess_failure_drives_owned_revision_and_new_test_with_immutable_history(
    tmp_path,
):
    bad = "def add_one(x):\n    return x\n"
    fixed = "def add_one(x):\n    return x + 1\n"
    tests = suite(({"x": 0}, 1), ({"x": -4}, -3), ({"x": 100}, 101))
    arguments = {"path": "work/candidate.py", "function": "add_one", "suite": tests}
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_write", {"path": "work/candidate.py", "content": bad}, 1),
            tool("python_test", arguments, 2),
            response("placeholder"),
            tool("python_test", arguments, 4),
            response(delivery("Corrected and tested candidate")),
        ],
        tools=["file_write", "file_revise", "python_test"],
    )
    generate = model.generate

    async def choose_revision(request):
        if len(model.requests) == 2:
            with store.connection() as connection:
                receipt = connection.execute(
                    "SELECT r.id FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id "
                    "WHERE i.tool_name='file_write' AND i.state='succeeded'"
                ).fetchone()[0]
            model.responses[0] = tool(
                "file_revise",
                {
                    "path": "work/candidate.py",
                    "prior_receipt_id": receipt,
                    "expected_sha256": hashlib.sha256(bad.encode()).hexdigest(),
                    "content": fixed,
                },
                3,
            )
        return await generate(request)

    model.generate = choose_revision
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert result["steps"] == 5 and result["tool_spent"] == 4
    assert (tmp_path / "work/candidate.py").read_text(encoding="utf-8") == fixed
    with store.connection() as connection:
        calls = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE tool_name='python_test' ORDER BY rowid"
        ).fetchall()
        assert [call["state"] for call in calls] == ["failed", "succeeded"]
        failure, success = [json.loads(call["result_json"])["output"] for call in calls]
        assert failure["exit_code"] == 2 and failure["failed"] == 3 and failure["passed"] == 0
        assert success["exit_code"] == 0 and success["passed"] == 3 and success["failed"] == 0
        assert failure["suite_sha256"] == success["suite_sha256"]
        assert failure["source_sha256"] != success["source_sha256"]
        assert settled_read_failure(connection, worker["id"], calls[0])
        assert not unresolved_calls(connection, worker["id"])
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 4
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='worker.progress' "
                "AND json_extract(payload,'$.phase')='self_repair'"
            ).fetchone()[0]
            == 1
        )


def test_python_test_preserves_workspace_path_and_original_authority(tmp_path):
    store, manager, _model, _worker = prepared(tmp_path, [], tools=["python_test"])
    arguments = {"path": "../outside.py", "function": "f", "suite": suite(({"x": 1}, 1))}
    with pytest.raises(DomainError, match="outside workspace"):
        manager.tools._scope("python_test", arguments)
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0
