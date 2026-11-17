"""Offline metrics/API contracts; these results are not live Provider evidence."""

import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_m5_lifespan_execution import load_main
from test_model_recovery_capability_sprint import ProbeMock
from test_team_reassignment_m5 import prepared_team

from forge_core.capability_benchmark import CapabilityBenchmarkRunner
from forge_core.domain import ForgeEvent, ModelProfile, now_iso
from forge_core.governance import parameter_digest
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.store import Store

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capability_sprint_metrics.py"
SPEC = importlib.util.spec_from_file_location("capability_sprint_metrics", SCRIPT)
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def test_metrics_never_creates_or_migrates_database(tmp_path):
    absent = tmp_path / "absent.sqlite3"
    with pytest.raises(ValueError, match="already exist"):
        metrics.read_metrics(absent)
    assert not absent.exists()
    store = Store(tmp_path / "existing.sqlite3")
    before = Path(store.path).read_bytes()
    report = metrics.read_metrics(store.path)
    assert report["model_calls"] == 0 and report["cost_microunits"] is None
    assert report["charged_tokens"] == 0 and report["tool_success_rate"]["value"] is None
    assert Path(store.path).read_bytes() == before
    assert metrics.read_metrics(store.path)["snapshot_sha256"] == report["snapshot_sha256"]


def insert_attempt(work, identifier, run_id=None, *, known=True, currency=None, cost=None):
    work.connection.execute(
        "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,"
        "worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,"
        "created_at,input_charged,output_charged,currency,cost_charged) "
        "VALUES (?,?, 'capability_benchmark','fixture',?,'p','m',100,100,?,1,?,?,?,?)",
        (
            identifier,
            identifier,
            run_id,
            "settled" if known else "reserved",
            10 if known else None,
            20 if known else None,
            currency,
            cost,
        ),
    )


def test_metrics_unknown_usage_cost_and_nonworker_provenance_are_explicit(tmp_path):
    store, _, _, _, _, _, _, _, runs = prepared_team(tmp_path)
    with store.unit_of_work() as work:
        insert_attempt(work, "known", runs[0]["id"], currency="USD", cost=5)
        insert_attempt(work, "unknown", known=False)
    report = metrics.read_metrics(store.path)
    assert report["model_calls"] == 2
    assert report["gateway_attempt_modes_from_worker_scope"] == {
        "mock": 1,
        "unattributed_non_worker": 1,
    }
    assert report["charged_tokens"] is None and report["known_charged_tokens"] == 30
    assert report["unknown_usage_attempts"] == 1
    assert report["cost_microunits"] is None and report["cost_currency"] is None
    assert report["admission_states"] == {"reserved": 1, "settled": 1}


def test_metrics_known_costs_do_not_mix_currencies(tmp_path):
    store = Store(tmp_path / "costs.sqlite3")
    with store.unit_of_work() as work:
        insert_attempt(work, "one", currency="USD", cost=4)
        insert_attempt(work, "two", currency="USD", cost=6)
    assert metrics.read_metrics(store.path)["cost_microunits"] == 10
    with store.unit_of_work() as work:
        insert_attempt(work, "three", currency="CNY", cost=2)
    assert metrics.read_metrics(store.path)["cost_microunits"] is None


def test_metrics_reports_bound_evidence_without_exposing_raw_content(tmp_path):
    store, quest, plan, _, _, _, _, _, runs = prepared_team(tmp_path)
    run = runs[0]
    secret = "PRIVATE_FIXTURE_CONTENT_NEVER_REPORT"
    parameters = {"path": secret}
    result = json.dumps({"output": secret})
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO action_intents(id,call_id,quest_id,worker_id,run_id,plan_id,"
            "plan_revision,tool_name,parameters_json,parameter_digest,resource_scope,"
            "risk,execution_mode,state,created_at,updated_at) "
            "VALUES ('intent','call',?,?,?,?,?,'file_read',?,?,'.','low','mock','succeeded',?,?)",
            (
                quest.id,
                run["worker_id"],
                run["id"],
                plan["id"],
                plan["revision"],
                json.dumps(parameters),
                parameter_digest(parameters),
                now_iso(),
                now_iso(),
            ),
        )
        work.connection.execute(
            "INSERT INTO action_receipts VALUES ('receipt','intent','mock',?,?,?)",
            (result, hashlib.sha256(result.encode()).hexdigest(), now_iso()),
        )
    for verdict, error in [("PASS", None), ("NEEDS_REVISION", None), ("BLOCKED", "timeout")]:
        store.append_event(
            ForgeEvent(
                event_type="review.result",
                quest_id=quest.id,
                worker_id=run["worker_id"],
                payload={
                    "verdict": verdict,
                    "error_code": error,
                    "run_id": run["id"],
                    "raw": secret,
                },
            )
        )
    store.append_event(
        ForgeEvent(
            event_type="runtime.worker_recovered",
            quest_id=quest.id,
            worker_id=run["worker_id"],
            payload={"run_id": run["id"], "raw": secret},
        )
    )
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='succeeded' WHERE id=?", (run["id"],))
    report = metrics.read_metrics(store.path)
    assert report["receipt_integrity_rate"] == {"passed": 1, "samples": 1, "value": 1}
    assert report["judged_review_pass_rate"] == {"passed": 1, "samples": 2, "value": 0.5}
    assert report["review_infrastructure_errors"] == 1
    assert report["recovered_run_current_success_rate"]["value"] == 1
    assert secret not in json.dumps(report)
    assert "receipt:receipt" in report["source_refs"]


def test_compare_keeps_modes_unknowns_and_currencies_separate():
    result = metrics.compare_rows(
        [
            {
                "execution_mode": "live",
                "strategy": "single",
                "success": True,
                "charged_tokens": 30,
                "cost_microunits": 2,
                "cost_currency": "USD",
            },
            {
                "execution_mode": "live",
                "strategy": "single",
                "success": None,
                "charged_tokens": float("inf"),
                "cost_microunits": 4,
                "cost_currency": "CNY",
            },
            {
                "execution_mode": "mock",
                "strategy": "single",
                "success": False,
                "elapsed_seconds": float("nan"),
            },
        ]
    )
    live, mock = result["groups"]
    assert live["task_success"] == {"passed": 1, "samples": 1, "value": 1}
    assert live["unknown_outcomes"] == 1
    assert live["charged_tokens"] == {"mean": 30, "n_known": 1, "n_unknown": 1}
    assert live["cost_microunits"]["mean"] is None and live["cost_currency"] is None
    assert mock["elapsed_seconds"]["mean"] is None
    assert "Exploratory" in result["conclusion"]
    with pytest.raises(ValueError, match="provenance"):
        metrics.compare_rows([{"strategy": "single", "success": True}])
    with pytest.raises(ValueError, match="strategy"):
        metrics.compare_rows([{"execution_mode": "live"}])


@pytest.mark.parametrize("model_ids", [[], ["m", "m"], ["a", "b", "c"]])
def test_probe_driver_rejects_invalid_plan_before_starting(tmp_path, model_ids):
    workspace = tmp_path / "never-created"
    with pytest.raises(ValueError):
        metrics.run_model_probes(workspace, model_ids, key="placeholder")
    assert not workspace.exists()


def test_probe_driver_never_replays_existing_workspace(tmp_path):
    with pytest.raises(ValueError, match="never replayed"):
        metrics.run_model_probes(tmp_path, ["m"], key="placeholder")


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "api.sqlite3"))
    main = load_main(tmp_path, monkeypatch)
    client = TestClient(
        main.app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 12345),
        raise_server_exceptions=False,
    )
    session = client.get("/health").json()["session"]
    token = hmac.new(
        bytes.fromhex(os.environ["FORGE_CORE_SECRET"]), session.encode(), hashlib.sha256
    ).hexdigest()
    client.headers["Authorization"] = "Bearer " + token
    return main, client


def configured_gateway():
    return ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
        ),
        credential_resolver=lambda _: "placeholder",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"id": "m"}]})),
    )


def test_intelligence_api_requires_auth_and_configured_provider(api):
    main, client = api
    auth = client.headers.pop("Authorization")
    assert client.post("/api/model-intelligence/discover").status_code == 401
    client.headers["Authorization"] = auth
    assert client.post("/api/model-intelligence/discover").status_code == 409
    main.store.save_model(ModelProfile(id="profile", provider="p", model_id="m", display_name="m"))
    assert (
        client.post("/api/model-intelligence/benchmark", json={"profile_id": "profile"}).status_code
        == 409
    )


def test_discovery_api_preserves_operator_profile_and_records_mock_origin(api):
    main, client = api
    original = ModelProfile(
        id="operator",
        provider="p",
        model_id="m",
        display_name="Kept",
        enabled=False,
        provenance={"source": "operator"},
    )
    main.store.save_model(original)
    main.provider = configured_gateway()
    first = client.post("/api/model-intelligence/discover")
    assert first.status_code == 200
    assert first.json()[0]["provenance"]["discovery"]["mode"] == "mock"
    assert client.post("/api/model-intelligence/discover").json() == []
    assert next(item for item in main.registry.list() if item.id == "operator") == original


def test_intelligence_api_excludes_mock_probe_from_live_measurements(api):
    import asyncio

    main, client = api
    main.store.save_model(
        ModelProfile(id="profile", provider="mock", model_id="m", display_name="m")
    )
    asyncio.run(
        CapabilityBenchmarkRunner(main.store).run(
            ProbeMock(), "m", mode="mock", dimensions=["mathematics"]
        )
    )
    snapshot = client.get("/api/model-intelligence/profile")
    assert snapshot.status_code == 200
    assert snapshot.json()["capabilities"]["measurements"]["mathematics"] is None
    assert client.get("/api/model-intelligence/absent").status_code == 404


def test_benchmark_api_rejects_mock_transport_as_live_and_bad_contracts(api):
    main, client = api
    main.store.save_model(ModelProfile(id="profile", provider="p", model_id="m", display_name="m"))
    main.provider = configured_gateway()
    response = client.post("/api/model-intelligence/benchmark", json={"profile_id": "profile"})
    assert response.status_code == 422 and "provenance" in response.json()["detail"]
    assert (
        client.post(
            "/api/model-intelligence/benchmark",
            json={"profile_id": "profile", "dimensions": ["coding"] * 8},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/model-intelligence/benchmark", json={"profile_id": "profile", "unexpected": True}
        ).status_code
        == 422
    )
    assert main.store.list_models()[0].capability_measurements == {}


def test_benchmark_route_passes_explicit_mode_and_dimensions_without_fake_live_ledger(
    api, monkeypatch
):
    main, client = api
    main.store.save_model(ModelProfile(id="profile", provider="p", model_id="m", display_name="m"))
    main.provider = configured_gateway()
    observed = []

    async def protocol_only(self, gateway, model_id, *, mode, dimensions=None):
        observed.append((gateway.execution_mode, model_id, mode, dimensions))
        return {"protocol_fixture": "mock_route_only", "results": []}

    monkeypatch.setattr(CapabilityBenchmarkRunner, "run", protocol_only)
    response = client.post(
        "/api/model-intelligence/benchmark",
        json={"profile_id": "profile", "dimensions": ["coding"]},
    )
    assert response.status_code == 200
    assert observed == [("mock", "m", "live", ["coding"])]
    with main.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 0
