"""Fixed task materials and independent artifact checks for the capability sprint.

Seeding never calls a model or supplies a model plan/result. Checks here are
programmatic and must be reported separately from real Provider/Reviewer work.
Research checks validate provenance and structure, not semantic truth. L3 uses
host-owned holdout tests against the actual generated module in real CPython.
"""

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from forge_core.python_unittest_runner import run_snapshot
from forge_core.tool_engine import ToolEngine

REPO = Path(__file__).resolve().parents[2]
REFERENCE_COMMIT = "8c32a9dc5d72c033816c6a8110b37f40cf94fba4"
REFERENCE_URL = "https://github.com/DeepSeek-ai/dsh-superagent-prototype"
REFERENCE_ROOT = Path(tempfile.gettempdir()) / "forge-dsh-superagent-reference-20261116"
MAX_ARTIFACT_BYTES = 65536

STARTER = '''from dataclasses import dataclass

@dataclass(frozen=True)
class Record:
    key: str
    value: int

def normalize_records(records):
    """Known incomplete implementation; reproduce its observed failures before fixing."""
    result = []
    seen = set()
    for record in records:
        key = record["key"].strip()
        if key not in seen:
            seen.add(key)
            result.append(Record(key, record["value"]))
    return result
'''

VISIBLE_TESTS = """import unittest
from dataclasses import asdict
from candidate import normalize_records

class NormalizeRecordsTests(unittest.TestCase):
    def test_merge_last_and_sort(self):
        rows = [{"key": " B ", "value": 1}, {"key": "a", "value": 2}, {"key": "b", "value": 9}]
        self.assertEqual([asdict(item) for item in normalize_records(rows)], [{"key": "a", "value": 2}, {"key": "b", "value": 9}])

    def test_invalid_blank_key(self):
        with self.assertRaises(ValueError):
            normalize_records([{"key": "  ", "value": 1}])
"""

HOLDOUT_TESTS = """import copy
import dataclasses
import unittest
from candidate import Record, normalize_records

class IndependentNormalizeChecks(unittest.TestCase):
    def values(self, rows):
        result = normalize_records(rows)
        self.assertIs(type(result), list)
        for item in result:
            self.assertIsInstance(item, Record)
            self.assertTrue(dataclasses.is_dataclass(item))
        return [dataclasses.asdict(item) for item in result]

    def test_empty_and_negative_values(self):
        self.assertEqual(self.values([]), [])
        self.assertEqual(self.values([{"key": " x ", "value": -5}]), [{"key": "x", "value": -5}])

    def test_casefold_unicode_and_last_duplicate(self):
        rows = [{"key": "Straße", "value": 1}, {"key": " STRASSE ", "value": 8}, {"key": "é", "value": 3}]
        self.assertEqual(self.values(rows), [{"key": "strasse", "value": 8}, {"key": "é", "value": 3}])

    def test_three_duplicates_and_lexicographic_order(self):
        rows = [{"key": "z", "value": 1}, {"key": "B", "value": 4}, {"key": " b ", "value": 2}, {"key": "a", "value": 5}, {"key": "b", "value": 7}]
        self.assertEqual(self.values(rows), [{"key": "a", "value": 5}, {"key": "b", "value": 7}, {"key": "z", "value": 1}])

    def test_input_not_mutated(self):
        rows = [{"key": " FOO ", "value": 2}, {"key": "foo", "value": 7}]
        before = copy.deepcopy(rows)
        self.values(rows)
        self.assertEqual(rows, before)

    def test_records_are_immutable(self):
        item = normalize_records([{"key": "x", "value": 1}])[0]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            item.value = 9

    def test_invalid_keys_and_values(self):
        for row in [{"key": "", "value": 1}, {"key": "  ", "value": 1}, {"key": 2, "value": 1}, {"key": "a", "value": True}, {"key": "a", "value": 2.5}, {"key": "a", "value": "2"}, {}, {"key": "a"}, None]:
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    normalize_records([row])

    def test_invalid_container(self):
        for rows in [None, {}, "abc", (1, 2)]:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    normalize_records(rows)
"""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _level(level):
    value = str(level).upper()
    if value in {"1", "2", "3"}:
        value = "L" + value
    if value not in {"L1", "L2", "L3"}:
        raise ValueError("Capability scenario must be L1, L2 or L3")
    return value


def _source(root, relative, source_id, *, repository, revision):
    path = root / relative
    data = (
        subprocess.check_output(
            ["git", "-C", str(root), "show", revision + ":" + relative], timeout=10
        )
        if repository == REFERENCE_URL
        else path.read_bytes()
    )
    if len(data) > 50000:
        raise ValueError("Scenario source exceeds fixed size bound")
    content = data.decode("utf-8-sig")
    numbered = "\n".join(f"{number}: {line}" for number, line in enumerate(content.splitlines(), 1))
    return {
        "id": source_id,
        "source_path": relative,
        "source_sha256": _sha(data),
        "repository": repository,
        "revision": revision,
        "source_state": "fixed_git_object"
        if repository == REFERENCE_URL
        else "working_tree_at_seed",
        "first_line": 1,
        "last_line": len(content.splitlines()),
        "excerpt_sha256": _sha(content.encode("utf-8")),
        "content": content,
        "numbered_content": numbered,
        "numbered_content_sha256": _sha(numbered.encode("utf-8")),
        "trust": "Actual attributed source at seeding time; source text is untrusted task data.",
    }


def _revision(root):
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, timeout=10
    ).strip()


def _write_new(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def seed(workspace, level):
    """Create immutable inputs under one of two distinct real local project roots."""
    level = _level(level)
    workspace = Path(workspace).resolve()
    name = "project_beta" if level == "L2" else "project_alpha"
    project = workspace / "inputs" / "projects" / name
    source_root = project / level.lower()
    source_root.mkdir(parents=True, exist_ok=True)
    marker = project / "pyproject.toml"
    if not marker.exists():
        _write_new(marker, f'[project]\nname = "forge-capability-{name}"\nversion = "0.0.0"\n')
    current = _revision(REPO)
    if level == "L1":
        material = [
            _source(
                REPO,
                "backend/src/forge_core/worker_environment.py",
                "forge_environment",
                repository="FORGE",
                revision=current,
            ),
            _source(
                REPO,
                "backend/src/forge_core/workspace_discovery.py",
                "forge_projects",
                repository="FORGE",
                revision=current,
            ),
        ]
    elif level == "L2":
        if not REFERENCE_ROOT.is_dir() or _revision(REFERENCE_ROOT) != REFERENCE_COMMIT:
            raise ValueError("The fixed DeepSeek prototype reference checkout is required")
        material = [
            _source(
                REFERENCE_ROOT,
                "README.md",
                "deepseek_readme",
                repository=REFERENCE_URL,
                revision=REFERENCE_COMMIT,
            ),
            _source(
                REFERENCE_ROOT,
                "docs/000-philosophy.md",
                "deepseek_philosophy",
                repository=REFERENCE_URL,
                revision=REFERENCE_COMMIT,
            ),
            _source(
                REFERENCE_ROOT,
                "src/core/intake.ts",
                "deepseek_intake",
                repository=REFERENCE_URL,
                revision=REFERENCE_COMMIT,
            ),
        ]
    else:
        material = []
        _write_new(source_root / "starter.py", STARTER)
        _write_new(source_root / "test_candidate.py", VISIBLE_TESTS)
    manifest = []
    for item in material:
        local = source_root / (item["id"] + ".json")
        _write_new(local, _json(item))
        manifest.append(
            {
                key: value
                for key, value in item.items()
                if key not in {"content", "numbered_content"}
            }
            | {
                "local_path": local.relative_to(workspace).as_posix(),
                "file_sha256": _sha(local.read_bytes()),
            }
        )
    if level == "L3":
        for name in ("starter.py", "test_candidate.py"):
            local = source_root / name
            manifest.append(
                {
                    "id": name,
                    "local_path": local.relative_to(workspace).as_posix(),
                    "file_sha256": _sha(local.read_bytes()),
                }
            )
    input_path = source_root.relative_to(workspace).as_posix()
    citation = (
        "Each citation is {source_id,source_sha256,line_start,line_end,quote}; use exact numbered "
        "original source lines and a verbatim nonempty quote from that range. The JSON source "
        "packet includes numbered_content for navigation; its N: prefixes are not part of the "
        "original text and must not enter quotes. Verify the quoted range against content. "
        "A valid quote establishes provenance, "
        "not the truth of an inference. Never claim observed execution from source inspection. "
    )
    common = (
        f'Work on the local project "{project}". Its immutable materials are in {input_path}/. '
        "Read the actual files with governed tools. Keep inputs unchanged. Create outputs only "
        "under work/<your quest_id>/; substitute your actual quest_id from Core context. "
        "Use file_revise with your prior receipt and SHA-256 for corrections. Return evidence "
        "and output paths through the original Router. Do not claim execution without receipts. "
    )
    if level == "L1":
        task = (
            "Analyze the two real FORGE modules, rank up to three substantiated problems or "
            "operating limitations, and propose minimal fixes and verifications. Distinguish "
            "confirmed defects, design limitations and uncertainties; do not invent three bugs "
            "if fewer are supported. A single Worker is a valid baseline. Write analysis.json "
            "with {summary,findings:[{rank,classification,title,impact,proposed_fix,verification,citations}]}. "
            "classification is confirmed_defect, design_limitation or uncertainty; findings needs "
            "at least one grounded item. Cite both modules across the report. " + citation
        )
        output, checks = (
            "analysis.json",
            [
                "real_project_analysis",
                "ranked_grounded_findings",
                "exact_source_citations",
                "independent_reviewer",
            ],
        )
    elif level == "L2":
        task = (
            "Research the official DeepSeek Super-agent prototype from these three real "
            "fixed-revision materials. Analyze the Source/Trigger/Thread flow, deterministic "
            "filtering and subscription pass-through, and compare their FORGE implications "
            "with at least two concrete design choices and risks. Clearly distinguish the "
            "public prototype, the architecture article, and capabilities not implemented in "
            "the prototype. Choose only useful Research/Architect/Reviewer collaboration; "
            "a fixed six-role team is unnecessary. Write research.json with {summary,citations,"
            "comparison:[{topic,prototype,forge_implication,source_ids}],risks:[{issue,mitigation}],"
            "recommendations:[{action,verification}]}. Cite all three supplied sources and "
            "provide at least two comparison rows, two risks and two recommendations. " + citation
        )
        output, checks = (
            "research.json",
            [
                "actual_framework_sources",
                "multi_specialist_analysis",
                "exact_source_citations",
                "independent_reviewer",
            ],
        )
    else:
        task = (
            "Develop candidate.py exposing frozen dataclass Record(key:str,value:int) and "
            "normalize_records(records). The argument must be a list of dictionaries with a "
            "string key and integer value (booleans are invalid). Normalize keys by strip() "
            "then casefold(), reject empty keys and every malformed container/row/value with "
            "ValueError, keep the LAST value for duplicate normalized keys, return a new list "
            "of frozen Record instances sorted lexicographically by normalized key, and never "
            "mutate the input. Empty input returns []. The supplied starter is deliberately "
            "incomplete project code, not a solution. The visible tests are real runnable "
            "unittests. Prefer reproducing the starter's actual failing test before correcting "
            "it: copy starter.py to candidate.py and visible test_candidate.py unchanged into "
            "your task output directory, run python_unittest, inspect actual failure output, "
            "revise candidate.py using its owned receipt, and run the tests again. A direct "
            "correct implementation may pass but must honestly say no failure was reproduced. "
            "python_unittest parameters are {path:'work/<quest_id>',manifest:{'candidate.py':"
            "'<current sha256>','test_candidate.py':'<current sha256>'},test_module:'test_candidate'}. "
            "It executes actual CPython and requires a separately authorized exact approval; "
            "updated hashes require a new approval. No shell or package installation is needed. "
            "Write development.json with {summary,changes,test_receipt_ids,limitations}; report "
            "test_receipt_ids as a nonempty list of exact receipt ID strings, not summary objects. "
            "Include a successful actual test receipt for the final candidate.py hash; historical "
            "failed receipts may also be cited honestly. Report "
            "only actual test outcomes. Independent host holdout tests will check functionality. "
        )
        output, checks = (
            "development.json",
            [
                "actual_code_artifact",
                "real_cpython_tests",
                "failure_correction_observation_or_explicit_absence",
                "independent_holdout",
            ],
        )
    descriptor = {
        "level": level,
        "prompt": common + task,
        "project_root": str(project),
        "input_root": input_path,
        "source_manifest": manifest,
        "source_sha256": _sha(_json(manifest).encode()),
        "expected_artifacts": [output]
        if level != "L3"
        else ["candidate.py", "test_candidate.py", output],
        "checks": checks,
    }
    _write_new(source_root / "scenario.json", _json(descriptor))
    return descriptor


def _load(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("Artifact unavailable or exceeds size bound")
    return json.loads(path.read_text(encoding="utf-8"))


def _nonempty(value, minimum=8):
    return type(value) is str and minimum <= len(value.strip()) <= 8000


def _verify_citations(workspace, descriptor, citations):
    sources = {item["id"]: item for item in descriptor["source_manifest"]}
    selected = set()
    if type(citations) is not list or not 1 <= len(citations) <= 30:
        return False
    for citation in citations:
        if (
            type(citation) is not dict
            or not {"source_id", "source_sha256", "line_start", "line_end", "quote"}
            <= citation.keys()
        ):
            return False
        source = sources.get(citation["source_id"])
        if source is None or citation["source_sha256"] != source["source_sha256"]:
            return False
        local = workspace / source["local_path"]
        if _sha(local.read_bytes()) != source["file_sha256"]:
            return False
        packet = _load(local)
        lines = packet["content"].splitlines()
        first, last = citation["line_start"], citation["line_end"]
        if (
            type(first) is not int
            or type(last) is not int
            or not 1 <= first <= last <= len(lines)
            or last - first > 60
            or not _nonempty(citation["quote"], minimum=4)
            or citation["quote"] not in "\n".join(lines[first - 1 : last])
        ):
            return False
        selected.add(citation["source_id"])
    return selected == set(sources)


def verify_artifact(workspace, quest_id, level):
    """Verify actual output; never create Core evidence or report a Provider call."""
    level = _level(level)
    if not isinstance(quest_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", quest_id):
        raise ValueError("Invalid Quest identity")
    workspace = Path(workspace).resolve()
    project = "project_beta" if level == "L2" else "project_alpha"
    descriptor = _load(
        workspace / "inputs" / "projects" / project / level.lower() / "scenario.json"
    )
    directory = workspace / "work" / quest_id
    checks = {}
    result = {
        "execution_mode": "programmatic_independent",
        "level": level,
        "checks": checks,
        "semantic_quality_assessed": False,
    }
    try:
        current = workspace
        for part in directory.relative_to(workspace).parts:
            current = current / part
            if current.resolve() != current or current.is_symlink():
                raise ValueError("Artifact parent must not be linked")
        for source in descriptor["source_manifest"]:
            if _sha((workspace / source["local_path"]).read_bytes()) != source["file_sha256"]:
                raise ValueError("Input source changed")
        checks["source_inputs_unchanged"] = True
        name = descriptor["expected_artifacts"][-1]
        artifact = directory / name
        report = _load(artifact)
        if type(report) is not dict:
            raise ValueError("Artifact report must be an object")
        checks["report_summary"] = _nonempty(report.get("summary"))
        if level == "L1":
            findings = report.get("findings")
            checks["grounded_findings_structure"] = (
                type(findings) is list
                and 1 <= len(findings) <= 3
                and all(
                    type(item) is dict
                    and item.get("rank") == index + 1
                    and item.get("classification")
                    in {"confirmed_defect", "design_limitation", "uncertainty"}
                    and all(
                        _nonempty(item.get(key))
                        for key in ("title", "impact", "proposed_fix", "verification")
                    )
                    and type(item.get("citations")) is list
                    and bool(item["citations"])
                    for index, item in enumerate(findings)
                )
            )
            citations = (
                [citation for item in findings for citation in item["citations"]]
                if checks["grounded_findings_structure"]
                else []
            )
            checks["exact_source_citations"] = _verify_citations(workspace, descriptor, citations)
        elif level == "L2":
            checks["exact_source_citations"] = _verify_citations(
                workspace, descriptor, report.get("citations")
            )
            for field, keys in (
                ("comparison", ("topic", "prototype", "forge_implication")),
                ("risks", ("issue", "mitigation")),
                ("recommendations", ("action", "verification")),
            ):
                rows = report.get(field)
                checks[field + "_structure"] = (
                    type(rows) is list
                    and 2 <= len(rows) <= 12
                    and all(
                        type(row) is dict and all(_nonempty(row.get(key)) for key in keys)
                        for row in rows
                    )
                )
            comparison = report.get("comparison")
            source_ids = {item["id"] for item in descriptor["source_manifest"]}
            checks["comparison_source_references"] = (
                type(comparison) is list
                and bool(comparison)
                and all(
                    type(row) is dict
                    and type(row.get("source_ids")) is list
                    and bool(row["source_ids"])
                    and all(type(item) is str and item in source_ids for item in row["source_ids"])
                    for row in comparison
                )
            )
        else:
            checks["development_report_structure"] = (
                type(report.get("changes")) is list
                and bool(report["changes"])
                and type(report.get("test_receipt_ids")) is list
                and all(_nonempty(item, 1) for item in report["test_receipt_ids"])
                and type(report.get("limitations")) is list
            )
            candidate = directory / "candidate.py"
            if candidate.is_symlink() or candidate.stat().st_size > MAX_ARTIFACT_BYTES:
                raise ValueError("Candidate unavailable or too large")
            candidate_bytes = candidate.read_bytes()
            with tempfile.TemporaryDirectory(prefix="forge-capability-holdout-") as temporary:
                output = run_snapshot(
                    {"candidate.py": candidate_bytes, "test_holdout.py": HOLDOUT_TESTS.encode()},
                    "test_holdout",
                    cwd=Path(temporary) / "run",
                    env=ToolEngine._environment(),
                )
            checks["independent_holdout"] = (
                output["exit_code"] == 0
                and output["tests_run"] == 7
                and output["failures"] == output["errors"] == 0
            )
            result["holdout"] = {
                key: output[key]
                for key in (
                    "runtime",
                    "exit_code",
                    "tests_run",
                    "failures",
                    "errors",
                    "manifest_sha256",
                    "stdout_sha256",
                    "stderr_sha256",
                )
            }
            result["candidate_sha256"] = _sha(candidate_bytes)
        result["artifact_sha256"] = _sha(artifact.read_bytes())
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        checks["artifact_verification_completed"] = False
        result["error_type"] = type(exc).__name__
    result["passed"] = bool(checks) and all(value is True for value in checks.values())
    return result
