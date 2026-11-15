"""Fixed, offline algorithm study; never reports synthetic results as live success.

Run from backend with --output <new-file.json>. Existing experiment files and
their historical results are never overwritten. No Provider requests are made.
"""

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import time
from pathlib import Path

from forge_core.context_service import ContextService
from forge_core.domain import ConversationMessage, ModelProfile, Quest, new_id, now_iso
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import CompositionLimits, RoleSpec, TeamComposerEngine

TEAM_CASES = (
    ("code-small", "coding", 1, ("coding",)),
    ("math-check", "mathematics", 1, ("math",)),
    ("research-architecture", "research", 2, ("research", "architect")),
    ("code-review", "coding", 2, ("coding", "reviewer")),
    (
        "research-experiment",
        "research",
        3,
        ("research", "math", "coding", "experiment", "reviewer"),
    ),
    (
        "system-development",
        "architecture",
        3,
        ("planner", "architect", "coding", "experiment", "reviewer"),
    ),
)
ROLE_CAPABILITIES = {
    "coding": "coding",
    "math": "mathematics",
    "research": "research",
    "architect": "architecture",
    "experiment": "mathematics",
    "reviewer": "instruction_following",
    "planner": "planning",
}
CONTEXT_CASES = (
    (
        "coding",
        1,
        "Keep the original Python source unchanged.",
        "Run the regression test before final delivery.",
    ),
    (
        "mathematics",
        1,
        "Use exact integer arithmetic.",
        "Check the zero and negative boundary cases.",
    ),
    ("research", 2, "Cite every factual conclusion.", "Retain the original source timestamp."),
    (
        "architecture",
        2,
        "Preserve the public API.",
        "The implementation must survive Core restart.",
    ),
    (
        "experiment",
        3,
        "Use the fixed random seed 41.",
        "Report failed trials as well as successes.",
    ),
    (
        "multimodal",
        3,
        "Keep all original image files.",
        "Do not infer unreadable text as verified facts.",
    ),
)
SUITE = "forge-intelligence-offline-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def profile(name, capabilities, price=1):
    return ModelProfile(
        id=name,
        provider="synthetic",
        model_id=name,
        display_name=name,
        capabilities=capabilities,
        context_window=8192,
        max_output_tokens=2048,
        input_cost_per_million=price,
        output_cost_per_million=price,
        currency="USD",
    )


def evaluate_team(roles, selected, limits):
    """Independent hard-contract grader: does not use Composer's utility score."""
    by_role = {item["role_id"]: item for item in selected}
    violations = []
    if set(by_role) != {role.id for role in roles}:
        violations.append("role_coverage")
    tokens = sum(item["estimated_tokens"] for item in selected)
    cost = sum(item["estimated_cost_microunits"] for item in selected)
    if tokens > limits.max_total_tokens or cost > limits.max_cost_microunits:
        violations.append("budget")
    for role in roles:
        member = by_role.get(role.id)
        if member is None:
            continue
        if any(
            member["capabilities"].get(name, -1) < minimum
            for name, minimum in role.required_capabilities.items()
        ):
            violations.append("capability")
    # A separate fixed specification tests whether selected capabilities reach
    # the desired specialist band; it is a synthetic proxy, not task execution.
    specialist_targets = sum(
        by_role.get(role.id, {}).get("capabilities", {}).get(name, -1) >= 90
        for role in roles
        for name in role.required_capabilities
    )
    return {
        "contract_passed": not violations,
        "violations": violations,
        "specialist_targets_met": specialist_targets,
        "specialist_targets_total": sum(len(role.required_capabilities) for role in roles),
        "estimated_tokens": tokens,
        "synthetic_cost_microunits": cost,
    }


def team_study():
    dimensions = set(ROLE_CAPABILITIES.values())
    models = [profile("general", dict.fromkeys(dimensions, 80))]
    models += [profile(f"specialist-{name}", {name: 96}, price=2) for name in sorted(dimensions)]
    variants = (
        "single_model",
        "static_team",
        "greedy",
        "coverage",
        "complementarity",
        "dynamic_recomposition",
        "no_coordination",
        "no_confidence",
    )
    rows = []
    for case_id, domain, difficulty, names in TEAM_CASES:
        roles = [
            RoleSpec(
                id=name,
                required_capabilities={ROLE_CAPABILITIES[name]: 70},
                input_tokens=200,
                output_tokens=100,
                max_tool_calls=0,
            )
            for name in names
        ]
        limits = CompositionLimits(
            max_members=len(roles),
            max_depth=1,
            max_concurrency=len(roles),
            max_total_tokens=300 * len(roles),
            max_total_tool_calls=0,
            max_cost_microunits=600 * len(roles),
            currency="USD",
        )
        for variant in variants:
            started = time.perf_counter()
            engine = TeamComposerEngine(
                confidence_weight=0 if variant == "no_confidence" else 5,
                coordination_weight=0 if variant == "no_coordination" else 1,
            )
            strategy = (
                variant
                if variant in {"single_model", "greedy", "coverage", "complementarity"}
                else "complementarity"
            )
            candidates = models[:1] if variant == "static_team" else models
            selected = engine.compose(roles, candidates, limits, strategy=strategy)["members"]
            reworks = 0
            completed_preserved = None
            if variant == "dynamic_recomposition" and len(roles) > 1:
                completed = canonical(selected[0])
                unavailable = selected[-1]["profile_id"]
                replacement_models = [model for model in models if model.id != unavailable]
                remaining = engine.compose(
                    roles[1:], replacement_models, limits, strategy="complementarity"
                )["members"]
                selected = [selected[0], *remaining]
                completed_preserved = canonical(selected[0]) == completed
                reworks = 1
            rows.append(
                {
                    "case": case_id,
                    "domain": domain,
                    "difficulty": difficulty,
                    "variant": variant,
                    "selected": [item["profile_id"] for item in selected],
                    **evaluate_team(roles, selected, limits),
                    "recomposition_count": reworks,
                    "completed_assignment_preserved": completed_preserved,
                    "algorithm_seconds": time.perf_counter() - started,
                    "actual_model_tokens": 0,
                    "actual_provider_cost": None,
                    "task_success": None,
                    "mode": "synthetic_selection",
                }
            )
    return {
        "cases": len(TEAM_CASES),
        "rows": rows,
        "summary": [
            {
                "variant": variant,
                "attempts": sum(row["variant"] == variant for row in rows),
                "contract_pass_rate": sum(
                    row["contract_passed"] for row in rows if row["variant"] == variant
                )
                / len(TEAM_CASES),
                "specialist_target_fraction": sum(
                    row["specialist_targets_met"] for row in rows if row["variant"] == variant
                )
                / sum(row["specialist_targets_total"] for row in rows if row["variant"] == variant),
            }
            for variant in variants
        ],
        "limitations": "Synthetic profiles and prices. Contract/specialist proxies do not measure model task quality, hierarchy or actual adaptive runtime. Runtime retention is separately regression tested.",
    }


def size(records):
    return len(
        canonical({"policy": "fixed", "output_reserve": 512, "records": records}).encode("utf-8")
    )


def pack(records, byte_limit):
    selected = []
    for record in records:
        if size([*selected, record]) <= byte_limit:
            selected.append(record)
    return selected


def context_study():
    rows = []
    byte_limit = 6144
    with tempfile.TemporaryDirectory(prefix="forge-phase2-compression-") as temporary:
        root = Path(temporary)
        for domain, difficulty, early, late in CONTEXT_CASES:
            folder = root / domain
            folder.mkdir()
            store = Store(folder / "core.sqlite3", workspace=Workspace.from_path(folder))
            quest = Quest(title=domain, goal=f"Finish the {domain} task with traceable sources")
            store.save_quest(quest)
            # The second obligation lies beyond the production capsule's excerpt
            # boundary. Only source recall can recover that omitted source text.
            content = early + " Context detail." * 95 + " " + late
            original = ConversationMessage(role="user", content=content)
            store.add_message(original)
            for index in range(48):
                store.add_message(
                    ConversationMessage(
                        role="assistant",
                        content=f"Routine {domain} progress {index}. " + "diagnostic " * 20,
                    )
                )
            originals = store.list_messages(limit=100)
            service = ContextService(store)
            service.compact_router(keep_recent=4)
            reopened = Store(store.path, workspace=store.workspace)
            service = ContextService(reopened)
            rebuilt = service.rebuild_router()
            full = [{"text": item["content"], "refs": [item["id"]]} for item in originals]
            task = [
                {
                    "text": item["text"],
                    "refs": item["source_refs"],
                    "omitted": item.get("omitted", False),
                }
                for item in rebuilt["capsule"]["hypotheses"]
            ]
            task.sort(key=lambda item: not item["omitted"])
            task += [
                {"text": item["content"], "refs": [item["id"]]}
                for item in rebuilt["recent_messages"]
            ]
            task = pack(task, byte_limit)
            recalled = []
            for item in task:
                if item.get("omitted"):
                    for ref in item["refs"]:
                        source = service.read_source("router", "main", ref)
                        recalled.append(
                            {
                                "text": source["content"],
                                "refs": [source["id"]],
                                "source_sha256": digest(source["content"]),
                            }
                        )
            variants = {
                "full_context": full,
                "recency_truncation": list(reversed(pack(list(reversed(full)), byte_limit))),
                "conventional_summary": [{"text": originals[0]["content"][:256], "refs": []}]
                + [{"text": item["content"][:128], "refs": []} for item in originals[-3:]],
                "task_centric": task,
                "task_centric_source_recall": pack([*recalled, *task], byte_limit),
            }
            for variant, records in variants.items():
                admitted = size(records) <= byte_limit
                retained = (
                    sum(any(needle in item["text"] for item in records) for needle in (early, late))
                    if admitted
                    else 0
                )
                grounded = (
                    sum(
                        any(
                            needle in item["text"] and original.id in item["refs"]
                            for item in records
                        )
                        for needle in (early, late)
                    )
                    if admitted
                    else 0
                )
                source = service.read_source("router", "main", original.id)
                if digest(source["content"]) != digest(content):
                    raise RuntimeError("Archived source failed exact content hash oracle")
                rows.append(
                    {
                        "domain": domain,
                        "difficulty": difficulty,
                        "variant": variant,
                        "input_byte_limit": byte_limit,
                        "serialized_bytes": size(records),
                        "budget_admitted": admitted,
                        "obligations": 2,
                        "retained_obligations": retained,
                        "source_grounded_obligations": grounded,
                        "source_exact_hash_verified": digest(source["content"]) == digest(content),
                        "core_reopened": True,
                        "model_calls": 0,
                        "model_tokens": 0,
                        "provider_cost": None,
                        "mode": "synthetic_context_actual_archive",
                    }
                )
    variants = list(variants)
    return {
        "cases": len(CONTEXT_CASES),
        "input_byte_limit": byte_limit,
        "rows": rows,
        "summary": [
            {
                "variant": variant,
                "admission_rate": sum(
                    row["budget_admitted"] for row in rows if row["variant"] == variant
                )
                / len(CONTEXT_CASES),
                "constraint_retention": sum(
                    row["retained_obligations"] for row in rows if row["variant"] == variant
                )
                / (2 * len(CONTEXT_CASES)),
                "source_grounded_recall": sum(
                    row["source_grounded_obligations"] for row in rows if row["variant"] == variant
                )
                / (2 * len(CONTEXT_CASES)),
            }
            for variant in variants
        ],
        "limitations": "Fixed extractive summary baseline, not model-generated summary. Equal serialized UTF-8 byte budgets, not measured provider token budgets. Full context exceeding the same budget is refused. Source hashes/IDs, not text similarity, establish source recovery.",
    }


def governance_study():
    source = Path(__file__).resolve().parents[1] / "run_governance_experiment.py"
    spec = importlib.util.spec_from_file_location("phase2_governance_study", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        module.main()
    result = json.loads(output.getvalue())
    # Preserve the original actual ToolEngine cases, plus explicitly labelled
    # counterfactual ablations. No synthetic prediction becomes trusted evidence.
    return {
        **result,
        "required_variants": {
            "model_claim_only": "counterfactual in ablations; never authorizes an action",
            "tool_receipt_verification": "actual tool receipt replay and forged-receipt cases",
            "independent_reviewer": "actual fixed NodeVerifier rejection case; no LLM review quality estimate",
            "evidence_gated_completion": "actual CompletionGate reached through NodeVerifier; no live model task",
        },
    }


def retrieval_study():
    source = Path(__file__).with_name("retrieval_evaluation.py")
    spec = importlib.util.spec_from_file_location("phase2_retrieval_study", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    destination = arguments.output.resolve()
    if destination.exists():
        raise SystemExit("Choose a new output path; historical experiment records are preserved")
    started = time.perf_counter()
    result = {
        "run_id": new_id("benchmark"),
        "created_at": now_iso(),
        "suite": SUITE,
        "execution_mode": "offline_synthetic",
        "live_provider_calls": 0,
        "dataset_sha256": digest([TEAM_CASES, CONTEXT_CASES, ROLE_CAPABILITIES]),
        "dataset": {
            "team": TEAM_CASES,
            "context": CONTEXT_CASES,
            "role_capabilities": ROLE_CAPABILITIES,
        },
        "limitations": "Exploratory deterministic algorithm regressions with small fixed test sets, no statistical generalization or real task-success claim. Actual runtime E2E evidence is reported separately.",
    }
    failed = False
    for name, operation in (
        ("team_composition", team_study),
        ("context_compression", context_study),
        ("governance", governance_study),
        ("retrieval", retrieval_study),
    ):
        try:
            result[name] = operation()
        except Exception as exc:  # noqa: BLE001 - preserve failed experiment sections
            failed = True
            result[name] = {"status": "failed", "error_type": type(exc).__name__}
    result["final_status"] = "failed" if failed else "completed"
    result["duration_seconds"] = time.perf_counter() - started
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(destination),
                "suite": SUITE,
                "live_provider_calls": 0,
                "final_status": result["final_status"],
                "duration_seconds": result["duration_seconds"],
            }
        )
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
