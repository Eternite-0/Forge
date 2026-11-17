"""Opt-in real Core/model failover experiment with one labelled HTTP fault.

Importing this module performs no network requests. The driver never mutates
historical workspaces, Provider files, model usage or Worker state. The proxy
forwards real requests unchanged, except for one ledger-bound injected 503.
"""

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from verify_superagent import REPO, live_core, snapshot, source_digest
from verify_superagent_capability import resolve_context_declaration, reviewed_notices_for_tasks

from forge_core.worker_model_usage import completed_gateway_rejection_proof

PRIMARY = "deepseek-v4.1-flash"
ALTERNATIVE = "glm-5.3-flash"
UPSTREAM = "http://127.0.0.1:8788/v1"
CALL_LIMIT, TOKEN_LIMIT, WALL_LIMIT = 16, 100000, 300
DRAIN_LIMIT, STOP_RESERVE = 90, 32
MARKER = "FORGE_RECOVERED_42"


def read_rows(database, sql, parameters=()):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        return [dict(row) for row in c.execute(sql, parameters)]


def injection_target(database, model):
    """Global inference concurrency is one; never infer purpose from prompt text."""
    if model != PRIMARY or not Path(database).is_file():
        return None
    try:
        active = read_rows(
            database,
            "SELECT attempt_id,purpose,worker_run_id,model_id,provider_id "
            "FROM model_budget_attempts WHERE state='reserved'",
        )
    except sqlite3.Error:
        return None
    if len(active) != 1:
        return None
    candidate = active[0]
    if (
        candidate["purpose"] != "worker"
        or not candidate["worker_run_id"]
        or candidate["model_id"] != PRIMARY
        or candidate["provider_id"] != live_core.PROVIDER_ID
    ):
        return None
    return candidate


class OneShotFaultProxy:
    """Ephemeral loopback forwarding proxy; it never resolves or logs credentials."""

    def __init__(self, database, *, upstream=UPSTREAM, credential=None):
        parsed = urlsplit(upstream)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/v1"
        ):
            raise ValueError("Only the explicit loopback v1 upstream is supported")
        self.database, self.upstream = Path(database), upstream
        self._expected_authorization = None if credential is None else "Bearer " + credential
        self.injections, self.forward_errors = [], []
        self.forwarded = 0
        self._lock = threading.Lock()
        self.server = self.thread = None

    def select_fault(self, body, path):
        if path != "/v1/chat/completions":
            return None
        try:
            model = json.loads(body).get("model")
        except (ValueError, AttributeError):
            return None
        with self._lock:
            if self.injections:
                return None
            target = injection_target(self.database, model)
            if target is None:
                return None
            record = {
                **target,
                "http_status": 503,
                "provenance": "intentional_host_fault_injection_not_natural_provider_failure",
                "forwarded_upstream": False,
                "request_sha256": hashlib.sha256(body).hexdigest(),
            }
            self.injections.append(record)
            return record

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Includes paths/headers; never write credentials or model text.

            def handle_request(self):
                if owner._expected_authorization is not None and not secrets.compare_digest(
                    self.headers.get("Authorization", ""), owner._expected_authorization
                ):
                    self.send_error(401)
                    return
                if self.path not in {"/v1/models", "/v1/chat/completions"}:
                    self.send_error(404)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400)
                    return
                if not 0 <= size <= 4 * 1024 * 1024:
                    self.send_error(413)
                    return
                body = self.rfile.read(size)
                if owner.select_fault(body, self.path) is not None:
                    payload = b'{"error":{"code":"intentional_test_503"}}'
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                try:
                    # Only the authenticated request's existing credential is
                    # forwarded to the named local upstream; no key is stored.
                    headers = {
                        name: self.headers[name]
                        for name in ("Authorization", "Content-Type")
                        if name in self.headers
                    }
                    with httpx.Client(
                        trust_env=False, timeout=60, follow_redirects=False
                    ) as client:
                        response = client.request(
                            self.command,
                            owner.upstream + self.path.removeprefix("/v1"),
                            headers=headers,
                            content=body,
                        )
                    with owner._lock:
                        owner.forwarded += 1
                    self.send_response(response.status_code)
                    self.send_header(
                        "Content-Type", response.headers.get("content-type", "application/json")
                    )
                    self.send_header("Content-Length", str(len(response.content)))
                    self.end_headers()
                    self.wfile.write(response.content)
                except (httpx.HTTPError, OSError) as exc:
                    with owner._lock:
                        owner.forward_errors.append(type(exc).__name__)
                    # An interrupted upstream request has an unknown outcome.
                    # Close the client connection, preserving Gateway's unknown
                    # reservation; never fabricate an HTTP rejection or usage.
                    self.close_connection = True

            do_GET = handle_request
            do_POST = handle_request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self

    def __exit__(self, *_args):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)


def registry_profiles(declared):
    profiles = []
    for index, model_id in enumerate((PRIMARY, ALTERNATIVE)):
        model = next((item for item in declared if item.get("id") == model_id), None)
        if model is None:
            raise ValueError("Both actual upstream model identities are required")
        context = resolve_context_declaration(model, 100000000 if model_id == PRIMARY else None)
        profile = deepcopy(live_core.MODEL_PROFILE)
        profile.update(
            id=model_id,
            model_id=model_id,
            display_name=model_id,
            context_window=context["context_window"],
            max_output_tokens=min(8192, model.get("max_output_tokens", 8192)),
        )
        for measurement in profile["capability_measurements"].values():
            measurement.update(
                value=max(0, measurement["value"] - index * 10),
                source="operator_probe_eligibility_prior_unmeasured",
                n_samples=None,
            )
        profile["provenance"] = {
            "context_declaration": context,
            "capability_source": "operator_probe_eligibility_prior_unmeasured",
            "selection_condition": "primary_prior_above_alternative_for_fault_probe_only",
            "protocol_source": "operator_probe_eligibility_not_verified_tool_success",
        }
        profiles.append(profile)
    return profiles


def failover_observations(
    database, workspace, quest_id, injected, initial_router, *, source_bytes=None
):
    state = snapshot(database)
    runs = read_rows(database, "SELECT id,quest_id,model_id,execution_mode,state FROM worker_runs")
    attempts = read_rows(
        database,
        "SELECT attempt_id,purpose,model_id,state,input_charged,output_charged "
        "FROM model_budget_attempts ORDER BY rowid",
    )
    local_reserved = read_rows(
        database,
        "SELECT call_id,reserved_tokens,reserved_cost_microunits "
        "FROM worker_model_reservations WHERE state='reserved'",
    )
    known_rejections, unresolved_local = [], []
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        for reservation in local_reserved:
            proof = completed_gateway_rejection_proof(c, reservation["call_id"])
            if (
                proof is not None
                and proof["attempt_id"] == injected.get("attempt_id")
                and proof["run_id"] == injected.get("worker_run_id")
            ):
                known_rejections.append({**proof, **reservation})
            else:
                unresolved_local.append(reservation)
    replacements = [
        run
        for run in runs
        if run["quest_id"] == quest_id
        and run["id"] != injected.get("worker_run_id")
        and run["model_id"] == ALTERNATIVE
    ]
    events = [(event, json.loads(event["payload"])) for event in state["events"]]
    reassigned = any(
        event["event_type"] == "superagent.expert_reassigned"
        and payload.get("source_run_id") == injected.get("worker_run_id")
        and payload.get("kind") == "model"
        for event, payload in events
    )
    completed = any(
        quest["id"] == quest_id and quest["status"] == "completed" for quest in state["quests"]
    )
    passed = any(
        event["event_type"] == "review.result"
        and payload.get("verdict") == "PASS"
        and payload.get("run_id") in {run["id"] for run in replacements}
        for event, payload in events
    )
    path = workspace / "work" / quest_id / "recovered.txt"
    # Capture source_bytes before Core starts. Never accept a platform-specific
    # hardcoded newline or rebaseline a source modified during execution.
    source = workspace / "inputs/value.txt"
    source_digest = (
        hashlib.sha256(source_bytes).hexdigest() if isinstance(source_bytes, bytes) else None
    )
    input_unchanged = (
        source_digest is not None and source.is_file() and source.read_bytes() == source_bytes
    )
    correct_bytes = input_unchanged and path.is_file() and path.read_bytes() == source_bytes
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    receipt_ok = correct_bytes and any(
        write["run_id"] in {run["id"] for run in replacements}
        and write["tool_name"] in {"file_write", "file_revise"}
        and Path(write["resource_scope"]).resolve() == path.resolve()
        and write["execution_mode"] == "live"
        and json.loads(write["result_json"]).get("sha256") == source_digest == digest
        and json.loads(write["result_json"]).get("bytes") == len(source_bytes)
        and any(
            source_read["run_id"] == write["run_id"]
            and source_read["tool_name"] == "file_read"
            and source_read["execution_mode"] == "live"
            and Path(source_read["resource_scope"]).resolve() == source.resolve()
            and json.loads(source_read["result_json"]).get("sha256") == source_digest
            and json.loads(source_read["result_json"]).get("content", "").encode("utf-8")
            == source_bytes
            for source_read in state["receipts"]
        )
        and any(
            read["run_id"] == write["run_id"]
            and read["tool_name"] == "file_read"
            and read["resource_scope"] == write["resource_scope"]
            and read["execution_mode"] == "live"
            and json.loads(read["result_json"]).get("content", "").encode("utf-8") == source_bytes
            and json.loads(read["result_json"]).get("sha256") == digest
            for read in state["receipts"]
        )
        for write in state["receipts"]
    )
    accounted = (
        all(
            attempt["state"] == "settled"
            or (
                attempt["attempt_id"] == injected.get("attempt_id")
                and attempt["state"] == "rejected"
                and bool(known_rejections)
                and attempt["input_charged"] is None
                and attempt["output_charged"] is None
            )
            for attempt in attempts
        )
        and not unresolved_local
    )
    unknown_tools = read_rows(
        database,
        "SELECT 1 FROM action_intents WHERE state='uncertain' "
        "UNION ALL SELECT 1 FROM worker_tool_calls WHERE state='uncertain' LIMIT 1",
    )
    return {
        "checks": {
            "native_model_reassignment": reassigned,
            "alternative_model_completed_real_worker": bool(replacements)
            and all(
                run["state"] == "succeeded" and run["execution_mode"] == "live"
                for run in replacements
            ),
            "real_artifact_matches_exact_acceptance": correct_bytes,
            "input_unchanged": input_unchanged,
            "replacement_source_bound_write_and_read_receipts": receipt_ok,
            "independent_reviewer_pass": passed,
            "quest_completed": completed,
            "same_original_router_delivered_result": state["router"] == initial_router
            and quest_id in reviewed_notices_for_tasks(state),
            "all_noninjected_attempts_settled": accounted,
            "known_rejection_reservation_retained_without_refund": len(known_rejections) == 1,
            "no_unknown_tool_outcomes": not unknown_tools,
        },
        "injected_attempts": [
            attempt for attempt in attempts if attempt["attempt_id"] == injected.get("attempt_id")
        ],
        "real_upstream_attempts": [
            attempt for attempt in attempts if attempt["attempt_id"] != injected.get("attempt_id")
        ],
        "known_rejection_usage_unknown": known_rejections,
        "worker_models": [
            {key: run[key] for key in ("id", "model_id", "state", "execution_mode")} for run in runs
        ],
        "artifact_sha256": digest,
        "source_artifact_binding": {
            "source_path": "inputs/value.txt",
            "baseline_sha256": source_digest,
            "baseline_bytes": len(source_bytes) if isinstance(source_bytes, bytes) else None,
            "artifact_sha256": digest,
        },
        "local_reserved_count": len(local_reserved),
        "unresolved_other_local_reservations": len(unresolved_local),
    }


def inflight_snapshot(database, injected):
    """Read-only operation status; a known rejection is never usage settlement."""
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN")
        count = lambda query: c.execute(query).fetchone()[0]
        local = c.execute(
            "SELECT call_id FROM worker_model_reservations WHERE state='reserved'"
        ).fetchall()
        unresolved, known = 0, 0
        for row in local:
            proof = completed_gateway_rejection_proof(c, row["call_id"])
            if (
                proof
                and proof["attempt_id"] == injected.get("attempt_id")
                and proof["run_id"] == injected.get("worker_run_id")
            ):
                known += 1
            else:
                unresolved += 1
        return {
            "model_calls": count("SELECT count(*) FROM model_budget_attempts"),
            "provider_reserved": count(
                "SELECT count(*) FROM model_budget_attempts WHERE state='reserved'"
            ),
            "provider_unknown": count(
                "SELECT count(*) FROM model_budget_attempts WHERE state='unknown'"
            ),
            "worker_model_unresolved": unresolved,
            "known_rejection_usage_unknown_reservations": known,
            "tool_inflight": count(
                "SELECT count(*) FROM action_intents WHERE state IN ('prepared','executing')"
            ),
            "tool_unknown": count("SELECT count(*) FROM action_intents WHERE state='uncertain'"),
            "worker_tool_inflight": count(
                "SELECT count(*) FROM worker_tool_calls WHERE state='running'"
            ),
            "worker_tool_unknown": count(
                "SELECT count(*) FROM worker_tool_calls WHERE state='uncertain'"
            ),
            "active_workers": count(
                "SELECT count(*) FROM worker_runs WHERE state IN ('queued','running','review_pending')"
            ),
            "router_pending": count(
                "SELECT count(*) FROM event_inbox WHERE consumer_id='router' AND processed_at IS NULL AND notice_dead_at IS NULL"
            ),
            "router_dead": count(
                "SELECT count(*) FROM event_inbox WHERE consumer_id='router' AND notice_dead_at IS NOT NULL"
            ),
        }


def no_pending_operations(counters):
    return not any(
        counters[name]
        for name in (
            "provider_reserved",
            "worker_model_unresolved",
            "tool_inflight",
            "worker_tool_inflight",
            "active_workers",
            "router_pending",
        )
    )


def drain_before_stop(core, database, future, proxy, began):
    """Observe existing Core work only; no retry, new event or database write."""
    started = time.monotonic()
    deadline = min(started + DRAIN_LIMIT, began + WALL_LIMIT - STOP_RESERVE)
    quiet, previous, counters, failures = None, None, None, []
    outcome = "deadline_preserving_unknown_no_replay"
    while time.monotonic() < deadline:
        if core.process is None or core.process.poll() is not None:
            outcome = "core_already_stopped"
            break
        if len(failures) >= 3:
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
            continue
        try:
            counters = inflight_snapshot(database, proxy.injections[0] if proxy.injections else {})
        except sqlite3.Error as exc:
            failures.append(type(exc).__name__)
            quiet = None
            continue
        signature = json.dumps(counters, sort_keys=True)
        if no_pending_operations(counters) and (future is None or future.done()):
            if signature != previous:
                quiet, previous = time.monotonic(), signature
            if quiet is not None and time.monotonic() - quiet >= 2:
                unknown = any(
                    counters[name]
                    for name in (
                        "provider_unknown",
                        "tool_unknown",
                        "worker_tool_unknown",
                        "router_dead",
                    )
                )
                outcome = "drained_with_unknown_preserved" if unknown else "quiescent"
                break
        else:
            quiet, previous = None, None
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    return {
        "provenance": "programmatic_host_read_only_drain_no_new_work_or_replay",
        "max_seconds": DRAIN_LIMIT,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "outcome": outcome,
        "last_counters": counters,
        "read_error_types": failures,
    }


def run(*, key):
    identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-failover-" + secrets.token_hex(3)
    workspace = REPO / ".local" / "superagent" / identifier
    workspace.mkdir(parents=True, exist_ok=False)
    source = workspace / "inputs/value.txt"
    source.parent.mkdir()
    source.write_text(MARKER + "\n", encoding="utf-8")
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    database = workspace / "data/forge.sqlite3"
    report = {
        "run_id": identifier,
        "status": "failed",
        "execution_mode": "live",
        "experiment": "intentional_single_http_503_model_replacement_not_natural_failure",
        "upstream_identity": "unverified_proxy_reported_identity",
        "limits": {
            "http_attempts": CALL_LIMIT,
            "tokens": TOKEN_LIMIT,
            "seconds": WALL_LIMIT,
            "retries": 0,
        },
        "source_sha256": source_digest(),
        "checks": {},
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "comparison_scope": "one_failover_condition_no_claim_of_optimal_model_or_team",
    }
    core = None
    executor = ThreadPoolExecutor(max_workers=1)
    future = None
    quiet = None
    began = time.monotonic()
    quest_id = initial_router = None
    with OneShotFaultProxy(database, credential=key) as proxy:
        try:
            with httpx.Client(trust_env=False, timeout=15, follow_redirects=False) as client:
                discovery = client.get(
                    proxy.base_url + "/models", headers={"Authorization": "Bearer " + key}
                )
                discovery.raise_for_status()
                profiles = registry_profiles(discovery.json()["data"])
            report["model_context_declarations"] = {
                profile["model_id"]: profile["provenance"]["context_declaration"]
                for profile in profiles
            }
            env = live_core.provider_env_from_key(key)
            env.update(
                FORGE_PROVIDER_BASE_URL=proxy.base_url,
                FORGE_MODEL_CALL_LIMIT=str(CALL_LIMIT),
                FORGE_PROVIDER_MAX_RETRIES="0",
                FORGE_PROVIDER_TIMEOUT_SECONDS="60",
                FORGE_ROUTER_CONTEXT_LIMIT=str(profiles[0]["context_window"]),
            )
            core = live_core.LiveCore(
                workspace, provider_env=env, token_limit=TOKEN_LIMIT, concurrency=1
            )
            core.start(timeout=45)
            for profile in profiles:
                result = core.call("POST", "/api/models", profile, timeout=15)
                if result["status"] != 200:
                    raise ValueError("Registry eligibility registration failed")
            initial_router = snapshot(database)["router"]
            prompt = (
                f'Work in local project "{workspace}". Use one coding Worker (single strategy). '
                "Read inputs/value.txt with a governed tool; write its exact bytes to "
                "work/<your actual quest_id>/recovered.txt and read the written file back. "
                "Do not modify inputs. Report the real paths and receipts in the original Router. "
                "Acceptance is exact byte preservation, source-bound write/read receipts and independent review. "
                "No Python, shell, network or additional expertise is required."
            )
            future = executor.submit(
                core.call,
                "POST",
                "/api/conversation/message",
                {"content": prompt, "request_id": identifier},
                timeout=75,
            )
            while time.monotonic() - began < WALL_LIMIT - DRAIN_LIMIT - STOP_RESERVE:
                if core.process.poll() is not None:
                    raise ValueError("Core exited")
                if future.done():
                    response = future.result()
                    if (
                        response["status"] != 200
                        or response["body"].get("intent") != "create_quest"
                    ):
                        raise ValueError("Router did not create the task")
                    quest_id = response["body"].get("quest_id")
                attempts = read_rows(database, "SELECT state FROM model_budget_attempts")
                if any(row["state"] == "unknown" for row in attempts):
                    raise ValueError("Unknown model outcome preserved without replay")
                if quest_id and proxy.injections:
                    report.update(
                        failover_observations(
                            database,
                            workspace,
                            quest_id,
                            proxy.injections[0],
                            initial_router,
                            source_bytes=source_bytes,
                        )
                    )
                    counters = inflight_snapshot(database, proxy.injections[0])
                    ready = all(report["checks"].values()) and no_pending_operations(counters)
                    signature = (
                        len(attempts),
                        snapshot(database)["message_digest"],
                        json.dumps(counters, sort_keys=True),
                    )
                    if ready:
                        quiet = (
                            quiet
                            if quiet is not None and quiet[0] == signature
                            else (signature, time.monotonic())
                        )
                        if time.monotonic() - quiet[1] >= 2:
                            break
                    else:
                        quiet = None
                if len(attempts) >= CALL_LIMIT and not any(
                    row["state"] == "reserved" for row in attempts
                ):
                    raise ValueError("HTTP attempt ceiling reached")
                time.sleep(0.25)
            else:
                raise TimeoutError("Bounded failover observation expired")
            report["checks"]["input_unchanged"] = (
                hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
            )
            report["checks"]["exactly_one_known_fault"] = len(proxy.injections) == 1
            report["checks"]["no_forwarding_transport_failures"] = not proxy.forward_errors
            report["status"] = "passed" if all(report["checks"].values()) else "failed"
        except Exception as exc:  # noqa: BLE001 - do not publish exception/provider text.
            report["failure_type"] = type(exc).__name__
        finally:
            if future:
                future.cancel()
            if core:
                if database.is_file():
                    report["shutdown_drain"] = drain_before_stop(
                        core, database, future, proxy, began
                    )
                    if report["shutdown_drain"]["outcome"] != "quiescent":
                        report["status"] = "failed"
                core.stop()
            executor.shutdown(wait=False, cancel_futures=True)
            if database.is_file() and quest_id and proxy.injections:
                try:
                    final = failover_observations(
                        database,
                        workspace,
                        quest_id,
                        proxy.injections[0],
                        initial_router,
                        source_bytes=source_bytes,
                    )
                    final["checks"] = {**report["checks"], **final["checks"]}
                    report.update(final)
                    if not all(report["checks"].values()):
                        report["status"] = "failed"
                except Exception as exc:  # noqa: BLE001 - final evidence read only.
                    report["final_accounting_failure_type"] = type(exc).__name__
                    report["status"] = "failed"
            report["injected_faults"] = list(proxy.injections)
            report["forwarded_http_requests_including_discovery"] = proxy.forwarded
            report["forwarding_error_types"] = list(proxy.forward_errors)
            report["accounting_limitations"] = (
                "The injected rejection has unknown usage and retains its complete local reservation. "
                "Only its unique completed_gateway_rejection_proof distinguishes a completed operation "
                "from an unresolved call. Every other reserved/unknown outcome prevents acceptance. "
                "No host retry, usage invention, refund or state repair is performed."
            )
    report["elapsed_seconds"] = round(time.monotonic() - began, 3)
    output = REPO / "docs/audit/superagent" / (identifier + ".json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-env", default="FORGE_PROVIDER_KEY_LIVE_TEST")
    args = parser.parse_args()
    credential = os.environ.get(args.credential_env)
    if not credential:
        raise SystemExit("The named process credential is required; no credential file is read")
    report, output = run(key=credential)
    print(json.dumps({"status": report["status"], "report": str(output)}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
