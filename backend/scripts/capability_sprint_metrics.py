"""Read-only Sprint metrics and bounded model-probe API driver.

No credential files are read. The optional driver uses a fresh isolated Core and
the named process environment credential, and never edits an existing Provider.
JSON reports contain counters, hashes and typed references, never raw content.
"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "phase2"))

import live_core

MAX_CALLS = 16
TOKEN_LIMIT = 100000
TIMEOUT_SECONDS = 900
DIMENSIONS = ["mathematics", "coding", "research", "planning", "architecture", "design", "vision"]


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _counts(rows, key):
    return dict(sorted(Counter(row[key] for row in rows).items()))


def _ratio(passed, total):
    return {"passed": passed, "samples": total, "value": passed / total if total else None}


def read_metrics(database):
    """Read one consistent SQLite snapshot without creating/migrating the DB.

    A Core state of completed and receipt hash integrity are observations, not
    an independent task-content oracle. Gateway attempt rows have no live/mock
    column: non-Worker call provenance therefore remains explicitly unattributed.
    """
    path = Path(database).resolve()
    if not path.is_file():
        raise ValueError("Metrics database must already exist")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        c.execute("BEGIN")

        def rows(query):
            return [dict(row) for row in c.execute(query)]

        calls = rows("SELECT * FROM model_budget_attempts ORDER BY created_at,attempt_id")
        runs = rows(
            "SELECT id,quest_id,worker_id,state,execution_mode,repairs FROM worker_runs ORDER BY id"
        )
        quests = rows("SELECT id,status FROM quests ORDER BY id")
        tools = rows(
            "SELECT run_id,call_id,tool_name,state,receipt_id FROM worker_tool_calls ORDER BY run_id,call_id"
        )
        receipts = rows(
            "SELECT r.*,i.run_id,i.execution_mode AS intent_mode,i.parameter_digest,i.parameters_json "
            "FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id ORDER BY r.id"
        )
        events = rows("SELECT id,event_type,worker_id,payload,created_at FROM events ORDER BY seq")
        archives = c.execute("SELECT COUNT(*) FROM context_archives").fetchone()[0]
        core_reads = c.execute(
            "SELECT COUNT(*) FROM worker_core_source_reads WHERE state='succeeded'"
        ).fetchone()[0]
        subscriptions = c.execute("SELECT COUNT(*) FROM superagent_subscriptions").fetchone()[0]
        deliveries = c.execute("SELECT COUNT(*) FROM superagent_trigger_deliveries").fetchone()[0]
    by_run = {row["id"]: row for row in runs}
    known_usage = [
        row
        for row in calls
        if row["input_charged"] is not None and row["output_charged"] is not None
    ]
    currencies = {row["currency"] for row in calls}
    cost_known = (
        bool(calls)
        and all(row["cost_charged"] is not None for row in calls)
        and len(currencies) == 1
        and None not in currencies
    )
    event_payloads = [(row, json.loads(row["payload"])) for row in events]
    reviews = [
        (row, payload) for row, payload in event_payloads if row["event_type"] == "review.result"
    ]
    judged = [
        payload
        for _, payload in reviews
        if payload.get("error_code") is None
        and payload.get("verdict") in {"PASS", "NEEDS_REVISION", "BLOCKED"}
    ]
    repaired = [
        row
        for row, payload in event_payloads
        if row["event_type"] == "worker.progress" and payload.get("phase") == "self_repair"
    ]
    recovered = [
        (row, payload)
        for row, payload in event_payloads
        if row["event_type"] == "runtime.worker_recovered"
    ]
    integrity = [
        row["execution_mode"] == row["intent_mode"]
        and hashlib.sha256(row["result_json"].encode()).hexdigest() == row["result_digest"]
        and _hash(json.loads(row["parameters_json"])) == row["parameter_digest"]
        for row in receipts
    ]
    settled_tools = [row for row in tools if row["state"] in {"succeeded", "failed"}]
    attributed_modes = Counter(
        by_run[row["worker_run_id"]]["execution_mode"]
        if row["worker_run_id"] in by_run
        else "unattributed_non_worker"
        for row in calls
    )
    stamps = [datetime.fromisoformat(row["created_at"]).timestamp() for row in events]
    source_refs = ["model_attempt:" + row["attempt_id"] for row in calls]
    source_refs.extend("run:" + row["id"] for row in runs)
    source_refs.extend("receipt:" + row["id"] for row in receipts)
    source_refs.extend("event:" + row["id"] for row, _ in recovered + reviews)
    return {
        "provenance": "programmatic_read_only_core_evidence",
        "worker_execution_modes": _counts(runs, "execution_mode"),
        "gateway_attempt_modes_from_worker_scope": dict(sorted(attributed_modes.items())),
        "model_calls": len(calls),
        "model_calls_by_purpose": _counts(calls, "purpose"),
        "admission_states": _counts(calls, "state"),
        "provider_model_pairs": sorted({(row["provider_id"], row["model_id"]) for row in calls}),
        "charged_tokens": sum(row["input_charged"] + row["output_charged"] for row in known_usage)
        if len(known_usage) == len(calls)
        else None,
        "known_charged_tokens": sum(
            row["input_charged"] + row["output_charged"] for row in known_usage
        ),
        "unknown_usage_attempts": len(calls) - len(known_usage),
        "cost_microunits": sum(row["cost_charged"] for row in calls) if cost_known else None,
        "cost_currency": next(iter(currencies)) if cost_known else None,
        "observed_event_span_seconds": max(stamps) - min(stamps) if stamps else None,
        "workers_by_state": _counts(runs, "state"),
        "quests_by_state": _counts(quests, "status"),
        "core_completed_quest_rate": _ratio(
            sum(row["status"] == "completed" for row in quests), len(quests)
        ),
        "tool_calls": len(tools),
        "tool_states": _counts(tools, "state"),
        "tool_success_rate": _ratio(
            sum(row["state"] == "succeeded" for row in settled_tools), len(settled_tools)
        ),
        "repair_events": len(repaired),
        "worker_repair_counters": sum(row["repairs"] for row in runs),
        "review_attempts": len(reviews),
        "review_verdicts": dict(
            sorted(Counter(payload.get("verdict", "unknown") for _, payload in reviews).items())
        ),
        "judged_review_pass_rate": _ratio(
            sum(payload["verdict"] == "PASS" for payload in judged), len(judged)
        ),
        "review_infrastructure_errors": sum(
            payload.get("error_code") is not None for _, payload in reviews
        ),
        "recovery_events": len(recovered),
        "recovered_run_current_success_rate": _ratio(
            sum(
                by_run.get(payload.get("run_id"), {}).get("state") == "succeeded"
                for _, payload in recovered
            ),
            len(recovered),
        ),
        "receipt_integrity_rate": _ratio(sum(integrity), len(integrity)),
        "context_archive_count": archives,
        "core_source_read_count": core_reads,
        "subscription_count": subscriptions,
        "trigger_delivery_count": deliveries,
        "source_refs": source_refs[:64],
        "source_refs_omitted": max(0, len(source_refs) - 64),
        "snapshot_sha256": _hash(
            {
                "calls": calls,
                "runs": runs,
                "quests": quests,
                "tools": tools,
                "events": events,
                "receipts": receipts,
                "archives": archives,
                "core_reads": core_reads,
                "subscriptions": subscriptions,
                "deliveries": deliveries,
            }
        ),
        "limitations": "Core completion is not content correctness. Receipt integrity checks stored hashes and binding, not external truth. Recovery rate observes current terminal state after requeue, not causal efficacy. Event span is not CPU time. Unknown prices/call usage remain null.",
    }


def compare_rows(rows):
    """Compare predeclared rows without selecting a winner or imputing unknowns."""
    grouped = defaultdict(list)
    for row in rows:
        mode = row.get("execution_mode")
        if mode not in {"live", "mock", "programmatic"}:
            raise ValueError("Comparison rows require explicit execution provenance")
        strategy = row.get("strategy")
        if not isinstance(strategy, str) or not strategy:
            raise ValueError("Comparison strategy is required")
        grouped[(mode, strategy)].append(row)
    groups = []
    for (mode, strategy), samples in sorted(grouped.items()):
        outcomes = [row["success"] for row in samples if type(row.get("success")) is bool]
        record = {
            "execution_mode": mode,
            "strategy": strategy,
            "n_rows": len(samples),
            "task_success": _ratio(sum(outcomes), len(outcomes)),
            "unknown_outcomes": len(samples) - len(outcomes),
        }
        currencies = {
            row.get("cost_currency") for row in samples if row.get("cost_microunits") is not None
        }
        comparable_cost = len(currencies) == 1 and all(
            isinstance(value, str) and value for value in currencies
        )
        record["cost_currency"] = next(iter(currencies)) if comparable_cost else None
        for field in (
            "charged_tokens",
            "elapsed_seconds",
            "tool_calls",
            "rework_count",
            "recovery_success_rate",
            "evidence_completeness_rate",
            "cost_microunits",
        ):
            values = [
                row[field]
                for row in samples
                if type(row.get(field)) in {int, float}
                and math.isfinite(row[field])
                and row[field] >= 0
                and (field != "cost_microunits" or comparable_cost)
            ]
            record[field] = {
                "mean": sum(values) / len(values) if values else None,
                "n_known": len(values),
                "n_unknown": len(samples) - len(values),
            }
        groups.append(record)
    return {
        "groups": groups,
        "conclusion": "Exploratory small-sample observations only; no optimal strategy or causal advantage established. Live, Mock and programmatic groups remain separate.",
    }


def run_model_probes(workspace, model_ids, *, key):
    """Two models maximum, unchanged aggregate 16-call/100k-token/900s ceilings."""
    if not key or not 1 <= len(model_ids) <= 2 or len(set(model_ids)) != len(model_ids):
        raise ValueError("One or two distinct models and an environment credential are required")
    workspace = Path(workspace).resolve()
    if workspace.exists():
        raise ValueError("Model probe requires a new workspace; uncertain runs are never replayed")
    core = live_core.LiveCore(
        workspace,
        provider_env=live_core.provider_env_from_key(key),
        token_limit=TOKEN_LIMIT,
        concurrency=1,
    )
    database = workspace / "data" / "forge.sqlite3"
    report = {
        "execution_mode": "live",
        "upstream_identity": "unverified_proxy_reported_identity",
        "status": "failed",
        "status_scope": "measurement_driver_completion_not_all_probe_answers_correct",
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limits": {
            "max_calls": MAX_CALLS,
            "token_limit": TOKEN_LIMIT,
            "timeout_seconds": TIMEOUT_SECONDS,
        },
        "models": [],
        "checks": {},
    }
    deadline = time.monotonic() + TIMEOUT_SECONDS

    def api(method, path, payload=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("model_probe_deadline")
        result = core.call(method, path, payload, timeout=remaining)
        if result["status"] != 200:
            raise RuntimeError("model_probe_api_status_" + str(result["status"]))
        return result["body"]

    try:
        core.start()
        discovered = api("POST", "/api/model-intelligence/discover")
        report["discovered_model_count"] = len(discovered)
        available = {
            item["model_id"]
            for item in api("GET", "/api/models")
            if item["provider"] == live_core.PROVIDER_ID
        }
        if set(model_ids) - available:
            raise RuntimeError("requested_model_not_discovered")
        for model_id in model_ids:
            metrics = read_metrics(database)
            if metrics["model_calls"] + 6 > MAX_CALLS or any(
                state != "settled" for state in metrics["admission_states"]
            ):
                raise RuntimeError("model_probe_call_or_settlement_ceiling")
            profile = deepcopy(live_core.MODEL_PROFILE)
            profile.update(
                id="sprint-probe-" + hashlib.sha256(model_id.encode()).hexdigest()[:16],
                model_id=model_id,
                display_name=model_id,
            )
            profile["capability_measurements"] = {}
            api("POST", "/api/models", profile)
            result = api(
                "POST",
                "/api/model-intelligence/benchmark",
                {"profile_id": profile["id"], "dimensions": DIMENSIONS},
            )
            intelligence = api("GET", "/api/model-intelligence/" + profile["id"])
            report["models"].append(
                {
                    "profile_id": profile["id"],
                    "model_id": model_id,
                    "probe": result,
                    "intelligence": intelligence,
                }
            )
            if any(item["status"] == "provider_failed" for item in result["results"]):
                raise RuntimeError("model_probe_provider_failure")
        report["checks"]["all_requested_models_probed"] = len(report["models"]) == len(model_ids)
        report["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 - persist only fixed driver codes, never remote text.
        code = str(exc)
        report["failure_code"] = (
            code
            if code.startswith(("model_probe_", "requested_model_"))
            else "model_probe_driver_error"
        )
    finally:
        core.stop(graceful=True)
        if database.exists():
            report["metrics"] = read_metrics(database)
            report["checks"]["final_calls_settled"] = all(
                state == "settled" for state in report["metrics"]["admission_states"]
            )
            report["checks"]["fixed_call_ceiling"] = report["metrics"]["model_calls"] <= MAX_CALLS
            report["checks"]["fixed_token_ceiling"] = (
                report["metrics"]["unknown_usage_attempts"] == 0
                and report["metrics"]["known_charged_tokens"] <= TOKEN_LIMIT
            )
            if not all(report["checks"].values()):
                report["status"] = "failed"
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference = os.environ.get("FORGE_PROVIDER_CREDENTIAL_REF", "FORGE_PROVIDER_KEY_LIVE_TEST")
    key = os.environ.get(reference)
    if not key:
        parser.error("Named Provider credential is absent from process environment")
    report = run_model_probes(args.workspace, args.model, key=key)
    report["created_at"] = datetime.now(UTC).isoformat()
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "models": len(report["models"]),
                "output": str(args.output),
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
