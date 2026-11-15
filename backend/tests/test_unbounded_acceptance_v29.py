"""Offline checks for the user's explicit cancellation of cumulative token caps."""

import copy
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / "phase2" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"unbounded_v29_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("level,cap", [(1, 180000), (2, 360000), (3, 630000)])
def test_unbounded_scenario_preserves_task_and_all_other_limits(tmp_path, level, cap):
    scenarios = load("acceptance_scenarios")
    bounded = scenarios.describe_scenario(level)
    unbounded = scenarios.describe_scenario(level, no_token_budget=True)
    assert bounded["scenario_id"] == f"forge-phase2-level-{level}-v3"
    assert bounded["limits"]["max_total_tokens"] == cap
    assert "token_budget_policy" not in bounded
    assert unbounded["limits"] == {
        key: value for key, value in bounded["limits"].items() if key != "max_total_tokens"
    }
    for key in ("prompt", "sources", "suggested_output_paths", "expected_checks"):
        assert unbounded[key] == bounded[key]
    first = scenarios.seed_scenario(level, tmp_path / "bounded")
    second = scenarios.seed_scenario(level, tmp_path / "unbounded", no_token_budget=True)
    assert first["input_manifest"] == second["input_manifest"]
    assert first["prompt"].startswith(bounded["prompt"] + " Experiment limits")
    assert second["prompt"].startswith(bounded["prompt"] + " Protocol change")
    assert "explicitly requested by the user" in second["prompt"]
    assert "max_total_tokens" not in second["prompt"]
    assert second["token_budget_policy"]["enforced"] is False
    manifest = tmp_path / "unbounded" / second["input_directory"] / "manifest.json"
    assert json.loads(manifest.read_text())["scenario_id"] == second["scenario_id"]


@pytest.mark.parametrize("limit,policy", [(None, "false"), (1234, "true")])
def test_live_core_emits_explicit_mode_without_none_string(tmp_path, monkeypatch, limit, policy):
    monkeypatch.setenv("FORGE_MODEL_TOKEN_LIMIT", "987654321")
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    core = load("live_core").LiveCore(tmp_path, provider_env={}, token_limit=limit, concurrency=2)
    env = core.env()
    assert env["FORGE_ENFORCE_TOKEN_BUDGETS"] == policy
    assert env.get("FORGE_MODEL_TOKEN_LIMIT") == (None if limit is None else str(limit))
    assert env["FORGE_ROUTER_CONTEXT_LIMIT"] == "65536"
    assert env["FORGE_ROUTER_OUTPUT_RESERVE"] == "4096"
    assert env["FORGE_MODEL_CONCURRENCY_LIMIT"] == "2"
    assert "None" not in env.values()


@pytest.mark.parametrize(
    "limit,extra",
    [
        (None, {"FORGE_ENFORCE_TOKEN_BUDGETS": "true"}),
        (100, {"FORGE_ENFORCE_TOKEN_BUDGETS": "false"}),
        (None, {"FORGE_ENFORCE_TOKEN_BUDGETS": "False"}),
        (None, {"FORGE_ENFORCE_TOKEN_BUDGETS": False}),
        (None, {"FORGE_MODEL_TOKEN_LIMIT": "1000000000"}),
        (100, {"FORGE_MODEL_TOKEN_LIMIT": "101"}),
        (100, {"FORGE_MODEL_TOKEN_LIMIT": "None"}),
    ],
)
def test_live_core_rejects_conflicting_or_malformed_mode(tmp_path, limit, extra):
    core = load("live_core").LiveCore(
        tmp_path, provider_env={}, token_limit=limit, concurrency=1, extra_env=extra
    )
    with pytest.raises(ValueError):
        core.env()


@pytest.fixture
def budget_fixture():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE model_budget_attempts(attempt_id TEXT,workspace_id TEXT,call_id TEXT,
            purpose TEXT,state TEXT,input_charged,output_charged,input_reserved,output_reserved);
        CREATE TABLE model_budget_opening(workspace_id TEXT,tokens INTEGER);
        CREATE TABLE worker_runs(id TEXT,quest_id TEXT,steps INTEGER,tool_spent INTEGER,
            depth INTEGER,team_digest TEXT);
        CREATE TABLE worker_tool_calls(run_id TEXT);
        CREATE TABLE events(quest_id TEXT,event_type TEXT,payload TEXT,seq INTEGER);
        CREATE TABLE team_proposals(quest_id TEXT,proposal_digest TEXT,body_json TEXT);
        """
    )
    scenario = load("acceptance_scenarios").describe_scenario(1, no_token_budget=True)
    record = {
        "duration_seconds": 1,
        "runtime_settings": {"token_budgets_enforced": False, "model_token_limit": None},
        "results": {"budget": {"token_budgets_enforced": False, "token_limit": None}},
    }
    yield connection, scenario, record
    connection.close()


def evaluate_budget(fixture):
    connection, scenario, record = fixture
    checks = {}
    load("acceptance_checks")._scenario_budgets(
        connection,
        SimpleNamespace(workspace=SimpleNamespace(id="ws")),
        "q",
        scenario,
        record,
        checks,
    )
    return checks


def insert_attempt(connection, state, charged):
    connection.execute(
        "INSERT INTO model_budget_attempts VALUES ('attempt','ws','call','worker',?,?,?,?,?)",
        (state, charged, 1 if charged is not None else None, 100, 20),
    )


def test_known_settled_usage_above_historical_cap_is_accounted_without_comparison(budget_fixture):
    connection, _scenario, _record = budget_fixture
    insert_attempt(connection, "settled", 2_000_000)
    connection.execute("INSERT INTO model_budget_opening VALUES ('ws', 7)")
    checks = evaluate_budget(budget_fixture)
    assert checks["scenario_model_usage_settled"]["status"] == "passed"
    assert checks["scenario_token_budget_policy"]["status"] == "passed"
    assert (
        checks["scenario_model_usage_settled"]["evidence"][0]["known_charged_tokens"] == 2_000_008
    )
    assert "scenario_total_token_budget" not in checks


@pytest.mark.parametrize(
    "state,charged", [("reserved", None), ("unknown", 2), ("settled", None), ("overrun", 2)]
)
def test_unknown_or_unsettled_usage_never_passes_without_cap(budget_fixture, state, charged):
    insert_attempt(budget_fixture[0], state, charged)
    check = evaluate_budget(budget_fixture)["scenario_model_usage_settled"]
    assert check["status"] == "unknown"
    assert check["evidence"][0]["unknown_attempts"] == ["attempt"]


def test_rejected_unsent_attempts_do_not_create_unknown_usage(budget_fixture):
    insert_attempt(budget_fixture[0], "rejected", None)
    assert evaluate_budget(budget_fixture)["scenario_model_usage_settled"]["status"] == "passed"


@pytest.mark.parametrize("opening", [-1, 1.5, "unknown"])
def test_malformed_opening_usage_cannot_reduce_or_invent_known_usage(budget_fixture, opening):
    connection, _scenario, _record = budget_fixture
    connection.execute("INSERT INTO model_budget_opening VALUES ('ws', ?)", (opening,))
    connection.execute("INSERT INTO model_budget_opening VALUES ('ws', 7)")
    with pytest.raises(ValueError, match="opening token usage"):
        evaluate_budget(budget_fixture)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_policy",
        "numeric_cap",
        "null_cap",
        "numeric_runtime",
        "numeric_core",
        "missing_core",
        "missing_runtime",
        "false_as_zero",
        "old_scenario",
    ],
)
def test_disabled_policy_requires_explicit_driver_and_observed_core_binding(
    budget_fixture, mutation
):
    _connection, scenario, record = budget_fixture
    if mutation == "missing_policy":
        del scenario["token_budget_policy"]
    elif mutation == "numeric_cap":
        scenario["limits"]["max_total_tokens"] = 999999999
    elif mutation == "null_cap":
        scenario["limits"]["max_total_tokens"] = None
    elif mutation == "numeric_runtime":
        record["runtime_settings"]["model_token_limit"] = 999999999
    elif mutation == "numeric_core":
        record["results"]["budget"]["token_limit"] = 999999999
    elif mutation == "missing_core":
        record["results"] = {}
    elif mutation == "missing_runtime":
        del record["runtime_settings"]["model_token_limit"]
    elif mutation == "false_as_zero":
        scenario["token_budget_policy"]["enforced"] = 0
    else:
        scenario["scenario_id"] = "forge-phase2-level-1-v3"
    assert evaluate_budget(budget_fixture)["scenario_token_budget_policy"]["status"] == "failed"


def test_unbounded_mode_still_checks_time_steps_tools_review_and_concurrency(budget_fixture):
    connection, _scenario, record = budget_fixture
    record["duration_seconds"] = 601
    connection.execute("INSERT INTO worker_runs VALUES ('run','q',17,17,2,'team')")
    connection.execute(
        "INSERT INTO team_proposals VALUES ('q','team',?)", ('{"limits":{"max_concurrency":2}}',)
    )
    for index in range(4):
        connection.execute(
            "INSERT INTO events VALUES ('q','review.started',?,?)",
            (json.dumps({"run_id": "run", "call_id": f"r{index}"}), index),
        )
    checks = evaluate_budget(budget_fixture)
    for key in (
        "scenario_duration_budget",
        "scenario_worker_step_budget",
        "scenario_worker_tool_budget",
        "scenario_worker_depth",
        "scenario_review_round_budget",
        "scenario_worker_concurrency",
    ):
        assert checks[key]["status"] == "failed"


def test_driver_no_token_budget_records_null_and_new_protocol_without_provider(
    tmp_path, monkeypatch
):
    driver = load("live_e2e")
    real_core = load("live_core").LiveCore
    created = []

    class OfflineCore(real_core):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def start(self):
            return self

        def call(self, _method, path, *_args, **_kwargs):
            if path == "/api/conversation":
                return {"status": 200, "body": {"router": {"router_session_id": "offline"}}}
            return {"status": 400, "body": {}}

    monkeypatch.setenv("FORGE_PROVIDER_KEY_LIVE_TEST", "offline-not-a-key")
    monkeypatch.setattr(driver, "REPO", tmp_path)
    monkeypatch.setattr(driver, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(driver, "LiveCore", OfflineCore)
    monkeypatch.setattr(driver, "drain_model_attempts", lambda _ws: {})
    args = SimpleNamespace(
        level="L1",
        scenario=True,
        prompt=None,
        token_limit=1,
        concurrency=2,
        extra_env=None,
        no_token_budget=True,
    )
    record = driver.drive(args)
    assert len(created) == 1 and created[0].token_limit is None
    assert record["runtime_settings"]["token_budgets_enforced"] is False
    assert record["runtime_settings"]["model_token_limit"] is None
    assert "max_total_tokens" not in record["scenario"]["limits"]
    saved = json.loads((driver.RUNS / record["run_id"] / "run.json").read_text())
    assert saved["runtime_settings"] == record["runtime_settings"]
    assert saved["failures"]  # Synthetic rejected Router response is never a live success.
    assert "None" not in created[0].env().values()
    malformed = copy.copy(args)
    malformed.extra_env = '{"FORGE_ENFORCE_TOKEN_BUDGETS":"true"}'
    with pytest.raises(ValueError, match="conflicts"):
        driver.drive(malformed)


@pytest.mark.parametrize("unknown_usage", [False, True])
def test_stopped_core_unbounded_handoff_uses_original_gateway_and_settled_ledger(
    tmp_path, monkeypatch, unknown_usage
):
    import httpx

    from forge_core.context_service import ContextService
    from forge_core.domain import ConversationMessage, DomainError, ModelProfile
    from forge_core.providers import ProviderError, ProviderGateway
    from forge_core.settings import Workspace
    from forge_core.store import Store

    driver, harness = load("live_e2e"), load("live_core")
    store = Store(workspace=Workspace.from_path(tmp_path))
    original = ConversationMessage(
        role="user", content="Keep original-result.csv; cite exact sources."
    )
    store.add_message(original)
    store.add_message(
        ConversationMessage(role="assistant", content="Registered; work remains pending.")
    )
    store.save_model(ModelProfile.model_validate(harness.MODEL_PROFILE))
    core = harness.LiveCore(
        tmp_path,
        provider_env=harness.provider_env_from_key("offline-not-a-key"),
        token_limit=None,
        concurrency=1,
        extra_env={"FORGE_ENFORCE_TOKEN_BUDGETS": "false"},
    )
    requests = []
    ledgers = []
    original_init = ProviderGateway.__init__

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert not body.get("tools") and not body.get("response_format")
        assert body["max_tokens"] == 4096
        packet = json.loads(body["messages"][1]["content"])
        assert packet["messages"][0]["id"] == original.id
        assert packet["messages"][0]["content"] == original.content
        response = {
            "id": "offline-compaction",
            "model": harness.MODEL_ID,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "Keep the original-result.csv rule; task remains unverified.",
                    },
                }
            ],
        }
        if not unknown_usage:
            response["usage"] = {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23}
        return httpx.Response(200, json=response)

    def inject_transport(self, config, **kwargs):
        ledgers.append(kwargs["budget_ledger"])
        assert kwargs["credential_resolver"](config.credential_ref) == "offline-not-a-key"
        original_init(self, config, transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    monkeypatch.setattr(ProviderGateway, "__init__", inject_transport)
    if unknown_usage:
        with pytest.raises((DomainError, ProviderError)):
            driver.compact_stopped_core(core)
        with store.connection() as connection:
            assert connection.execute("SELECT count(*) FROM context_archives").fetchone()[0] == 0
            attempt = connection.execute("SELECT * FROM model_budget_attempts").fetchone()
            assert attempt["state"] != "settled"
        with pytest.raises(DomainError, match="blocks source replay"):
            driver.compact_stopped_core(core)
    else:
        report = driver.compact_stopped_core(core)
        assert report["compaction_mode"] == "model_gateway_handoff"
        assert report["token_budgets_enforced"] is False
        assert report["archive_created"] is True and report["source_readback_available"] is True
        assert report["model_handoff"]["status"] == "unverified"
        assert len(report["model_checkpoints"]) == 1
        checkpoint = report["model_checkpoints"][0]
        assert checkpoint["source_ids"] == [original.id]
        assert checkpoint["gateway_attempt"]["state"] == "settled"
        assert checkpoint["gateway_attempt"]["input_charged"] == 15
        assert checkpoint["gateway_attempt"]["output_charged"] == 8
        assert checkpoint["gateway_attempt"]["purpose"] == "context_compaction"
        assert checkpoint["archive"]["model_handoff"] == report["model_handoff"]
        assert (
            ContextService(store).read_source("router", "main", original.id)["content"]
            == original.content
        )
        assert "offline-not-a-key" not in json.dumps(report)
    assert len(requests) == 1
    assert len(ledgers) == (2 if unknown_usage else 1)
    assert ledgers[0].enforce_token_budgets is False and ledgers[0].token_limit is None
