"""Offline benchmark graders and ablations; zero Provider requests."""

import importlib.util
from pathlib import Path


def study():
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "phase2" / "intelligence_benchmark.py"
    )
    spec = importlib.util.spec_from_file_location("intelligence_benchmark", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_independent_team_grader_detects_coverage_capability_and_budget_failures():
    from forge_core.team_composition import CompositionLimits, RoleSpec

    module = study()
    role = RoleSpec(
        id="coding", input_tokens=10, output_tokens=10, required_capabilities={"coding": 70}
    )
    limits = CompositionLimits(
        max_members=1,
        max_depth=0,
        max_concurrency=1,
        max_total_tokens=20,
        max_cost_microunits=10,
        currency="USD",
    )
    broken = [
        {
            "role_id": "coding",
            "capabilities": {"coding": 40},
            "estimated_tokens": 21,
            "estimated_cost_microunits": 11,
        }
    ]
    assert set(module.evaluate_team([role], broken, limits)["violations"]) == {
        "budget",
        "capability",
    }
    assert module.evaluate_team([role], [], limits)["violations"] == ["role_coverage"]


def test_fixed_team_study_compares_distinct_policies_and_preserves_completed_assignments():
    result = study().team_study()
    assert len(result["rows"]) == 48
    summaries = {row["variant"]: row for row in result["summary"]}
    assert summaries["static_team"]["specialist_target_fraction"] == 0
    assert summaries["greedy"]["specialist_target_fraction"] == 1
    assert all(row["contract_passed"] for row in result["rows"])
    assert all(
        row["completed_assignment_preserved"]
        for row in result["rows"]
        if row["recomposition_count"]
    )
    assert all(
        row["task_success"] is None and row["mode"] == "synthetic_selection"
        for row in result["rows"]
    )


def test_equal_budget_compression_study_checks_source_ids_hashes_and_real_reopen():
    result = study().context_study()
    summaries = {row["variant"]: row for row in result["summary"]}
    assert summaries["full_context"]["admission_rate"] == 0
    assert summaries["conventional_summary"]["source_grounded_recall"] == 0
    assert summaries["task_centric"]["source_grounded_recall"] < 1
    assert summaries["task_centric_source_recall"]["source_grounded_recall"] == 1
    assert all(row["core_reopened"] and row["source_exact_hash_verified"] for row in result["rows"])
