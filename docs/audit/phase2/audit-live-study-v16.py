"""Reproduce the independent fixed-case study audit without application imports."""

import collections
import json
import runpy
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
audit = runpy.run_path(str(ROOT / "audit-independent-v16.py"))
source = ROOT / "live-algorithm-study-v16-results.json"
records = ROOT / "live-algorithm-study-v16-results-records"
original_ref = audit["evidence"](source)
report = audit["read"](source)
manifest = audit["read"](records / "manifest.json")
dataset = {case["case_id"]: case for case in manifest["dataset"]}
consistency = audit["live_study"](source)

for case in dataset.values():
    for item in case["sources"]:
        assert audit["sha"](item["content"].encode()) == item["sha256"]
        assert len(item["content"].splitlines()) == item["lines"]

rows, errors, case_summary, groups = [], [], [], collections.defaultdict(list)
for row in report["rows"]:
    case = dataset[row["case_id"]]
    actual = audit["independently_score"](case, row.get("candidate"))
    candidate = row.get("candidate") or {}
    accepted = row.get("policy_acceptance")
    if row["family"] == "governance" and (
        row["false_acceptance"] != bool(accepted and not actual)
        or row["false_rejection"] != bool(not accepted and actual)
    ):
        errors.append(
            "governance_decision_count:" + row["case_id"] + ":" + row["variant"]
        )
    stages = row["stages"]
    usage = row["usage"]
    if usage["model_calls"] != len(stages) or any(
        a["state"] != "settled" for a in usage["attempts"]
    ):
        errors.append(
            "settlement_or_call_count:" + row["case_id"] + ":" + row["variant"]
        )
    rowpath = (
        records
        / (row["case_id"] + "-" + row["family"] + "-" + row["variant"])
        / "row.json"
    )
    result = {
        "case_id": row["case_id"],
        "family": row["family"],
        "variant": row["variant"],
        "original_row": audit["evidence"](rowpath),
        "expected_answers": audit["oracle"](case),
        "returned_answers": candidate.get("answers"),
        "independent_task_success": actual,
        "original_task_success": row["score"]["task_success"],
        "violations": row["score"]["violations"],
        "model_calls": len(stages),
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "policy_acceptance": accepted,
        "false_acceptance": row.get("false_acceptance"),
        "false_rejection": row.get("false_rejection"),
        "review_verdict": row.get("review_verdict"),
    }
    rows.append(result)
    groups[(row["family"], row["variant"])].append(result)
for case_id, case in dataset.items():
    group = [row for row in rows if row["case_id"] == case_id]
    case_summary.append(
        {
            "case_id": case_id,
            "domain": case["domain"],
            "difficulty": case["difficulty"],
            "expected_answers": audit["oracle"](case),
            "rows": len(group),
            "task_successes": sum(row["independent_task_success"] for row in group),
            "answer_distribution": dict(
                collections.Counter(
                    json.dumps(row["returned_answers"]) for row in group
                )
            ),
        }
    )
summary = [
    {
        "family": family,
        "variant": variant,
        "cases": len(group),
        "task_successes": sum(row["independent_task_success"] for row in group),
        "false_acceptances": sum(bool(row["false_acceptance"]) for row in group),
        "false_rejections": sum(bool(row["false_rejection"]) for row in group),
        "model_calls": sum(row["model_calls"] for row in group),
        "input_tokens": sum(row["input_tokens"] for row in group),
        "output_tokens": sum(row["output_tokens"] for row in group),
    }
    for (family, variant), group in groups.items()
]
limitations = [
    "Exploratory six fixed microtasks and one sample per case/variant; no statistical/general-task superiority claim.",
    "All strategies use the same real model inventory. No diverse-model choice advantage is measured.",
    "Variants generate independent candidates; governance differences therefore combine policy and sample variation.",
    "The benchmark controller performs approved source-tool reads; it does not prove autonomous tool selection or full Quest execution.",
    "Dynamic recomposition gets deterministic failed-check labels and one correction stage, while single-model does not; its outcome cannot isolate composition from feedback/call count.",
    "Evidence-gated acceptance uses this fixed deterministic oracle. Zero false accepts covers this specification, not unseen-task correctness.",
    "A schema-valid answer and real source citation/receipt can coexist with wrong arithmetic or logic. Source identity/range checks do not prove the citation entails the answer.",
    "Proxy-reported model identity is not independently verified; upstream model internals are outside this audit.",
    "Unknown price remains null. Common caps are equal maxima; observed token usage and call counts differ.",
]
result = {
    "schema_version": 1,
    "generated_at": datetime.now(UTC).isoformat(),
    "status": "original_study_scores_independently_verified"
    if consistency["passed"] and not errors
    else "audit_found_inconsistencies",
    "phase2_acceptance_claim": False,
    "original_report": original_ref,
    "consistency": consistency,
    "independent_errors": errors,
    "cases": case_summary,
    "summary": summary,
    "rows": rows,
    "totals": {
        "rows": len(rows),
        "task_successes": sum(row["independent_task_success"] for row in rows),
        "task_failures": sum(not row["independent_task_success"] for row in rows),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "model_calls": sum(row["model_calls"] for row in rows),
        "unknown_attempts": consistency["unknown_attempts"],
        "cost": None,
        "sum_row_token_caps": report["sum_of_row_token_caps"],
    },
    "violation_counts": dict(
        collections.Counter(v for row in rows for v in row["violations"])
    ),
    "verified_examples": [
        {
            "case_id": "code-2",
            "variant": "single_model",
            "actual": [-1, -2, 8, 4, -1],
            "expected": [-1, 2, 8, 4, -1],
            "reason": "For input (0,-2), a is nonnegative and a<=b is false; a-b is 2.",
        },
        {
            "case_id": "schedule-1",
            "variant": "single_model",
            "actual": [-1, 3, 3],
            "expected": [2, 2, -1],
            "reason": "Machine2 has minimum cost and wins the equal-cost ID tie for sizes1/7; size9 has no eligible machine.",
        },
        {
            "case_id": "governance-1",
            "variant": "single_model",
            "actual": [1, 1],
            "expected": [1, 0],
            "reason": "The second stream revokes b before writing b, so its write count is zero.",
        },
    ],
    "limitations": limitations,
    "inspection": {
        "application_imports": 0,
        "provider_calls": 0,
        "runtime_databases_opened": 0,
        "credentials_read": False,
        "original_results_modified": False,
        "resampling": False,
    },
}
assert original_ref == audit["evidence"](source)
with (ROOT / "live-algorithm-study-v16-independent.json").open(
    "x", encoding="utf-8", newline="\n"
) as stream:
    stream.write(
        json.dumps(
            result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    )
lines = [
    "# Independent live study audit — v16",
    "",
    "The original 90-row study scores are independently consistent: **25 task successes and 65 task failures**. All 90 rows completed their model stages; stage completion is distinct from task success. No original score or sample was changed.",
    "",
    "The independent checker uses its own task algorithms and answer/source/constraint validation, importing neither the study scorer nor any FORGE module. It recomputed the fixed dataset and oracle hashes, verified both script hashes against the v16 frozen manifest, checked all 90 persisted row records, and matched every task-success score and reported summary. There were no mismatches, over-budget rows or unresolved attempts.",
    "",
    "Observed usage is **91,487 input + 19,756 output tokens across 135 model calls**. The sum of independent row caps is 1,440,000 tokens. Cost remains unknown.",
    "",
    "| Case | Expected answers | Successful rows / 15 |",
    "| --- | --- | --- |",
]
for row in case_summary:
    lines.append(
        f"| {row['case_id']} | `{row['expected_answers']}` | {row['task_successes']} / 15 |"
    )
lines += [
    "",
    "The low scores reflect actual wrong answers. For `(0,-2)`, the code returns `2`, while ten code-2 rows returned `-2` at that position. The schedule-1 single-model sample returned `[-1,3,3]` instead of `[2,2,-1]`. The governance-1 sample counted a revoked write, returning `[1,1]` instead of `[1,0]`. Source citations and valid JSON did not establish correct reasoning.",
    "",
    "Governance false acceptance counts were 4/6 for model claims, 3/6 for receipt verification, and 3/6 for independent review. Independent review also rejected two correct candidates. The fixed oracle gate had zero false accepts/rejects on its six samples; this is a property of the stated fixed specification and cannot establish general safety or correctness.",
    "",
    "The study is a small single-model controlled policy comparison. Variants use independently sampled candidates and unequal observed call counts. Dynamic recomposition additionally receives deterministic failed-check feedback. Those differences prevent attributing its three successes to team selection alone. No variant demonstrates broad superiority, and none certifies autonomous complex-Quest acceptance.",
    "",
    "The JSON companion includes exact original artifact hashes, all 90 row references, independent expected/returned answers, per-case distributions and limitations. No Provider calls, database reads, credentials or resampling were used in this audit.",
    "",
]
with (ROOT / "live-algorithm-study-v16-independent.md").open(
    "x", encoding="utf-8", newline="\n"
) as stream:
    stream.write("\n".join(lines))
print(json.dumps({"status": result["status"], "totals": result["totals"]}))
