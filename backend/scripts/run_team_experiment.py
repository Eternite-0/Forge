"""Fixed synthetic team-selection baselines and scoring ablations."""

import json
import random
import time

from forge_core.domain import ModelProfile
from forge_core.team_composition import CompositionLimits, RoleSpec, TeamComposerEngine


def main():
    rng = random.Random(42)

    def model(name, capabilities, price, *, confidence=None):
        item = ModelProfile(
            id=name,
            model_id=name,
            provider="synthetic",
            display_name=name,
            capabilities=capabilities,
            context_window=8192,
            max_output_tokens=1024,
            input_cost_per_million=price,
            output_cost_per_million=price,
            currency="USD",
        )
        for measurement in item.capability_measurements.values():
            measurement.confidence = confidence
        return item

    general = model("general", {"research": 80, "coding": 80}, 1)
    researcher = model("researcher", {"research": 81, "vision": rng.randint(90, 99)}, 1)
    coder = model("coder", {"coding": 81, "mathematics": rng.randint(90, 99)}, 1)
    uncertain = model("a_uncertain", {"coding": 80}, 1)
    confident = model("z_confident", {"coding": 80}, 1, confidence=0.9)
    expensive = model("a_expensive", {"research": 95, "coding": 90}, 4)
    cheaper = model("z_cheaper", {"research": 80}, 1)

    def role(name):
        return RoleSpec(
            id=name, required_capabilities={name: 70}, input_tokens=200, output_tokens=100
        )

    scenarios = [
        ("coordination", [role("research"), role("coding")], [general, researcher, coder], 3000),
        ("confidence", [role("coding")], [uncertain, confident], 3000),
        ("budget_trap", [role("research"), role("coding")], [expensive, cheaper], 1500),
    ]
    variants = [
        ("single_model", "single_model", 5, 1),
        ("greedy", "greedy", 5, 1),
        ("coverage", "coverage", 5, 1),
        ("complementarity", "complementarity", 5, 1),
        ("no_confidence", "complementarity", 0, 1),
        ("no_coordination", "complementarity", 5, 0),
    ]
    rows = []
    for task_id, roles, models, cap in scenarios:
        limits = CompositionLimits(
            max_members=len(roles),
            max_depth=1,
            max_concurrency=len(roles),
            max_total_tokens=300 * len(roles),
            max_cost_microunits=cap,
            currency="USD",
        )
        for label, strategy, confidence_weight, coordination_weight in variants:
            start = time.perf_counter()
            try:
                result = TeamComposerEngine(
                    confidence_weight=confidence_weight, coordination_weight=coordination_weight
                ).compose(roles, models, limits, strategy=strategy)
                members = result["members"]
                required = {name for item in roles for name in item.required_capabilities}
                violations = sum(
                    member["capabilities"].get(name, -1) < threshold
                    for member, item in zip(members, roles, strict=True)
                    for name, threshold in item.required_capabilities.items()
                )
                violations += int(result["estimated_tokens"] > limits.max_total_tokens)
                violations += int(
                    result["estimated_cost_microunits"] is None
                    or result["estimated_cost_microunits"] > cap
                )
                row = {
                    "selected": [item["profile_id"] for item in members],
                    "constraint_violations": violations,
                    "required_covered": sum(
                        any(item["capabilities"].get(name, -1) >= 70 for item in members)
                        for name in required
                    ),
                    "coverage_strength": sum(
                        max(item["capabilities"].get(name, 0) for item in members)
                        for name in required
                    ),
                    "estimated_cost_microunits": result["estimated_cost_microunits"],
                    "currency": result["currency"],
                    "coordination_proxy": max(0, len({item["profile_id"] for item in members}) - 1),
                    "status": "selected",
                }
            except ValueError as exc:
                row = {
                    "status": "no_feasible_selection",
                    "reason": str(exc),
                    "constraint_violations": 0,
                }
            rows.append(
                {
                    "task_id": task_id,
                    "variant": label,
                    **row,
                    "elapsed_seconds": time.perf_counter() - start,
                }
            )
    summaries = []
    for label, *_ in variants:
        subset = [row for row in rows if row["variant"] == label]
        selected = [row for row in subset if row["status"] == "selected"]
        summaries.append(
            {
                "variant": label,
                "attempts": len(subset),
                "feasible_selections": len(selected),
                "infeasible_selections": len(subset) - len(selected),
                "violating_selections": sum(row["constraint_violations"] > 0 for row in selected),
                "constraint_violation_denominator": len(selected),
                "constraint_violation_rate": sum(
                    row["constraint_violations"] > 0 for row in selected
                )
                / len(selected)
                if selected
                else None,
                "coverage_strength_sum": sum(row["coverage_strength"] for row in selected),
                "estimated_cost_microunits_sum": sum(
                    row["estimated_cost_microunits"] for row in selected
                ),
                "coordination_proxy_sum": sum(row["coordination_proxy"] for row in selected),
                "elapsed_seconds_sum": sum(row["elapsed_seconds"] for row in subset),
            }
        )
    print(
        json.dumps(
            {
                "dataset": "synthetic-team-v2",
                "seed": 42,
                "mode": "synthetic",
                "purpose": "selection/protocol correctness; not real-model task performance",
                "coordination_proxy": "distinct selected profiles minus one; synthetic, not measured latency",
                "scenarios": len(scenarios),
                "metric_definitions": {
                    "constraint_violation_rate": "violating selected teams / feasible selected teams; refusals reported separately",
                    "coverage_strength": "sum of maximum selected synthetic capability value over required dimensions",
                    "estimated_cost": "synthetic USD prices and fixed token estimates, not observed provider spend",
                    "timing": "single local execution of selection algorithm, not model task latency",
                },
                "summary": summaries,
                "results": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
