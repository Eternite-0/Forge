"""Assemble final, content-free verification from retained local evidence only."""

import argparse
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "docs/audit/phase2"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference(path):
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha(path),
        "bytes": path.stat().st_size,
    }


def tests(version):
    xml = AUDIT / f"frozen-tests-{version}.xml"
    suites = ET.parse(xml).getroot().findall("testsuite")
    totals = {
        key: sum(int(s.get(key, "0")) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    totals["passed"] = (
        totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    )
    log = AUDIT / f"frozen-tests-{version}.log"
    text = log.read_text(encoding="utf-8-sig")
    totals.update(
        junit_seconds=sum(float(s.get("time", "0")) for s in suites),
        console_summary=text.splitlines()[-1],
        warning="Existing Starlette/httpx TestClient deprecation"
        if "StarletteDeprecationWarning" in text
        else None,
        command=f"backend/.venv/Scripts/python.exe -m pytest backend/tests -c backend/pyproject.toml -q --junitxml=docs/audit/phase2/frozen-tests-{version}.xml",
        evidence=[reference(xml), reference(log)],
        execution_mode="local_tests; Mock Provider fixtures are not live results",
    )
    return totals


def build(version, publication):
    manifest_path = AUDIT / f"source-manifest-{version}.json"
    manifest = read(manifest_path)
    changed = [
        entry["path"]
        for entry in manifest["files"]
        if sha(ROOT / entry["path"]) != entry["sha256"]
    ]
    calculated = hashlib.sha256(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    current_scope = {
        path.replace("\\", "/")
        for path in subprocess.check_output(
            [
                "rg",
                "--files",
                "backend",
                "-g",
                "*.py",
                "-g",
                "*.json",
                "-g",
                "*.toml",
                "-g",
                "*lock*",
            ],
            cwd=ROOT,
            text=True,
        ).splitlines()
    }
    frozen_scope = {entry["path"] for entry in manifest["files"]}
    if (
        changed
        or current_scope != frozen_scope
        or calculated != manifest["manifest_sha256"]
    ):
        raise ValueError("Frozen executable source integrity check failed")
    executable_files = sorted(
        set((ROOT / "backend/src").rglob("*.py"))
        | set((ROOT / "backend/scripts/phase2").rglob("*.py"))
    )
    executable_manifest = [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)}
        for path in executable_files
    ]
    executable_sha = hashlib.sha256(
        json.dumps(executable_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    index_path = publication / "index.json"
    index = read(index_path)
    runs = []
    for entry in index["runs"]:
        path = publication / entry["published_snapshot"]["path"]
        if (
            not path.resolve().is_relative_to(publication.resolve())
            or sha(path) != entry["published_snapshot"]["sha256"]
        ):
            raise ValueError("Published run integrity check failed")
        run = read(path)
        runs.append(
            {
                "run_id": run["run_id"],
                "level": run["level"],
                "source_sha256": run["code_snapshot_sha256"],
                "status": run["acceptance"]["status"],
                "final_status": run["actual_final_status"],
                "failed_checks": run["acceptance"]["failed_checks"],
                "unknown_checks": run["acceptance"]["unknown_checks"],
                "model_calls": run["model_calls"],
                "tool_calls": run["tool_calls"],
                "token_usage": run["token_usage"],
                "duration_seconds": run["duration_seconds"],
                "model_settlement_wait": run.get("model_settlement_wait"),
                "retry_count": run["retry_count"],
                "retry_breakdown": run["retry_breakdown"],
                "retry_accounting": run.get("retry_accounting"),
                "runtime_settings": run["runtime_settings"],
                "review_verdicts": dict(
                    Counter(
                        item.get("verdict") or "no_verdict"
                        for item in run["review_results"]
                    )
                ),
                "artifact_count": len(run["artifact_hashes"]),
                "restart": run["restart"],
                "compaction": run["compaction"],
                "semantic_test_runs": run["semantic_test_runs"],
                "cost": run["cost"],
                "evidence": reference(path),
                "original_sanitized_snapshot_sha256": entry["source_snapshot"][
                    "sha256"
                ],
            }
        )
    accepted = {
        level: [
            run["run_id"]
            for run in runs
            if run["level"] == level and run["status"] == "passed"
        ]
        for level in ("L1", "L2", "L3")
    }
    probe_paths = [
        AUDIT / "provider-probe.json",
        AUDIT / "provider-probe-reasoning-none.json",
    ]
    documentation = [
        ROOT / path
        for path in (
            "README.md",
            "docs/STATUS.md",
            "docs/QUALITY_GATES.md",
            "docs/ROADMAP.md",
            "docs/PHASE2_AGENT_INTELLIGENCE_REPORT.md",
            "docs/PHASE2_STATUS.md",
            "docs/PHASE2_EXPERIMENTS.md",
            "docs/adr/ADR-009-agent-intelligence-contracts-and-review.md",
            "docs/audit/phase2/execution-chain-audit.md",
        )
    ]
    gaps = [
        "General autonomous replanning of the remaining running DAG and hierarchical expansion remain incomplete.",
        "Memory retrieval is lexical with bilingual aliases, not learned semantic retrieval.",
        "Coding/experiments support fixed syntax/numeric tools and own-artifact revision, not arbitrary repository patch/test execution.",
        "The independent reviewer uses the same approved model in a separate context; no model-diversity quality claim is made.",
        "Complete message-specific ACP payload contracts and several online quality metrics remain incomplete.",
        "Algorithm comparisons are fixed exploratory offline mechanism benchmarks, not equal-budget live task-quality gains.",
        "Actual upstream model identity, context/output/rate maxima and prices remain unknown; configured ceilings are not measurements of service maxima.",
        "Unknown in-flight remote outcomes remain fenced; they are never silently replayed or declared successful.",
        "An intermittent Windows startup/reopen test failure is retained; later focused passes do not establish a source fix for the earlier SQLite I/O or HTTP reset errors.",
    ]
    full_tests = tests(version)
    usage_unreported_runs = [
        run["run_id"]
        for run in runs
        if any(
            type(run["token_usage"].get(field)) is not int
            for field in ("input", "output", "unknown_attempts")
        )
    ]
    known_unknown_attempts = sum(
        value
        for run in runs
        if type(value := run["token_usage"].get("unknown_attempts")) is int
    )
    check_results = {}
    for name in ("ruff", "format", "pip", "diff"):
        log_path = AUDIT / f"frozen-{name}-{version}.log"
        log_text = log_path.read_text(encoding="utf-8-sig").strip()
        successful = {
            "ruff": log_text == "All checks passed!",
            "format": re.fullmatch(r"[0-9]+ files already formatted", log_text)
            is not None,
            "pip": log_text == "No broken requirements found.",
            "diff": log_text == "git diff --check: passed",
        }[name]
        if not successful:
            raise ValueError(f"Recorded {name} check does not confirm success")
        check_results[name] = {"status": "passed", "evidence": reference(log_path)}
    if full_tests["failures"] or full_tests["errors"]:
        raise ValueError("Final test suite contains failures or errors")
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "phase": "Phase 2 Agent Intelligence",
        "status": "incomplete",
        "completion_claim": False,
        "reason": "Strict live acceptance is reported per run; remaining requested capabilities and evaluation gaps prevent an overall completion claim.",
        "integration_owner": "root",
        "branch": branch,
        "base_commit": manifest["base_commit"],
        "committed_or_pushed_by_this_task": False,
        "source": {
            "version": version,
            "manifest": reference(manifest_path),
            "manifest_sha256": manifest["manifest_sha256"],
            "file_count": manifest["file_count"],
            "current_files_match": True,
            "live_executable_snapshot_sha256": executable_sha,
        },
        "backend_tests": full_tests,
        "checks": check_results,
        "live": {
            "execution_mode": "live native FORGE Gateway and governed local tools",
            "provider": "wb-local",
            "credential_selection": "enabled local WorkBuddy WB_IN; no credential value recorded",
            "requested_model": "deepseek-v4.1-flash",
            "upstream_identity": "proxy-reported; independently unverified",
            "publication": reference(index_path),
            "run_count": len(runs),
            "accepted_runs_by_level": accepted,
            "all_three_levels_ever_accepted": all(accepted.values()),
            "accepted_current_source_runs_by_level": {
                level: [
                    run["run_id"]
                    for run in runs
                    if run["level"] == level
                    and run["status"] == "passed"
                    and run["source_sha256"] == executable_sha
                ]
                for level in ("L1", "L2", "L3")
            },
            "unknown_attempts": None
            if usage_unreported_runs
            else known_unknown_attempts,
            "known_unknown_attempts": known_unknown_attempts,
            "unknown_usage_run_count": len(usage_unreported_runs),
            "unknown_usage_runs": usage_unreported_runs,
            "reported_input_tokens": sum(
                run["token_usage"].get("input") or 0 for run in runs
            ),
            "reported_output_tokens": sum(
                run["token_usage"].get("output") or 0 for run in runs
            ),
            "usage_aggregation_note": "Reported token sums include only supplied charged values. Missing run statistics and unknown remote attempts prevent a complete usage total. The known unknown-attempt count is a lower bound when historical run statistics are missing; missing values are never proof of zero usage.",
            "cost": None,
            "cost_reason": "No trusted upstream pricing; unknown is not zero.",
            "retry_metric_note": "Historical snapshots without disjoint_event_counts_v2 retain recorded overlapping repair/review counts; do not interpret their sum as distinct retries. New v2 driver counters use separate durable events.",
            "runs": runs,
            "limitations": "Failed and historical runs retain their original outcomes and source identities. An accepted earlier run does not certify later source. These totals exclude separate protocol probes and external diagnostics.",
        },
        "provider_protocol_probes": [
            {
                "evidence": reference(path),
                "execution_mode": "live",
                "result": read(path),
            }
            for path in probe_paths
        ],
        "offline_algorithm_benchmark": {
            "evidence": reference(AUDIT / f"benchmark-final-{version}.json"),
            "execution_mode": "offline_synthetic",
            "live_provider_calls": 0,
            "limitations": "Small fixed deterministic mechanism comparisons; no real task-quality/statistical generalization claim.",
        },
        "preservation": {
            "evidence": reference(AUDIT / f"preservation-{version}.json"),
            "phase1_evidence": "355/355 original evidence files byte-identical (340 earlier subset plus 15 hardening records)",
            "original_tests": "364/364 function identities retained; exact AST/assertion changes disclosed in the audit, not claimed byte-identical",
        },
        "remaining_limitations": gaps,
        "additional_diagnostics": [
            reference(path)
            for path in (
                AUDIT / "acceptance-v15.md",
                AUDIT / "acceptance-independent-v15.json",
                AUDIT / "live-usage-v15-audit.json",
                AUDIT / "worker-model-usage-migration-v15-backup.json",
                AUDIT / "worker-model-usage-v15-compat-first.xml",
                AUDIT / "reviewer-claim-diagnostic-v12.json",
                AUDIT / "first-archive-v12-intermediate-failures.md",
                AUDIT / "capacity-design-v13.json",
                AUDIT / "restart-known-usage-v14-diagnosis.json",
                AUDIT / "worker-model-usage-migration-v14.json",
                AUDIT / "startup-io-v14-diagnosis.json",
                AUDIT / "acceptance-baseline-v12-repeat.xml",
                AUDIT / "frozen-tests-v13.xml",
                AUDIT / "frozen-tests-v14.xml",
                AUDIT / "review-capacity-preview-v13-tests-first.xml",
                AUDIT / "review-capacity-preview-v13-tests-fixed.xml",
            )
            if path.is_file()
        ],
        "earlier_requirement_gap_audit": reference(
            AUDIT / "unverified-requirements-v6.json"
        ),
        "documentation": [reference(path) for path in documentation],
        "evidence_builder": reference(Path(__file__).resolve()),
        "scope_exclusions": [
            "UI expansion",
            "desktop packaging",
            "automatic push or merge",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"v[0-9]+", args.version) is None:
        raise ValueError("Invalid frozen version")
    result = build(args.version, args.publication.resolve())
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(
            result,
            stream,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        stream.write("\n")
    print(
        json.dumps(
            {
                "path": str(args.output),
                "status": result["status"],
                "live_runs": result["live"]["run_count"],
            }
        )
    )


if __name__ == "__main__":
    main()
