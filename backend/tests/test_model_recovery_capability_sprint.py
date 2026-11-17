"""Programmatic/Mock tests; none of these fixtures is a real Provider result."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_team_reassignment_m5 import prepared_team
from test_watchdog import worker  # noqa: F401 - real SQLite fixture.
from test_worker_runtime import graph  # noqa: F401 - dependency of imported worker fixture.

from forge_core.capability_benchmark import PROBES, CapabilityBenchmarkRunner
from forge_core.domain import ForgeEvent
from forge_core.model_intelligence import ObservationStore
from forge_core.model_performance import model_health_snapshot, performance_snapshot
from forge_core.runtime import ModelResponse
from forge_core.store import Store
from forge_core.watchdog import Watchdog
from forge_core.worker_model_usage import reserve_worker_model_call


def observe(store, model, outcomes, *, mode="mock", provider="p"):
    gateway = SimpleNamespace(execution_mode=mode)
    service = ObservationStore(store)
    for outcome in outcomes:
        service.record(
            gateway,
            model,
            provider,
            {"outcome": outcome, "latency_seconds": 5, "ttft_seconds": 1, "output_tokens": 20},
        )


def test_health_separates_provenance_and_never_calls_transport_success_task_success(tmp_path):
    store = Store(tmp_path / "model.sqlite3")
    observe(store, "m", ["success", "error", "timeout", "error"])
    observe(store, "m", ["success"] * 3, mode="live")
    with store.connection() as c:
        health = model_health_snapshot(c, "p", "m", mode="mock", now=100)
        live = model_health_snapshot(c, "p", "m", mode="live", now=100)
        metrics = performance_snapshot(c, "p", "m", mode="mock")["measurements"]
    assert health["recommendation"] == "degraded" and health["consecutive_failures"] == 3
    assert live["recommendation"] == "available" and live["consecutive_failures"] == 0
    assert health["transport_failure_rate"]["n_samples"] == 4
    assert 0 < health["transport_failure_rate"]["confidence"]["lower"] < 0.75
    assert metrics["tokens_per_second"]["value"] == 5
    assert metrics["transport_error_rate"]["value"] == 0.5
    assert metrics["transport_timeout_rate"]["value"] == 0.25
    assert "task_success_rate" not in metrics


def test_health_unknown_reservation_is_not_reclassified_by_later_transport_success(worker):  # noqa: F811
    store, _, session, _, _, _ = worker
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT * FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()
        reserve_worker_model_call(work.connection, row, "unresolved-call", 100, 10, None, [])
    observe(store, session["model_id"], ["success"], provider=session["provider_id"])
    with store.connection() as c:
        health = model_health_snapshot(c, session["provider_id"], session["model_id"], mode="mock")
    assert health["recommendation"] == "blocked_unknown" and health["unknown_attempts"] == 1
    assert "model_call:unresolved-call" in health["source_refs"]
    with store.connection() as c:
        assert c.execute("SELECT state FROM worker_model_reservations").fetchone()[0] == "reserved"


def test_health_provider_cooldown_changes_projection_without_profile_mutation(tmp_path):
    store = Store(tmp_path / "cooldown.sqlite3")
    with store.unit_of_work() as work:
        work.connection.execute("INSERT INTO model_provider_cooldowns VALUES ('p',150)")
    with store.connection() as c:
        assert model_health_snapshot(c, "p", "m", now=100)["recommendation"] == "degraded"
        assert model_health_snapshot(c, "p", "m", now=151)["recommendation"] == "unknown"
    assert store.list_models() == []


def test_watchdog_reports_degraded_provider_after_all_workers_have_stopped(tmp_path):
    store = Store(tmp_path / "provider-stopped.sqlite3")
    observe(store, "m", ["timeout", "error", "timeout"], mode="live")
    watch = Watchdog(store, clock=lambda: 100)
    report = watch.tick()
    assert report["provider_issues"][0]["recommendation"] == "degraded"
    assert report["provider_issues"][0]["consecutive_failures"] == 3
    assert report["stalled_workers"] == [] and store.list_models() == []


@pytest.mark.parametrize("bound", [0, -1, float("nan"), float("inf"), 3601])
def test_progress_timeout_is_finite_and_bounded(tmp_path, bound):
    with pytest.raises(ValueError, match="progress timeout"):
        Watchdog(Store(tmp_path / "invalid.sqlite3"), worker_progress_timeout=bound)


def test_real_review_verdict_metrics_distinguish_infrastructure_failure_from_judgment(tmp_path):
    store, _, _, _, _, _, _, _, runs = prepared_team(tmp_path)
    run = runs[0]
    for verdict, code in [("PASS", None), ("NEEDS_REVISION", None), ("BLOCKED", "timeout")]:
        store.append_event(
            ForgeEvent(
                event_type="review.result",
                quest_id=run["quest_id"],
                worker_id=run["worker_id"],
                payload={
                    "run_id": run["id"],
                    "provider_id": "p",
                    "model_id": "m",
                    "execution_mode": "mock",
                    "verdict": verdict,
                    "error_code": code,
                },
            )
        )
    with store.connection() as c:
        metrics = performance_snapshot(c, "p", "m", mode="mock")["measurements"]
    assert metrics["independent_review_verdict_pass_rate"]["value"] == 0.5
    assert metrics["independent_review_verdict_pass_rate"]["n_samples"] == 2
    assert metrics["independent_review_error_rate"]["value"] == 1 / 3


def keep_live_lease(store, run_id):
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET deadline=1000,lease_until=900 WHERE id=?", (run_id,)
        )


def test_healthy_heartbeat_does_not_hide_stalled_worker_and_help_survives_restart(worker):  # noqa: F811
    store, _, session, _, model, tool = worker
    keep_live_lease(store, session["id"])
    now = [100.0]
    watch = Watchdog(store, clock=lambda: now[0], worker_progress_timeout=20)
    assert watch.tick()["stalled_workers"] == []
    now[0] = 125
    watch.heartbeat("worker")
    report = watch.tick()
    assert len(report["stalled_workers"]) == 1 and report["stalled_components"] == []
    assert report["stalled_workers"][0]["recovery"] == "request_router_assessment"
    Watchdog(store, clock=lambda: 130.0, worker_progress_timeout=20).tick()
    help_events = [
        event for event in store.list_events() if event["event_type"] == "worker.help_requested"
    ]
    assert len(help_events) == 1
    with store.connection() as c:
        current = c.execute("SELECT * FROM worker_runs WHERE id=?", (session["id"],)).fetchone()
    assert current["state"] == "running" and current["generation"] == session["generation"] + 1
    assert model.calls == 0 and tool.calls == []


def test_capsule_checkpoint_advances_activity_but_not_outcomes_or_reclaim(worker):  # noqa: F811
    store, _, session, _, _, _ = worker
    keep_live_lease(store, session["id"])
    now = [100.0]
    watch = Watchdog(store, clock=lambda: now[0], worker_progress_timeout=20)
    watch.tick()
    now[0] = 115
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_capsules SET revision=revision+1 WHERE run_id=?", (session["id"],)
        )
    assert watch.tick()["stalled_workers"] == []
    now[0] = 130
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET generation=generation+1 WHERE id=?", (session["id"],)
        )
    stalled = watch.tick()["stalled_workers"]
    assert stalled[0]["reason_code"] == "worker_no_productive_progress"
    now[0] = 136
    assert watch.tick()["stalled_workers"][0]["reason_code"] == "worker_no_checkpoint_progress"


def test_provider_wait_is_observed_once_without_stealing_lease_or_replaying(worker):  # noqa: F811
    store, _, session, _, model, tool = worker
    keep_live_lease(store, session["id"])
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT * FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()
        reserve_worker_model_call(work.connection, row, "wait-call", 100, 10, None, [])
        work.connection.execute(
            "INSERT INTO model_provider_cooldowns VALUES (?,?)", (row["provider_id"], 300)
        )
    now = [100.0]
    watch = Watchdog(store, clock=lambda: now[0], worker_progress_timeout=20)
    watch.tick()
    now[0] = 110
    assert watch.tick()["stalled_workers"] == []
    now[0] = 125
    report = watch.tick()
    assert report["stalled_workers"][0]["unsettled_effect_or_call"]
    assert report["provider_issues"][0]["recommendation"] == "degraded"
    assert report["provider_issues"][0]["in_flight_calls"] == 1
    assert report["provider_issues"][0]["unknown_attempts"] == 0
    watch.tick()
    assert (
        len(
            [
                event
                for event in store.list_events()
                if event["event_type"] == "worker.help_requested"
            ]
        )
        == 1
    )
    with store.connection() as c:
        assert (
            c.execute("SELECT state FROM worker_runs WHERE id=?", (session["id"],)).fetchone()[0]
            == "running"
        )
    assert model.calls == 0 and tool.calls == []


def test_capacity_backoff_queued_worker_is_not_a_stalled_running_worker(worker):  # noqa: F811
    store, _, session, _, _, _ = worker
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='queued',lease_until=NULL,lease_token=NULL WHERE id=?",
            (session["id"],),
        )
    report = Watchdog(store, clock=lambda: 900, worker_progress_timeout=20).tick()
    assert report["stalled_workers"] == [] and report["requeued"] == []
    assert not any(event["event_type"] == "worker.help_requested" for event in store.list_events())


def test_two_watchdogs_emit_one_help_for_the_same_durable_frontier(worker):  # noqa: F811
    store, _, session, _, _, _ = worker
    keep_live_lease(store, session["id"])
    Watchdog(store, clock=lambda: 100, worker_progress_timeout=20).tick()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda _: Watchdog(store, clock=lambda: 125, worker_progress_timeout=20).tick(),
                range(2),
            )
        )
    assert (
        len(
            [
                event
                for event in store.list_events()
                if event["event_type"] == "worker.help_requested"
            ]
        )
        == 1
    )


class ProbeMock:
    execution_mode = "mock"
    config = SimpleNamespace(id="mock")

    def __init__(self, fail=False):
        self.requests, self.fail = [], fail

    async def generate(self, request):
        self.requests.append(request)
        if self.fail:
            raise TimeoutError("must not persist remote failure text")
        dimension = request.messages[0]["content"].split(" ")[2].rstrip(".")
        expected = PROBES[dimension][1]
        if dimension == "coding":
            expected = {
                "code": "def clamp(value, low, high):\n    if value < low:\n        return low\n    if value > high:\n        return high\n    return value\n"
            }
        return ModelResponse(
            json.dumps(expected),
            [],
            {"input_tokens": 10, "output_tokens": 10},
            "mock",
            request.model_id,
            "stop",
        )


def test_capability_probes_persist_bounded_measured_results_without_overriding_registry(tmp_path):
    store = Store(tmp_path / "probes.sqlite3")
    runner, gateway = CapabilityBenchmarkRunner(store), ProbeMock()
    report = asyncio.run(runner.run(gateway, "m", mode="mock"))
    assert len(gateway.requests) == 6
    assert all(item["passed"] is True for item in report["results"] if item["status"] == "judged")
    assert report["results"][-1]["status"] == "unsupported"
    assert all(
        request.max_retries == 0 and request.max_output_tokens == 512
        for request in gateway.requests
    )
    assert all(
        request.budget_context.purpose == "capability_benchmark" for request in gateway.requests
    )
    summary = runner.summarize("m", "mock", mode="mock")
    assert summary["measurements"]["coding"]["n_samples"] == 1
    assert summary["measurements"]["coding"]["confidence"]["lower"] < 0.21
    assert summary["measurements"]["vision"] is None and store.list_models() == []
    assert all("output" not in item and "code" not in item for item in report["results"])
    assert runner.summarize("m", "mock", mode="live")["measurements"]["coding"] is None


def test_capability_failure_stops_further_calls_without_losing_diagnostic(tmp_path):
    runner, gateway = (
        CapabilityBenchmarkRunner(Store(tmp_path / "failed.sqlite3")),
        ProbeMock(fail=True),
    )
    result = asyncio.run(runner.run(gateway, "m", mode="mock"))
    assert len(gateway.requests) == 1 and result["results"][0]["reason_code"] == "timeout"
    assert result["results"][1]["status"] == "not_attempted"
    assert "must not persist" not in json.dumps(result)
    with pytest.raises(ValueError, match="provenance"):
        asyncio.run(runner.run(gateway, "m", mode="live"))


def test_capability_probes_use_real_gateway_accounting_with_scripted_http_only(tmp_path):
    import httpx

    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway

    store = Store(tmp_path / "http-probe.sqlite3")
    ledger = ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1)

    def answer(request):
        dimension = json.loads(request.content)["messages"][0]["content"].split(" ")[2].rstrip(".")
        expected = PROBES[dimension][1]
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {"message": {"content": json.dumps(expected)}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
        ),
        credential_resolver=lambda _: "placeholder",
        transport=httpx.MockTransport(answer),
        budget_ledger=ledger,
    )
    report = asyncio.run(
        CapabilityBenchmarkRunner(store).run(
            gateway, "m", mode="mock", dimensions=["mathematics", "research"]
        )
    )
    assert all(item["passed"] for item in report["results"])
    with store.connection() as c:
        calls = c.execute("SELECT * FROM model_budget_attempts").fetchall()
    assert len(calls) == 2 and all(row["state"] == "settled" for row in calls)
    assert all(row["purpose"] == "capability_benchmark" for row in calls)
    assert sum(row["input_charged"] + row["output_charged"] for row in calls) == 40
    assert report["mode"] == "mock"
