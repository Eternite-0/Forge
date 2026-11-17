"""Bounded real-model capability acceptance through the authenticated Core.

No credentials are read from files here. Inputs and independent checks are fixed
by capability_sprint_scenarios. Published output contains metadata/hashes only.
Restart/compaction are explicitly labelled host fault-injection, not model work.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capability_continuity_evidence as continuity_evidence
import capability_sprint_metrics as metrics
import capability_sprint_scenarios as scenarios
import verify_superagent as baseline

from forge_core.context_service import ContextService
from forge_core.governance import POLICY_VERSION, parameter_digest
from forge_core.memory import MemoryService
from forge_core.python_unittest_runner import read_snapshot
from forge_core.settings import Workspace
from forge_core.store import Store

live_core = baseline.live_core
REPO = baseline.REPO
SQLITE_READ_ATTEMPTS = 3
SHUTDOWN_DRAIN_SECONDS = 130
_READ_OBSERVATIONS = ContextVar("capability_sqlite_read_observations", default=None)
_API_DOMAIN_CODES = {
    "Invalid Router model decision": "invalid_router_model_decision",
    "Invalid Router notice decision": "invalid_router_notice_decision",
    "Router context changed concurrently": "router_context_changed_concurrently",
    "Router context budget exceeded": "router_context_budget_exceeded",
    "Router revision conflict": "router_revision_conflict",
    "Router response did not complete": "router_response_incomplete",
    "Router tool step limit exceeded": "router_tool_step_limit",
    "Model structured output support is unknown": "model_structured_output_unknown",
    "Context source integrity failure": "context_source_integrity_failure",
}


class Failure(RuntimeError):
    pass


class CoreAPIError(Failure):
    def __init__(self, status, body):
        super().__init__("core_api_status_" + str(status))
        detail = body.get("detail") if isinstance(body, dict) else None
        self.observation = {
            "http_status": status,
            "detail_sha256": hashlib.sha256(
                json.dumps(
                    detail, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            "detail_code": _API_DOMAIN_CODES.get(detail, "unclassified_core_detail")
            if isinstance(detail, str)
            else "unclassified_core_detail",
        }


def core_api(core, method, path, payload=None):
    reply = core.call(method, path, payload, timeout=180)
    if reply["status"] != 200:
        raise CoreAPIError(reply["status"], reply["body"])
    return reply["body"]


def submit_in_order(levels, submit):
    """Wait for each Router response, leaving dispatched Workers autonomous."""
    for level in levels:
        submit(level).result()


def error_metadata(exc):
    """Publish typed SQLite codes without remote data or exception messages."""
    detail = {"exception_type": type(exc).__name__}
    if isinstance(exc, sqlite3.Error):
        detail["sqlite_errorcode"] = getattr(exc, "sqlite_errorcode", None)
        detail["sqlite_errorname"] = getattr(exc, "sqlite_errorname", None)
    if isinstance(exc, CoreAPIError):
        detail.update(exc.observation)
    return detail


def read_only(operation, *, stage, deadline=None):
    """Retry only explicit SQLite BUSY/IOERR reads, at most three total attempts.

    Callers must supply a read-only SQLite operation. This never wraps API,
    Provider, tool, context compaction, artifact verification or restart work.
    """
    for attempt in range(1, SQLITE_READ_ATTEMPTS + 1):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            retry = (
                type(code) is int
                and code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_IOERR}
                and attempt < SQLITE_READ_ATTEMPTS
                and (deadline is None or time.monotonic() + 0.05 * attempt < deadline)
            )
            observations = _READ_OBSERVATIONS.get()
            if observations is not None:
                observations.append(
                    {"stage": stage, "attempt": attempt, "will_retry": retry, **error_metadata(exc)}
                )
            if not retry:
                raise
            time.sleep(0.05 * attempt)


def snapshot(database):
    return read_only(lambda: baseline.snapshot(database), stage="core_snapshot")


def record_diagnostic_error(report, stage, exc, **scope):
    report.setdefault("diagnostic_errors", []).append(
        {"stage": stage, **scope, **error_metadata(exc)}
    )
    report.setdefault("failure_code", stage + "_failed")
    report["status"] = "failed"


def source_digest():
    digest = hashlib.sha256()
    files = sorted((REPO / "backend" / "src").rglob("*.py"))
    files += [
        Path(__file__),
        Path(scenarios.__file__),
        Path(metrics.__file__),
        Path(continuity_evidence.__file__),
        Path(live_core.__file__),
    ]
    for path in files:
        digest.update(path.relative_to(REPO).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def rows(database, query, parameters=()):
    database = Path(database).resolve()

    def read():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as c:
            c.row_factory = sqlite3.Row
            return [dict(row) for row in c.execute(query, parameters)]

    return read_only(read, stage="core_query")


def task_mapping(state, prefix):
    return {
        item["request_id"][len(prefix) :]: item["id"]
        for item in state["request_quests"]
        if item["request_id"] and item["request_id"].startswith(prefix)
    }


def reviewed_notices_for_tasks(state):
    """Only a delivered Quest completion proves the final Router handback.

    A Worker result precedes independent review. A status message about that
    claim cannot satisfy the final completion gate, even if review later passes.
    """
    events = {event["id"]: event for event in state["events"]}
    completed = {quest["id"] for quest in state["quests"] if quest["status"] == "completed"}
    result = set()
    for message in state["messages"]:
        metadata = json.loads(message["metadata"])
        if message["role"] != "assistant" or metadata.get("source") != "router_notice":
            continue
        for event_id in metadata.get("source_event_ids", [metadata.get("event_id")]):
            event = events.get(event_id)
            if event is None or event["quest_id"] not in completed:
                continue
            if event["event_type"] == "quest.completed" or (
                event["event_type"] == "quest.status_changed"
                and json.loads(event["payload"]).get("to") == "completed"
            ):
                result.add(event["quest_id"])
    return result


def resolve_context_declaration(declared, operator_window=None):
    """An explicit operator declaration wins; discovery stays attributed data."""
    fields = {
        name: declared[name]
        for name in ("context_window", "context_length", "max_input_tokens", "max_output_tokens")
        if type(declared.get(name)) is int and declared[name] > 0
    }
    windows = [value for name, value in fields.items() if name != "max_output_tokens"]
    if operator_window is not None and (type(operator_window) is not int or operator_window < 1):
        raise ValueError("Invalid operator context declaration")
    if operator_window is None and not windows:
        raise Failure("provider_context_metadata_missing")
    effective = operator_window if operator_window is not None else min(windows)
    return {
        "source": "operator_declared" if operator_window is not None else "provider_declared",
        "independently_measured": False,
        "context_window": effective,
        "fields": fields,
        "operator_context_window": operator_window,
        "declaration_conflict_retained": any(window != effective for window in windows),
        "execution_budget_grant": False,
    }


def verify_project_links(database, mapping, descriptors):
    links = rows(
        database,
        "SELECT p.root,l.quest_id FROM task_project_links l "
        "JOIN workspace_projects p ON p.id=l.project_id",
    )
    actual = {item["quest_id"]: Path(item["root"]).resolve() for item in links}
    return {
        "two_detected_project_roots": len(
            {actual.get(quest) for quest in mapping.values()} - {None}
        )
        == 2,
        "correct_task_project_associations": all(
            actual.get(quest) == Path(descriptors[level]["project_root"]).resolve()
            for level, quest in mapping.items()
        ),
    }


def parallel_task_ownership(intervals, workers):
    """Count distinct tasks with overlapping ownership, not CPU parallelism."""
    quests = {worker["id"]: worker["quest_id"] for worker in workers}
    boundaries = sorted(
        (stamp, change, quests[item["run_id"]])
        for item in intervals
        if item["run_id"] in quests
        for stamp, change in ((item["started_at"], 1), (item["ended_at"], -1))
    )
    active, maximum = {}, 0
    for _stamp, change, quest in boundaries:
        active[quest] = active.get(quest, 0) + change
        maximum = max(maximum, sum(count > 0 for count in active.values()))
    return maximum


def check_budget(database, state, task_count):
    if len(state["calls"]) > 32 * task_count:
        raise Failure("aggregate_call_ceiling")
    if any(item["state"] in {"unknown", "uncertain"} for item in state["calls"]):
        raise Failure("unknown_provider_outcome_no_replay")
    for item in rows(
        database,
        "SELECT quest_id,COUNT(*) AS calls,SUM(COALESCE(input_charged,input_reserved)+COALESCE(output_charged,output_reserved)) AS tokens FROM model_budget_attempts GROUP BY quest_id",
    ):
        # Router calls have no Quest scope; they still count in the aggregate.
        if item["quest_id"] is not None and (item["calls"] > 32 or item["tokens"] > 500000):
            raise Failure("per_task_budget_ceiling")


def approved_python_requests(database, workspace, quest_ids):
    """Validate only this test's exact R2 manifests before host authorization."""
    pending = rows(database, "SELECT * FROM approvals WHERE status='PENDING' ORDER BY created_at")
    valid = []
    for approval in pending:
        if approval["quest_id"] not in quest_ids or approval["action_type"] != "python_unittest":
            raise Failure("unexpected_approval_not_automatically_authorized")
        if (
            approval["risk"] != "R2"
            or approval["policy_version"] != POLICY_VERSION
            or approval["consumed_at"] is not None
            or approval["decided_at"] is not None
            or approval["decided_by"] is not None
            or datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC)
        ):
            raise Failure("python_approval_risk_mismatch")
        # R2 preparation stops before an action intent exists. The durable
        # request binds the approval to the exact reserved Worker tool call.
        bindings = rows(
            database,
            """SELECT c.arguments_json,c.tool_name,c.state AS call_state,
            c.receipt_id,c.result_json,r.quest_id,r.plan_revision,r.execution_mode,
            r.state AS run_state,r.awaiting_authorization,q.plan_revision AS quest_revision,
            q.status AS quest_status,b.call_id
            FROM tool_approval_requests b
            JOIN worker_tool_calls c ON c.run_id=b.run_id AND c.call_id=b.call_id
            JOIN worker_runs r ON r.id=b.run_id JOIN quests q ON q.id=r.quest_id
            WHERE b.approval_id=?""",
            (approval["id"],),
        )
        if len(bindings) != 1:
            raise Failure("python_approval_source_unavailable")
        binding = bindings[0]
        if (
            binding["quest_id"] != approval["quest_id"]
            or binding["plan_revision"] != approval["plan_revision"]
            or binding["quest_revision"] != approval["plan_revision"]
            or binding["quest_status"] not in {"approved", "queued", "running"}
            or binding["execution_mode"] != "live"
            or binding["tool_name"] != "python_unittest"
            or binding["receipt_id"] is not None
            or binding["result_json"] is not None
            or rows(
                database, "SELECT id FROM action_intents WHERE call_id=?", (binding["call_id"],)
            )
        ):
            raise Failure("python_approval_binding_mismatch")
        parameters = json.loads(binding["arguments_json"])
        if (
            type(parameters) is not dict
            or set(parameters) != {"path", "manifest", "test_module"}
            or type(parameters["path"]) is not str
            or type(parameters["manifest"]) is not dict
            or parameter_digest(parameters) != approval["parameter_digest"]
        ):
            raise Failure("python_approval_parameters_mismatch")
        expected = workspace / "work" / approval["quest_id"]
        if (
            Path(approval["resource_scope"]).resolve() != expected.resolve()
            or (workspace / parameters["path"]).resolve() != expected.resolve()
        ):
            raise Failure("python_approval_scope_mismatch")
        test_module = parameters["test_module"]
        if (
            not isinstance(test_module, str)
            or not re.fullmatch(r"test_[A-Za-z0-9_]{1,64}", test_module)
            or set(parameters["manifest"]) != {"candidate.py", test_module + ".py"}
        ):
            raise Failure("python_approval_manifest_out_of_scope")
        read_snapshot(expected, parameters["manifest"], test_module)
        if binding["run_state"] == "running" and binding["call_state"] == "running":
            # Approval creation and the subsequent blocked checkpoint use two
            # transactions. Wait for the latter before authorizing/restarting.
            continue
        if (
            binding["run_state"] != "blocked"
            or binding["awaiting_authorization"] != 1
            or binding["call_state"] != "pending"
        ):
            raise Failure("python_approval_worker_not_waiting")
        valid.append(approval)
    return valid


def restart_boundary(database, *, required_completed_quest_ids=()):
    """Observe a quiescent boundary; check again after stop before any restart.

    The read snapshot is not a scheduling fence. A failed post-stop check aborts
    the experiment without restarting/replaying an unknown call or side effect.
    """
    database = Path(database).resolve()
    queries = (
        "SELECT 1 FROM model_budget_attempts WHERE state!='settled' LIMIT 1",
        "SELECT 1 FROM worker_model_reservations WHERE state='reserved' LIMIT 1",
        "SELECT 1 FROM action_intents WHERE state IN ('prepared','executing','uncertain') LIMIT 1",
        "SELECT 1 FROM worker_tool_calls WHERE state IN ('running','uncertain') LIMIT 1",
        "SELECT 1 FROM worker_runs WHERE state NOT IN ('blocked','succeeded','failed','cancelled') OR lease_token IS NOT NULL LIMIT 1",
        "SELECT 1 FROM node_verification_attempts WHERE state='checking' LIMIT 1",
    )

    def read():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            c.execute("BEGIN")
            if any(c.execute(query).fetchone() for query in queries):
                return False
            required = set(required_completed_quest_ids)
            for quest_id in required:
                plan = c.execute(
                    "SELECT p.id FROM quests q JOIN quest_plans p ON p.quest_id=q.id "
                    "AND p.revision=q.plan_revision WHERE q.id=? AND q.status='completed' "
                    "AND q.conversation_id='main' AND p.status='approved'",
                    (quest_id,),
                ).fetchone()
                if plan is None:
                    return False
                nodes = c.execute(
                    "SELECT status FROM task_nodes WHERE plan_id=?", (plan["id"],)
                ).fetchall()
                if not nodes or any(
                    node["status"] not in {"succeeded", "failed", "skipped"} for node in nodes
                ):
                    return False
            if required:
                state = {
                    "quests": [
                        dict(row)
                        for row in c.execute(
                            "SELECT id,status FROM quests WHERE conversation_id='main'"
                        )
                    ],
                    "messages": [
                        dict(row)
                        for row in c.execute(
                            "SELECT role,metadata FROM messages WHERE conversation_id='main'"
                        )
                    ],
                    "events": [
                        dict(row)
                        for row in c.execute(
                            "SELECT id,event_type,quest_id,payload FROM events "
                            "WHERE conversation_id='main'"
                        )
                    ],
                }
                if not required <= reviewed_notices_for_tasks(state):
                    return False
            return True

    return read_only(read, stage="restart_boundary_read")


def stop_for_restart(
    core,
    database,
    report,
    *,
    required_completed_quest_ids,
    workspace=None,
    held_python_approvals=(),
):
    """Require confirmed orderly shutdown before stopped-state reads or mutation."""
    stopped = core.stop()
    report["restart_core_stop"] = stopped
    confirmed = isinstance(stopped, dict) and stopped.get("graceful_confirmed") is True
    report["checks"]["restart_graceful_stop_confirmed"] = confirmed
    if not confirmed:
        raise Failure("restart_graceful_stop_unconfirmed_no_replay")
    if not restart_boundary(database, required_completed_quest_ids=required_completed_quest_ids):
        raise Failure("restart_boundary_changed_no_replay")
    if held_python_approvals:
        actual = approved_python_requests(
            database, workspace, {item["quest_id"] for item in held_python_approvals}
        )

        def identity(items):
            return {(item["id"], item["parameter_digest"], item["plan_revision"]) for item in items}

        preserved = identity(actual) == identity(held_python_approvals)
        report["checks"]["restart_exact_pending_approvals_preserved"] = preserved
        if not preserved:
            raise Failure("restart_approval_boundary_changed_no_replay")


def blocked_observations(database):
    """Read reason codes from durable events, where Worker failure stores them."""
    return rows(
        database,
        """SELECT r.id AS run_id,r.state,r.awaiting_authorization,
        (SELECT json_extract(e.payload,'$.reason_code') FROM events e
         WHERE e.worker_id=r.worker_id AND e.event_type IN ('worker.blocked','worker.failed')
         AND json_extract(e.payload,'$.run_id')=r.id ORDER BY e.seq DESC LIMIT 1) AS reason
        FROM worker_runs r WHERE r.state IN ('blocked','failed')""",
    )


def task_team_observation(database, quest_id):
    """Project actual selection and Worker topology without objective text."""
    quest = rows(database, "SELECT metadata,plan_revision FROM quests WHERE id=?", (quest_id,))[0]
    metadata = json.loads(quest["metadata"])
    team = metadata.get("execution_team")
    independent = metadata.get("independent_dispatches", [])
    projection = {
        "execution_team": {
            "strategy": team["strategy"],
            "assignments": [
                {"role": item["role"], "depends_on": item.get("depends_on", [])}
                for item in team["assignments"]
            ],
        }
        if team
        else None,
        "independent_dispatches": [
            {"role": item["role"], "depends_on": []} for item in independent
        ],
    }
    projection["selected_strategy"] = (
        team["strategy"] if team else "parallel" if len(independent) > 1 else "single"
    )
    workers = rows(
        database,
        "SELECT worker_id,role_id,node_key FROM worker_runs WHERE quest_id=? ORDER BY rowid",
        (quest_id,),
    )
    projection["actual_worker_count"] = len({item["worker_id"] for item in workers})
    projection["actual_roles"] = [item["role_id"] for item in workers]
    projection["actual_dependencies"] = rows(
        database,
        """SELECT e.source,e.target,e.condition
        FROM task_edges e JOIN quest_plans p ON p.id=e.plan_id
        WHERE p.quest_id=? AND p.revision=? ORDER BY e.source,e.target""",
        (quest_id, quest["plan_revision"]),
    )
    return projection


def strategy_observed(observation, strategy):
    """A prompt label never proves which team the Router actually dispatched."""
    selected = observation["execution_team"]
    count = observation["actual_worker_count"]
    if strategy == "single":
        return (
            observation["selected_strategy"] == "single"
            and count == 1
            and not observation["actual_dependencies"]
        )
    if strategy == "fixed":
        return (
            selected is not None
            and selected["strategy"] == "sequential"
            and count == 2
            and selected["assignments"]
            == [{"role": "research", "depends_on": []}, {"role": "architect", "depends_on": [0]}]
            and observation["actual_roles"] == ["research_1", "architect_2"]
            and observation["actual_dependencies"]
            == [{"source": "task_1", "target": "task_2", "condition": "success"}]
        )
    roles = {
        role.rsplit("_", 1)[0] if role.rsplit("_", 1)[-1].isdigit() else role
        for role in observation["actual_roles"]
    }
    return count >= 2 and len(roles) >= 2


def verify_development_receipts(database, workspace, quest_id):
    """Require stored live tests of the delivered code, without executing anything.

    A failed historical test is valid evidence, but cannot establish that the
    final candidate passed. All cited receipts must bind to this Quest's exact
    Worker call, consumed R2 approval, manifest and immutable result digest.
    """
    checks = {
        "nonempty_test_receipt_ids": False,
        "live_source_bound_test_receipts": False,
        "successful_test_matches_final_candidate": False,
    }
    result = {"provenance": "programmatic_read_only_core_evidence", "checks": checks}
    try:
        if not isinstance(quest_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", quest_id):
            raise ValueError
        workspace = Path(workspace).resolve()
        directory = workspace / "work" / quest_id
        for path in (workspace / "work", directory, directory / "candidate.py"):
            if path.resolve() != path or path.is_symlink():
                raise ValueError
        artifact = directory / "development.json"
        development = scenarios._load(artifact)
        candidate = directory / "candidate.py"
        if not candidate.is_file() or candidate.stat().st_size > scenarios.MAX_ARTIFACT_BYTES:
            raise ValueError
        candidate_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
        result["candidate_sha256"] = candidate_sha256
        result["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        receipt_ids = development["test_receipt_ids"]
        if (
            type(receipt_ids) is not list
            or not 1 <= len(receipt_ids) <= 64
            or any(
                type(item) is not str or not re.fullmatch(r"receipt_[A-Za-z0-9_-]{1,128}", item)
                for item in receipt_ids
            )
            or len(set(receipt_ids)) != len(receipt_ids)
        ):
            raise ValueError
        checks["nonempty_test_receipt_ids"] = True
        evidence = rows(
            database,
            """SELECT r.id AS receipt_id,r.intent_id,r.execution_mode AS receipt_mode,
            r.result_json AS receipt_json,r.result_digest,i.*,
            w.quest_id AS worker_quest_id,w.worker_id AS runtime_worker_id,
            w.plan_id AS runtime_plan_id,w.plan_revision AS runtime_plan_revision,
            w.execution_mode AS worker_mode,c.tool_name AS reserved_tool,
            c.arguments_json,c.state AS tool_state,c.result_json AS tool_result_json,
            a.quest_id AS approval_quest_id,a.plan_revision AS approval_revision,
            a.action_type,a.parameter_digest AS approval_digest,a.resource_scope AS approval_scope,
            a.risk AS approval_risk,a.policy_version,a.status AS approval_status,
            a.decided_by,a.consumed_at
            FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id
            JOIN worker_runs w ON w.id=i.run_id
            JOIN worker_tool_calls c ON c.run_id=i.run_id AND c.call_id=i.call_id AND c.receipt_id=r.id
            LEFT JOIN approvals a ON a.id=i.approval_id
            WHERE r.id IN ("""
            + ",".join("?" for _ in receipt_ids)
            + ")",
            tuple(receipt_ids),
        )
        if len(evidence) != len(receipt_ids):
            raise ValueError
        matching = []
        for row in evidence:
            parameters = json.loads(row["parameters_json"])
            output = json.loads(row["receipt_json"])
            recorded = json.loads(row["tool_result_json"])
            manifest = parameters["manifest"]
            module = parameters["test_module"]
            if (
                row["quest_id"] != quest_id
                or row["worker_quest_id"] != quest_id
                or row["worker_id"] != row["runtime_worker_id"]
                or row["plan_id"] != row["runtime_plan_id"]
                or row["plan_revision"] != row["runtime_plan_revision"]
                or {row["receipt_mode"], row["execution_mode"], row["worker_mode"]} != {"live"}
                or row["tool_name"] != "python_unittest"
                or row["reserved_tool"] != "python_unittest"
                or row["state"] not in {"succeeded", "failed"}
                or row["tool_state"] != row["state"]
                or row["risk"] != "R2"
                or row["approval_quest_id"] != quest_id
                or row["approval_revision"] != row["plan_revision"]
                or row["action_type"] != "python_unittest"
                or row["approval_risk"] != "R2"
                or row["policy_version"] != POLICY_VERSION
                or row["approval_status"] != "APPROVED"
                or row["decided_by"] != "local_user"
                or not row["consumed_at"]
                or row["approval_digest"] != row["parameter_digest"]
                or row["approval_scope"] != row["resource_scope"]
                or parameter_digest(parameters) != row["parameter_digest"]
                or parameter_digest(json.loads(row["arguments_json"])) != row["parameter_digest"]
                or hashlib.sha256(row["receipt_json"].encode()).hexdigest() != row["result_digest"]
                or recorded.get("call_id") != row["call_id"]
                or recorded.get("intent_id") != row["intent_id"]
                or recorded.get("receipt_id") != row["receipt_id"]
                or recorded.get("state") != row["state"]
                or recorded.get("output") != output
                or (workspace / parameters["path"]).resolve() != directory
                or Path(row["resource_scope"]).resolve() != directory
                or type(module) is not str
                or not re.fullmatch(r"test_[A-Za-z0-9_]{1,64}", module)
                or type(manifest) is not dict
                or set(manifest) != {"candidate.py", module + ".py"}
                or any(
                    type(value) is not str or not re.fullmatch(r"[a-f0-9]{64}", value)
                    for value in manifest.values()
                )
                or output.get("runtime") != "cpython_unittest_v1"
                or output.get("test_module") != module
                or output.get("manifest_sha256") != parameter_digest(manifest)
                or output.get("manifest_json")
                != json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            ):
                raise ValueError
            if (
                row["state"] == "succeeded"
                and type(output.get("exit_code")) is int
                and output["exit_code"] == 0
                and type(output.get("tests_run")) is int
                and output["tests_run"] >= 1
                and output.get("failures") == output.get("errors") == 0
                and manifest["candidate.py"] == candidate_sha256
            ):
                matching.append(row["receipt_id"])
        checks["live_source_bound_test_receipts"] = True
        checks["successful_test_matches_final_candidate"] = bool(matching)
        result["receipt_ids"] = receipt_ids
        result["matching_success_receipt_ids"] = matching
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result["error_code"] = "development_test_evidence_rejected"
    result["passed"] = all(checks.values())
    return result


def collect_task_results(database, workspace, mapping, descriptors, report, strategy):
    """Retain independent artifacts from completed tasks even if a later task fails."""
    completed = {
        item["id"] for item in rows(database, "SELECT id FROM quests WHERE status='completed'")
    }
    for level, quest_id in mapping.items():
        task = report["tasks"].setdefault(
            level, {"quest_id": quest_id, "source_sha256": descriptors[level]["source_sha256"]}
        )
        try:
            task["team_observation"] = task_team_observation(database, quest_id)
            if level == "L2":
                report["checks"]["observed_team_condition_L2"] = strategy_observed(
                    task["team_observation"], strategy
                )
        except Exception as exc:  # noqa: BLE001 - one task must not erase other observations.
            record_diagnostic_error(
                report, "task_team_observation", exc, level=level, quest_id=quest_id
            )
        if quest_id in completed and not task.get("independent_check_attempted"):
            task["independent_check_attempted"] = True
            try:
                # This can execute L3 holdout code and must never be retried by
                # the read-only SQLite policy above.
                task["independent_check"] = scenarios.verify_artifact(workspace, quest_id, level)
                report["checks"]["artifact_" + level] = task["independent_check"]["passed"]
                if level == "L3":
                    task["test_receipt_check"] = verify_development_receipts(
                        database, workspace, quest_id
                    )
                    receipt_check = task["test_receipt_check"]
                    same_artifacts = all(
                        receipt_check.get(key) == task["independent_check"].get(key)
                        for key in ("candidate_sha256", "artifact_sha256")
                    )
                    receipt_check["checks"]["same_independently_checked_artifacts"] = same_artifacts
                    receipt_check["passed"] = receipt_check["passed"] and same_artifacts
                    report["checks"]["source_bound_tests_L3"] = receipt_check["passed"]
            except Exception as exc:  # noqa: BLE001 - continue to later completed tasks.
                record_diagnostic_error(
                    report, "task_artifact_observation", exc, level=level, quest_id=quest_id
                )


def shutdown_snapshot(database, *, deadline):
    """Read in-flight counters only; never change admission or replay outcomes."""
    database = Path(database).resolve()

    def read():
        with closing(
            sqlite3.connect(
                database.as_uri() + "?mode=ro",
                uri=True,
                timeout=min(1, max(0.01, deadline - time.monotonic())),
            )
        ) as c:
            c.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            c.execute("BEGIN")
            queries = {
                "model_calls": "SELECT COUNT(*) FROM model_budget_attempts",
                "provider_reserved": "SELECT COUNT(*) FROM model_budget_attempts WHERE state='reserved'",
                "provider_unknown": "SELECT COUNT(*) FROM model_budget_attempts WHERE state IN ('unknown','uncertain')",
                "worker_model_reserved": "SELECT COUNT(*) FROM worker_model_reservations WHERE state='reserved'",
                "tool_inflight": "SELECT COUNT(*) FROM action_intents WHERE state IN ('prepared','executing')",
                "tool_unknown": "SELECT COUNT(*) FROM action_intents WHERE state='uncertain'",
                "worker_tool_running": "SELECT COUNT(*) FROM worker_tool_calls WHERE state='running'",
                "worker_tool_unknown": "SELECT COUNT(*) FROM worker_tool_calls WHERE state='uncertain'",
                "active_workers": "SELECT COUNT(*) FROM worker_runs WHERE state IN ('queued','running','review_pending')",
            }
            return {key: c.execute(query).fetchone()[0] for key, query in queries.items()}

    return read_only(read, stage="shutdown_inflight_read", deadline=deadline)


def drain_before_stop(core, database, futures, report):
    """Observe already running work for <=120s Provider timeout plus 10s grace.

    This submits no API/Provider/tool calls. Unknown outcomes remain unknown.
    A timeout is explicit and never causes the experiment to be restarted.
    """
    began = time.monotonic()
    deadline = began + SHUTDOWN_DRAIN_SECONDS
    quiet, last = None, None
    errors = []
    for future in futures.values():
        future.cancel()  # Only not-yet-running work is cancelled.
    outcome = "drain_deadline_no_replay"
    while time.monotonic() < deadline:
        process = getattr(core, "process", None)
        if process is None or process.poll() is not None:
            outcome = "core_already_stopped_no_replay"
            break
        pending = sum(not future.done() for future in futures.values())
        if not errors:
            try:
                last = (
                    shutdown_snapshot(database, deadline=deadline)
                    if Path(database).exists()
                    else None
                )
            except Exception as exc:  # noqa: BLE001 - preserve shutdown accounting failure.
                errors.append(error_metadata(exc))
        if errors:
            # Exhausted read retries give no authority to assume settlement.
            # Keep the already running requests alive for the same fixed grace
            # period; do not create another unbounded series of read retries.
            outcome = "drain_read_unavailable_no_replay"
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
            continue
        if last is None and not pending:
            outcome = "no_database_or_pending_request"
            break
        inflight = last is None or any(
            last[key]
            for key in (
                "provider_reserved",
                "worker_model_reserved",
                "tool_inflight",
                "worker_tool_running",
                "active_workers",
            )
        )
        now = time.monotonic()
        if not pending and not inflight:
            signature = json.dumps(last, sort_keys=True)
            quiet = quiet if quiet is not None and quiet[0] == signature else (signature, now)
            if now - quiet[1] >= 2:
                unknown = any(
                    last[key] for key in ("provider_unknown", "tool_unknown", "worker_tool_unknown")
                )
                outcome = "drained_with_unknown_preserved_no_replay" if unknown else "drained"
                break
        else:
            quiet = None
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    record = {
        "provenance": "programmatic_host_shutdown_observation_no_new_work_or_replay",
        "limit_seconds": SHUTDOWN_DRAIN_SECONDS,
        "elapsed_seconds": round(time.monotonic() - began, 3),
        "outcome": outcome,
        "pending_futures": sum(not future.done() for future in futures.values()),
        "last_accounting": last,
    }
    if errors:
        record["read_errors"] = errors
    # Observe failed futures without allowing their secondary failures to erase
    # the first cause, and without exposing arbitrary exception messages.
    record["future_errors"] = []
    for level, future in futures.items():
        if future.done() and not future.cancelled():
            exc = future.exception()
            if exc is not None:
                record["future_errors"].append({"level": level, **error_metadata(exc)})
    report["shutdown_drain"] = record
    if outcome != "drained":
        report["status"] = "failed"


def finalize_observations(
    core, executor, database, report, *, task_count, observe_tasks=None, futures=None
):
    """Keep the original failure even when shutdown or diagnostic reads fail."""
    database = Path(database).resolve()

    if futures is not None:
        try:
            drain_before_stop(core, database, futures, report)
        except Exception as exc:  # noqa: BLE001 - first cause survives drain errors.
            record_diagnostic_error(report, "shutdown_drain", exc)

    for stage, operation in (
        ("core_stop", core.stop),
        ("executor_shutdown", lambda: executor.shutdown(wait=False, cancel_futures=True)),
    ):
        try:
            result = operation()
            if stage == "core_stop" and isinstance(result, dict):
                report["final_core_stop"] = result
                report["checks"]["final_graceful_stop_confirmed"] = (
                    result.get("graceful_confirmed") is True
                )
                if not report["checks"]["final_graceful_stop_confirmed"]:
                    report.setdefault("failure_code", "final_graceful_stop_unconfirmed_no_replay")
        except Exception as exc:  # noqa: BLE001 - preserve independent teardown failures.
            record_diagnostic_error(report, stage, exc)
    if database.exists():
        if observe_tasks is not None:
            try:
                observe_tasks()
            except Exception as exc:  # noqa: BLE001 - retain earlier task checks on failure.
                record_diagnostic_error(report, "task_observations", exc)
        try:
            report["metrics"] = read_only(
                lambda: metrics.read_metrics(database), stage="metrics_read"
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics must not replace the cause.
            record_diagnostic_error(report, "metrics", exc)
        try:
            state = snapshot(database)
            report["checks"]["final_calls_settled"] = all(
                item["state"] == "settled" for item in state["calls"]
            )
            report["checks"]["fixed_aggregate_calls"] = len(state["calls"]) <= 32 * task_count
            report["checks"]["fixed_admission_call_ceiling"] = len(state["calls"]) <= report.get(
                "limits", {}
            ).get("aggregate_calls", 32 * task_count)
        except Exception as exc:  # noqa: BLE001 - incomplete startup may lack schema.
            record_diagnostic_error(report, "final_snapshot", exc)
        else:
            try:
                # Recap and shutdown draining may settle calls after the loop's
                # last observation. Apply the same per-task ceilings again.
                check_budget(database, state, task_count)
                report["checks"]["final_per_task_budget"] = True
            except Exception as exc:  # noqa: BLE001 - retain first failure and final evidence.
                report["checks"]["final_per_task_budget"] = False
                record_diagnostic_error(report, "final_budget", exc)
    if "source_sha256" in report:
        report["checks"]["source_unchanged_after_drain"] = (
            report["source_sha256"] == source_digest()
        )
    if report.get("failure_code") or not all(report["checks"].values()):
        report["status"] = "failed"


def compact_and_recall(workspace, quest_ids):
    """Use existing transaction/hash-checked ContextService while Core is stopped."""
    store = Store(workspace=Workspace.from_path(workspace))
    compact = ContextService(store).compact_router(keep_recent=0)
    chain = MemoryService(store).recall_chain(
        ["quest:" + quest for quest in quest_ids], max_depth=3, max_nodes=64, max_bytes=65536
    )
    archives = rows(
        workspace / "data" / "forge.sqlite3",
        "SELECT id,source_hash,capsule_hash FROM context_archives",
    )
    return {
        "provenance": "programmatic_host_fault_injection_on_real_model_history",
        "compacted_source_count": compact["source_count"],
        "archives": archives,
        "recall_refs": [node["ref"] for node in chain["nodes"]],
        "recall_incomplete": chain["incomplete"],
        "source_hashes": [node["source_hash"] for node in chain["nodes"]],
    }


def scenario_levels(strategy):
    """Comparison selection changes tasks, never their original acceptance."""
    if strategy == "adaptive":
        return ["L1", "L2", "L3"]
    if strategy == "continuity":
        return ["L2", "L3"]
    if strategy == "development":
        return ["L3"]
    if strategy == "project":
        return ["L1"]
    if strategy in {"single", "fixed", "specialists"}:
        return ["L2"]
    raise ValueError("Unknown comparison strategy")


def run(strategy="adaptive", *, key, call_limit=None, operator_context_window=None):
    levels = scenario_levels(strategy)
    if call_limit is not None and (type(call_limit) is not int or call_limit < 1):
        raise ValueError("Invalid acceptance call limit")
    integrated = strategy in {"adaptive", "continuity"}
    run_id = (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + "-capability-"
        + strategy
        + "-"
        + secrets.token_hex(3)
    )
    workspace = REPO / ".local" / "superagent" / run_id
    workspace.mkdir(parents=True)
    descriptors = {level: scenarios.seed(workspace, level) for level in levels}
    env = live_core.provider_env_from_key(key)
    if call_limit is not None:
        env["FORGE_MODEL_CALL_LIMIT"] = str(call_limit)
    # Discovery never silently replaces an explicit operator declaration.
    # Context capacity remains separate from the unchanged task spend ceiling.
    with httpx.Client(trust_env=False, timeout=15) as client:
        response = client.get(
            env["FORGE_PROVIDER_BASE_URL"] + "/models", headers={"Authorization": "Bearer " + key}
        )
        if response.status_code != 200:
            raise Failure("provider_metadata_unavailable")
        declared = next(
            item for item in response.json()["data"] if item["id"] == live_core.MODEL_ID
        )
    context_metadata = resolve_context_declaration(declared, operator_context_window)
    context_window = context_metadata["context_window"]
    env["FORGE_ROUTER_CONTEXT_LIMIT"] = str(context_window)
    core = live_core.LiveCore(
        workspace,
        provider_env=env,
        token_limit=500000 * len(levels),
        concurrency=2,
        private_control=True,
    )
    database = workspace / "data" / "forge.sqlite3"
    report = {
        "run_id": run_id,
        "strategy": strategy,
        "scenario_levels": levels,
        "execution_mode": "live",
        "upstream_identity": "unverified_proxy_reported_identity",
        "context_metadata": context_metadata,
        "source_sha256": source_digest(),
        "limits": {
            "task_calls": 32,
            "task_tokens": 500000,
            "task_seconds": 900,
            "gateway_retries": 0,
            "aggregate_calls": min(call_limit or 32 * len(levels), 32 * len(levels)),
        },
        "status": "failed",
        "checks": {},
        "tasks": {},
        "approval_provenance": "trusted_host_test_authorization_for_exact_user_authorized_manifest_not_model_approval",
        "restart_boundary_policy": "Host restart only after every original Worker is quiescent "
        "and all calls/effects settle; L3 exact approval is held until L2 is completed, its "
        "current DAG is terminal and its reviewed completion reaches the original Router. "
        "The identical boundary is checked after confirmed private shutdown. This tests "
        "durable resumption, not interruption of active computation.",
    }
    read_observations = []
    read_observation_token = _READ_OBSERVATIONS.set(read_observations)
    prefix = run_id + "-"
    executor = ThreadPoolExecutor(max_workers=2)
    futures, resolved, delivered = {}, set(), set()
    thread_deliveries = []
    mapping = {}
    restarted = False
    began = time.monotonic()
    task_started = {}
    quiet = None
    stopped_quiet = None

    def api(method, path, payload=None):
        return core_api(core, method, path, payload)

    def submit(level):
        prompt = descriptors[level]["prompt"]
        if strategy == "single":
            prompt += (
                " Comparison condition: use exactly one general Worker; execution_team single."
            )
        elif strategy == "fixed":
            prompt += " Comparison condition: use exactly two fixed sequential specialists: Research then Architect integrator. The integrator reads the predecessor delivery and writes the final report."
        elif level == "L2":
            prompt += " This task requires at least two useful distinct specialist roles; choose responsibilities and topology dynamically. The final integrator owns research.json and must verify the full predecessor delivery."
        futures[level] = executor.submit(
            api,
            "POST",
            "/api/conversation/message",
            {"content": prompt, "request_id": prefix + level},
        )
        task_started[level] = time.monotonic()
        return futures[level]

    def observe_tasks():
        # A failure may happen between the Router commit and this loop's next
        # snapshot. Recover task identities from committed request IDs as well.
        mapping.update(task_mapping(snapshot(database), prefix))
        collect_task_results(database, workspace, mapping, descriptors, report, strategy)
        if thread_deliveries:
            observed = read_only(
                lambda: continuity_evidence.observe(
                    database, workspace, mapping, thread_deliveries
                ),
                stage="worker_continuity_evidence",
            )
            report["worker_continuity_evidence"] = observed
            report["checks"]["complete_thread_consumption"] = observed[
                "complete_thread_consumption"
            ]
        if integrated and restarted:
            post = [item for item in thread_deliveries if item["phase"] == "after_restart"]
            observed = read_only(
                lambda: continuity_evidence.observe(
                    database,
                    workspace,
                    mapping,
                    post,
                    after_evidence_rowid=report["continuity"]["restart_evidence_rowid"],
                ),
                stage="post_restart_worker_evidence",
            )
            report["post_restart_worker_evidence"] = observed
            report["checks"]["post_restart_complete_thread_consumption"] = observed[
                "complete_thread_consumption"
            ]
            report["checks"]["typed_multihop_recall"] = observed["typed_multihop_recall"]

    try:
        core.start(timeout=60)
        profile = deepcopy(live_core.MODEL_PROFILE)
        profile["context_window"] = context_window
        profile["provenance"]["context_note"] = context_metadata["source"]
        api("POST", "/api/models", profile)
        initial_router = snapshot(database)["router"]
        submit_in_order(levels, submit)
        while time.monotonic() - began < 900 * len(levels):
            if core.process.poll() is not None:
                raise Failure("core_exited")
            state = snapshot(database)
            check_budget(database, state, len(levels))
            mapping.update(task_mapping(state, prefix))
            for level, future in list(futures.items()):
                if future.done() and level not in resolved:
                    value = future.result()
                    if level not in mapping:
                        mapping.update(task_mapping(snapshot(database), prefix))
                    if value.get("intent") != "create_quest" or value.get(
                        "quest_id"
                    ) != mapping.get(level):
                        raise Failure("router_did_not_create_independent_task")
                    resolved.add(level)
                completed = any(
                    item["id"] == mapping.get(level) and item["status"] == "completed"
                    for item in state["quests"]
                )
                if (
                    level in task_started
                    and not completed
                    and time.monotonic() - task_started[level] > 900
                ):
                    raise Failure("per_task_deadline")
            # Exercise direct continuation during actual Worker ownership.
            for level, quest_id in list(mapping.items()):
                subscribed = {item["run_id"] for item in state["subscriptions"] if item["active"]}
                if level not in delivered and any(
                    w["quest_id"] == quest_id and w["id"] in subscribed for w in state["workers"]
                ):
                    before = sum(item["purpose"] == "router_trigger" for item in state["calls"])
                    trigger = api(
                        "POST",
                        "/api/triggers",
                        {
                            "source_id": "conversation",
                            "external_id": prefix + "followup-" + level,
                            "thread_key": quest_id,
                            "actor": "local_user",
                            "addressed": True,
                            "payload": {
                                "instruction": "Keep original scope and deliverables. Read the full continuation reference, following all remaining offset pages until complete; report only observed results.",
                                "verification_marker": "CAPABILITY_" + level,
                                "padding": "untrusted continuation data " * 80,
                            },
                        },
                    )
                    after = snapshot(database)
                    report["checks"]["pass_through_" + level] = (
                        trigger.get("status") == "passed_through"
                        and bool(trigger.get("deliveries"))
                        and sum(item["purpose"] == "router_trigger" for item in after["calls"])
                        == before
                    )
                    thread_deliveries.append(
                        {
                            "level": level,
                            "phase": "before_restart" if integrated else "task_followup",
                            "trigger_id": trigger["id"],
                            "run_ids": [item["run_id"] for item in trigger.get("deliveries", [])],
                        }
                    )
                    delivered.add(level)
            terminal = {item["id"] for item in state["quests"] if item["status"] == "completed"}
            approvals = approved_python_requests(
                database, workspace, {mapping["L3"]} if "L3" in mapping else set()
            )
            if (
                integrated
                and approvals
                and not restarted
                and all(f.done() for f in futures.values())
                and "L2" in mapping
                and restart_boundary(database, required_completed_quest_ids=(mapping["L2"],))
            ):
                # R2 approval wait is an explicit no-tool-running boundary. No
                # unknown call/effect is killed or replayed to fabricate recovery.
                stop_for_restart(
                    core,
                    database,
                    report,
                    required_completed_quest_ids=(mapping["L2"],),
                    workspace=workspace,
                    held_python_approvals=approvals,
                )
                report["continuity"] = compact_and_recall(workspace, list(mapping.values()))
                report["continuity"]["restart_evidence_rowid"] = rows(
                    database, "SELECT COALESCE(MAX(rowid),0) AS frontier FROM evidence"
                )[0]["frontier"]
                before_ids = {item["id"] for item in state["workers"]}
                core = live_core.LiveCore(
                    workspace,
                    provider_env=env,
                    token_limit=500000 * len(levels),
                    concurrency=2,
                    private_control=True,
                )
                core.start(timeout=60)
                after = snapshot(database)
                report["checks"]["restart_original_router_and_workers"] = after[
                    "router"
                ] == initial_router and before_ids <= {item["id"] for item in after["workers"]}
                report["checks"]["compaction_of_real_history"] = (
                    report["continuity"]["compacted_source_count"] > 0
                )
                report["continuity"]["host_typed_refs_observed"] = all(
                    "quest:" + quest in report["continuity"]["recall_refs"]
                    for quest in mapping.values()
                ) and any(ref.startswith("worker:") for ref in report["continuity"]["recall_refs"])
                restarted = True
                # Only this post-restart delivery asks the original coding Worker
                # to recover actual complete sources. Host refs above are not a
                # substitute for model-directed reads or Worker archive recovery.
                before = sum(item["purpose"] == "router_trigger" for item in after["calls"])
                trigger = api(
                    "POST",
                    "/api/triggers",
                    {
                        "source_id": "conversation",
                        "external_id": prefix + "after-restart-L3",
                        "thread_key": mapping["L3"],
                        "actor": "local_user",
                        "addressed": True,
                        "payload": {
                            "instruction": "Continue the original task with unchanged deliverables and permissions. "
                            "Read this entire continuation through all pages. Before delivery, use recall_object "
                            "from your existing superagent.original_request.reference; fully read that object "
                            "and follow two useful typed relations within your task/project, one at a time, "
                            "reading each complete object through all returned offsets. Prefer the short "
                            "request-to-project-to-current-task path when available. Each next reference "
                            "must come from the preceding actual tool result; do not guess object IDs or "
                            "repeat unrelated source files. Preserve original tool/model-step and spending "
                            "limits. Cite only actual observations in your normal evidence references.",
                            "verification_marker": "CAPABILITY_AFTER_RESTART_L3",
                        },
                    },
                )
                resumed = snapshot(database)
                report["checks"]["post_restart_thread_pass_through"] = (
                    trigger.get("status") == "passed_through"
                    and bool(trigger.get("deliveries"))
                    and sum(item["purpose"] == "router_trigger" for item in resumed["calls"])
                    == before
                )
                thread_deliveries.append(
                    {
                        "level": "L3",
                        "phase": "after_restart",
                        "trigger_id": trigger["id"],
                        "run_ids": [item["run_id"] for item in trigger.get("deliveries", [])],
                    }
                )
                approvals = approved_python_requests(database, workspace, {mapping["L3"]})
            # Keep the R2 pause until the declared restart boundary has been
            # observed. Authorizing early could consume the sole safe pause.
            for approval in approvals if not integrated or restarted else []:
                api(
                    "POST",
                    "/api/approvals/" + approval["id"] + "/decision",
                    {
                        "decision": "APPROVED",
                        "expected_digest": approval["parameter_digest"],
                        "expected_plan_revision": approval["plan_revision"],
                    },
                )
                report.setdefault("exact_python_approval_ids", []).append(approval["id"])
            failures = [w for w in state["workers"] if w["state"] in {"failed", "blocked"}]
            pending_ids = {a["quest_id"] for a in approvals}
            unrecoverable = [w for w in failures if w["quest_id"] not in pending_ids]
            if unrecoverable:
                # Allow the existing help/replacement/review machinery a bounded
                # event turn; this is not another model retry budget.
                report["blocked_observations"] = blocked_observations(database)
            done = len(resolved) == len(levels) and all(
                mapping[level] in terminal for level in levels
            )
            quiet, ready = baseline.quiescent_since(
                state, done and set(mapping.values()) <= reviewed_notices_for_tasks(state), quiet
            )
            if ready:
                break
            stopped = (
                unrecoverable
                and all(item["state"] == "settled" for item in state["calls"])
                and not any(w["state"] in {"running", "queued", "paused"} for w in state["workers"])
                and not approvals
            )
            stopped_quiet, stopped_ready = baseline.quiescent_since(
                state, bool(stopped), stopped_quiet
            )
            if stopped_ready:
                raise Failure("workers_stopped_without_progress")
            time.sleep(0.5)
        else:
            raise Failure("bounded_scenario_deadline")
        observe_tasks()
        intervals, maximum = baseline.running_intervals(state)
        report["worker_running_intervals"] = intervals
        report["max_parallel_ownership"] = maximum
        report["max_parallel_task_ownership"] = parallel_task_ownership(intervals, state["workers"])
        if integrated:
            report["checks"].update(verify_project_links(database, mapping, descriptors))
            report["checks"]["parallel_workers"] = maximum >= 2
            report["checks"]["parallel_independent_tasks"] = (
                report["max_parallel_task_ownership"] >= 2
            )
            report["checks"]["mid_scenario_restart_while_development_waited"] = restarted
            # After compaction, ask the actual Router to recover all tasks. IDs
            # are user-supplied recall keys, never fabricated model outputs.
            recap = api(
                "POST",
                "/api/conversation/message",
                {
                    "content": "Summarize the observed "
                    + ", ".join(levels)
                    + " results and actual artifact paths for "
                    + ", ".join(mapping.values())
                    + ". Use historical source references; do not create work or infer completion from intent.",
                    "request_id": prefix + "recap",
                },
            )
            text = recap.get("assistant_message", {}).get("content", "")
            report["checks"]["router_recap_after_compaction"] = recap.get("intent") in {
                "chat",
                "status_query",
            } and all(
                {"L1": "analysis.json", "L2": "research.json", "L3": "candidate.py"}[level] in text
                for level in levels
            )
            report["checks"]["subscription_pass_through_observed"] = all(
                report["checks"].get("pass_through_" + level, False) for level in levels
            )
        report["checks"]["final_results_original_router"] = set(
            mapping.values()
        ) <= reviewed_notices_for_tasks(state)
        report["checks"]["hidden_live_workers"] = all(
            item["hidden"] == 1 for item in state["hidden"]
        ) and all(item["execution_mode"] == "live" for item in state["workers"])
        report["checks"]["source_unchanged"] = report["source_sha256"] == source_digest()
        report["status"] = "passed" if all(report["checks"].values()) else "failed"
    except Exception as exc:  # noqa: BLE001 - no remote messages/logs/credentials are published.
        report.setdefault(
            "failure_code",
            str(exc) if isinstance(exc, (Failure, baseline.ProbeFailure)) else type(exc).__name__,
        )
        report.setdefault("primary_exception", error_metadata(exc))
    finally:
        finalize_observations(
            core,
            executor,
            database,
            report,
            task_count=len(levels),
            observe_tasks=observe_tasks,
            futures=futures,
        )
        if read_observations:
            report["sqlite_read_observations"] = read_observations
        _READ_OBSERVATIONS.reset(read_observation_token)
        report["elapsed_seconds"] = round(time.monotonic() - began, 3)
        output = REPO / "docs" / "audit" / "superagent" / (run_id + ".json")
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "report": str(output),
                    "failure_code": report.get("failure_code"),
                    "model_calls": report.get("metrics", {}).get("model_calls"),
                }
            )
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy",
        choices=[
            "adaptive",
            "continuity",
            "single",
            "fixed",
            "specialists",
            "development",
            "project",
        ],
        default="adaptive",
    )
    args = parser.parse_args()
    key = os.environ.get("FORGE_PROVIDER_KEY_LIVE_TEST")
    if not key:
        parser.error("Provider environment credential missing")
    return 0 if run(args.strategy, key=key)["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
