"""Assemble the current record only after independent, immutable evidence exists."""

import argparse
import json
from pathlib import Path

from build_verification import AUDIT, ROOT, build, read, reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v17")
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = args.version
    publication = args.publication.resolve()
    record = build(version, publication)
    independent_path = AUDIT / f"acceptance-independent-{version}.json"
    independent = read(independent_path)
    goals_path = AUDIT / f"acceptance-independent-{version}-goals.json"
    goals = read(goals_path)
    current = record["live"]["accepted_current_source_runs_by_level"]
    completed = (
        independent.get("phase2_complete") is True
        and independent.get("evidence_consistency_passed") is True
        and len(goals) == 15
        and {goal.get("number") for goal in goals} == set(range(1, 16))
        and all(goal.get("acceptance_status") == "satisfied" for goal in goals)
        and all(current.get(level) for level in ("L1", "L2", "L3"))
    )
    record.update(
        status="complete" if completed else "incomplete",
        completion_claim=completed,
        reason=(
            "The frozen backend passes the full suite and static checks; each strict live level and all fifteen bounded final standards have independently checked evidence."
            if completed
            else "One or more frozen-source, strict-live or final-standard evidence gates remain open."
        ),
        independent_acceptance=reference(independent_path),
        final_standard_matrix=goals,
        final_standard_matrix_evidence=reference(goals_path),
        remaining_limitations=[
            "Acceptance covers the recorded bounded tasks, tools and budgets; arbitrary-task correctness and indefinite continuity are not established.",
            "The available real Provider exposes one requested model. Multi-model selection gains and different-model review quality are unproven.",
            "Image/audio/video execution and unrestricted repository shell/patch/test execution are outside the implemented tool set.",
            "Typed approvals remain necessary for plans, teams and governed effects. Shared serial contingency can exhaust; it does not guarantee every worst-case correction.",
            "Upstream model identity and prices are not independently known; monetary costs remain null.",
            "Historical failed runs, missing usage and unresolved remote outcomes retain their original evidence and fences.",
        ],
    )
    record["live_algorithm_study"] = {
        "execution_mode": "live controlled microstudy; distinct from autonomous strict Quests",
        "source_version": "v16",
        "results": reference(AUDIT / "live-algorithm-study-v16-results.json"),
        "independent_scoring": reference(
            AUDIT / "live-algorithm-study-v16-independent.json"
        ),
        "task_successes": 25,
        "task_failures": 65,
        "rows": 90,
        "calls": 135,
        "known_tokens": 111243,
        "cost": None,
        "limitations": "Six fixed cases and fifteen policy variants using one model; exploratory results do not establish generalized policy superiority.",
    }
    record["semantic_crossquest_probe"] = {
        "execution_mode": "one fixed live exploratory query",
        "raw_probe_passed": False,
        "raw_probe": reference(AUDIT / "semantic-crossquest-v17-raw.json"),
        "independent_source_audit": reference(
            AUDIT / "semantic-crossquest-v17-independent.json"
        ),
        "interpretation": "The original false result is retained. Its oracle omitted automatically indexed constraints; the independent read-only audit verifies all three returned records against their original target source and unchanged trust.",
        "calls": 1,
        "known_tokens": 1826,
        "cost": None,
    }
    record["documentation"].append(
        reference(ROOT / "docs/adr/ADR-010-bounded-serial-review-reserve.md")
    )
    record["assembly_builder"] = reference(Path(__file__).resolve())
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
    print(json.dumps({"output": str(args.output), "completion_claim": completed}))


if __name__ == "__main__":
    main()
