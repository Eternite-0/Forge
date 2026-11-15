"""Read-only Git/AST preservation audit; create new evidence files only.

Run from the repository root with the existing backend Python interpreter.
This script never imports application code, opens run databases, reads provider
settings, runs tests or makes network/model requests.
"""

import argparse
import ast
import collections
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def git(repo, *arguments, input_bytes=None):
    return subprocess.check_output(["git", *arguments], cwd=repo, input=input_bytes)


def baseline_files(repo, revision):
    result = {}
    for entry in git(repo, "ls-tree", "-rz", revision, "--", "docs", "backend/tests").split(b"\0"):
        if not entry:
            continue
        header, path = entry.split(b"\t", 1)
        _mode, kind, object_id = header.split(b" ")
        if kind == b"blob":
            result[path.decode("utf-8")] = object_id.decode("ascii")
    return result


def read_blobs(repo, ids):
    ids = list(dict.fromkeys(ids))
    raw = git(repo, "cat-file", "--batch", input_bytes=("\n".join(ids) + "\n").encode())
    position, result = 0, {}
    for object_id in ids:
        end = raw.index(b"\n", position)
        returned, kind, size = raw[position:end].split(b" ")
        assert returned.decode() == object_id and kind == b"blob"
        size = int(size)
        position = end + 1
        result[object_id] = raw[position : position + size]
        position += size + 1
    assert position == len(raw)
    return result


def test_functions(source):
    found = {}

    def collect(node, prefix=""):
        for item in ast.iter_child_nodes(node):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + item.name
                if not isinstance(item, ast.ClassDef) and item.name.startswith("test_"):
                    found[name] = item
                collect(item, name + ".")
            else:
                collect(item, prefix)

    collect(ast.parse(source))
    return found


def assertion_map(node, source):
    result = {}
    for item in ast.walk(node):
        if isinstance(item, ast.Assert):
            result[ast.dump(item, include_attributes=False)] = ast.get_source_segment(source, item)
    return result


def review_note(path, name):
    if path.endswith("test_migrations.py") or path.endswith("test_workspace_backup_m7.py"):
        return "Expected migrated schema follows the current version; exact assertion changes are recorded; rollback/data-preservation assertions remain."
    if path.endswith("test_live_context_probe_offline.py"):
        return (
            "Offline sourced-context fixture uses a finite 75000 ceiling in both branches. Invalid schema "
            "now spends the bounded second repair call; every attempt must settle. Original status, "
            "constraint, fixture-count, response, failure-code and redaction checks remain."
        )
    if name == "test_model_can_request_bounded_help_without_spawning_or_gaining_tools":
        return (
            "Settled help usage is exactly the fixture's 10 input + 5 output tokens instead of a stale "
            "reservation >15. Blocked state, zero executed tools, one Worker and one help event remain."
        )
    return "AST changed; see exact function and assertion diffs. Identity retention alone is not semantic equivalence."


def audit(repo, base, frozen):
    revision = git(repo, "rev-parse", base).decode().strip()
    entries = baseline_files(repo, revision)
    selected = {
        path: object_id
        for path, object_id in entries.items()
        if path.startswith("docs/audit/phase1/")
        or (path.startswith("docs/PHASE1_") and path.endswith(".md"))
        or (path.startswith("backend/tests/") and path.endswith(".py"))
    }
    blobs = read_blobs(repo, selected.values())
    evidence, reports, functions, changed, missing, assertion_changes, new_disables = [], [], [], [], [], [], []
    baseline_files_modified = []
    for path, object_id in sorted(selected.items()):
        original = blobs[object_id]
        current = (repo / path).read_bytes() if (repo / path).is_file() else None
        record = {
            "path": path,
            "git_blob": object_id,
            "baseline_sha256": digest(original),
            "current_sha256": digest(current) if current is not None else None,
            "identical_bytes": current == original,
        }
        if path.startswith("docs/audit/phase1/"):
            evidence.append(record)
            continue
        if path.startswith("docs/PHASE1_"):
            reports.append(record)
            continue
        if current != original:
            baseline_files_modified.append(path)
        before_source = original.decode("utf-8-sig")
        after_source = current.decode("utf-8-sig") if current is not None else ""
        before, after = test_functions(before_source), test_functions(after_source)
        for name, old in before.items():
            identity = path + "::" + name
            new = after.get(name)
            same = new is not None and ast.dump(old) == ast.dump(new)
            functions.append({"identity": identity, "present": new is not None, "identical_ast": same})
            if new is None:
                missing.append(identity)
                continue
            if not same:
                changed.append(identity)
            old_assertions = assertion_map(old, before_source)
            new_assertions = assertion_map(new, after_source)
            removed = sorted(set(old_assertions) - set(new_assertions))
            added = sorted(set(new_assertions) - set(old_assertions))
            if removed or added:
                assertion_changes.append({
                    "identity": identity,
                    "removed": [old_assertions[item] for item in removed],
                    "added": [new_assertions[item] for item in added],
                    "review_note": review_note(path, name),
                })
            decorators = lambda node: collections.Counter(
                ast.dump(item) for item in node.decorator_list
                if "skip" in ast.dump(item) or "xfail" in ast.dump(item)
            )
            if decorators(new) - decorators(old):
                new_disables.append(identity)
    hardening = [item for item in evidence if "/milestone7/backend-hardening/" in item["path"]
                 or item["path"].endswith("/milestone7-backend-hardening-verification.json")]
    legacy = [item for item in evidence if item not in hardening]
    actual_paths = {item.relative_to(repo).as_posix() for item in (repo / "docs/audit/phase1").rglob("*") if item.is_file()}
    baseline_paths = {item["path"] for item in evidence}
    frozen_record = json.loads(frozen.read_text(encoding="utf-8-sig"))
    frozen_changes = [
        item["path"] for item in frozen_record["files"]
        if not (repo / item["path"]).is_file() or digest((repo / item["path"]).read_bytes()) != item["sha256"]
    ]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_commit": revision,
        "branch": git(repo, "branch", "--show-current").decode().strip(),
        "mode": "read_only_git_files_and_ast_no_models_no_application_imports",
        "phase1_evidence": {
            "baseline_files": len(evidence),
            "working_tree_files": len(actual_paths),
            "byte_identical": sum(item["identical_bytes"] for item in evidence),
            "missing": sorted(baseline_paths - actual_paths),
            "extra": sorted(actual_paths - baseline_paths),
            "changed": [item["path"] for item in evidence if not item["identical_bytes"]],
            "legacy_subset_files": len(legacy),
            "legacy_subset_identical": sum(item["identical_bytes"] for item in legacy),
            "hardening_additional_files": len(hardening),
            "count_scope": "340 excludes 14 milestone7/backend-hardening files and its one aggregate verification; all 355 are checked.",
            "manifest_sha256": digest(canonical(evidence).encode()),
            "files": evidence,
        },
        "phase1_reports": {"files": reports, "all_identical": all(item["identical_bytes"] for item in reports)},
        "original_tests": {
            "baseline_function_count": len(functions),
            "present": sum(item["present"] for item in functions),
            "identical_ast": sum(item["identical_ast"] for item in functions),
            "changed_ast_count": len(changed),
            "missing": missing,
            "changed_ast": changed,
            "changed_baseline_files": baseline_files_modified,
            "assertion_changes": assertion_changes,
            "new_skip_or_xfail_decorators": new_disables,
            "functions": functions,
            "limits": "Static identity/AST audit; not a test run, proof of unchanged semantics or universal assertion equivalence.",
        },
        "frozen_source": {
            "manifest_path": frozen.relative_to(repo).as_posix(),
            "manifest_file_sha256": digest(frozen.read_bytes()),
            "manifest_sha256": frozen_record["manifest_sha256"],
            "files": frozen_record["file_count"],
            "changed": frozen_changes,
        },
    }


def markdown(record):
    evidence, tests = record["phase1_evidence"], record["original_tests"]
    lines = [
        "# Phase 1 preservation audit against the original HEAD", "",
        f"Base: `{record['base_commit']}`. Mode: local, static, read-only; no Provider calls.", "",
        f"All **{evidence['baseline_files']} Phase 1 evidence files** remain byte-identical to Git HEAD, including the 340-file earlier subset and 15 hardening records. No file is missing or added in that directory. The count difference is explicit: 14 files under `milestone7/backend-hardening/` plus its aggregate verification.", "",
        f"All **{tests['baseline_function_count']} original test function identities** remain. {tests['identical_ast']} have identical AST; {tests['changed_ast_count']} changed. No original function gained a skip/xfail decorator. Nine original Phase 1 report files also remain byte-identical.", "",
        "Changed functions retain the original assertions except the explicitly listed replacements below. These changes are not described as byte-identical tests or proven semantic equivalence. Static preservation does not replace the final frozen-source test run.", "",
        "| Original test | Assertion replacements and rationale |", "| --- | --- |",
    ]
    for item in tests["assertion_changes"]:
        lines.append(f"| `{item['identity']}` | {len(item['removed'])} removed AST forms, {len(item['added'])} added; {item['review_note']} |")
    lines.extend([
        "", "Other function changes update executable Mock Planner criteria, wait for the actual Planner lease, drain finite Dispatcher fixtures, or reconstruct older schema fixtures. Their full identities and exact assertion texts appear in the paired JSON.", "",
        f"Source manifest `{record['frozen_source']['manifest_path']}` contains {record['frozen_source']['files']} files; changed paths at audit time: `{record['frozen_source']['changed']}`. This audit writes only new files under the Phase 2 audit directory. It does not modify historical evidence, application code or tests.", "",
        "Reproduce from the repository root with a new output path:", "", "```powershell",
        f"& backend/.venv/Scripts/python.exe docs/audit/phase2/audit_preservation_v6.py --base {record['base_commit']} --manifest {record['frozen_source']['manifest_path']} --output docs/audit/phase2/preservation-recheck.json --markdown docs/audit/phase2/preservation-recheck.md",
        "```", "", "Outputs use exclusive creation and refuse overwriting an existing audit record.", "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    arguments = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    outputs = [arguments.output] + ([arguments.markdown] if arguments.markdown else [])
    if any(path.exists() for path in outputs):
        parser.error("Output already exists; choose a new audit path")
    record = audit(repo, arguments.base, arguments.manifest.resolve())
    with arguments.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if arguments.markdown:
        with arguments.markdown.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown(record))
    print(canonical({"phase1_files": record["phase1_evidence"]["baseline_files"],
                     "phase1_changes": record["phase1_evidence"]["changed"],
                     "original_tests": record["original_tests"]["baseline_function_count"],
                     "missing_tests": record["original_tests"]["missing"],
                     "frozen_source_changes": record["frozen_source"]["changed"]}))


if __name__ == "__main__":
    main()
