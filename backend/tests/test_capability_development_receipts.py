"""Synthetic SQLite evidence fixtures; no Provider or model execution claims."""

import hashlib
import json
import sqlite3

import pytest
from test_verify_superagent_capability import driver


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


@pytest.fixture
def development_evidence(tmp_path):
    directory = tmp_path / "work" / "quest_test"
    directory.mkdir(parents=True)
    (directory / "candidate.py").write_text("VALUE = 3\n", encoding="utf-8")
    database = tmp_path / "fixture.sqlite3"
    tables = {
        "quests": "id,status",
        "action_receipts": "id,intent_id,execution_mode,result_json,result_digest",
        "action_intents": "id,call_id,quest_id,worker_id,run_id,plan_id,plan_revision,tool_name,parameters_json,parameter_digest,resource_scope,risk,approval_id,execution_mode,state",
        "worker_runs": "id,quest_id,worker_id,plan_id,plan_revision,execution_mode",
        "worker_tool_calls": "run_id,call_id,tool_name,arguments_json,state,receipt_id,result_json",
        "approvals": "id,quest_id,plan_revision,action_type,parameter_digest,resource_scope,risk,policy_version,status,decided_by,consumed_at",
    }
    with sqlite3.connect(database) as c:
        for table, columns in tables.items():
            c.execute(
                f"CREATE TABLE {table} ({','.join(name + ' TEXT' for name in columns.split(','))})"
            )
        c.execute("INSERT INTO quests VALUES ('quest_test','completed')")

    def add_receipt(number, *, failed=False, candidate_hash=None):
        receipt_id = f"receipt_fixture_{number}"
        intent_id = f"action_{number}"
        approval_id = f"approval_{number}"
        call_id = f"call_{number}"
        run_id = f"run_{number}"
        manifest = {
            "candidate.py": candidate_hash or digest((directory / "candidate.py").read_bytes()),
            "test_candidate.py": digest("synthetic test source"),
        }
        parameters = {
            "path": "work/quest_test",
            "manifest": manifest,
            "test_module": "test_candidate",
        }
        output = {
            "runtime": "cpython_unittest_v1",
            "manifest_json": canonical(manifest),
            "manifest_sha256": driver.parameter_digest(manifest),
            "test_module": "test_candidate",
            "exit_code": 2 if failed else 0,
            "tests_run": 1,
            "failures": int(failed),
            "errors": 0,
        }
        state = "failed" if failed else "succeeded"
        wrapper = {
            "call_id": call_id,
            "intent_id": intent_id,
            "receipt_id": receipt_id,
            "state": state,
            "output": output,
        }
        parameter_hash = driver.parameter_digest(parameters)
        with sqlite3.connect(database) as c:
            c.execute(
                "INSERT INTO action_receipts VALUES (?,?,?,?,?)",
                (receipt_id, intent_id, "live", canonical(output), digest(canonical(output))),
            )
            c.execute(
                "INSERT INTO action_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    intent_id,
                    call_id,
                    "quest_test",
                    "worker",
                    run_id,
                    "plan",
                    "1",
                    "python_unittest",
                    canonical(parameters),
                    parameter_hash,
                    str(directory),
                    "R2",
                    approval_id,
                    "live",
                    state,
                ),
            )
            c.execute(
                "INSERT INTO worker_runs VALUES (?,?,?,?,?,?)",
                (run_id, "quest_test", "worker", "plan", "1", "live"),
            )
            c.execute(
                "INSERT INTO worker_tool_calls VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    call_id,
                    "python_unittest",
                    canonical(parameters),
                    state,
                    receipt_id,
                    canonical(wrapper),
                ),
            )
            c.execute(
                "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    approval_id,
                    "quest_test",
                    "1",
                    "python_unittest",
                    parameter_hash,
                    str(directory),
                    "R2",
                    driver.POLICY_VERSION,
                    "APPROVED",
                    "local_user",
                    "fixture-consumed",
                ),
            )
        return receipt_id

    receipt_id = add_receipt(1)
    development = {
        "summary": "Synthetic development report",
        "changes": ["code"],
        "test_receipt_ids": [receipt_id],
        "limitations": [],
    }
    (directory / "development.json").write_text(canonical(development), encoding="utf-8")
    return database, tmp_path, directory, development, add_receipt


def check(fixture):
    database, workspace, directory, development, _ = fixture
    (directory / "development.json").write_text(canonical(development), encoding="utf-8")
    return driver.verify_development_receipts(database, workspace, "quest_test")


def test_live_bound_success_can_include_failed_historical_candidate(development_evidence):
    _database, _workspace, _directory, development, add = development_evidence
    failure = add(2, failed=True, candidate_hash=digest("older failed candidate"))
    development["test_receipt_ids"].insert(0, failure)
    result = check(development_evidence)
    assert result["passed"] and result["checks"]["live_source_bound_test_receipts"]
    assert result["matching_success_receipt_ids"] == ["receipt_fixture_1"]


@pytest.mark.parametrize(
    "ids", [[], ["receipt_fabricated"], ["receipt_fixture_1", "receipt_fixture_1"]]
)
def test_empty_missing_or_duplicate_receipts_fail(development_evidence, ids):
    development_evidence[3]["test_receipt_ids"] = ids
    assert not check(development_evidence)["passed"]


@pytest.mark.parametrize(
    "tamper",
    [
        "cross_quest",
        "mock_receipt",
        "mock_intent",
        "mock_worker",
        "digest",
        "reserved_arguments",
        "approval",
        "stale_candidate",
        "failed_only",
        "zero_tests",
    ],
)
def test_unbound_mock_or_stale_test_evidence_fails(development_evidence, tamper):
    database, _workspace, directory, development, add = development_evidence
    with sqlite3.connect(database) as c:
        if tamper == "cross_quest":
            c.execute("UPDATE action_intents SET quest_id='quest_other'")
        elif tamper == "mock_receipt":
            c.execute("UPDATE action_receipts SET execution_mode='mock'")
        elif tamper == "mock_intent":
            c.execute("UPDATE action_intents SET execution_mode='mock'")
        elif tamper == "mock_worker":
            c.execute("UPDATE worker_runs SET execution_mode='mock'")
        elif tamper == "digest":
            c.execute("UPDATE action_receipts SET result_digest=?", ("0" * 64,))
        elif tamper == "reserved_arguments":
            c.execute("UPDATE worker_tool_calls SET arguments_json='{}'")
        elif tamper == "approval":
            c.execute("UPDATE approvals SET consumed_at=NULL")
        elif tamper == "stale_candidate":
            (directory / "candidate.py").write_text("VALUE = 4\n", encoding="utf-8")
        elif tamper == "zero_tests":
            output = json.loads(c.execute("SELECT result_json FROM action_receipts").fetchone()[0])
            output["tests_run"] = 0
            wrapper = json.loads(
                c.execute("SELECT result_json FROM worker_tool_calls").fetchone()[0]
            )
            wrapper["output"] = output
            c.execute(
                "UPDATE action_receipts SET result_json=?,result_digest=?",
                (canonical(output), digest(canonical(output))),
            )
            c.execute("UPDATE worker_tool_calls SET result_json=?", (canonical(wrapper),))
    if tamper == "failed_only":
        development["test_receipt_ids"] = [add(2, failed=True)]
    assert not check(development_evidence)["passed"]


def test_completed_l3_requires_both_artifact_and_receipt_checks(development_evidence, monkeypatch):
    database, workspace, directory, development, _ = development_evidence
    monkeypatch.setattr(driver, "task_team_observation", lambda *_: {"actual_worker_count": 1})

    def artifact(*_):
        return {
            "passed": True,
            "candidate_sha256": digest((directory / "candidate.py").read_bytes()),
            "artifact_sha256": digest((directory / "development.json").read_bytes()),
        }

    monkeypatch.setattr(driver.scenarios, "verify_artifact", artifact)
    for receipt_ids, expected in [(["receipt_fixture_1"], True), ([], False)]:
        development["test_receipt_ids"] = receipt_ids
        (directory / "development.json").write_text(canonical(development), encoding="utf-8")
        report = {"tasks": {}, "checks": {}}
        driver.collect_task_results(
            database,
            workspace,
            {"L3": "quest_test"},
            {"L3": {"source_sha256": "fixture"}},
            report,
            "development",
        )
        assert report["checks"]["artifact_L3"]
        assert report["checks"]["source_bound_tests_L3"] is expected
