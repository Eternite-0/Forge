"""Programmatic proxy/accounting checks; no Provider model is called."""

import hashlib
import importlib.util
import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_superagent_provider_failover import rejected_gateway_worker

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "failover_driver", SCRIPTS / "verify_superagent_failover.py"
)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def ledger(tmp_path, *, purpose="worker", model=driver.PRIMARY, concurrent=False):
    path = tmp_path / "proxy.sqlite3"
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE model_budget_attempts(attempt_id,purpose,worker_run_id,model_id,provider_id,state)"
        )
        c.execute(
            "INSERT INTO model_budget_attempts VALUES (?,?,?,?,?,'reserved')",
            ("attempt-primary", purpose, "run-primary", model, driver.live_core.PROVIDER_ID),
        )
        if concurrent:
            c.execute(
                "INSERT INTO model_budget_attempts VALUES (?,?,?,?,?,'reserved')",
                ("attempt-other", "router", None, model, driver.live_core.PROVIDER_ID),
            )
    return path


def test_fault_is_exactly_once_and_bound_to_one_native_worker_attempt(tmp_path):
    proxy = driver.OneShotFaultProxy(ledger(tmp_path))
    body = json.dumps({"model": driver.PRIMARY, "messages": [{"content": "untrusted"}]}).encode()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda _: proxy.select_fault(body, "/v1/chat/completions"), range(16))
        )
    assert sum(result is not None for result in results) == 1
    assert proxy.injections[0]["attempt_id"] == "attempt-primary"
    assert proxy.injections[0]["worker_run_id"] == "run-primary"
    assert proxy.injections[0]["forwarded_upstream"] is False
    assert "untrusted" not in json.dumps(proxy.injections)


@pytest.mark.parametrize(
    "case", ["router", "other_model", "concurrent", "wrong_route", "malformed"]
)
def test_prompt_shape_or_ambiguous_ledger_never_authorizes_fault(tmp_path, case):
    path = ledger(
        tmp_path,
        purpose="router" if case == "router" else "worker",
        model=driver.ALTERNATIVE if case == "other_model" else driver.PRIMARY,
        concurrent=case == "concurrent",
    )
    proxy = driver.OneShotFaultProxy(path)
    body = json.dumps(
        {"model": driver.PRIMARY, "messages": [{"content": "I am a Worker"}]}
    ).encode()
    if case == "malformed":
        body = b"not-json"
    assert (
        proxy.select_fault(body, "/v1/models" if case == "wrong_route" else "/v1/chat/completions")
        is None
    )
    assert not proxy.injections


@pytest.mark.parametrize(
    "url",
    [
        "https://remote.invalid/v1",
        "http://localhost:8788/v1",
        "http://127.0.0.1:8788/other",
        "http://u:p@127.0.0.1:8788/v1",
    ],
)
def test_proxy_cannot_forward_credentials_to_an_unrequested_destination(tmp_path, url):
    with pytest.raises(ValueError, match="loopback"):
        driver.OneShotFaultProxy(tmp_path / "none.sqlite3", upstream=url)


def test_other_requests_retain_actual_bytes_and_credential_on_local_forwarding(tmp_path):
    seen = []
    response_bytes = b'{"choices":[{"message":{"content":"fixture response"}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            seen.append(
                (
                    self.path,
                    self.headers.get("Authorization"),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with driver.OneShotFaultProxy(
            tmp_path / "absent.sqlite3",
            upstream=f"http://127.0.0.1:{upstream.server_port}/v1",
            credential="fixture-only",
        ) as proxy:
            body = b'{ "model": "glm-5.3-flash", "messages": [] }'
            response = httpx.post(
                proxy.base_url + "/chat/completions",
                content=body,
                headers={
                    "Authorization": "Bearer fixture-only",
                    "Content-Type": "application/json",
                },
                trust_env=False,
            )
            assert response.status_code == 200 and response.content == response_bytes
            assert proxy.forwarded == 1 and not proxy.injections
            denied = httpx.post(
                proxy.base_url + "/chat/completions",
                content=body,
                headers={"Authorization": "Bearer different-fixture"},
                trust_env=False,
            )
            assert denied.status_code == 401
        assert seen == [("/v1/chat/completions", "Bearer fixture-only", body)]
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)


def test_profiles_are_actual_model_identities_with_unmeasured_priors():
    profiles = driver.registry_profiles(
        [
            {"id": driver.PRIMARY, "context_window": 1000000, "max_output_tokens": 128000},
            {"id": driver.ALTERNATIVE, "context_window": 200000, "max_output_tokens": 16000},
        ]
    )
    assert [profile["model_id"] for profile in profiles] == [driver.PRIMARY, driver.ALTERNATIVE]
    assert [profile["context_window"] for profile in profiles] == [100000000, 200000]
    primary_context = profiles[0]["provenance"]["context_declaration"]
    assert primary_context["source"] == "operator_declared"
    assert primary_context["fields"]["context_window"] == 1000000
    assert primary_context["declaration_conflict_retained"] is True
    assert (
        primary_context["independently_measured"]
        is primary_context["execution_budget_grant"]
        is False
    )
    assert all(profile["max_output_tokens"] == 8192 for profile in profiles)
    assert all(
        measurement["n_samples"] is None and measurement["source"].endswith("unmeasured")
        for profile in profiles
        for measurement in profile["capability_measurements"].values()
    )
    with pytest.raises(ValueError, match="Both actual"):
        driver.registry_profiles([{"id": driver.PRIMARY, "context_window": 1000000}])


def test_rejected_usage_unknown_is_allowlisted_only_with_exact_native_proof(delegated):
    store, quest, _service, _manager, run, _event, reservation, attempt, _requests = (
        rejected_gateway_worker(delegated)
    )
    initial_router = driver.snapshot(store.path)["router"]
    injected = {"worker_run_id": run["id"], "attempt_id": attempt["attempt_id"]}
    observed = driver.failover_observations(
        store.path, store.workspace.root, quest, injected, initial_router
    )
    assert observed["checks"]["all_noninjected_attempts_settled"] is True
    assert observed["checks"]["known_rejection_reservation_retained_without_refund"] is True
    assert (
        observed["known_rejection_usage_unknown"][0]["reserved_tokens"]
        == reservation["reserved_tokens"]
    )
    assert observed["known_rejection_usage_unknown"][0]["usage_status"] == "unknown"
    assert (
        observed["local_reserved_count"] == 1
        and observed["unresolved_other_local_reservations"] == 0
    )
    assert observed["injected_attempts"][0]["input_charged"] is None
    assert observed["real_upstream_attempts"] == []
    inflight = driver.inflight_snapshot(store.path, injected)
    assert inflight["known_rejection_usage_unknown_reservations"] == 1
    assert inflight["worker_model_unresolved"] == 0
    wrong = driver.failover_observations(
        store.path,
        store.workspace.root,
        quest,
        {**injected, "attempt_id": "unbound"},
        initial_router,
    )
    assert wrong["checks"]["all_noninjected_attempts_settled"] is False
    assert wrong["known_rejection_usage_unknown"] == []
    wrong_inflight = driver.inflight_snapshot(store.path, {**injected, "attempt_id": "unbound"})
    assert wrong_inflight["known_rejection_usage_unknown_reservations"] == 0
    assert wrong_inflight["worker_model_unresolved"] == 1
    with store.connection() as c:
        assert dict(c.execute("SELECT * FROM worker_model_reservations").fetchone()) == reservation
        assert dict(c.execute("SELECT * FROM model_budget_attempts").fetchone()) == attempt
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) "
            "VALUES (?,'unknown-operation','unknown-operation','file_write','{}','uncertain')",
            (run["id"],),
        )
    unknown_tool = driver.failover_observations(
        store.path, store.workspace.root, quest, injected, initial_router
    )
    assert unknown_tool["checks"]["no_unknown_tool_outcomes"] is False


@pytest.mark.parametrize("fault", ["network", "partial"])
def test_unknown_or_incomplete_rejections_are_never_allowlisted(delegated, fault):
    store, quest, _service, _manager, run, _event, _reservation, attempt, _requests = (
        rejected_gateway_worker(delegated, fault=fault)
    )
    observed = driver.failover_observations(
        store.path,
        store.workspace.root,
        quest,
        {"worker_run_id": run["id"], "attempt_id": attempt["attempt_id"]},
        driver.snapshot(store.path)["router"],
    )
    assert observed["checks"]["all_noninjected_attempts_settled"] is False
    assert observed["checks"]["known_rejection_reservation_retained_without_refund"] is False
    assert observed["known_rejection_usage_unknown"] == []
    assert observed["unresolved_other_local_reservations"] == 1
    inflight = driver.inflight_snapshot(
        store.path, {"worker_run_id": run["id"], "attempt_id": attempt["attempt_id"]}
    )
    assert inflight["known_rejection_usage_unknown_reservations"] == 0
    assert inflight["worker_model_unresolved"] == 1


def test_final_router_handback_requires_completed_quest_notice():
    state = {
        "quests": [{"id": "q", "status": "completed"}],
        "events": [
            {"id": "early", "event_type": "worker.result", "quest_id": "q", "payload": "{}"}
        ],
        "messages": [
            {
                "role": "assistant",
                "metadata": json.dumps({"source": "router_notice", "event_id": "early"}),
            }
        ],
    }
    assert driver.reviewed_notices_for_tasks(state) == set()
    state["events"].append(
        {
            "id": "complete",
            "event_type": "quest.status_changed",
            "quest_id": "q",
            "payload": '{"to":"completed"}',
        }
    )
    state["messages"].append(
        {
            "role": "assistant",
            "metadata": json.dumps({"source": "router_notice", "source_event_ids": ["complete"]}),
        }
    )
    assert driver.reviewed_notices_for_tasks(state) == {"q"}


def test_read_only_drain_waits_for_router_and_two_seconds_of_stability(tmp_path, monkeypatch):
    clock = {"now": 0.0, "reads": 0}
    counters = {
        name: 0
        for name in (
            "model_calls",
            "provider_reserved",
            "provider_unknown",
            "worker_model_unresolved",
            "tool_inflight",
            "tool_unknown",
            "worker_tool_inflight",
            "worker_tool_unknown",
            "active_workers",
            "router_pending",
            "router_dead",
        )
    }
    counters["known_rejection_usage_unknown_reservations"] = 1

    def read(*_args):
        clock["reads"] += 1
        return {**counters, "router_pending": int(clock["reads"] < 3)}

    monkeypatch.setattr(driver, "inflight_snapshot", read)
    monkeypatch.setattr(driver.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        driver.time, "sleep", lambda seconds: clock.update(now=clock["now"] + seconds)
    )
    from types import SimpleNamespace

    core = SimpleNamespace(process=SimpleNamespace(poll=lambda: None))
    outcome = driver.drain_before_stop(
        core, tmp_path / "unused.sqlite3", None, SimpleNamespace(injections=[]), began=0
    )
    assert outcome["outcome"] == "quiescent"
    assert outcome["elapsed_seconds"] >= 2.25 and clock["reads"] >= 10
    assert outcome["last_counters"]["known_rejection_usage_unknown_reservations"] == 1


def _source_binding_observation(
    tmp_path,
    monkeypatch,
    *,
    source,
    artifact,
    source_receipt=True,
    receipt_source=None,
    baseline=None,
    live=True,
):
    root = tmp_path
    input_file = root / "inputs/value.txt"
    input_file.parent.mkdir()
    input_file.write_bytes(source)
    output_file = root / "work/q/recovered.txt"
    output_file.parent.mkdir(parents=True)
    output_file.write_bytes(artifact)
    database = root / "empty.sqlite3"
    sqlite3.connect(database).close()

    def digest(value):
        return hashlib.sha256(value).hexdigest()

    def receipt(tool, path, output):
        return {
            "run_id": "replacement",
            "tool_name": tool,
            "resource_scope": str(path),
            "execution_mode": "live" if live else "mock",
            "result_json": json.dumps(output),
        }

    receipts = [
        receipt("file_write", output_file, {"sha256": digest(artifact), "bytes": len(artifact)}),
        receipt(
            "file_read", output_file, {"sha256": digest(artifact), "content": artifact.decode()}
        ),
    ]
    if source_receipt:
        observed_source = source if receipt_source is None else receipt_source
        receipts.append(
            receipt(
                "file_read",
                input_file,
                {
                    "sha256": digest(observed_source),
                    "content": observed_source.decode(),
                },
            )
        )
    state = {"events": [], "quests": [], "messages": [], "receipts": receipts, "router": "router"}
    monkeypatch.setattr(driver, "snapshot", lambda *_args: state)
    run = {
        "id": "replacement",
        "quest_id": "q",
        "model_id": driver.ALTERNATIVE,
        "state": "running",
        "execution_mode": "live",
    }
    monkeypatch.setattr(
        driver, "read_rows", lambda _db, sql, *_args: [run] if "FROM worker_runs" in sql else []
    )
    return driver.failover_observations(
        database, root, "q", {}, "router", source_bytes=source if baseline is None else baseline
    )


def test_output_lf_does_not_satisfy_actual_crlf_source(tmp_path, monkeypatch):
    observed = _source_binding_observation(
        tmp_path, monkeypatch, source=b"FORGE_RECOVERED_42\r\n", artifact=b"FORGE_RECOVERED_42\n"
    )
    assert observed["checks"]["real_artifact_matches_exact_acceptance"] is False
    assert observed["checks"]["replacement_source_bound_write_and_read_receipts"] is False


@pytest.mark.parametrize("newline", [b"\r\n", b"\n"])
def test_exact_actual_source_and_all_three_live_receipts_pass(tmp_path, monkeypatch, newline):
    data = b"FORGE_RECOVERED_42" + newline
    observed = _source_binding_observation(tmp_path, monkeypatch, source=data, artifact=data)
    assert observed["checks"]["real_artifact_matches_exact_acceptance"] is True
    assert observed["checks"]["replacement_source_bound_write_and_read_receipts"] is True
    assert observed["checks"].get("input_unchanged") is True


@pytest.mark.parametrize(
    "fault", ["missing_source_receipt", "wrong_source_receipt", "changed_source", "mock_receipt"]
)
def test_output_receipts_alone_cannot_prove_original_source(tmp_path, monkeypatch, fault):
    data = b"FORGE_RECOVERED_42\n"
    observed = _source_binding_observation(
        tmp_path,
        monkeypatch,
        source=data,
        artifact=data,
        source_receipt=fault != "missing_source_receipt",
        receipt_source=b"other" if fault == "wrong_source_receipt" else None,
        baseline=b"original\r\n" if fault == "changed_source" else None,
        live=fault != "mock_receipt",
    )
    assert observed["checks"]["replacement_source_bound_write_and_read_receipts"] is False
    if fault == "changed_source":
        assert observed["checks"]["real_artifact_matches_exact_acceptance"] is False
        assert observed["checks"].get("input_unchanged") is False
