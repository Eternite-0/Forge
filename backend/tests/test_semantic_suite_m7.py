import pytest

from forge_core.semantic_suite import run_semantic_suite

CORRECT = """
if eligible_a == 1:
    if eligible_b == 1:
        if cost_a <= cost_b:
            result = 0
        else:
            result = 1
    else:
        result = 0
else:
    if eligible_b == 1:
        result = 1
    else:
        result = -1
"""


def test_fixed_scheduler_suite_checks_feasibility_cost_and_tie():
    report = run_semantic_suite(CORRECT)
    assert report["passed"] is True
    assert len(report["cases"]) >= 8
    assert len(report["suite_digest"]) == 64 and len(report["source_sha256"]) == 64
    assert all(case["passed"] for case in report["cases"])


def test_constant_and_untrusted_ast_cannot_pass():
    assert run_semantic_suite("result = 0")["passed"] is False
    report = run_semantic_suite("import os\nresult = 0")
    assert report["passed"] is False
    assert all(not case["passed"] for case in report["cases"])
    with pytest.raises(ValueError):
        run_semantic_suite(CORRECT, suite_id="model_supplied_suite")


def test_resource_and_input_contract_are_bounded():
    with pytest.raises(ValueError):
        run_semantic_suite("x" * 16001)
    with pytest.raises(TypeError):
        run_semantic_suite({"source": CORRECT, "expected": 0})
    assert run_semantic_suite("result = 1e999")["passed"] is False


def test_isolated_cli_accepts_only_fixed_contract():
    import json
    import subprocess
    import sys

    def run(payload):
        return subprocess.run(
            [sys.executable, "-I", "-m", "forge_core.semantic_suite"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    success = run({"source": CORRECT, "suite_id": "scheduler-v1"})
    assert success.returncode == 0
    assert json.loads(success.stdout)["passed"] is True
    assert run({"source": CORRECT, "expected": [0]}).returncode != 0
