"""Programmatic real CPython/tool tests; no real Provider is used here."""

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest
from test_tool_engine import mock_run as mock_run  # noqa: PLC0414
from test_tool_engine import reserve
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core import python_unittest_runner as runner
from forge_core.domain import DomainError
from forge_core.governance import GovernanceService
from forge_core.tool_engine import ToolAuthorizationRequired, ToolEngine
from forge_core.worker_recovery import settled_read_failure, unresolved_calls


def sources(expected=3):
    return {
        "calculator.py": b"from dataclasses import dataclass\n@dataclass\nclass Calculator:\n    base: int\n    def add(self, value):\n        return self.base + value\n",
        "test_calculator.py": (
            "import unittest\nfrom calculator import Calculator\n"
            "class CalculatorTests(unittest.TestCase):\n"
            "    def test_add(self):\n"
            f"        self.assertEqual(Calculator(1).add(2), {expected})\n"
        ).encode(),
    }


def manifest(snapshot):
    return {name: hashlib.sha256(data).hexdigest() for name, data in snapshot.items()}


def test_real_cpython_supports_imports_classes_and_actual_assertion_failure(tmp_path):
    good = runner.run_snapshot(
        sources(), "test_calculator", cwd=tmp_path / "good", env=ToolEngine._environment()
    )
    bad = runner.run_snapshot(
        sources(expected=4), "test_calculator", cwd=tmp_path / "bad", env=ToolEngine._environment()
    )
    assert good["exit_code"] == 0 and good["tests_run"] == 1 and good["failures"] == 0
    assert bad["exit_code"] == 2 and bad["tests_run"] == 1 and bad["failures"] == 1
    assert "AssertionError: 3 != 4" in bad["stderr"]
    assert good["manifest_sha256"] != bad["manifest_sha256"]
    assert Path(good["cwd"]).is_relative_to(tmp_path)
    assert good["argv"][1:3] == ["-I", "-B"]
    assert good["runtime"] == "cpython_unittest_v1"
    assert not list(tmp_path.rglob("__pycache__"))


def test_clean_environment_does_not_inherit_provider_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_PROVIDER_KEY_TEST", "not-a-real-key")
    snapshot = {
        "test_environment.py": (
            b"import unittest, os\nclass EnvironmentTests(unittest.TestCase):\n"
            b"    def test_no_credential(self):\n"
            b"        self.assertNotIn('FORGE_PROVIDER_KEY_TEST', os.environ)\n"
        )
    }
    result = runner.run_snapshot(
        snapshot, "test_environment", cwd=tmp_path / "environment", env=ToolEngine._environment()
    )
    assert result["exit_code"] == 0 and result["tests_run"] == 1


@pytest.mark.parametrize(
    "name",
    [
        "../outside.py",
        "/absolute.py",
        "a\\escape.py",
        "a/../b.py",
        ".env",
        "nested//x.py",
        "-test.py",
        "CON.py",
        "NUL/test_candidate.py",
    ],
)
def test_manifest_cannot_select_paths_or_non_python_files(name):
    with pytest.raises(ValueError):
        runner.validate_manifest({name: "0" * 64}, "test_candidate")


def test_manifest_binds_files_and_test_module_without_expanding_budget(tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    for name, data in sources().items():
        (directory / name).write_bytes(data)
    original = manifest(sources())
    assert runner.read_snapshot(directory, original, "test_calculator") == sources()
    (directory / "calculator.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="approved manifest"):
        runner.read_snapshot(directory, original, "test_calculator")
    with pytest.raises(ValueError, match="declared"):
        runner.validate_manifest(original, "missing_test")
    with pytest.raises(ValueError, match="1-32"):
        runner.validate_manifest({f"f{index}.py": "0" * 64 for index in range(33)}, "f0")
    with pytest.raises(ValueError, match="manifest entry"):
        runner.validate_manifest({"test_case.py": "0" * 64, "TEST_CASE.py": "0" * 64}, "test_case")
    huge = b"#" * (runner.MAX_BYTES + 1)
    (directory / "test_huge.py").write_bytes(huge)
    with pytest.raises(ValueError, match="approved manifest"):
        runner.read_snapshot(directory, manifest({"test_huge.py": huge}), "test_huge")


def test_linked_source_is_rejected(tmp_path):
    target = tmp_path / "outside.py"
    target.write_bytes(b"#source")
    source = tmp_path / "source"
    source.mkdir()
    try:
        (source / "test_link.py").symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation unavailable to this Windows user")
    with pytest.raises(ValueError, match="links"):
        runner.read_snapshot(source, manifest({"test_link.py": b"#source"}), "test_link")


def test_completed_parent_cannot_leave_test_descendants_running(tmp_path):
    marker = tmp_path / "escaped-child.txt"
    code = (
        "import time,pathlib;time.sleep(1);pathlib.Path("
        + repr(str(marker))
        + ").write_text('alive')"
    )
    snapshot = {
        "test_tree.py": (
            "import unittest,subprocess,sys\nclass TreeTests(unittest.TestCase):\n"
            "    def test_child(self):\n"
            f"        subprocess.Popen([sys.executable, '-c', {code!r}])\n"
            "        self.assertTrue(True)\n"
        ).encode()
    }
    result = runner.run_snapshot(
        snapshot, "test_tree", cwd=tmp_path / "tree", env=ToolEngine._environment()
    )
    assert result["exit_code"] == 0
    time.sleep(1.1)
    assert not marker.exists()


def test_timeout_cleans_process_tree_and_preserves_unknown_effect_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MAX_SECONDS", 0.3)
    snapshot = {
        "test_wait.py": (
            b"import unittest,time\nclass WaitTests(unittest.TestCase):\n"
            b"    def test_wait(self):\n        time.sleep(30)\n"
        )
    }
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="external effects may be unknown"):
        runner.run_snapshot(
            snapshot, "test_wait", cwd=tmp_path / "wait", env=ToolEngine._environment()
        )
    assert time.monotonic() - started < 6


def test_zero_tests_cannot_report_success(tmp_path):
    with pytest.raises(ValueError, match="unittest result"):
        runner.run_snapshot(
            {"test_empty.py": b"# No test case\n"},
            "test_empty",
            cwd=tmp_path / "empty",
            env=ToolEngine._environment(),
        )


def test_unittest_subtests_may_report_more_failures_than_test_methods(tmp_path):
    snapshot = {
        "test_subcases.py": (
            b"import unittest\nclass SubcaseTests(unittest.TestCase):\n"
            b"    def test_subcases(self):\n"
            b"        for value in range(3):\n"
            b"            with self.subTest(value=value):\n"
            b"                self.assertEqual(value, 99)\n"
        )
    }
    result = runner.run_snapshot(
        snapshot,
        "test_subcases",
        cwd=tmp_path / "subcases",
        env=ToolEngine._environment(),
    )
    assert result["exit_code"] == 2 and result["tests_run"] == 1 and result["failures"] == 3


def test_excess_output_cannot_report_success(tmp_path):
    excessive = {
        "test_output.py": (
            b"import unittest\nclass OutputTests(unittest.TestCase):\n"
            b"    def test_output(self):\n        print('x' * 400000)\n"
        )
    }
    with pytest.raises(RuntimeError, match="execution stopped|output exceeds"):
        runner.run_snapshot(
            excessive,
            "test_output",
            cwd=tmp_path / "excessive",
            env=ToolEngine._environment(),
        )


def allow_python_tool(store, plan):
    """Bind this offline fixture's plan/team/run to the additional real tool."""
    with store.unit_of_work() as work:
        connection = work.connection
        graph = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE id=?", (plan["id"],)
            ).fetchone()[0]
        )
        graph["nodes"][0]["allowed_tools"].append("python_unittest")
        graph_json = runner.canonical(graph)
        plan_digest = hashlib.sha256(graph_json.encode()).hexdigest()
        body = json.loads(
            connection.execute(
                "SELECT body_json FROM team_proposals WHERE id='team-id'"
            ).fetchone()[0]
        )
        body["plan_digest"] = plan_digest
        body["members"][0]["tool_scope"].append("python_unittest")
        body_json = runner.canonical(body)
        team_digest = hashlib.sha256(body_json.encode()).hexdigest()
        tools_json = runner.canonical(body["members"][0]["tool_scope"])
        connection.execute(
            "UPDATE quest_plans SET graph_json=?,digest=? WHERE id=?",
            (graph_json, plan_digest, plan["id"]),
        )
        connection.execute(
            "UPDATE task_nodes SET allowed_tools=? WHERE plan_id=?", (tools_json, plan["id"])
        )
        connection.execute(
            "UPDATE team_proposals SET body_json=?,plan_digest=?,proposal_digest=? WHERE id='team-id'",
            (body_json, plan_digest, team_digest),
        )
        connection.execute(
            "UPDATE worker_runs SET allowed_tools_json=?,plan_digest=?,team_digest=? WHERE id='run'",
            (tools_json, plan_digest, team_digest),
        )


def test_real_tool_requires_exact_r2_approval_and_commits_failure_then_success(mock_run):
    store, quest, worker, plan = mock_run
    allow_python_tool(store, plan)
    directory = store.workspace.root / "work" / quest.id
    directory.mkdir(parents=True)
    engine, governance = ToolEngine(store), GovernanceService(store)
    arguments = {
        "quest_id": quest.id,
        "worker_id": worker.id,
        "run_id": "run",
        "lease_token": "lease",
        "tool_name": "python_unittest",
    }
    receipts = []
    for number, expected in enumerate((4, 3)):
        snapshot = sources(expected)
        for name, data in snapshot.items():
            (directory / name).write_bytes(data)
        parameters = {
            "path": f"work/{quest.id}",
            "manifest": manifest(snapshot),
            "test_module": "test_calculator",
        }
        call_id = f"test-{number}"
        reserve(store, call_id, "python_unittest", parameters)
        with pytest.raises(ToolAuthorizationRequired):
            engine.prepare(call_id=call_id, parameters=parameters, **arguments)
        approval = governance.request_approval(
            quest.id,
            "python_unittest",
            parameters,
            str(directory),
            "R2",
            "Run only this exact test snapshot",
        )
        governance.decide_approval(approval["id"], "APPROVED", "local_user")
        result = asyncio.run(engine.execute(call_id=call_id, parameters=parameters, **arguments))
        assert result["state"] == ("failed" if number == 0 else "succeeded")
        assert result["output"]["failures"] == (1 if number == 0 else 0)
        assert result["output"]["manifest_json"] == runner.canonical(parameters["manifest"])
        assert result["output"]["cwd"] != str(directory)
        receipts.append(result["receipt_id"])
        assert governance.get_approval(approval["id"])["consumed_at"]
        assert (
            asyncio.run(engine.execute(call_id=call_id, parameters=parameters, **arguments))[
                "receipt_id"
            ]
            == result["receipt_id"]
        )
    assert len(set(receipts)) == 2
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
        assert [
            row[0] for row in connection.execute("SELECT state FROM action_intents ORDER BY rowid")
        ] == ["failed", "succeeded"]


def test_task_root_manifest_and_approval_cannot_be_rebound(mock_run):
    store, quest, worker, plan = mock_run
    allow_python_tool(store, plan)
    directory = store.workspace.root / "work" / quest.id
    directory.mkdir(parents=True)
    snapshot = sources()
    for name, data in snapshot.items():
        (directory / name).write_bytes(data)
    engine = ToolEngine(store)
    parameters = {
        "path": f"work/{quest.id}",
        "manifest": manifest(snapshot),
        "test_module": "test_calculator",
    }
    args = {
        "call_id": "bound",
        "quest_id": quest.id,
        "worker_id": worker.id,
        "run_id": "run",
        "lease_token": "lease",
        "tool_name": "python_unittest",
        "parameters": parameters,
    }
    reserve(store, "bound", "python_unittest", parameters)
    approval = GovernanceService(store).request_approval(
        quest.id, "python_unittest", parameters, str(directory), "R2", "Exact approved source"
    )
    GovernanceService(store).decide_approval(approval["id"], "APPROVED", "local_user")
    (directory / "calculator.py").write_text("different", encoding="utf-8")
    with pytest.raises(DomainError, match="snapshot"):
        engine.prepare(**args)
    assert GovernanceService(store).get_approval(approval["id"])["consumed_at"] is None
    with pytest.raises(DomainError, match="task's artifact"):
        engine._python_test_snapshot(quest.id, str(store.workspace.root), parameters)
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0


def test_tool_timeout_remains_uncertain_and_is_not_replayed(mock_run, monkeypatch):
    store, quest, worker, plan = mock_run
    allow_python_tool(store, plan)
    directory = store.workspace.root / "work" / quest.id
    directory.mkdir(parents=True)
    snapshot = {
        "test_wait.py": (
            b"import unittest,time\nclass WaitTests(unittest.TestCase):\n"
            b"    def test_wait(self):\n        time.sleep(30)\n"
        )
    }
    (directory / "test_wait.py").write_bytes(snapshot["test_wait.py"])
    parameters = {
        "path": f"work/{quest.id}",
        "manifest": manifest(snapshot),
        "test_module": "test_wait",
    }
    reserve(store, "timeout", "python_unittest", parameters)
    governance = GovernanceService(store)
    approval = governance.request_approval(
        quest.id, "python_unittest", parameters, str(directory), "R2", "Exact timeout scenario"
    )
    governance.decide_approval(approval["id"], "APPROVED", "local_user")
    engine = ToolEngine(store)
    arguments = {
        "call_id": "timeout",
        "quest_id": quest.id,
        "worker_id": worker.id,
        "run_id": "run",
        "lease_token": "lease",
        "tool_name": "python_unittest",
        "parameters": parameters,
    }
    monkeypatch.setattr(runner, "MAX_SECONDS", 0.3)
    outcome = asyncio.run(engine.execute(**arguments))
    assert outcome["state"] == "uncertain" and outcome["receipt_id"] is None
    monkeypatch.setattr(
        runner, "run_snapshot", lambda *_a, **_kw: pytest.fail("Unknown effect replayed")
    )
    assert asyncio.run(engine.execute(**arguments))["state"] == "uncertain"
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


def corrected_worker(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [],
        tools=["file_write", "file_revise", "python_unittest"],
    )
    quest_id = worker["quest_id"]
    directory = tmp_path / "work" / quest_id
    directory.mkdir()
    good = sources()
    bad = dict(good)
    bad["calculator.py"] = good["calculator.py"].replace(b"self.base + value", b"self.base")
    parameters = {
        "path": f"work/{quest_id}",
        "manifest": manifest(bad),
        "test_module": "test_calculator",
    }
    model.responses.extend(
        [
            tool(
                "file_write",
                {
                    "path": f"work/{quest_id}/calculator.py",
                    "content": bad["calculator.py"].decode(),
                },
                1,
            ),
            tool(
                "file_write",
                {
                    "path": f"work/{quest_id}/test_calculator.py",
                    "content": bad["test_calculator.py"].decode(),
                },
                2,
            ),
            tool("python_unittest", parameters, 3),
            response("placeholder revision"),
            tool("python_unittest", {**parameters, "manifest": manifest(good)}, 5),
            response(delivery("Implemented Calculator and corrected the actual failed unittest")),
        ]
    )
    generate = model.generate

    async def select_owned_revision(request):
        if len(model.requests) == 3:
            with store.connection() as connection:
                receipt = connection.execute(
                    "SELECT r.id FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id "
                    "WHERE i.tool_name='file_write' AND i.resource_scope=?",
                    (str(directory / "calculator.py"),),
                ).fetchone()[0]
            model.responses[0] = tool(
                "file_revise",
                {
                    "path": f"work/{quest_id}/calculator.py",
                    "prior_receipt_id": receipt,
                    "expected_sha256": hashlib.sha256(bad["calculator.py"]).hexdigest(),
                    "content": good["calculator.py"].decode(),
                },
                4,
            )
        return await generate(request)

    model.generate = select_owned_revision
    approvals = []
    for _round in range(3):
        outcome = asyncio.run(manager.run(worker["id"]))
        if outcome["state"] == "review_pending":
            break
        assert outcome["state"] == "blocked" and outcome["awaiting_authorization"]
        governance = GovernanceService(store)
        pending = [
            item for item in governance.list_approvals(quest_id) if item["status"] == "PENDING"
        ]
        assert len(pending) == 1
        approval = pending[0]
        governance.decide_approval(
            approval["id"],
            "APPROVED",
            "local_user",
            expected_digest=approval["parameter_digest"],
            expected_plan_revision=approval["plan_revision"],
        )
        approvals.append(approval["id"])
        manager.retry(worker["id"])
    assert outcome["state"] == "review_pending"
    return store, manager, model, worker, outcome, approvals


def test_worker_observes_real_cpython_failure_then_revises_and_passes_after_new_approval(tmp_path):
    from forge_core.independent_review import _review_receipts

    store, manager, model, worker, outcome, approvals = corrected_worker(tmp_path)
    assert outcome["repairs"] == 1 and outcome["tool_spent"] == 5 and outcome["steps"] == 6
    assert len(approvals) == 2 and len(model.requests) == 6
    with store.connection() as connection:
        calls = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE tool_name='python_unittest' ORDER BY rowid"
        ).fetchall()
        assert [call["state"] for call in calls] == ["failed", "succeeded"]
        assert settled_read_failure(connection, worker["id"], calls[0])
        assert not unresolved_calls(connection, worker["id"])
        failed, passed = [json.loads(call["result_json"])["output"] for call in calls]
        assert failed["exit_code"] == 2 and failed["failures"] == 1
        assert passed["exit_code"] == 0 and passed["failures"] == 0
        assert failed["manifest_sha256"] != passed["manifest_sha256"]
        receipts, _artifacts = _review_receipts(store, connection, manager.get(worker["id"]))
        failure = next(
            item for item in receipts if item.get("state") == "failed_authorized_python_test"
        )
        assert failure["output"] == failed and "read_only" not in failure["state"]
        assert "independent correctness" in failure["trust"]
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 5
    assert any(
        message["role"] == "tool" and "AssertionError" in message["content"]
        for message in model.requests[3].messages
    )


@pytest.mark.parametrize(
    "tamper",
    ["actor", "approval_digest", "approval_risk", "approval_policy", "manifest", "runtime"],
)
def test_python_failure_recovery_requires_original_approval_and_manifest(tmp_path, tamper):
    store, _manager, _model, worker, _outcome, approvals = corrected_worker(tmp_path)
    with store.unit_of_work() as work:
        connection = work.connection
        call = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE tool_name='python_unittest' AND state='failed'"
        ).fetchone()
        assert settled_read_failure(connection, worker["id"], call)
        if tamper in {"actor", "approval_digest", "approval_risk", "approval_policy"}:
            column = {
                "actor": "decided_by",
                "approval_digest": "parameter_digest",
                "approval_risk": "risk",
                "approval_policy": "policy_version",
            }[tamper]
            value = "R0" if tamper == "approval_risk" else "forged"
            connection.execute(f"UPDATE approvals SET {column}=? WHERE id=?", (value, approvals[0]))
        else:
            receipt = connection.execute(
                "SELECT * FROM action_receipts WHERE id=?", (call["receipt_id"],)
            ).fetchone()
            output = json.loads(receipt["result_json"])
            output["manifest_sha256" if tamper == "manifest" else "runtime"] = "forged"
            serialized = runner.canonical(output)
            import sqlite3

            with pytest.raises(sqlite3.IntegrityError, match="immutable receipt"):
                connection.execute(
                    "UPDATE action_receipts SET result_json=?,result_digest=? WHERE id=?",
                    (serialized, hashlib.sha256(serialized.encode()).hexdigest(), receipt["id"]),
                )
            call_result = json.loads(call["result_json"])
            call_result["output"] = output
            connection.execute(
                "UPDATE worker_tool_calls SET result_json=? WHERE call_id=?",
                (runner.canonical(call_result), call["call_id"]),
            )
            call = connection.execute(
                "SELECT * FROM worker_tool_calls WHERE call_id=?", (call["call_id"],)
            ).fetchone()
        assert not settled_read_failure(connection, worker["id"], call)
        assert unresolved_calls(connection, worker["id"])
