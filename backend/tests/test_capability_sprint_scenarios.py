"""Programmatic scenario/holdout verification, with no Provider calls."""

import importlib.util
import json
from pathlib import Path

import pytest

from forge_core.python_unittest_runner import run_snapshot
from forge_core.tool_engine import ToolEngine

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capability_sprint_scenarios.py"
SPEC = importlib.util.spec_from_file_location("capability_sprint_scenarios_test", SCRIPT)
scenarios = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scenarios)

CORRECT = """from dataclasses import dataclass
@dataclass(frozen=True)
class Record:
    key: str
    value: int

def normalize_records(records):
    if type(records) is not list:
        raise ValueError("records must be a list")
    values = {}
    for row in records:
        if type(row) is not dict or type(row.get("key")) is not str or type(row.get("value")) is not int:
            raise ValueError("invalid row")
        key = row["key"].strip().casefold()
        if not key:
            raise ValueError("empty key")
        values[key] = row["value"]
    return [Record(key, values[key]) for key in sorted(values)]
"""


def write_report(root, quest_id, name, data):
    path = root / "work" / quest_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def citations_for(root, descriptor):
    result = []
    for item in descriptor["source_manifest"]:
        packet = json.loads((root / item["local_path"]).read_text(encoding="utf-8"))
        line, quote = next(
            (index, text)
            for index, text in enumerate(packet["content"].splitlines(), 1)
            if len(text.strip()) >= 4
        )
        result.append(
            {
                "source_id": item["id"],
                "source_sha256": item["source_sha256"],
                "line_start": line,
                "line_end": line,
                "quote": quote,
            }
        )
    return result


def test_l1_seed_uses_actual_sources_and_grounded_structure_is_not_a_semantic_verdict(tmp_path):
    descriptor = scenarios.seed(tmp_path, "L1")
    assert Path(descriptor["project_root"]).name == "project_alpha"
    assert len(descriptor["source_manifest"]) == 2
    for item in descriptor["source_manifest"]:
        assert item["source_sha256"] == scenarios._sha(
            (scenarios.REPO / item["source_path"]).read_bytes()
        )
        assert item["source_state"] == "working_tree_at_seed"
        packet = json.loads((tmp_path / item["local_path"]).read_text(encoding="utf-8"))
        numbered = "\n".join(
            f"{index}: {line}" for index, line in enumerate(packet["content"].splitlines(), 1)
        )
        assert packet["numbered_content"] == numbered
        assert packet["numbered_content_sha256"] == scenarios._sha(numbered.encode())
        assert "numbered_content" not in item and "content" not in item
    report = {
        "summary": "Programmatic fixture validates structure without claiming an actual model finding.",
        "findings": [
            {
                "rank": 1,
                "classification": "design_limitation",
                "title": "Private directories are not a sandbox",
                "impact": "Execution boundaries require a distinct security mechanism.",
                "proposed_fix": "Keep permissions and actual OS isolation explicit.",
                "verification": "Check independent path and authorization tests.",
                "citations": citations_for(tmp_path, descriptor),
            }
        ],
    }
    write_report(tmp_path, "quest_fixture", "analysis.json", report)
    verified = scenarios.verify_artifact(tmp_path, "quest_fixture", "L1")
    assert verified["passed"] and verified["execution_mode"] == "programmatic_independent"
    assert verified["semantic_quality_assessed"] is False
    report["findings"][0]["citations"][0]["quote"] = (
        "Invented quote that is absent from the original source"
    )
    write_report(tmp_path, "quest_fixture", "analysis.json", report)
    assert not scenarios.verify_artifact(tmp_path, "quest_fixture", "L1")["passed"]


@pytest.mark.parametrize("findings", [None, [None], [{"rank": 1}], []])
def test_malformed_l1_findings_fail_closed(tmp_path, findings):
    scenarios.seed(tmp_path, "L1")
    write_report(
        tmp_path,
        "quest_fixture",
        "analysis.json",
        {"summary": "Malformed programmatic fixture.", "findings": findings},
    )
    assert not scenarios.verify_artifact(tmp_path, "quest_fixture", "L1")["passed"]


def test_non_object_report_fails_closed(tmp_path):
    scenarios.seed(tmp_path, "L1")
    write_report(tmp_path, "quest_fixture", "analysis.json", ["invalid report"])
    result = scenarios.verify_artifact(tmp_path, "quest_fixture", "L1")
    assert not result["passed"] and result["error_type"] == "ValueError"


def test_changed_source_and_missing_report_fail_closed(tmp_path):
    descriptor = scenarios.seed(tmp_path, "L1")
    assert not scenarios.verify_artifact(tmp_path, "quest_missing", "L1")["passed"]
    path = tmp_path / descriptor["source_manifest"][0]["local_path"]
    path.write_text("{}", encoding="utf-8")
    result = scenarios.verify_artifact(tmp_path, "quest_fixture", "L1")
    assert not result["passed"] and result["error_type"] == "ValueError"


@pytest.mark.skipif(
    not scenarios.REFERENCE_ROOT.is_dir(), reason="Fixed external reference checkout unavailable"
)
def test_l2_uses_real_fixed_prototype_and_a_distinct_project_root(tmp_path):
    alpha = scenarios.seed(tmp_path, "L1")
    beta = scenarios.seed(tmp_path, "L2")
    assert beta["project_root"] != alpha["project_root"]
    assert Path(beta["project_root"]).name == "project_beta"
    assert len(beta["source_manifest"]) == 3
    assert {item["revision"] for item in beta["source_manifest"]} == {scenarios.REFERENCE_COMMIT}
    assert {item["source_state"] for item in beta["source_manifest"]} == {"fixed_git_object"}
    report = {
        "summary": "Fixture checks attributed prototype materials without claiming actual research quality.",
        "citations": citations_for(tmp_path, beta),
        "comparison": [
            {
                "topic": "Thread subscriptions",
                "prototype": "Source-scoped intake and fan-out",
                "forge_implication": "Retain scoped direct event delivery",
                "source_ids": ["deepseek_intake"],
            }
        ]
        * 2,
        "risks": [
            {
                "issue": "A research prototype has deployment limitations",
                "mitigation": "Verify each implemented capability explicitly",
            }
        ]
        * 2,
        "recommendations": [
            {
                "action": "Keep deterministic admission before model inference",
                "verification": "Test actual filter and event fan-out outcomes",
            }
        ]
        * 2,
    }
    write_report(tmp_path, "quest_research", "research.json", report)
    assert scenarios.verify_artifact(tmp_path, "quest_research", "L2")["passed"]
    report["comparison"][0]["source_ids"] = ["invented_source"]
    write_report(tmp_path, "quest_research", "research.json", report)
    assert not scenarios.verify_artifact(tmp_path, "quest_research", "L2")["passed"]
    report["comparison"][0]["source_ids"] = ["deepseek_intake"]
    report["citations"][0]["source_sha256"] = "0" * 64
    write_report(tmp_path, "quest_research", "research.json", report)
    assert not scenarios.verify_artifact(tmp_path, "quest_research", "L2")["passed"]


def test_starter_really_fails_visible_tests_without_model_or_mock_result(tmp_path):
    descriptor = scenarios.seed(tmp_path, "L3")
    assert descriptor["expected_artifacts"] == [
        "candidate.py",
        "test_candidate.py",
        "development.json",
    ]
    output = run_snapshot(
        {
            "candidate.py": scenarios.STARTER.encode(),
            "test_candidate.py": scenarios.VISIBLE_TESTS.encode(),
        },
        "test_candidate",
        cwd=tmp_path / "starter-run",
        env=ToolEngine._environment(),
    )
    assert output["exit_code"] == 2 and output["tests_run"] == 2 and output["failures"] == 2


def test_l3_holdout_executes_actual_generated_code_and_rejects_incorrect_code(tmp_path):
    scenarios.seed(tmp_path, "L3")
    quest_id = "quest_development"
    report = {
        "summary": "Programmatic correct implementation fixture.",
        "changes": ["Normalize and validate records"],
        "test_receipt_ids": [],
        "limitations": ["This fixture is not a real model result"],
    }
    write_report(tmp_path, quest_id, "development.json", report)
    candidate = tmp_path / "work" / quest_id / "candidate.py"
    candidate.write_text(scenarios.STARTER, encoding="utf-8")
    failed = scenarios.verify_artifact(tmp_path, quest_id, "L3")
    assert not failed["passed"] and failed["holdout"]["exit_code"] == 2
    candidate.write_text(CORRECT, encoding="utf-8")
    passed = scenarios.verify_artifact(tmp_path, quest_id, "L3")
    assert passed["passed"] and passed["holdout"]["tests_run"] == 7
    assert passed["holdout"]["failures"] == passed["holdout"]["errors"] == 0
    assert passed["execution_mode"] == "programmatic_independent"
    assert passed["candidate_sha256"] == scenarios._sha(candidate.read_bytes())
    assert passed["semantic_quality_assessed"] is False


@pytest.mark.parametrize("level", ["L0", "L4", 4, None])
def test_invalid_scenarios_do_not_seed_inputs(tmp_path, level):
    with pytest.raises(ValueError):
        scenarios.seed(tmp_path, level)
    assert not list(tmp_path.iterdir())
