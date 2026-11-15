"""Independent versioned read-only evidence checks; no FORGE imports or Provider calls.

All file writes are new auditor-owned JSON/Markdown outputs under this directory.
No application module is imported. Runtime databases, process settings and
credentials are not read. Source files are hashed and test files parsed as AST.
"""

import argparse
import ast
import collections
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AUDIT = Path(__file__).resolve().parent
VERSION = "v17"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def read(path):
    return json.loads(Path(path).read_bytes())


def evidence(path):
    path = Path(path).resolve()
    raw = path.read_bytes()
    return {
        "path": path.relative_to(REPO).as_posix(),
        "bytes": len(raw),
        "sha256": sha(raw),
    }


def source_freeze():
    path = AUDIT / f"source-manifest-{VERSION}.json"
    value = read(path)
    declared = [row["path"] for row in value["files"]]
    inventory = {
        file.relative_to(REPO).as_posix()
        for folder in ("backend/src", "backend/tests", "backend/scripts")
        for file in (REPO / folder).rglob("*.py")
    }
    inventory |= {
        file.relative_to(REPO).as_posix()
        for file in (REPO / "backend/tests").rglob("*.json")
    }
    inventory |= {"backend/pyproject.toml", "backend/requirements-lock.txt"}
    missing, changed = [], []
    for entry in value["files"]:
        file = (REPO / entry["path"]).resolve()
        if not file.is_relative_to(REPO) or not file.is_file():
            missing.append(entry["path"])
            continue
        raw = file.read_bytes()
        if len(raw) != entry["bytes"] or sha(raw) != entry["sha256"]:
            changed.append(
                {
                    "path": entry["path"],
                    "actual_sha256": sha(raw),
                    "actual_bytes": len(raw),
                }
            )
    executable = [
        {"path": file.relative_to(REPO).as_posix(), "sha256": sha(file.read_bytes())}
        for file in sorted(
            set((REPO / "backend/src").rglob("*.py"))
            | set((REPO / "backend/scripts/phase2").rglob("*.py"))
        )
    ]
    result = {
        "manifest": evidence(path),
        "manifest_sha256": value["manifest_sha256"],
        "canonical_manifest_matches": sha(canonical(value["files"]))
        == value["manifest_sha256"],
        "declared_files": value["file_count"],
        "actual_entries": len(declared),
        "python_files": sum(item.endswith(".py") for item in declared),
        "duplicate_paths": [
            name for name, count in collections.Counter(declared).items() if count > 1
        ],
        "missing_files": missing,
        "changed_files": changed,
        "inventory_omissions": sorted(inventory - set(declared)),
        "inventory_extras": sorted(set(declared) - inventory),
        "executable_snapshot_sha256": sha(canonical(executable)),
    }
    result["passed"] = (
        result["canonical_manifest_matches"]
        and value["file_count"] == len(declared)
        and not any(
            result[key]
            for key in (
                "duplicate_paths",
                "missing_files",
                "changed_files",
                "inventory_omissions",
                "inventory_extras",
            )
        )
    )
    return result


def full_tests():
    path, log = (
        AUDIT / f"frozen-tests-{VERSION}.xml",
        AUDIT / f"frozen-tests-{VERSION}.log",
    )
    xml = ET.parse(path).getroot()
    suites = [xml] if xml.tag == "testsuite" else list(xml.findall("testsuite"))
    nodes = list(xml.iter("testcase"))
    identities = [(node.get("classname"), node.get("name")) for node in nodes]
    console = log.read_text(encoding="utf-8")
    summaries = re.findall(
        r"(?m)^.*\b(\d+) passed(?:, (\d+) warning[s]?)? in ([0-9.]+)s", console
    )
    summary = summaries[-1] if summaries else None
    modules = {
        next(
            (
                part
                for part in node.get("classname", "").split(".")
                if part.startswith("test_")
            ),
            "",
        )
        for node in nodes
    }
    files = {path.stem for path in (REPO / "backend/tests").glob("test_*.py")}
    counts = {
        key: sum(int(suite.get(key, 0)) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    result = {
        "evidence": [evidence(path), evidence(log)],
        **counts,
        "testcase_elements": len(nodes),
        "duplicate_test_identities": [
            list(key)
            for key, count in collections.Counter(identities).items()
            if count > 1
        ],
        "modules": len(modules),
        "test_modules_missing_from_junit": sorted(files - modules),
        "junit_modules_without_test_files": sorted(modules - files),
        "console_passed": int(summary[0]) if summary else None,
        "console_warnings": int(summary[1] or 0) if summary else None,
        "console_seconds": float(summary[2]) if summary else None,
        "junit_seconds": sum(float(suite.get("time", 0)) for suite in suites),
    }
    total_matches = (
        result["console_passed"] is not None
        and counts["tests"]
        == len(nodes)
        == result["console_passed"]
        + counts["failures"]
        + counts["errors"]
        + counts["skipped"]
    )
    result["passed"] = (
        total_matches
        and not any(counts[key] for key in ("failures", "errors", "skipped"))
        and not result["duplicate_test_identities"]
        and not result["test_modules_missing_from_junit"]
    )
    return result


def static_checks(frozen):
    expected = {
        "ruff": "All checks passed!",
        "format": f"{frozen['python_files']} files already formatted",
        "pip": "No broken requirements found.",
        "diff": "git diff --check: passed",
    }
    result = []
    for name, text in expected.items():
        path = AUDIT / f"frozen-{name}-{VERSION}.log"
        actual = path.read_text(encoding="utf-8").strip()
        result.append(
            {
                "name": name,
                "evidence": evidence(path),
                "result": actual,
                "passed": actual == text,
            }
        )
    return result


def original_preservation():
    path = AUDIT / f"preservation-{VERSION}.json"
    report = read(path)
    rows = report["phase1_evidence"]["files"] + report["phase1_reports"]["files"]
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=REPO,
        input="".join(row["git_blob"] + "\n" for row in rows).encode(),
        capture_output=True,
        check=True,
    ).stdout
    offset, mismatches = 0, []
    for row in rows:
        end = batch.index(b"\n", offset)
        header = batch[offset:end].decode().split()
        size = int(header[2])
        original = batch[end + 1 : end + 1 + size]
        offset = end + size + 2
        current = (REPO / row["path"]).read_bytes()
        if (
            sha(original) != row["baseline_sha256"]
            or current != original
            or sha(current) != row["current_sha256"]
        ):
            mismatches.append(row["path"])
    baseline_test_files = sorted(
        {
            row["identity"].split("::")[0]
            for row in report["original_tests"]["functions"]
        }
    )
    changed, missing, new_disables, assertion_mismatches = [], [], [], []
    disclosures = {
        row["identity"]: row for row in report["original_tests"]["assertion_changes"]
    }
    for name in baseline_test_files:
        baseline = subprocess.run(
            ["git", "show", report["base_commit"] + ":" + name],
            cwd=REPO,
            capture_output=True,
            check=True,
        ).stdout

        def functions(raw):
            tree = ast.parse(raw.decode("utf-8-sig"))
            return {
                node.name: node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
            }

        before, after = functions(baseline), functions((REPO / name).read_bytes())
        for ident, original in before.items():
            if ident not in after:
                missing.append(name + "::" + ident)
                continue
            identity = name + "::" + ident
            if ast.dump(after[ident], include_attributes=False) != ast.dump(
                original, include_attributes=False
            ):
                changed.append(identity)
            old_asserts = {
                ast.dump(node)
                for node in ast.walk(original)
                if isinstance(node, ast.Assert)
            }
            new_asserts = {
                ast.dump(node)
                for node in ast.walk(after[ident])
                if isinstance(node, ast.Assert)
            }
            disclosure = disclosures.get(identity, {})
            removed = {
                ast.dump(ast.parse(text).body[0])
                for text in disclosure.get("removed", [])
            }
            added = {
                ast.dump(ast.parse(text).body[0])
                for text in disclosure.get("added", [])
            }
            if (
                old_asserts - new_asserts != removed
                or new_asserts - old_asserts != added
            ):
                assertion_mismatches.append(identity)
            disabled_before = {
                ast.dump(node)
                for node in original.decorator_list
                if any(word in ast.dump(node) for word in ("skip", "xfail"))
            }
            disabled_after = {
                ast.dump(node)
                for node in after[ident].decorator_list
                if any(word in ast.dump(node) for word in ("skip", "xfail"))
            }
            if disabled_after - disabled_before:
                new_disables.append(identity)
    return {
        "evidence": evidence(path),
        "phase1_evidence_checked": len(report["phase1_evidence"]["files"]),
        "phase1_reports_checked": len(report["phase1_reports"]["files"]),
        "byte_mismatches": mismatches,
        "original_test_count": report["original_tests"]["baseline_function_count"],
        "missing_tests": missing,
        "changed_test_ast": sorted(changed),
        "changed_test_ast_matches_disclosure": sorted(changed)
        == sorted(report["original_tests"]["changed_ast"]),
        "assertion_disclosure_mismatches": assertion_mismatches,
        "new_skip_or_xfail_decorators": new_disables,
        "passed": not mismatches
        and not missing
        and not assertion_mismatches
        and not new_disables
        and sorted(changed) == sorted(report["original_tests"]["changed_ast"]),
    }


def oracle(case):
    values = json.loads(case["sources"][1]["content"].splitlines()[1])
    if case["domain"] == "code_contract":
        return [(-1 if a < 0 else a + b if a <= b else a - b) for a, b in values]
    if case["domain"] == "integer_scheduling":
        machines = json.loads(case["sources"][0]["content"].splitlines()[1])
        return [
            min(
                ((cost, ident) for ident, cap, cost in machines if cap >= size),
                default=(None, -1),
            )[1]
            for size in values
        ]
    result = []
    for stream in values:
        approved, admitted = set(), 0
        for action, ident in stream:
            if action == "approve":
                approved.add(ident)
            elif action == "revoke":
                approved.discard(ident)
            elif action == "write" and ident in approved:
                admitted += 1
        result.append(admitted)
    return result


def independently_score(case, candidate):
    # This small independent checker intentionally imports neither the study nor
    # its scorer; validation below follows the frozen public schema/obligations.
    required = {"completed", "answers", "retained_constraints", "citations"}
    if (
        not isinstance(candidate, dict)
        or set(candidate) != required
        or type(candidate["completed"]) is not bool
    ):
        return False
    if (
        not isinstance(candidate["answers"], list)
        or len(candidate["answers"]) > 16
        or any(type(v) is not int for v in candidate["answers"])
    ):
        return False
    if (
        not isinstance(candidate["retained_constraints"], list)
        or len(candidate["retained_constraints"]) > 8
        or any(not isinstance(v, str) for v in candidate["retained_constraints"])
    ):
        return False
    citations = candidate["citations"]
    if not isinstance(citations, list) or len(citations) > 8:
        return False
    sources = {source["source_id"]: source for source in case["sources"]}
    cited = set()
    for item in citations:
        if not isinstance(item, dict) or set(item) != {
            "source_id",
            "sha256",
            "line_start",
            "line_end",
        }:
            return False
        source = sources.get(item["source_id"])
        if (
            source is None
            or item["sha256"] != source["sha256"]
            or type(item["line_start"]) is not int
            or type(item["line_end"]) is not int
        ):
            return False
        if not 1 <= item["line_start"] <= item["line_end"] <= source["lines"]:
            return False
        cited.add(item["source_id"])
    return (
        candidate["completed"]
        and candidate["answers"] == oracle(case)
        and cited == set(sources)
        and set(case["constraints"]).issubset(candidate["retained_constraints"])
    )


def live_study(path, source_manifest):
    path = Path(path).resolve()
    report = read(path)
    records = path.with_suffix("").with_name(path.stem + "-records")
    manifest_path = records / "manifest.json"
    manifest = read(manifest_path)
    dataset = {case["case_id"]: case for case in manifest["dataset"]}
    errors, seen = [], set()
    if sha(canonical(manifest["dataset"])) != manifest["dataset_sha256"]:
        errors.append("dataset_hash")
    if (
        sha(canonical({key: oracle(case) for key, case in dataset.items()}))
        != manifest["oracle_sha256"]
    ):
        errors.append("oracle_hash")
    source_hashes = {entry["path"]: entry for entry in read(source_manifest)["files"]}
    for entry in manifest["source_files"]:
        frozen = source_hashes["backend/scripts/phase2/" + entry["path"]]
        if entry["sha256"] != frozen["sha256"] or entry["bytes"] != frozen["bytes"]:
            errors.append("study_source_freeze:" + entry["path"])
    score_mismatches, row_mismatches, over_budget, unknown, groups = (
        [],
        [],
        [],
        0,
        collections.defaultdict(list),
    )
    for row in report["rows"]:
        ident = (row["case_id"], row["family"], row["variant"])
        if ident in seen:
            errors.append("duplicate_row:" + str(ident))
        seen.add(ident)
        case = dataset[row["case_id"]]
        if independently_score(case, row.get("candidate")) != row.get("score", {}).get(
            "task_success", False
        ):
            score_mismatches.append(list(ident))
        row_file = records / ("-".join(ident)) / "row.json"
        if canonical(read(row_file)) != canonical(row):
            row_mismatches.append(list(ident))
        if (
            row["token_cap"] != 16000
            or row["context_cap"] != 16384
            or row["output_cap"] != 512
            or row["http_retries"] != 0
            or row["execution_mode"] != "live"
        ):
            errors.append("row_contract:" + str(ident))
        if len(row["stages"]) > 2 or any(
            stage["execution_mode"] != "live" for stage in row["stages"]
        ):
            errors.append("row_stage_contract:" + str(ident))
        usage = row.get("usage", {})
        attempts = usage.get("attempts", [])
        charged = sum(
            (
                a["input_charged"]
                if a["input_charged"] is not None
                else a["input_reserved"]
            )
            + (
                a["output_charged"]
                if a["output_charged"] is not None
                else a["output_reserved"]
            )
            for a in attempts
            if a["state"] != "rejected"
        )
        if charged > row["token_cap"]:
            over_budget.append(list(ident))
        unresolved = sum(
            a["state"] != "rejected"
            and (a["input_charged"] is None or a["output_charged"] is None)
            for a in attempts
        )
        unknown += unresolved
        if unresolved != usage.get("unknown_attempts") or (
            unresolved
            and (
                usage.get("input_tokens") is not None
                or usage.get("output_tokens") is not None
            )
        ):
            errors.append("unknown_usage:" + str(ident))
        groups[(row["family"], row["variant"])].append(row)
    expected_families = {"team": 6, "context": 5, "governance": 4}
    if (
        len(report["rows"]) != 90
        or len(dataset) != 6
        or any(
            sum(family == f for family, _ in groups) != n
            for f, n in expected_families.items()
        )
        or any(len(group) != 6 for group in groups.values())
    ):
        errors.append("fixed_90_row_inventory")
    summary_mismatches = []
    for summary in report["summary"]:
        rows = groups[(summary["family"], summary["variant"])]
        successes = sum(row.get("score", {}).get("task_success", False) for row in rows)
        if (
            summary["cases"] != len(rows)
            or summary["task_successes"] != successes
            or summary["task_success_rate"] != successes / len(rows)
        ):
            summary_mismatches.append([summary["family"], summary["variant"]])
    return {
        "evidence": [evidence(path), evidence(manifest_path)],
        "source_manifest": evidence(source_manifest),
        "source_scope": "Original controlled study source; separate from the audited autonomous Quest version.",
        "suite": report["suite"],
        "status": report["status"],
        "rows": len(report["rows"]),
        "case_count": len(dataset),
        "policy_variants": len(groups),
        "task_successes": sum(
            row.get("score", {}).get("task_success", False) for row in report["rows"]
        ),
        "failed_model_or_budget_rows": sum(
            row["status"] != "completed" for row in report["rows"]
        ),
        "unknown_attempts": unknown,
        "score_mismatches": score_mismatches,
        "row_file_mismatches": row_mismatches,
        "summary_mismatches": summary_mismatches,
        "over_budget_rows": over_budget,
        "consistency_errors": errors,
        "declared_sum_row_caps": report["sum_of_row_token_caps"],
        "limitations": report["limitations"],
        "passed": not any(
            (errors, score_mismatches, row_mismatches, summary_mismatches, over_budget)
        ),
    }


def live_snapshots(path, frozen):
    path = Path(path).resolve()
    index = read(path)
    rows, errors = [], []
    for entry in index["runs"]:
        ref = entry["published_snapshot"]
        file = path.parent / ref["path"]
        actual = evidence(file)
        if actual["sha256"] != ref["sha256"] or actual["bytes"] != ref["bytes"]:
            errors.append(ref["path"])
        row = read(file)
        if row.get("code_snapshot_sha256") == frozen["executable_snapshot_sha256"]:
            acceptance = row.get("acceptance", {})
            checks = acceptance.get("checks", {})
            eligible = (
                acceptance.get("passed") is True
                and acceptance.get("status") == "passed"
                and row.get("actual_final_status") == "completed"
                and not row.get("failures")
                and all(status == "passed" for status in checks.values())
            )
            rows.append(
                {
                    "run_id": row["run_id"],
                    "level": row["level"],
                    "evidence": actual,
                    "acceptance": acceptance,
                    "strictly_accepted": eligible,
                    "actual_final_status": row.get("actual_final_status"),
                    "failures": row.get("failures"),
                    "model_calls": row.get("model_calls"),
                    "tool_calls": row.get("tool_calls"),
                    "token_usage": row.get("token_usage"),
                    "cost": row.get("cost"),
                    "restart": row.get("restart"),
                    "compaction": row.get("compaction"),
                    "context_archives": row.get("context_archives"),
                    "review_results": row.get("review_results"),
                    "conversation_count": row.get("conversation_count"),
                    "semantic_test_runs": row.get("semantic_test_runs"),
                }
            )
    levels = {row["level"] for row in rows if row["strictly_accepted"]}
    return {
        "index": evidence(path),
        "published_run_count": index["run_count"],
        "snapshot_hash_mismatches": errors,
        "current_source_runs": rows,
        "accepted_current_source_levels": sorted(levels),
        "all_three_levels_accepted": {"L1", "L2", "L3"}.issubset(levels),
        "passed": not errors and index["run_count"] == len(index["runs"]),
    }


def live_usage(path, frozen, live):
    report = read(path)
    errors, known, unknown = [], 0, 0
    snapshot_runs = {row["run_id"]: row for row in live["current_source_runs"]}
    if report.get("source_version") != VERSION:
        errors.append("usage_source_version")
    if report.get("frozen_manifest_sha256") != frozen["manifest_sha256"]:
        errors.append("usage_frozen_manifest")
    if report.get("current_frozen_source_mismatches"):
        errors.append("usage_current_frozen_source")
    if report.get("frozen_manifest_file_sha256") != frozen["manifest"]["sha256"]:
        errors.append("usage_frozen_manifest_file")
    for row in report["runs"]:
        run_id = row["run_id"]
        raw_path = REPO / row["driver_record_path"]
        if sha(raw_path.read_bytes()) != row["driver_record_sha256"]:
            errors.append("usage_driver_record_hash:" + run_id)
        if (
            row.get("driver_code_snapshot_sha256")
            != frozen["executable_snapshot_sha256"]
        ):
            errors.append("usage_executable_snapshot:" + run_id)
        if row.get("read_snapshot_total_changes") != 0:
            errors.append("usage_readonly_snapshot:" + run_id)
        if not row.get("accounting_checks_passed") or not row.get(
            "driver_usage_matches_database"
        ):
            errors.append("usage_accounting:" + run_id)
        if not row.get("gateway_tokens_within_original_scenario_limit"):
            errors.append("usage_original_scenario_limit:" + run_id)
        if not row.get("held_allocation_audit", {}).get("passed"):
            errors.append("usage_held_allocation:" + run_id)
        if not row.get("unknown_or_sent_reservations_never_refunded"):
            errors.append("usage_reservation_retention:" + run_id)
        if any(
            not item.get("receipt_hash_matches")
            for item in row.get("artifact_hash_checks", [])
        ):
            errors.append("usage_artifact_hash:" + run_id)
        if any(
            not item.get("original_live_evidence_valid")
            for item in row.get("succeeded_node_live_evidence_revalidation", [])
        ):
            errors.append("usage_succeeded_node_provenance:" + run_id)
        if run_id not in snapshot_runs:
            errors.append("usage_missing_current_published_run:" + run_id)
        else:
            snapshot = snapshot_runs[run_id]
            if row["acceptance_passed"] != snapshot["acceptance"].get("passed"):
                errors.append("usage_published_acceptance:" + run_id)
            snapshot_usage = snapshot.get("token_usage") or {}
            if (
                sum(snapshot_usage.get(key, 0) or 0 for key in ("input", "output"))
                != row["known_gateway_tokens"]
            ):
                errors.append("usage_published_known_tokens:" + run_id)
        known += row["known_gateway_tokens"]
        unknown += row["missing_gateway_usage_count"]
    if {row["run_id"] for row in report["runs"]} != set(snapshot_runs):
        errors.append("usage_current_run_inventory")
    return {
        "evidence": evidence(path),
        "runs": len(report["runs"]),
        "known_tokens": known,
        "unknown_attempts": unknown,
        "consistency_errors": errors,
        "passed": not errors,
        "limitations": "Checks the independent read-only audit artifact against original finished records and published snapshots; this auditor opens no runtime database.",
    }


def main():
    global VERSION
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v17")
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--study", type=Path)
    parser.add_argument(
        "--study-source-manifest", type=Path, default=AUDIT / "source-manifest-v16.json"
    )
    parser.add_argument("--live-index", type=Path)
    parser.add_argument("--usage-audit", type=Path)
    parser.add_argument("--goals", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"v[1-9][0-9]*", args.version):
        parser.error("version must be a v-prefixed positive integer")
    VERSION = args.version
    frozen = source_freeze()
    if args.source_only:
        print(json.dumps(frozen, ensure_ascii=False, indent=2))
        return
    if not all((args.study, args.live_index, args.goals, args.usage_audit)):
        parser.error(
            "Final audit requires study, live-index, usage-audit and independently reviewed goals JSON"
        )
    tests = full_tests()
    static = static_checks(frozen)
    preservation = original_preservation()
    study = live_study(args.study, args.study_source_manifest)
    live = live_snapshots(args.live_index, frozen)
    usage = live_usage(args.usage_audit, frozen, live)
    goals = read(args.goals)
    goal_inventory_valid = (
        isinstance(goals, list)
        and len(goals) == 15
        and {goal.get("number") for goal in goals} == set(range(1, 16))
        and all(
            isinstance(goal.get("goal"), str) and goal["goal"].strip() for goal in goals
        )
        and all(
            isinstance(goal.get("evidence_boundary"), str)
            and goal["evidence_boundary"].strip()
            for goal in goals
        )
    )
    evidence_ok = (
        frozen["passed"]
        and tests["passed"]
        and all(row["passed"] for row in static)
        and preservation["passed"]
        and study["passed"]
        and live["passed"]
        and usage["passed"]
        and goal_inventory_valid
    )
    complete = (
        evidence_ok
        and live["all_three_levels_accepted"]
        and all(goal["acceptance_status"] == "satisfied" for goal in goals)
    )
    report = {
        "schema_version": 1,
        "source_version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "phase2_accepted" if complete else "phase2_incomplete",
        "phase2_complete": complete,
        "evidence_consistency_passed": evidence_ok,
        "scope": "Independent read-only byte/hash, full-suite/static record, original-evidence, independent live-study scoring, published strict-live and 15-goal evidence audit; no test or Provider reruns.",
        "inspection": {
            "application_code_imports": 0,
            "provider_calls": 0,
            "runtime_databases_opened": 0,
            "credentials_read": False,
            "source_or_existing_evidence_modified": False,
        },
        "source_freeze": frozen,
        "full_tests": tests,
        "static_checks": static,
        "preservation": preservation,
        "live_algorithm_study": study,
        "strict_live_runs": live,
        "independent_usage_audit": usage,
        "goals_evidence": evidence(args.goals),
        "goal_inventory_valid": goal_inventory_valid,
        "final_standard_matrix": goals,
        "limitations": [
            "Record consistency and deterministic fixed-case scores do not prove correctness on arbitrary tasks.",
            "The auditor does not independently identify the upstream model or inspect hidden Provider state.",
            "Published runtime observations are checked against frozen code identity; no application database was opened by this auditor.",
        ],
    }
    output = AUDIT / f"acceptance-independent-{VERSION}.json"
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(
                report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
            )
            + "\n"
        )
    lines = [
        f"# Independent final acceptance — {VERSION}",
        "",
        "**Phase 2 accepted.**" if complete else "**Phase 2 remains incomplete.**",
        "",
        f"Frozen source: {frozen['actual_entries']} files; byte/hash inventory {'matches' if frozen['passed'] else 'does not match'}. Canonical SHA-256 `{frozen['manifest_sha256']}`.",
        "",
        f"Full backend suite: {tests['console_passed']} passed, {tests['failures']} failures, {tests['errors']} errors, {tests['skipped']} skipped; {tests['console_warnings']} warning(s), {tests['console_seconds']} seconds. All four static-check records {'pass' if all(row['passed'] for row in static) else 'do not pass'}.",
        "",
        f"The independent fixed-case scorer checked {study['rows']} live study rows. Task successes: {study['task_successes']}; model/budget failures: {study['failed_model_or_budget_rows']}; unresolved attempts: {study['unknown_attempts']}. This controlled single-model microstudy is separate from autonomous Quest acceptance.",
        "",
        "Accepted strict live levels on the frozen executable snapshot: "
        + (", ".join(live["accepted_current_source_levels"]) or "none")
        + ".",
        "",
        "| Final standard | Status | Evidence boundary |",
        "| --- | --- | --- |",
    ]
    for goal in goals:
        lines.append(
            f"| {goal['number']}. {goal['goal']} | {goal['acceptance_status']} | {goal['evidence_boundary']} |"
        )
    lines += [
        "",
        "Historical failures are retained. This auditor imported no FORGE module, reran no tests, made no Provider calls, read no credentials and opened no runtime database. It wrote only new auditor-owned evidence files.",
        "",
        f"See `acceptance-independent-{VERSION}.json` for exact artifact hashes, byte inventory, per-run strict checks, independent row-score mismatches and limitations.",
        "",
    ]
    with (AUDIT / f"acceptance-independent-{VERSION}.md").open(
        "x", encoding="utf-8", newline="\n"
    ) as stream:
        stream.write("\n".join(lines))
    print(
        json.dumps(
            {
                "output": str(output),
                "phase2_complete": complete,
                "evidence_consistency_passed": evidence_ok,
            }
        )
    )


if __name__ == "__main__":
    main()
