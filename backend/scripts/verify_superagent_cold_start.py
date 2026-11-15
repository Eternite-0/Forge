"""Bounded live acceptance of timer cold-start and adaptive independent Workers.

Credentials come only from the named environment variable. Reports contain
allowlisted facts and hashes; local workspace evidence remains private.
"""

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from verify_superagent import (
    REPO,
    ProbeFailure,
    call,
    final_accounting,
    guard_bounds,
    live_core,
    notices_for_tasks,
    quiescent_since,
    running_intervals,
    snapshot,
    source_digest,
    verified_artifact,
)


def run(args):
    credential = os.environ.get(args.credential_env)
    if not credential:
        raise ProbeFailure("provider_credential_environment_missing")
    identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-cold-" + secrets.token_hex(3)
    workspace = REPO / ".local" / "superagent" / identifier
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True)
    for label, value in (("alpha", "ALPHA_42"), ("beta", "BETA_56")):
        (inputs / f"{label}.txt").write_text(value + "\n", encoding="utf-8")
    watch_path = inputs / "heartbeat.txt"
    watch_path.write_text("Telemetry heartbeat: healthy. No work requested.\n", encoding="utf-8")
    provider_env = live_core.provider_env_from_key(credential)
    provider_env.update(
        {
            "FORGE_PROVIDER_BASE_URL": args.base_url,
            "FORGE_PROVIDER_CREDENTIAL_REF": args.credential_env,
            args.credential_env: credential,
            "FORGE_PROVIDER_TIMEOUT_SECONDS": "120",
            "FORGE_PROVIDER_MAX_RETRIES": "0",
        }
    )
    live_core.MODEL_ID = args.model
    profile = deepcopy(live_core.MODEL_PROFILE)
    profile.update(id=args.model, model_id=args.model)
    core = live_core.LiveCore(
        workspace,
        provider_env=provider_env,
        token_limit=args.token_limit,
        concurrency=2,
    )
    database = workspace / "data" / "forge.sqlite3"
    report = {
        "run_id": identifier,
        "execution_mode": "live",
        "model_id": args.model,
        "upstream_identity": "unverified_proxy_reported_identity",
        "capability_source": "existing_exploratory_operator_priors_not_benchmark",
        "source_sha256": source_digest(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checks": {},
        "status": "failed",
        "max_calls": args.max_calls,
    }
    started = time.monotonic()
    max_running = 0
    state = None
    quiet = None
    try:
        core.start(timeout=60)
        call(core, "POST", "/api/models", profile)
        call(
            core,
            "POST",
            "/api/triggers/sources",
            {
                "source_id": "scheduled_work",
                "kind": "timer",
                "mode": "candidate",
            },
        )
        call(
            core,
            "POST",
            "/api/triggers/sources",
            {
                "source_id": "file_telemetry",
                "kind": "file",
                "mode": "candidate",
            },
        )
        call(
            core,
            "POST",
            "/api/triggers/file-watches",
            {
                "source_id": "file_telemetry",
                "path": "inputs/heartbeat.txt",
                "thread_key": "health-telemetry",
            },
        )
        call(
            core,
            "POST",
            "/api/triggers/schedules",
            {
                "source_id": "scheduled_work",
                "thread_key": "two-independent-deliverables",
                "due_at": time.time(),
                "payload": {
                    "request": (
                        "This is an operator-scheduled independent work request. Dispatch ONE task "
                        "with TWO independent Workers using independent_dispatches: one coding Worker "
                        "reads inputs/alpha.txt, writes alpha-report.txt under the task write_scope and "
                        "reads it back, preserving ALPHA_42; one research Worker reads inputs/beta.txt, "
                        "writes beta-report.txt under write_scope and reads it back, preserving BETA_56. "
                        "The assignments are independent, no DAG edges or shared output files. Each "
                        "Worker reports only its assigned result; summarize both in the one conversation."
                    )
                },
            },
        )
        watch_path.write_text(
            "Telemetry heartbeat updated: healthy. Informational only; no task or action requested.\n",
            encoding="utf-8",
        )
        while time.monotonic() - started < args.timeout:
            if core.process.poll() is not None:
                raise ProbeFailure("isolated_core_exited")
            state = snapshot(database)
            guard_bounds(state, args)
            max_running = max(max_running, sum(w["state"] == "running" for w in state["workers"]))
            if any(e["event_type"] == "review.blocked" for e in state["events"]):
                raise ProbeFailure("independent_review_blocked")
            if any(w["state"] in {"blocked", "failed"} for w in state["workers"]):
                raise ProbeFailure("worker_blocked_or_failed")
            completed = {q["id"] for q in state["quests"] if q["status"] == "completed"}
            quiet, ready = quiescent_since(
                state, (len(completed) == 1 and completed <= notices_for_tasks(state)), quiet
            )
            if ready:
                break
            time.sleep(0.5)
        else:
            raise ProbeFailure("bounded_acceptance_timeout")
        quests = state["quests"]
        report["checks"]["cold_event_created_one_task"] = len(quests) == 1
        quest_id = quests[0]["id"]
        report["checks"]["adaptive_two_hidden_workers"] = len(state["workers"]) == 2 and all(
            w["hidden"] == 1 for w in state["hidden"]
        )
        intervals, actual_parallel = running_intervals(state)
        report["worker_running_intervals"] = intervals
        report["max_running_workers_from_events"] = actual_parallel
        report["checks"]["parallel_workers_observed"] = actual_parallel == 2
        report["checks"]["both_independent_artifacts_verified"] = all(
            verified_artifact(state, workspace, quest_id, marker)
            for marker in ("ALPHA_42", "BETA_56")
        )
        report["checks"]["router_delivered_event_result"] = quest_id in notices_for_tasks(state)
        with closing(sqlite3.connect(database)) as connection:
            connection.row_factory = sqlite3.Row
            triggers = [
                dict(row)
                for row in connection.execute(
                    "SELECT source_id,status,quest_id FROM superagent_triggers"
                )
            ]
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata FROM quests WHERE id=?",
                    (quest_id,),
                ).fetchone()[0]
            )
            mandate = connection.execute(
                "SELECT m.source_sha256,t.payload_sha256,t.payload_json FROM delegation_mandates m "
                "JOIN superagent_triggers t ON t.id=m.source_trigger_id WHERE m.quest_id=?",
                (quest_id,),
            ).fetchone()
            team = json.loads(
                connection.execute(
                    "SELECT body_json FROM team_proposals WHERE quest_id=? AND status='approved'",
                    (quest_id,),
                ).fetchone()[0]
            )
        report["checks"]["two_router_assignments_persisted"] = (
            len(metadata.get("independent_dispatches", [])) == 2
        )
        report["checks"]["two_role_contracts_and_selected_models"] = (
            len(team["members"]) == 2
            and len({member["role_id"] for member in team["members"]}) == 2
            and all(member["model_id"] == args.model for member in team["members"])
        )
        report["checks"]["unchanged_original_mandate_source_hash"] = (
            mandate is not None
            and mandate["source_sha256"]
            == mandate["payload_sha256"]
            == hashlib.sha256(mandate["payload_json"].encode("utf-8")).hexdigest()
        )
        report["checks"]["timer_dispatched_by_slow_path"] = any(
            t["source_id"] == "scheduled_work"
            and t["status"] == "routed"
            and t.get("quest_id") == quest_id
            for t in triggers
        )
        report["checks"]["file_noise_ignored_by_slow_path"] = any(
            t["source_id"] == "file_telemetry" and t["status"] == "ignored" for t in triggers
        )
        report["checks"]["actual_router_trigger_inference"] = any(
            item["purpose"] == "router_trigger" for item in state["calls"]
        )
        recall = call(core, "GET", f"/api/recall?reference=quest:{quest_id}")
        report["checks"]["typed_recall_links_to_execution"] = any(
            item.get("ref", "").startswith(("run:", "worker:", "plan:"))
            for item in recall.get("relations", [])
        )
        report["checks"]["no_unsettled_calls"] = all(
            item["state"] == "settled" for item in state["calls"]
        )
        report["checks"]["source_snapshot_unchanged"] = source_digest() == report["source_sha256"]
        report["status"] = "passed" if all(report["checks"].values()) else "failed"
    except Exception as exc:  # noqa: BLE001 - publish no remote text.
        report["failure_code"] = str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__
    finally:
        core.stop()
        if database.exists():
            state = snapshot(database)
            final_accounting(report, state)
            report["model_calls"] = len(state["calls"])
            report["charged_tokens"] = sum(
                (item["input_charged"] or 0) + (item["output_charged"] or 0)
                for item in state["calls"]
            )
            report["admission_states"] = {
                value: sum(item["state"] == value for item in state["calls"])
                for value in sorted({item["state"] for item in state["calls"]})
            }
        report["max_running_workers_observed"] = max_running
        report["seconds"] = round(time.monotonic() - started, 3)
        output = REPO / "docs" / "audit" / "superagent" / f"{identifier}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "report": str(output),
                    "failure_code": report.get("failure_code"),
                }
            )
        )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-env", default="FORGE_PROVIDER_KEY_LIVE_TEST")
    parser.add_argument("--base-url", default="http://127.0.0.1:8788/v1")
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--max-calls", type=int, default=32)
    parser.add_argument("--token-limit", type=int, default=500000)
    parser.add_argument("--timeout", type=int, default=900)
    arguments = parser.parse_args()
    if not 4 <= arguments.max_calls <= 64 or not 1 <= arguments.timeout <= 1800:
        parser.error("Invalid bounded acceptance limits")
    raise SystemExit(run(arguments))
