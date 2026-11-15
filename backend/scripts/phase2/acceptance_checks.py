"""Read-only Phase 2 acceptance checks against durable execution evidence.

This evaluator makes no Provider calls, runs no candidate code, and never opens
Store (which can migrate/create a database). A missing observation is unknown;
contradictory durable evidence is failed. Neither state is an acceptance pass.
Content judgments rely on the separately validated, source-bound live review.
"""

import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from forge_core.context_service import ContextService
from forge_core.domain import DomainError
from forge_core.evidence import _verify_worker_context_call
from forge_core.governance import parameter_digest
from forge_core.independent_review import review_pass_matches
from forge_core.semantic_evidence import SemanticVerifier
from forge_core.settings import Workspace
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import verify_file_revision_chain
from forge_core.worker_schema import DELIVERY_FIELDS


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class _ReadOnlyStore:
    def __init__(self, root):
        self.path = root / "data" / "forge.sqlite3"
        self.workspace = Workspace.from_path(root)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            yield connection
        finally:
            connection.close()


def _path(root, value):
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("path escapes workspace")
    return path


def _read(path, limit=2_000_000):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("acceptance input exceeds bounded read")
    return data


def _outcome(checks, name, passed, detail, evidence=None):
    checks[name] = {
        "status": "unknown" if passed is None else "passed" if passed else "failed",
        "detail": detail,
        "evidence": evidence or [],
    }


def _finish(scenario, checks):
    for expected in scenario.get("expected_checks", []):
        if expected not in checks:
            _outcome(checks, expected, None, "Required observation was not available.")
    failed = [key for key, value in checks.items() if value["status"] == "failed"]
    unknown = [key for key, value in checks.items() if value["status"] == "unknown"]
    return {
        "status": "failed" if failed else "unknown" if unknown else "passed",
        "passed": not failed and not unknown,
        "scenario_id": scenario.get("scenario_id"),
        "checks": checks,
        "failed_checks": failed,
        "unknown_checks": unknown,
        "trust": "Durable local execution evidence; model review is a bounded content judgment, not a correctness proof.",
    }


def _receipt(connection, call, run):
    """Recheck the immutable intent/receipt and the Worker call projection."""
    row = connection.execute(
        "SELECT r.*,r.rowid AS receipt_sequence,i.call_id,i.quest_id,i.worker_id,i.run_id,i.plan_id,i.plan_revision,"
        "i.tool_name,i.parameters_json,i.parameter_digest,i.resource_scope,"
        "i.execution_mode AS intent_mode,i.state AS intent_state "
        "FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE r.id=?",
        (call["receipt_id"],),
    ).fetchone()
    if row is None:
        return None
    params = json.loads(row["parameters_json"])
    output = json.loads(row["result_json"])
    projected = json.loads(call["result_json"])
    valid = (
        call["state"] == row["intent_state"] == "succeeded"
        and row["execution_mode"] == row["intent_mode"] == run["execution_mode"] == "live"
        and (row["call_id"], row["tool_name"]) == (call["call_id"], call["tool_name"])
        and (row["quest_id"], row["worker_id"], row["run_id"], row["plan_id"], row["plan_revision"])
        == (run["quest_id"], run["worker_id"], run["id"], run["plan_id"], run["plan_revision"])
        and params == json.loads(call["arguments_json"])
        and parameter_digest(params) == row["parameter_digest"]
        and _sha(row["result_json"].encode()) == row["result_digest"]
        and projected.get("receipt_id") == row["id"]
        and projected.get("state") == "succeeded"
        and projected.get("output") == output
    )
    return {**dict(row), "parameters": params, "output": output} if valid else None


_CORE_READ_TOOLS = frozenset(
    {
        "worker_read_context_source",
        "worker_read_action_receipt",
        "worker_read_completed_delivery",
    }
)


def _core_context_read(store, connection, call, run):
    """Validate native Core source provenance without manufacturing an action receipt."""
    if (
        call["tool_name"] not in _CORE_READ_TOOLS
        or run["execution_mode"] != "live"
        or not _verify_worker_context_call(store, connection, run["id"], call)
    ):
        return None
    output = json.loads(call["result_json"])
    if (
        call["tool_name"] in {"worker_read_action_receipt", "worker_read_completed_delivery"}
        and output.get("execution_mode") != "live"
    ):
        return None
    return {
        "run_id": run["id"],
        "call_id": call["call_id"],
        "tool_name": call["tool_name"],
        "result_sha256": _sha(call["result_json"].encode()),
        "provenance": "Core revalidated original source and bound Worker tool exchange",
    }


def _prefetched_sources(store, connection, run):
    """Revalidate explicit Core reads; never synthesize a model tool exchange."""
    from forge_core.worker_prefetch import verified_sources

    entries = verified_sources(store, connection, run, historical=True)
    receipts, core_reads = [], []
    for entry in entries:
        if run["execution_mode"] != "live":
            raise DomainError("Prefetched acceptance source is not live")
        if entry["tool_name"] == "file_read":
            row = connection.execute(
                "SELECT r.*,r.rowid AS receipt_sequence,i.call_id,i.quest_id,i.worker_id,"
                "i.run_id,i.plan_id,i.plan_revision,i.tool_name,i.parameters_json,"
                "i.parameter_digest,i.resource_scope,i.execution_mode AS intent_mode,"
                "i.state AS intent_state FROM action_receipts r JOIN action_intents i "
                "ON i.id=r.intent_id WHERE r.id=?",
                (entry["receipt_id"],),
            ).fetchone()
            if row is None or row["execution_mode"] != "live":
                raise DomainError("Prefetched acceptance receipt is unavailable")
            receipts.append(
                {
                    **dict(row),
                    "parameters": json.loads(row["parameters_json"]),
                    "output": entry["output"],
                    "source_origin": "core_prefetch",
                }
            )
        elif entry["tool_name"] == "worker_read_completed_delivery":
            core_reads.append(
                {
                    "run_id": run["id"],
                    "call_id": entry["call_id"],
                    "tool_name": entry["tool_name"],
                    "result_sha256": entry["result_sha256"],
                    "source_origin": "core_prefetch",
                    "provenance": "Core admission, complete user source block and original successful producer revalidated",
                }
            )
        else:
            raise DomainError("Unknown prefetched acceptance source")
    return receipts, core_reads


def _inputs(scenario, root, checks, receipts):
    manifest = scenario.get("input_manifest")
    if manifest is None:
        _outcome(checks, "input_integrity", None, "Seeded input manifest was not supplied.")
        return []
    documents, errors, source_reads = [], [], set()
    for item in manifest:
        try:
            path = _path(root, item["path"])
            data = _read(path)
            document = json.loads(data)
            if (
                _sha(data) != item["sha256"]
                or len(data) != item["bytes"]
                or document["source_path"] != item["source_path"]
                or document["source_sha256"] != item["source_sha256"]
            ):
                raise ValueError("input manifest mismatch")
            documents.append(document)
            for receipt in receipts:
                if (
                    receipt["tool_name"] == "file_read"
                    and Path(receipt["resource_scope"]).resolve() == path
                    and receipt["output"].get("sha256") == item["sha256"]
                ):
                    source_reads.add(item["path"])
        except (OSError, ValueError, KeyError, TypeError):
            errors.append(item.get("path", "invalid manifest entry"))
    _outcome(
        checks,
        "input_integrity",
        bool(manifest) and not errors,
        "Seeded copies match their attributed manifest.",
        errors,
    )
    _outcome(
        checks,
        "input_sources_read",
        bool(manifest) and len(source_reads) == len(manifest),
        "Each source copy needs a successful live file_read receipt with the seeded hash.",
        sorted(source_reads),
    )
    return documents


def _citations(documents, values):
    """Validate citation identity/range; this does not prove the prose is true."""
    units = []

    def visit(value):
        if isinstance(value, str):
            units.append(value)
        elif isinstance(value, dict):
            units.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(values)
    matched = []
    for document in documents:
        source, digest = document["source_path"], document["source_sha256"]
        ranges = [
            (fragment["first_line"], fragment["last_line"])
            for fragment in document.get("fragments", [])
        ]
        if not ranges:
            ranges = [(document["first_line"], document["last_line"])]
        for unit in units:
            citations = []
            if isinstance(unit, dict):
                direct_strings = [value for value in unit.values() if isinstance(value, str)]
                # Identity and location must share a citation object. Flattening
                # the whole report could borrow another source's valid line.
                if not any(source in value for value in direct_strings) or not any(
                    digest in value for value in direct_strings
                ):
                    continue
                for key in ("first_line", "line_start", "start_line", "line", "line_number"):
                    start = unit.get(key)
                    end = next(
                        (
                            unit[name]
                            for name in ("last_line", "line_end", "end_line")
                            if name in unit
                        ),
                        start,
                    )
                    if type(start) is int and type(end) is int:
                        citations.append((start, end))
                for key in ("lines", "line_range", "line_numbers", "source_lines"):
                    value = unit.get(key)
                    if (
                        isinstance(value, list)
                        and value
                        and all(type(item) is int for item in value)
                    ):
                        citations.append((min(value), max(value)))
                    elif isinstance(value, dict):
                        first = next(
                            (
                                value[name]
                                for name in ("start", "first", "first_line", "start_line")
                                if name in value
                            ),
                            None,
                        )
                        last = next(
                            (
                                value[name]
                                for name in ("end", "last", "last_line", "end_line")
                                if name in value
                            ),
                            first,
                        )
                        if type(first) is int and type(last) is int:
                            citations.append((first, last))
                    elif isinstance(value, str):
                        for start, end in re.findall(
                            r"(?i)(?:\bL|\blines?\s*)?(\d+)\s*(?:[-–:]\s*L?(\d+))?", value
                        ):
                            citations.append((int(start), int(end or start)))
            else:
                if source not in unit or digest not in unit:
                    continue
                for start, end in re.findall(
                    re.escape(source)
                    + r"(?i:\s*[:#]\s*|\s+)(?:lines?\s*|L)?(\d+)(?:\s*[-–]\s*L?(\d+))?",
                    unit,
                ):
                    citations.append((int(start), int(end or start)))
                mentioned = [doc["source_path"] for doc in documents if doc["source_path"] in unit]
                if mentioned == [source]:
                    for start, end in re.findall(
                        r"(?i)(?:\bL|\blines?\s*)(\d+)(?:\s*[-–]\s*L?(\d+))?", unit
                    ):
                        citations.append((int(start), int(end or start)))
            if any(
                first <= start <= end <= last for start, end in citations for first, last in ranges
            ):
                matched.append(source)
                break
    return matched


def _specialists(members, nodes, required):
    """Assign each requested specialization to a different approved Worker node."""
    patterns = {
        "research": r"research|investigat|survey|研究|调研",
        "architecture": r"architect|interface|架构|接口",
        "writing": r"writ|report|synthes|文档|报告",
        "mathematics": r"math|formal|deriv|数学|推导",
        "coding": r"cod|implement|develop|编程|实现",
        "experiment": r"experiment|benchmark|boundary|test|实验|测试",
        "review": r"review|verif|critic|审查|审阅|复核|校验",
    }
    canonical = {
        "research": "research",
        "math": "mathematics",
        "architect": "architecture",
        "coding": "coding",
        "experiment": "experiment",
        "reviewer": "review",
        "review": "review",
    }
    candidates = {name: [] for name in required}
    for member in members:
        key, role = member.get("task_node_key"), member.get("role_id", "")
        objective = next((node["objective"] for node in nodes if node["node_key"] == key), "")
        text = _json([role, member.get("responsibility"), objective]).lower()
        for name in required:
            if (
                canonical.get(role) == name
                or (name == "writing" or role not in canonical)
                and re.search(patterns[name], text)
            ):
                candidates[name].append({"node_key": key, "role_id": role})

    def assign(index, used, result):
        if index == len(required):
            return result
        name = required[index]
        for member in candidates[name]:
            if member["node_key"] not in used:
                answer = assign(index + 1, used | {member["node_key"]}, {**result, name: member})
                if answer is not None:
                    return answer
        return None

    return assign(0, set(), {})


def _scenario_budgets(connection, store, quest_id, scenario, record, checks):
    """Count every workspace attempt, including coordination and reservations."""
    limits = scenario.get("limits", {})
    attempts = connection.execute(
        "SELECT * FROM model_budget_attempts WHERE workspace_id=?", (store.workspace.id,)
    ).fetchall()
    known, reserved, unknown = 0, 0, []
    for opening in connection.execute(
        "SELECT tokens FROM model_budget_opening WHERE workspace_id=?",
        (store.workspace.id,),
    ):
        if type(opening["tokens"]) is not int or opening["tokens"] < 0:
            raise ValueError("Invalid opening token usage")
        known += opening["tokens"]
    for attempt in attempts:
        if attempt["state"] == "rejected":
            continue
        incomplete = attempt["state"] != "settled"
        for charged, allocation in (
            ("input_charged", "input_reserved"),
            ("output_charged", "output_reserved"),
        ):
            value = attempt[charged]
            if type(value) is int and value >= 0:
                known += value
            else:
                reserve = attempt[allocation]
                if type(reserve) is not int or reserve < 0:
                    raise ValueError("Invalid model reservation")
                reserved += reserve
                incomplete = True
        if incomplete:
            unknown.append(attempt["attempt_id"])
    _outcome(
        checks,
        "scenario_model_usage_settled",
        None if unknown else True,
        "Every sent workspace model attempt requires settled, known nonnegative usage, including coordination, review and retries; an unresolved attempt never counts as zero or PASS.",
        [
            {
                "known_charged_tokens": known,
                "unknown_reserved_tokens": reserved,
                "unknown_attempts": unknown,
            }
        ],
    )
    cap = limits.get("max_total_tokens")
    policy = scenario.get("token_budget_policy")
    if cap is None or policy is not None:
        runtime = record.get("runtime_settings", {})
        observed = record.get("results", {}).get("budget", {})
        valid = (
            "max_total_tokens" not in limits
            and scenario.get("scenario_id")
            == f"forge-phase2-level-{scenario.get('level')}-v29-no-token-budget"
            and isinstance(policy, dict)
            and policy.get("enforced") is False
            and policy
            == {
                "mode": "disabled_by_user",
                "enforced": False,
                "protocol_change": "user_cancelled_cumulative_token_cap",
                "historical_scenario_id": f"forge-phase2-level-{scenario.get('level')}-v3",
                "usage_accounting": "all_model_attempts_must_have_known_settled_usage",
            }
            and runtime.get("token_budgets_enforced") is False
            and "model_token_limit" in runtime
            and runtime["model_token_limit"] is None
            and observed.get("token_budgets_enforced") is False
            and "token_limit" in observed
            and observed["token_limit"] is None
        )
        _outcome(
            checks,
            "scenario_token_budget_policy",
            valid,
            "The user's changed protocol, driver settings and observed Core budget API must all explicitly disable cumulative token budgets without a numeric replacement cap.",
            [{"policy": policy, "runtime_settings": runtime, "core_budget": observed}],
        )
    if cap is not None:
        status = False if known + reserved > cap else None if unknown else True
        _outcome(
            checks,
            "scenario_total_token_budget",
            status,
            "All workspace Gateway attempts include Router, Planner, Composer, Worker, Reviewer, HTTP retries and unknown reservations; rejected unsent attempts consume zero.",
            [
                {
                    "known_charged_tokens": known,
                    "unknown_reserved_tokens": reserved,
                    "conservative_total": known + reserved,
                    "scenario_limit": cap,
                    "unknown_attempts": unknown,
                }
            ],
        )
    cap = limits.get("max_duration_seconds")
    duration = record.get("duration_seconds")
    measured = type(duration) in (int, float) and math.isfinite(duration) and duration >= 0
    if cap is not None:
        _outcome(
            checks,
            "scenario_duration_budget",
            duration <= cap if measured else None,
            "Whole-driver elapsed time includes coordination, execution, independent review and restart.",
            [{"observed_seconds": duration if measured else None, "scenario_limit": cap}],
        )
    runs = [
        dict(row)
        for row in connection.execute("SELECT * FROM worker_runs WHERE quest_id=?", (quest_id,))
    ]
    for name, field, limit in (
        ("scenario_worker_step_budget", "steps", "max_worker_steps"),
        ("scenario_worker_tool_budget", "tool_spent", "max_worker_tool_calls"),
        ("scenario_worker_depth", "depth", "max_depth"),
    ):
        cap = limits.get(limit)
        if cap is not None:
            exceeded = [
                {"run_id": run["id"], "observed": run[field]}
                for run in runs
                if type(run[field]) is not int or run[field] < 0 or run[field] > cap
            ]
            if field == "tool_spent":
                for run in runs:
                    actual = connection.execute(
                        "SELECT count(*) FROM worker_tool_calls WHERE run_id=?", (run["id"],)
                    ).fetchone()[0]
                    if actual > cap:
                        exceeded.append({"run_id": run["id"], "observed_tool_requests": actual})
            _outcome(
                checks,
                name,
                not exceeded,
                "All historical Worker runs respect the scenario limit of " + str(cap) + ".",
                exceeded,
            )
    rounds = {}
    for event in connection.execute(
        "SELECT payload FROM events WHERE quest_id=? AND event_type='review.started'", (quest_id,)
    ):
        start = json.loads(event[0])
        released = connection.execute(
            "SELECT payload FROM events WHERE event_type='review.released' AND json_extract(payload,'$.call_id')=? ORDER BY seq DESC LIMIT 1",
            (start.get("call_id"),),
        ).fetchone()
        release = json.loads(released[0]) if released else {}
        late = connection.execute(
            "SELECT 1 FROM events WHERE event_type='review.late_settlement' AND json_extract(payload,'$.call_id')=?",
            (start.get("call_id"),),
        ).fetchone()
        admitted = connection.execute(
            "SELECT 1 FROM model_budget_attempts WHERE call_id=? AND purpose='independent_reviewer' AND state!='rejected'",
            (start.get("call_id"),),
        ).fetchone()
        if release.get("unsent") is True and late is None and admitted is None:
            continue
        run_id = start.get("run_id")
        rounds[run_id] = rounds.get(run_id, 0) + 1
    cap = limits.get("max_review_rounds")
    if cap is not None:
        _outcome(
            checks,
            "scenario_review_round_budget",
            all(count <= cap for count in rounds.values()),
            "Reviewer attempts accumulate across revised Worker deliveries; only proven-unsent releases are excluded.",
            [{"rounds_by_run": rounds, "scenario_limit": cap}],
        )
    cap = limits.get("max_concurrency")
    if cap is not None:
        teams = {run["team_digest"] for run in runs}
        exceeded = []
        for digest in teams:
            team = connection.execute(
                "SELECT body_json FROM team_proposals WHERE quest_id=? AND proposal_digest=?",
                (quest_id, digest),
            ).fetchone()
            approved_limit = (
                json.loads(team[0]).get("limits", {}).get("max_concurrency") if team else None
            )
            if type(approved_limit) is not int or approved_limit < 1 or approved_limit > cap:
                exceeded.append(
                    {"team_digest": digest, "approved_worker_concurrency": approved_limit}
                )
        _outcome(
            checks,
            "scenario_worker_concurrency",
            not exceeded,
            "Trusted Worker admission is bounded by each executed team's approved concurrency, at most "
            + str(cap)
            + "; Gateway coordination concurrency is separate.",
            exceeded,
        )


def _restart_anchors(connection, record, restart):
    """Compare captured quiescent restart anchors and their final durable rows."""
    before, after = restart.get("before"), restart.get("after_start")
    if before is None or after is None:
        return None, "Completed-node before/after-start snapshots were not recorded."
    quest_id = record.get("quest_id")
    if not quest_id:
        return None, "Restart snapshots need the target Quest ID."
    required = {
        "nodes",
        "receipts",
        "attempts",
        "worker_counters",
        "unsettled_model_attempts",
        "unsettled_actions",
        "running_workers",
    }
    if (
        not isinstance(before, dict)
        or not isinstance(after, dict)
        or not required <= set(before)
        or not required <= set(after)
    ):
        return False, "Restart snapshot shape is incomplete."
    quiet = all(
        type(snapshot[key]) is int and snapshot[key] == 0
        for snapshot in (before, after)
        for key in ("unsettled_model_attempts", "unsettled_actions", "running_workers")
    )
    completed = [node for node in before["nodes"] if node.get("status") == "succeeded"]
    remaining = [
        node
        for node in before["nodes"]
        if node.get("status") not in {"succeeded", "failed", "skipped"}
    ]
    retained = (
        bool(completed)
        and bool(remaining)
        and all(node in after["nodes"] for node in completed)
        and bool(before["receipts"])
        and all(row in after["receipts"] for row in before["receipts"])
        and bool(before["attempts"])
        and all(
            row.get("state") in {"settled", "rejected"} and row in after["attempts"]
            for row in before["attempts"]
        )
        and bool(before["worker_counters"])
        and all(row in after["worker_counters"] for row in before["worker_counters"])
    )
    actual_nodes = connection.execute(
        "SELECT n.plan_id,n.node_key FROM task_nodes n JOIN quest_plans p ON p.id=n.plan_id "
        "JOIN quests q ON q.id=p.quest_id AND q.plan_revision=p.revision WHERE q.id=?",
        (quest_id,),
    ).fetchall()
    retained = retained and {(node["plan_id"], node["node_key"]) for node in before["nodes"]} == {
        (node["plan_id"], node["node_key"]) for node in actual_nodes
    }
    for node in completed:
        row = connection.execute(
            "SELECT n.plan_id,n.node_key,n.status,n.worker_id FROM task_nodes n "
            "JOIN quest_plans p ON p.id=n.plan_id WHERE p.quest_id=? AND n.plan_id=? AND n.node_key=?",
            (quest_id, node["plan_id"], node["node_key"]),
        ).fetchone()
        retained = retained and row is not None and dict(row) == node
    for receipt in before["receipts"]:
        row = connection.execute(
            "SELECT r.id,r.result_digest,r.execution_mode FROM action_receipts r "
            "JOIN action_intents i ON i.id=r.intent_id WHERE i.quest_id=? AND r.id=?",
            (quest_id, receipt["id"]),
        ).fetchone()
        retained = retained and row is not None and dict(row) == receipt
    for attempt in before["attempts"]:
        row = connection.execute(
            "SELECT attempt_id,state,input_charged,output_charged FROM model_budget_attempts WHERE attempt_id=?",
            (attempt["attempt_id"],),
        ).fetchone()
        retained = retained and row is not None and dict(row) == attempt
    for counters in before["worker_counters"]:
        row = connection.execute(
            "SELECT id,token_spent,steps,tool_spent FROM worker_runs WHERE quest_id=? AND id=?",
            (quest_id, counters["id"]),
        ).fetchone()
        retained = (
            retained
            and row is not None
            and all(
                type(counters[key]) is int and counters[key] >= 0 and row[key] >= counters[key]
                for key in ("token_spent", "steps", "tool_spent")
            )
        )
    resumed = type(restart.get("resume_status")) is int and restart["resume_status"] == 200
    return (
        quiet and retained and resumed,
        "Completed nodes, exact receipts/settled attempts and Worker counters survive a quiescent restart with remaining execution and successful resume.",
    )


def _continuity(store, root, record, checks):
    restart = record.get("restart")
    if restart is None:
        _outcome(
            checks,
            "core_restart_continuity",
            None,
            "No before/after Core restart observation was recorded.",
        )
    else:
        valid = (
            restart.get("router_identity_retained") is True
            and restart.get("same_workspace") is True
        )
        with store.connection() as connection:
            identity = connection.execute(
                "SELECT router_session_id FROM router_state WHERE conversation_id='main'"
            ).fetchone()
        before = restart.get("initial_router_identity")
        after = restart.get("restored_router_identity")
        observed = before is not None and after is not None
        valid = valid and observed and identity is not None and before == after == identity[0]
        expected_scope = restart.get("after") == "completed_nodes_with_remaining_execution"
        with store.connection() as connection:
            connection.execute("BEGIN")
            anchors, anchor_detail = _restart_anchors(connection, record, restart)
        if not observed:
            status = None
        elif not valid or not expected_scope:
            status = False
        else:
            status = anchors
        _outcome(
            checks,
            "core_restart_continuity",
            status,
            "Observed restart scope: "
            + str(restart.get("after", "unspecified"))
            + ". "
            + anchor_detail,
            [restart],
        )
    compaction = record.get("compaction")
    if compaction is None:
        _outcome(
            checks,
            "context_compaction_continuity",
            None,
            "No compaction/source-readback observation was recorded.",
        )
        return
    verified = compaction.get("verified_sources", [])
    valid = (
        compaction.get("archive_created") is True
        and compaction.get("source_readback_available") is True
        and compaction.get("source_count", 0) > 0
        and len(verified) == compaction.get("source_count")
        and len({item.get("source_id") for item in verified}) == len(verified)
    )
    try:
        with store.connection() as connection:
            archive_count = connection.execute(
                "SELECT count(*) FROM context_archives WHERE scope='router' AND scope_id='main'"
            ).fetchone()[0]
            quest = connection.execute(
                "SELECT metadata FROM quests WHERE id=?", (record.get("quest_id"),)
            ).fetchone()
            metadata = json.loads(quest["metadata"]) if quest else {}
            original_id = metadata.get("original_request_message_id")
            original = connection.execute(
                "SELECT content FROM messages WHERE id=? AND role='user' AND conversation_id='main'",
                (original_id,),
            ).fetchone()
        valid = valid and archive_count > 0
        valid = (
            valid
            and original is not None
            and _sha(original["content"].encode()) == metadata.get("original_request_sha256")
            and any(item.get("source_id") == original_id for item in verified)
        )
        for item in verified:
            restored = ContextService(store).read_source("router", "main", item["source_id"])
            valid = valid and _sha(restored["content"].encode()) == item["content_sha256"]
        rebuilt = ContextService(store).rebuild_router()
        valid = valid and bool(rebuilt.get("capsule"))
    except (DomainError, OSError, KeyError, TypeError, ValueError, sqlite3.Error):
        valid = False
    _outcome(
        checks,
        "context_compaction_continuity",
        valid,
        "Archive and rebuilt Router capsule validate; exact source readbacks include this Quest's integrity-bound original user request.",
        verified,
    )


def evaluate_scenario(scenario, inspection, workspace, *, run_record=None):
    """Return passed/failed/unknown per check; only all-passed means acceptance.

    ``inspection`` is the driver's inspection snapshot; durable facts are read
    again from the database. ``run_record`` supplies the Quest ID and direct
    restart/compaction observations. It is optional for standalone inspection.
    """
    root = Path(workspace).resolve()
    record, checks = run_record or {}, {}
    store = _ReadOnlyStore(root)
    if not store.path.is_file():
        _outcome(
            checks, "database_evidence", None, "Core database does not exist; nothing was created."
        )
        return _finish(scenario, checks)
    try:
        with store.connection() as connection:
            connection.execute("BEGIN")
            identity = connection.execute(
                "SELECT id,root FROM workspace WHERE singleton=1"
            ).fetchone()
            if identity is None or Path(identity["root"]).resolve() != root:
                _outcome(
                    checks,
                    "database_evidence",
                    False,
                    "Database workspace identity does not match the inspected workspace.",
                )
                return _finish(scenario, checks)
            store.workspace = Workspace.from_path(root, identity["id"])
            _outcome(
                checks,
                "database_evidence",
                True,
                "Read an existing database using SQLite read-only mode.",
            )
            quest_id = record.get("quest_id") or inspection.get("quest_id")
            if quest_id:
                quest = connection.execute(
                    "SELECT * FROM quests WHERE id=?", (quest_id,)
                ).fetchone()
            else:
                quests = connection.execute("SELECT * FROM quests LIMIT 2").fetchall()
                quest = quests[0] if len(quests) == 1 else None
            if quest is None:
                _outcome(
                    checks,
                    "completed_quest",
                    None,
                    "Target Quest is missing or ambiguous; supply run_record.quest_id.",
                )
                return _finish(scenario, checks)
            quest = dict(quest)
            _outcome(
                checks,
                "completed_quest",
                quest["status"] == "completed",
                "Actual Quest status: " + quest["status"],
                [quest["id"]],
            )
            _scenario_budgets(connection, store, quest["id"], scenario, record, checks)
            plan = connection.execute(
                "SELECT * FROM quest_plans WHERE quest_id=? AND revision=?",
                (quest["id"], quest["plan_revision"]),
            ).fetchone()
            if plan is None:
                _outcome(
                    checks,
                    "approved_current_dag",
                    False,
                    "No plan belongs to the current Quest revision.",
                )
                return _finish(scenario, checks)
            graph = json.loads(plan["graph_json"])
            nodes = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM task_nodes WHERE plan_id=?", (plan["id"],)
                )
            ]
            node_bound = scenario.get("limits", {}).get(
                "max_nodes", {1: 1, 2: 4, 3: 7}.get(scenario.get("level"), 7)
            )
            graph_nodes = {node["key"]: node for node in graph.get("nodes", [])}
            plan_valid = (
                plan["status"] == "approved"
                and plan["approved_by"] == "local_user"
                and bool(plan["approved_at"])
                and _sha(_json(graph).encode()) == plan["digest"]
                and 0 < len(nodes) <= node_bound
                and len(graph_nodes) == len(nodes)
                and all(
                    node["node_key"] in graph_nodes
                    and graph_nodes[node["node_key"]]["objective"] == node["objective"]
                    and graph_nodes[node["node_key"]]["success_criteria"]
                    == json.loads(node["criteria_json"])
                    and graph_nodes[node["node_key"]]["allowed_tools"]
                    == json.loads(node["allowed_tools"])
                    for node in nodes
                )
            )
            _outcome(
                checks,
                "approved_current_dag",
                plan_valid,
                "Current immutable graph, task projections, user approval and scenario node bound agree.",
                [plan["id"], len(nodes)],
            )
            all_runs = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM worker_runs WHERE quest_id=? AND plan_id=? ORDER BY rowid",
                    (quest["id"], plan["id"]),
                )
            ]
            runs = [
                run
                for run in all_runs
                if any(
                    node["worker_id"] == run["worker_id"] and node["node_key"] == run["node_key"]
                    for node in nodes
                )
            ]
            current = {run["node_key"]: run for run in runs}
            valid_workers = len(current) == len(nodes) and all(
                node["status"] == "succeeded"
                and node["node_key"] in current
                and current[node["node_key"]]["state"] == "succeeded"
                and current[node["node_key"]]["execution_mode"] == "live"
                and current[node["node_key"]]["plan_revision"] == quest["plan_revision"]
                and current[node["node_key"]]["plan_digest"] == plan["digest"]
                for node in nodes
            )
            _outcome(
                checks,
                "successful_worker_nodes",
                valid_workers,
                "Every current node needs a succeeded live Worker bound to this approved revision.",
                [run["id"] for run in current.values()],
            )
            attempts = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM model_budget_attempts WHERE quest_id=? OR "
                    "(quest_id IS NULL AND purpose='router' AND workspace_id=?)",
                    (quest["id"], store.workspace.id),
                )
            ]
            settled = [
                item
                for item in attempts
                if item["state"] == "settled" and item["provider_id"] and item["model_id"]
            ]
            purposes = {item["purpose"] for item in settled}
            real_calls = (
                {"router", "planner", "team_composer", "worker"} <= purposes
                and all(
                    any(
                        item["purpose"] == "worker"
                        and item["worker_run_id"] == run["id"]
                        and (item["provider_id"], item["model_id"])
                        == (run["provider_id"], run["model_id"])
                        for item in settled
                    )
                    for run in current.values()
                )
                and bool(current)
            )
            _outcome(
                checks,
                "real_provider_calls",
                real_calls,
                "Settled Gateway attempts must cover Router, Planner, team composition and every live Worker.",
                sorted(purposes),
            )
            unknown_usage = [
                item["attempt_id"]
                for item in attempts
                if item["state"] != "rejected"
                and (item["input_charged"] is None or item["output_charged"] is None)
            ]
            _outcome(
                checks,
                "accounted_model_usage",
                None if unknown_usage else True,
                "Unsettled or unknown usage remains visible and is not treated as zero.",
                unknown_usage,
            )
            if scenario.get("level") in {2, 3}:
                proposals = connection.execute(
                    "SELECT payload FROM events WHERE quest_id=? AND event_type='quest.plan_proposed' "
                    "AND json_extract(payload,'$.plan_id')=? ORDER BY seq DESC LIMIT 1",
                    (quest["id"], plan["id"]),
                ).fetchone()
                provenance = json.loads(proposals[0]) if proposals else {}
                provenance_keys = {
                    "planner_call_id",
                    "response_sha256",
                    "graph_digest",
                    "source_event_id",
                }
                provenance_present = provenance_keys <= set(provenance) and all(
                    provenance.get(key) is not None for key in provenance_keys
                )
                source_event = connection.execute(
                    "SELECT 1 FROM events WHERE id=? AND quest_id=? "
                    "AND event_type IN ('quest.proposed','quest.proposal_updated')",
                    (provenance.get("source_event_id"), quest["id"]),
                ).fetchone()
                native_plan = (
                    plan_valid
                    and provenance_present
                    and provenance.get("graph_digest") == plan["digest"]
                    and isinstance(provenance.get("response_sha256"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", provenance["response_sha256"]) is not None
                    and source_event is not None
                    and any(
                        item["purpose"] == "planner"
                        and item["call_id"] == provenance.get("planner_call_id")
                        for item in settled
                    )
                )
                _outcome(
                    checks,
                    "model_generated_dag",
                    native_plan if provenance_present else None,
                    "Current approved graph digest is bound to the exact settled native Planner call, response hash and source Quest event; absent binding remains unknown.",
                    [provenance] if provenance else [],
                )
            members, team_errors = [], []
            for run in current.values():
                try:
                    body = TeamCompositionService(store).get_historical_approved(
                        quest["id"],
                        quest["plan_revision"],
                        run["team_digest"],
                        connection=connection,
                        run_id=run["id"],
                    )
                    member = body["members"][run["member_index"]]
                    if (
                        member["role_id"],
                        member["task_node_key"],
                        member["provider_id"],
                        member["model_id"],
                    ) != (run["role_id"], run["node_key"], run["provider_id"], run["model_id"]):
                        raise ValueError("team member mismatch")
                    members.append(member)
                except (DomainError, KeyError, IndexError, TypeError, ValueError):
                    team_errors.append(run["id"])
            _outcome(
                checks,
                "bounded_approved_team",
                bool(members)
                and not team_errors
                and len(members) <= scenario.get("limits", {}).get("max_members", node_bound),
                "Execution members match historically approved immutable team contracts.",
                team_errors,
            )
            receipts, bad_receipts, deliveries, passed_reviews = [], [], [], []
            core_reads, bad_core_reads = [], []
            for run in current.values():
                try:
                    prefetched_receipts, prefetched_core_reads = _prefetched_sources(
                        store, connection, run
                    )
                    receipts.extend(prefetched_receipts)
                    core_reads.extend(prefetched_core_reads)
                except (DomainError, KeyError, TypeError, ValueError):
                    bad_core_reads.append(run["id"])
                    bad_receipts.append(run["id"])
                for call in connection.execute(
                    "SELECT * FROM worker_tool_calls WHERE run_id=?", (run["id"],)
                ):
                    if call["state"] != "succeeded":
                        continue
                    if call["tool_name"] in _CORE_READ_TOOLS:
                        source = _core_context_read(store, connection, call, run)
                        if source is None:
                            bad_core_reads.append(call["call_id"])
                            bad_receipts.append(call["call_id"])
                        else:
                            core_reads.append(source)
                        continue
                    receipt = _receipt(connection, call, run)
                    if receipt is None:
                        bad_receipts.append(call["call_id"])
                    else:
                        receipts.append(receipt)
                message = connection.execute(
                    "SELECT message_json FROM worker_messages WHERE run_id=? AND role='assistant' ORDER BY ordinal DESC LIMIT 1",
                    (run["id"],),
                ).fetchone()
                if message:
                    content = json.loads(message[0]).get("content")
                    try:
                        deliveries.append({"run_id": run["id"], "content": json.loads(content)})
                    except (TypeError, ValueError):
                        pass
                node = next(node for node in nodes if node["node_key"] == run["node_key"])
                for criterion in json.loads(node["criteria_json"]):
                    if (
                        criterion == "delivery_review" or criterion.startswith("json_review:")
                    ) and review_pass_matches(store, connection, run, criterion):
                        passed_reviews.append({"run_id": run["id"], "criterion": criterion})
            _outcome(
                checks,
                "receipt_integrity",
                not bad_receipts,
                "Successful action tools need live immutable intent/receipt bindings; native Core reads need original source and Worker exchange provenance.",
                bad_receipts,
            )
            _outcome(
                checks,
                "core_context_read_integrity",
                not bad_core_reads,
                "Core reads are revalidated separately and never counted as action receipts, source file reads or artifact writes.",
                {"verified": core_reads, "invalid_call_ids": bad_core_reads},
            )
            _outcome(
                checks,
                "structured_deliveries",
                len(deliveries) == len(current)
                and bool(deliveries)
                and all(
                    isinstance(item["content"], dict)
                    and set(DELIVERY_FIELDS) <= set(item["content"])
                    for item in deliveries
                ),
                "Every current Worker actually supplied all eight delivery fields.",
            )
            documents = _inputs(scenario, root, checks, receipts)
            artifacts, artifact_errors, latest_artifacts = {}, [], {}
            for receipt in receipts:
                if receipt["tool_name"] not in {"file_write", "file_revise"}:
                    continue
                try:
                    path = _path(root, receipt["resource_scope"])
                    chain = verify_file_revision_chain(connection, receipt["run_id"], receipt["id"])
                    if Path(chain["path"]).resolve() != path:
                        raise ValueError("artifact revision path mismatch")
                    previous = latest_artifacts.get(path)
                    if (
                        previous is None
                        or receipt["receipt_sequence"] > previous[0]["receipt_sequence"]
                    ):
                        latest_artifacts[path] = (receipt, chain)
                except (DomainError, OSError, ValueError, KeyError, TypeError):
                    artifact_errors.append(receipt["resource_scope"])
            for path, (receipt, chain) in latest_artifacts.items():
                try:
                    data = _read(path)
                    if (
                        _sha(data) != chain["sha256"]
                        or len(data) != chain["bytes"]
                        or receipt["parameters"].get("content", "").encode() != data
                    ):
                        raise ValueError("artifact hash mismatch")
                    artifacts[path.relative_to(root).as_posix()] = {
                        "sha256": _sha(data),
                        "receipt_id": receipt["id"],
                        "run_id": receipt["run_id"],
                        "revision_receipt_ids": chain["revision_receipt_ids"],
                        "content": json.loads(data)
                        if path.suffix == ".json"
                        else data.decode("utf-8"),
                    }
                except (OSError, ValueError, KeyError, TypeError, UnicodeError):
                    artifact_errors.append(receipt["resource_scope"])
            required_paths = scenario.get("suggested_output_paths", [])
            artifact_ok = not artifact_errors and all(path in artifacts for path in required_paths)
            if required_paths:
                _outcome(
                    checks,
                    "artifact_hashes",
                    artifact_ok,
                    "Required output files match the latest live write/revision receipt, validated same-run revision chains and exact final bytes; superseded versions remain historical evidence.",
                    [
                        {
                            "path": path,
                            "sha256": item["sha256"],
                            "receipt_id": item["receipt_id"],
                            "revision_receipt_ids": item["revision_receipt_ids"],
                        }
                        for path, item in artifacts.items()
                    ]
                    + artifact_errors,
                )
            level = scenario.get("level")
            independent = (
                bool(current)
                and all(
                    all(
                        criterion.startswith("semantic_numeric:scheduler-v1:")
                        or {"run_id": run["id"], "criterion": criterion} in passed_reviews
                        for node in nodes
                        if node["node_key"] == run["node_key"]
                        for criterion in json.loads(node["criteria_json"])
                    )
                    for run in current.values()
                )
                and all(
                    any(item["criterion"] == "json_review:" + path for item in passed_reviews)
                    for path in required_paths
                    if path.endswith(".json")
                )
            )
            _outcome(
                checks,
                "independent_content_review",
                independent,
                "Live PASS is recomputed from the current source packet, latest review claim and settled Reviewer Gateway attempt.",
                passed_reviews,
            )
            if level == 1:
                _outcome(
                    checks,
                    "one_worker",
                    len(nodes) == len(current) == 1
                    and len({run["worker_id"] for run in all_runs}) == 1,
                    "L1 uses one background execution Worker.",
                )
                names = {receipt["tool_name"] for receipt in receipts}
                allowed = {tool for node in nodes for tool in json.loads(node["allowed_tools"])}
                readonly = {
                    "file_read",
                    "directory_list",
                    "worker_read_action_receipt",
                    "worker_read_context_source",
                }
                _outcome(
                    checks,
                    "readonly_tool_receipts",
                    {"file_read", "directory_list"} <= names <= readonly and allowed <= readonly,
                    "L1 requires successful live file_read and directory_list receipts with read-only approved tools.",
                    sorted(names),
                )
                _outcome(
                    checks,
                    "independent_delivery_review",
                    len(passed_reviews) == 1
                    and passed_reviews[0]["criterion"]
                    in {"delivery_review", "json_review:@delivery"},
                    "L1 structured delivery needs an independent, current live PASS.",
                    passed_reviews,
                )
                content = deliveries[0]["content"] if len(deliveries) == 1 else {}
                text = _json(content)
                findings = content.get("findings", []) if isinstance(content, dict) else []
                sufficient = len(findings) >= 3 and bool(
                    re.search(r"(?i)rank|priorit|impact|高优先|优先级|影响", text)
                )
                insufficient = bool(
                    re.search(
                        r"(?i)(?:insufficient|fewer than three|not enough).{0,100}(?:evidence|substantiat|finding)|证据不足|不足三|少于三",
                        text,
                    )
                )
                _outcome(
                    checks,
                    "three_ranked_findings_or_explicit_insufficient_evidence",
                    (sufficient or insufficient) and independent,
                    "Ranked finding structure or explicit evidence limitation must accompany the source-bound independent PASS.",
                )
                matched = _citations(documents, content)
                _outcome(
                    checks,
                    "real_source_citations",
                    bool(documents) and len(matched) == len(documents) and independent,
                    "The independently reviewed L1 delivery cites each source path/hash and an exact supplied line range.",
                    matched,
                )
            if level in {2, 3}:
                if level == 2:
                    assigned = _specialists(members, nodes, ["research", "architecture", "writing"])
                    specialized = (
                        3 <= len(nodes) == len(current) <= 4
                        and len({member.get("role_id") for member in members}) >= 3
                        and assigned is not None
                    )
                    _outcome(
                        checks,
                        "three_or_four_specialist_workers",
                        specialized,
                        "Three or four approved specialist Workers cover research, architecture and report writing; content review is checked separately.",
                        [assigned] if assigned else [],
                    )
                    checks["artifact_receipt_hashes"] = dict(checks["artifact_hashes"])
                else:
                    assigned = _specialists(
                        members,
                        nodes,
                        [
                            "research",
                            "mathematics",
                            "architecture",
                            "coding",
                            "experiment",
                            "review",
                        ],
                    )
                    _outcome(
                        checks,
                        "research_math_architecture_coding_experiment_review",
                        6 <= len(nodes) == len(current) <= 7
                        and len({member.get("role_id") for member in members}) >= 6
                        and assigned is not None
                        and independent
                        and bool(passed_reviews),
                        "Six distinct approved Worker nodes cover research, mathematics, architecture, coding, experiment and review; live independent content review is also required.",
                        [assigned] if assigned else [],
                    )
                final_report = artifacts.get(f"work/l{level}-report.json", {}).get("content")
                matched = _citations(
                    documents,
                    final_report,
                )
                _outcome(
                    checks,
                    "real_source_citations",
                    bool(documents) and len(matched) == len(documents) and independent,
                    "Each exact source path/hash and complete supplied line range occurs in the independently reviewed final JSON report; citation relevance is judged by Reviewer.",
                    matched,
                )
            if level == 3:
                candidate = artifacts.get("work/l3-scheduler.py", {})
                report_text = _json(artifacts.get("work/l3-report.json", {}).get("content"))
                numeric = []
                for receipt in receipts:
                    if receipt["tool_name"] != "numeric_ast":
                        continue
                    params = receipt["parameters"]
                    arguments = params.get("inputs", {})
                    if (
                        isinstance(params.get("source"), str)
                        and _sha(params["source"].encode()) == candidate.get("sha256")
                        and (receipt["id"] in report_text or receipt["call_id"] in report_text)
                        and set(arguments) == {"eligible_a", "eligible_b", "cost_a", "cost_b"}
                        and all(
                            type(arguments[key]) is int and arguments[key] in {0, 1}
                            for key in ("eligible_a", "eligible_b")
                        )
                        and all(
                            type(arguments[key]) is int and arguments[key] >= 0
                            for key in ("cost_a", "cost_b")
                        )
                    ):
                        numeric.append(receipt)
                inputs = {_json(receipt["parameters"].get("inputs", {})) for receipt in numeric}
                _outcome(
                    checks,
                    "real_numeric_ast_receipts",
                    len(numeric) >= 2 and len(inputs) >= 2,
                    "Final report experiments need at least two valid distinct input sets, exact final source bytes and referenced successful live numeric_ast receipt/call IDs.",
                    [item["id"] for item in numeric],
                )
                experiment_node = (
                    assigned.get("experiment", {}).get("node_key") if assigned else None
                )
                experiment_run = current.get(experiment_node, {}).get("id")
                _outcome(
                    checks,
                    "experiment_specialist_execution",
                    experiment_run is not None
                    and any(item["run_id"] == experiment_run for item in numeric),
                    "The approved experiment specialist itself executed a referenced successful experiment against final scheduler source.",
                    [item["id"] for item in numeric if item["run_id"] == experiment_run],
                )
                coding_node = assigned.get("coding", {}).get("node_key") if assigned else None
                coding_run = current.get(coding_node, {}).get("id")
                _outcome(
                    checks,
                    "coding_specialist_artifact",
                    coding_run is not None and candidate.get("run_id") == coding_run,
                    "The independently tested scheduler was actually written/revised by the approved coding specialist.",
                    [candidate.get("receipt_id")] if candidate else [],
                )
                semantic = []
                for row in connection.execute(
                    "SELECT * FROM semantic_test_runs WHERE quest_id=? AND plan_id=? AND suite_id='scheduler-v1'",
                    (quest["id"], plan["id"]),
                ):
                    if (
                        row["worker_run_id"] not in {run["id"] for run in current.values()}
                        or row["artifact_path"] != "work/l3-scheduler.py"
                    ):
                        continue
                    try:
                        SemanticVerifier(store).validate_evidence(
                            connection,
                            quest_id=quest["id"],
                            plan_id=plan["id"],
                            plan_revision=quest["plan_revision"],
                            worker_id=row["worker_id"],
                            worker_run_id=row["worker_run_id"],
                            semantic_run_id=row["id"],
                        )
                        semantic.append(row["id"])
                    except (DomainError, OSError, KeyError, TypeError, ValueError):
                        pass
                _outcome(
                    checks,
                    "independent_fixed_semantic_suite",
                    bool(semantic),
                    "Current scheduler artifact needs source-bound trusted fixed-suite provenance and all independently validated cases passing.",
                    semantic,
                )
                _continuity(store, root, record, checks)
            conversation_count = connection.execute(
                "SELECT count(*) FROM conversations"
            ).fetchone()[0]
            _outcome(
                checks,
                "single_conversation",
                conversation_count == 1 and quest["conversation_id"] == "main",
                "Exactly one persistent main conversation exists.",
                [conversation_count],
            )
            notices = []
            for message in connection.execute(
                "SELECT id,content,metadata FROM messages WHERE role='assistant' AND conversation_id='main'"
            ):
                metadata = json.loads(message["metadata"])
                if (
                    metadata.get("source") != "router_notice"
                    or metadata.get("execution_mode") != "live"
                    or not message["content"].strip()
                ):
                    continue
                event = connection.execute(
                    "SELECT event_type,payload FROM events WHERE id=? AND quest_id=?",
                    (metadata.get("event_id"), quest["id"]),
                ).fetchone()
                if event and (
                    event["event_type"] in {"quest.completed", "quest.status.completed"}
                    or (
                        event["event_type"] == "quest.status_changed"
                        and json.loads(event["payload"]).get("to") == "completed"
                    )
                ):
                    notices.append(message["id"])
            _outcome(
                checks,
                "router_completion_summary",
                bool(notices) and "router_notice" in purposes,
                "A live Router notice references this Quest's durable completion event and has a settled Gateway attempt.",
                notices,
            )
    except (
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        DomainError,
    ) as exc:
        _outcome(
            checks,
            "inspection_integrity",
            False,
            "Evidence inspection rejected malformed or inconsistent records: " + type(exc).__name__,
        )
    return _finish(scenario, checks)
