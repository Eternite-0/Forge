import hashlib
import hmac
import importlib
import secrets

from conftest import RouterMockProvider
from fastapi.testclient import TestClient


def test_api_trust_boundary(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "trust.sqlite3"))
    from forge_api import main

    importlib.reload(main)
    main.router.provider = RouterMockProvider("create_quest")
    main.router.model_id = "mock"
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    health = client.get("/health")
    session = health.json()["session"]
    token = hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/conversation").status_code == 401
    assert client.post("/api/conversation/message", json={"content": "hello"}).status_code == 401
    assert client.get("/api/conversation", headers=headers).status_code == 200
    preflight = client.options(
        "/api/conversation/message",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://tauri.localhost"
    windows_request = client.get(
        "/api/conversation", headers={**headers, "Origin": "http://tauri.localhost"}
    )
    assert windows_request.status_code == 200
    assert windows_request.headers["access-control-allow-origin"] == "http://tauri.localhost"
    assert (
        client.post(
            "/api/conversation/message",
            headers={**headers, "Origin": "https://evil.example"},
            json={"content": "hello"},
        ).status_code
        == 403
    )
    assert (
        client.get("/api/conversation", headers={**headers, "Host": "evil.example"}).status_code
        == 403
    )
    assert (
        client.get("/api/conversation", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    malformed = client.get("/api/conversation", headers=[(b"authorization", b"Bearer \xff")])
    assert malformed.status_code == 401
    assert malformed.json()["request_id"] == malformed.headers["x-request-id"]
    assert (
        client.get(
            "/api/conversation",
            headers=[("Authorization", f"Bearer {token}"), ("Authorization", f"Bearer {token}")],
        ).status_code
        == 401
    )

    created = client.post(
        "/api/conversation/message", headers=headers, json={"content": "帮我开发数据层"}
    )
    assert created.status_code == 200
    quest = client.get("/api/quests", headers=headers).json()[0]
    path = f"/api/quests/{quest['id']}/transition"
    forged = client.post(
        path,
        headers=headers,
        json={"status": "completed", "evidence": {"tests": "passed"}, "approval": "approved"},
    )
    assert forged.status_code == 422
    assert client.post(path, headers=headers, json={"status": "completed"}).status_code == 409
    no_evidence_route = client.post(
        f"/api/quests/{quest['id']}/evidence", headers=headers, json={"test": "passed"}
    )
    assert no_evidence_route.status_code == 404
    assert no_evidence_route.json()["code"] == "HTTP_404"
    assert no_evidence_route.json()["request_id"] == no_evidence_route.headers["x-request-id"]
    assert (
        client.get(
            "/api/conversation",
            headers=[
                ("Host", "127.0.0.1:8765"),
                ("Host", "evil.example"),
                ("Authorization", f"Bearer {token}"),
            ],
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/conversation",
            headers=[
                ("Origin", "http://localhost:5173"),
                ("Origin", "https://evil.example"),
                ("Authorization", f"Bearer {token}"),
            ],
        ).status_code
        == 403
    )
    remote = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("192.0.2.1", 12345))
    assert remote.get("/api/conversation", headers=headers).status_code == 403
    db = tmp_path / "trust.sqlite3"
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        if path.exists():
            content = path.read_bytes()
            assert secret.encode() not in content and token.encode() not in content
    previous = token
    importlib.reload(main)
    other = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    assert (
        other.get("/api/conversation", headers={"Authorization": f"Bearer {previous}"}).status_code
        == 401
    )
    assert other.get("/health").json()["session"] != session


def test_startup_requires_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("FORGE_CORE_SECRET", raising=False)
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "no-secret.sqlite3"))
    import pytest

    from forge_api import main

    with pytest.raises(ValueError, match="FORGE_CORE_SECRET"):
        importlib.reload(main)


def test_secret_in_workspace_env_is_rejected(tmp_path, monkeypatch):
    import pytest

    from forge_core.settings import Settings

    (tmp_path / ".env").write_text("FORGE_CORE_SECRET=placeholder\n", encoding="utf-8")
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    with pytest.raises(ValueError, match="must not be stored"):
        Settings.load(workspace_root=tmp_path)


def test_approval_and_grants_http_are_user_controlled(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    from forge_api import main

    importlib.reload(main)
    main.router.provider = RouterMockProvider("create_quest")
    main.router.model_id = "mock"
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    session = client.get("/health").json()["session"]
    token = hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/conversation/message", headers=headers, json={"content": "帮我开发数据层"})
    quest_id = client.get("/api/quests", headers=headers).json()[0]["id"]
    body = {
        "quest_id": quest_id,
        "action_type": "publish",
        "parameters": {"file": "one"},
        "resource_scope": "example",
        "risk": "R2",
        "reason": "publish",
    }
    assert client.post("/api/approvals", json=body).status_code == 401
    response = client.post("/api/approvals", headers=headers, json=body)
    assert response.status_code == 200
    approval = response.json()
    assert approval["status"] == "PENDING"
    assert any(
        a["id"] == approval["id"]
        for a in client.get(f"/api/approvals?quest_id={quest_id}", headers=headers).json()
    )
    assert (
        client.get(f"/api/approvals/{approval['id']}", headers=headers).json()["id"]
        == approval["id"]
    )
    path = f"/api/approvals/{approval['id']}/decision"
    assert (
        client.post(
            path, headers=headers, json={"decision": "APPROVED", "actor": "model"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, headers=headers, json={"decision": "APPROVED", "parameters": {"file": "two"}}
        ).status_code
        == 422
    )
    decision_body = {
        "decision": "APPROVED",
        "expected_digest": approval["parameter_digest"],
        "expected_plan_revision": approval["plan_revision"],
    }
    decision = client.post(path, headers=headers, json=decision_body)
    assert decision.status_code == 200 and decision.json()["decided_by"] == "local_user"
    assert client.post(path, headers=headers, json=decision_body).status_code == 409
    assert (
        client.post(f"/api/approvals/{approval['id']}/revoke", headers=headers).json()["status"]
        == "REVOKED"
    )
    assert (
        client.post("/api/approvals", headers=headers, json={**body, "risk": "R0"}).status_code
        == 409
    )
    grant = {"scope": "work", "expected_plan_revision": approval["plan_revision"]}
    assert client.post(f"/api/quests/{quest_id}/grants", json=grant).status_code == 401
    assert (
        client.post(f"/api/quests/{quest_id}/grants", headers=headers, json=grant).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/quests/{quest_id}/grants/revoke", headers=headers, json=grant
        ).status_code
        == 200
    )


def test_api_model_measurements_cannot_claim_benchmark_provenance(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "model-api.sqlite3"))
    from forge_api import main

    importlib.reload(main)
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    session = client.get("/health").json()["session"]
    token = hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
    payload = {
        "id": "m",
        "provider": "example",
        "display_name": "m",
        "capability_measurements": {
            "coding": {
                "value": 90,
                "source": "benchmark",
                "n_samples": 999,
                "benchmark_version": "fake",
            }
        },
        "performance_measurements": {"ttft": {"value": 2, "source": "gateway", "n_samples": 999}},
    }
    result = client.post("/api/models", headers={"Authorization": f"Bearer {token}"}, json=payload)
    assert result.status_code == 200
    profile = client.get("/api/models", headers={"Authorization": f"Bearer {token}"}).json()[0]
    for field, key in (("capability_measurements", "coding"), ("performance_measurements", "ttft")):
        assert profile[field][key]["source"] == "user_override"
        assert profile[field][key]["n_samples"] is None
        assert profile[field][key]["benchmark_version"] is None


def test_router_gateway_observations_are_recorded_as_mock(tmp_path, monkeypatch):
    import json

    import httpx

    secret = secrets.token_hex(32)
    settings = {
        "FORGE_CORE_SECRET": secret,
        "FORGE_DB_PATH": str(tmp_path / "observations.sqlite3"),
        "FORGE_PROVIDER_ID": "test",
        "FORGE_PROVIDER_KIND": "openai_compatible",
        "FORGE_PROVIDER_BASE_URL": "https://example.test/v1",
        "FORGE_PROVIDER_CREDENTIAL_REF": "FORGE_PROVIDER_KEY_TEST",
        "FORGE_PROVIDER_KEY_TEST": "placeholder",
        "FORGE_PROVIDER_SUPPORTS_TOOLS": "true",
        "FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA": "true",
        "FORGE_ROUTER_MODEL_ID": "model",
        "FORGE_ROUTER_CONTEXT_LIMIT": "32768",
        "FORGE_MODEL_TOKEN_LIMIT": "100000",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    from forge_api import main

    importlib.reload(main)
    decision = {
        "intent": "chat",
        "reply": "你好",
        "title": None,
        "goal": None,
        "quest_id": None,
        "candidates": [],
    }
    main.provider._transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(decision)}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )
    )
    main.provider.execution_mode = "mock"
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    session = client.get("/health").json()["session"]
    token = hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
    response = client.post(
        "/api/conversation/message",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": "你好"},
    )
    assert response.status_code == 200
    with main.store.connection() as connection:
        row = connection.execute("SELECT * FROM model_observations").fetchone()
    assert row["mode"] == "mock"
    assert row["model_id"] == "model"
    assert row["outcome"] == "success"


def test_core_model_budget_uses_operator_price_and_reconciliation(tmp_path, monkeypatch):
    import json

    secret = secrets.token_hex(32)
    config = {
        "FORGE_CORE_SECRET": secret,
        "FORGE_DB_PATH": str(tmp_path / "budget.sqlite3"),
        "FORGE_MODEL_TOKEN_LIMIT": "30000",
        "FORGE_MODEL_CONCURRENCY_LIMIT": "2",
        "FORGE_MODEL_COST_LIMIT_MICROUNITS": "5000",
        "FORGE_MODEL_COST_CURRENCY": "USD",
        "FORGE_MODEL_SCOPE_LIMITS_JSON": json.dumps(
            [{"scope": "quest", "scope_id": "q", "token_limit": 3000, "concurrency_limit": 1}]
        ),
        "FORGE_MODEL_HISTORICAL_COST_MICROUNITS": "100",
        "FORGE_MODEL_SCOPE_COST_RECONCILIATIONS_JSON": json.dumps(
            [{"scope": "quest", "scope_id": "q", "currency": "USD", "cost_microunits": 7}]
        ),
        "FORGE_PRICE_BOUNDS_JSON": json.dumps(
            [
                {
                    "provider_id": "p",
                    "model_id": "m",
                    "currency": "USD",
                    "input_microunits_per_million": 100,
                    "output_microunits_per_million": 200,
                    "version": "v1",
                    "covers_all_input_cache_and_fees": True,
                    "covers_all_output_and_fees": True,
                }
            ]
        ),
    }
    for key, value in config.items():
        monkeypatch.setenv(key, value)
    from forge_api import main

    importlib.reload(main)
    assert main.model_budget.token_limit == 30000
    assert main.model_budget.price_bounds[("p", "m")].output_microunits_per_million == 200
    assert main.model_budget.scope_cost_reconciliations[("quest", "q", "USD")] == 7
    assert main.model_budget.scope_limits[("quest", "q")] == (3000, 1)


def test_composer_model_call_uses_gateway_budget(tmp_path, monkeypatch):
    import asyncio

    import httpx

    from forge_core.domain import Quest

    values = {
        "FORGE_CORE_SECRET": secrets.token_hex(32),
        "FORGE_DB_PATH": str(tmp_path / "composer.sqlite3"),
        "FORGE_PROVIDER_ID": "test",
        "FORGE_PROVIDER_KIND": "openai_compatible",
        "FORGE_PROVIDER_BASE_URL": "https://example.invalid/v1",
        "FORGE_PROVIDER_CREDENTIAL_REF": "FORGE_PROVIDER_KEY_TEST",
        "FORGE_PROVIDER_KEY_TEST": "placeholder",
        "FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA": "true",
        "FORGE_ROUTER_MODEL_ID": "m",
        "FORGE_ROUTER_CONTEXT_LIMIT": "32768",
        "FORGE_MODEL_TOKEN_LIMIT": "100000",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    from forge_api import main

    importlib.reload(main)
    main.provider._transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            },
        )
    )
    main.provider.execution_mode = "mock"
    quest = Quest(title="compose", goal="compose task")
    main.store.save_quest(quest)
    plan = main.quest_graph.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": "work",
                    "success_criteria": ["done"],
                    "budget_tokens": 20000,
                }
            ],
            "edges": [],
            "max_total_tokens": 30000,
        },
        expected_version=0,
    )
    main.quest_graph.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    for _ in range(10):
        main.dispatcher.tick()
    assert asyncio.run(main.process_pending_team()) is False
    with main.store.connection() as connection:
        row = connection.execute(
            "SELECT purpose,quest_id FROM model_budget_attempts WHERE purpose='team_composer'"
        ).fetchone()
    assert (row["purpose"], row["quest_id"]) == ("team_composer", quest.id)
