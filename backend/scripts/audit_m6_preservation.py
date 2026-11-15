"""Compare M5 test assertions and historical reports with the M6 workspace."""

import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".local/phase1/milestone6-baseline"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def functions(path):
    result = {}

    def visit(nodes, scope=""):
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                visit(node.body, scope + node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                result[scope + node.name] = [
                    ast.dump(item, include_attributes=False)
                    for item in ast.walk(node)
                    if isinstance(item, ast.Assert)
                ]

    visit(ast.parse(path.read_text(encoding="utf-8")).body)
    return result


def main():
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    report_entries = [
        entry
        for entry in manifest
        if entry["path"].startswith(
            (
                "docs/PHASE0_AUDIT",
                "docs/PHASE1_MILESTONE1_REPORT",
                "docs/PHASE1_MILESTONE2_REPORT",
                "docs/PHASE1_MILESTONE3_REPORT",
                "docs/PHASE1_MILESTONE4_REPORT",
                "docs/PHASE1_MILESTONE5_REPORT",
                "docs/audit/",
                "docs/adr/",
            )
        )
    ]
    report_changes = [
        entry["path"]
        for entry in report_entries
        if not (ROOT / entry["path"]).is_file() or digest(ROOT / entry["path"]) != entry["sha256"]
    ]
    test_entries = [entry for entry in manifest if entry["path"].startswith("backend/tests/")]
    changed_tests = [
        entry["path"]
        for entry in test_entries
        if not (ROOT / entry["path"]).is_file() or digest(ROOT / entry["path"]) != entry["sha256"]
    ]
    missing, changed_asserts, count = [], [], 0
    for entry in test_entries:
        if not entry["path"].endswith(".py") or not Path(entry["path"]).name.startswith("test_"):
            continue
        old_path, new_path = BASE / entry["path"], ROOT / entry["path"]
        old = functions(old_path)
        new = functions(new_path) if new_path.is_file() else {}
        count += len(old)
        for name, assertions in old.items():
            if name not in new:
                missing.append({"path": entry["path"], "test": name})
                continue
            absent = [assertion for assertion in assertions if assertion not in new[name]]
            if absent:
                changed_asserts.append({"path": entry["path"], "test": name, "count": len(absent)})
    print(
        json.dumps(
            {
                "baseline": str(BASE),
                "historical_report_files": len(report_entries),
                "changed_historical_reports": report_changes,
                "historical_test_files": len(test_entries),
                "changed_test_files": changed_tests,
                "historical_test_functions": count,
                "missing_test_functions": missing,
                "changed_assertion_ast": changed_asserts,
                "reviewed_assertion_changes": [
                    "test_each_migration_step changes schema constant 7 to 8 while preserving v7 row assertion",
                    "test_anthropic_multiturn_tool_conversion adds required budget_context while retaining response == ok",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    allowed = {
        ("backend/tests/test_migrations.py", "test_each_migration_step", 1),
        ("backend/tests/test_providers.py", "test_anthropic_multiturn_tool_conversion", 1),
    }
    observed = {(item["path"], item["test"], item["count"]) for item in changed_asserts}
    if report_changes or missing or observed - allowed:
        sys.exit(1)


if __name__ == "__main__":
    main()
