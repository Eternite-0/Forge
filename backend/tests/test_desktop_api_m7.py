"""Authenticated desktop API exposes paginated user history and bounded status."""

import hashlib
import hmac
import importlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from forge_core.domain import ConversationMessage, ForgeEvent, ModelProfile, Quest
from forge_core.memory import MemoryService


def test_desktop_api_history_memory_and_redaction(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("FORGE_PROVIDER_KEY_PRIVATE", "never-return-this-key")
    from forge_api import main

    importlib.reload(main)
    for index in range(7):
        main.store.add_message(ConversationMessage(role="user", content=f"entry-{index}"))
    for index in range(7):
        main.store.save_quest(Quest(title=f"quest-{index}", goal="example"))
    memory = MemoryService(main.store)
    memory_id = memory.remember(
        "example project note", kind="project", layer="L2", source_refs=["user:fixture"]
    )
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    challenge = client.get("/health").json()["session"]
    bearer = hmac.new(bytes.fromhex(secret), challenge.encode(), hashlib.sha256).hexdigest()
    headers = {"Authorization": "Bearer " + bearer}
    assert client.get("/api/memory?query=project").status_code == 401
    first = client.get("/api/conversation?limit=3", headers=headers).json()
    second = client.get(
        "/api/conversation", params={"limit": 3, "before": first["next_before"]}, headers=headers
    ).json()
    assert [item["content"] for item in first["messages"]] == ["entry-4", "entry-5", "entry-6"]
    assert [item["content"] for item in second["messages"]] == ["entry-1", "entry-2", "entry-3"]
    assert first["has_more"] and second["has_more"]
    assert (
        client.get("/api/conversation", params={"after": first["resume_after"]}).status_code == 401
    )
    for index in range(7, 12):
        main.store.add_message(ConversationMessage(role="assistant", content=f"entry-{index}"))
    delta_response = client.get(
        "/api/conversation", params={"limit": 3, "after": first["resume_after"]}, headers=headers
    )
    assert delta_response.status_code == 200
    delta = delta_response.json()
    assert delta["mode"] == "delta" and delta["has_newer"]
    assert [item["content"] for item in delta["messages"]] == ["entry-7", "entry-8", "entry-9"]
    tail = client.get(
        "/api/conversation", params={"limit": 3, "after": delta["resume_after"]}, headers=headers
    ).json()
    assert not tail["has_newer"] and [item["content"] for item in tail["messages"]] == [
        "entry-10",
        "entry-11",
    ]
    assert client.get("/api/conversation?after=%3F", headers=headers).status_code == 422
    assert (
        client.get(
            "/api/conversation",
            params={"before": first["next_before"], "after": first["resume_after"]},
            headers=headers,
        ).status_code
        == 422
    )
    quest_page = client.get("/api/quests?limit=3", headers=headers).json()
    older = client.get(
        "/api/quests", params={"limit": 3, "before": quest_page["next_before"]}, headers=headers
    ).json()
    assert [item["title"] for item in quest_page["items"]] == ["quest-6", "quest-5", "quest-4"]
    assert [item["title"] for item in older["items"]] == ["quest-3", "quest-2", "quest-1"]
    assert isinstance(client.get("/api/quests", headers=headers).json(), list)
    items = client.get("/api/memory?query=project", headers=headers).json()["items"]
    assert any(item["id"] == memory_id for item in items)
    assert client.post(f"/api/memory/{memory_id}/revoke", headers=headers).status_code == 200
    assert all(
        item["id"] != memory_id
        for item in client.get("/api/memory?query=project", headers=headers).json()["items"]
    )
    for path in ("/api/activity", "/api/settings", "/api/budget"):
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assert secret not in response.text and "never-return-this-key" not in response.text


def test_approval_requires_seen_digest_and_current_revision(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "approval.sqlite3"))
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="task", goal="review")
    main.store.save_quest(quest)
    approval = main.governance.request_approval(
        quest.id, "publish", {"file": "one"}, "example", "R2", "publish"
    )
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    challenge = client.get("/health").json()["session"]
    headers = {
        "Authorization": "Bearer "
        + hmac.new(bytes.fromhex(secret), challenge.encode(), hashlib.sha256).hexdigest()
    }
    url = f"/api/approvals/{approval['id']}/decision"
    assert client.post(url, headers=headers, json={"decision": "APPROVED"}).status_code == 422
    body = {
        "decision": "APPROVED",
        "expected_digest": "0" * 64,
        "expected_plan_revision": approval["plan_revision"],
    }
    assert client.post(url, headers=headers, json=body).status_code == 409
    assert main.governance.get_approval(approval["id"])["status"] == "PENDING"
    body["expected_digest"] = approval["parameter_digest"]
    with main.store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (quest.id,)
        )
    assert client.post(url, headers=headers, json=body).status_code == 409
    assert main.governance.get_approval(approval["id"])["status"] == "PENDING"


def test_workspace_grant_requires_current_revision(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("FORGE_DB_PATH", raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="task", goal="review")
    main.store.save_quest(quest)
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    challenge = client.get("/health").json()["session"]
    headers = {
        "Authorization": "Bearer "
        + hmac.new(bytes.fromhex(secret), challenge.encode(), hashlib.sha256).hexdigest()
    }
    url = f"/api/quests/{quest.id}/grants"
    assert client.post(url, headers=headers, json={"scope": "work"}).status_code == 422
    assert (
        client.post(
            url, headers=headers, json={"scope": "work", "expected_plan_revision": 1}
        ).status_code
        == 409
    )
    assert (
        client.post(
            url, headers=headers, json={"scope": "work", "expected_plan_revision": 0}
        ).status_code
        == 200
    )
    assert (
        client.post(
            url + "/revoke", headers=headers, json={"scope": "work", "expected_plan_revision": 1}
        ).status_code
        == 409
    )
    assert (
        client.post(
            url + "/revoke", headers=headers, json={"scope": "work", "expected_plan_revision": 0}
        ).status_code
        == 200
    )


def test_settings_write_is_versioned_and_cannot_set_secrets(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("FORGE_DB_PATH", raising=False)
    from forge_api import main

    importlib.reload(main)
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    challenge = client.get("/health").json()["session"]
    headers = {
        "Authorization": "Bearer "
        + hmac.new(bytes.fromhex(secret), challenge.encode(), hashlib.sha256).hexdigest()
    }
    current = client.get("/api/settings", headers=headers).json()
    config = {
        "provider": {
            "id": "sample",
            "kind": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "credential_ref": "FORGE_PROVIDER_KEY_SAMPLE",
            "supports_tools": True,
            "supports_json_schema": True,
        },
        "router_model_id": "model",
        "router_context_limit": 32768,
        "model_token_limit": 50000,
        "model_concurrency_limit": 2,
        "network_hosts": ["example.invalid"],
    }
    body = {"expected_revision": current["revision"], "config": config}
    assert client.post("/api/settings", json=body).status_code == 401
    updated = client.post("/api/settings", headers=headers, json=body)
    assert updated.status_code == 200 and updated.json()["restart_required"] is True
    assert client.post("/api/settings", headers=headers, json=body).status_code == 409
    body["expected_revision"] = updated.json()["revision"]
    body["config"]["provider"]["api_key"] = "never-store"
    assert client.post("/api/settings", headers=headers, json=body).status_code == 422
    assert "never-store" not in (tmp_path / "data" / "operator-settings.json").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "url,opt_in,provider_id,accepted",
    [
        ("http://127.0.0.1:8788/v1", False, "valid", False),
        ("http://127.0.0.1:8788/v1", True, "valid", True),
        ("http://localhost:8788/v1", True, "valid", False),
        ("https://example.invalid/v1?token=x", False, "valid", False),
        ("https://example.invalid/v1", False, "bad id", False),
    ],
)
def test_operator_provider_loopback_and_identity_validation(url, opt_in, provider_id, accepted):
    from forge_core.operator_settings import OperatorConfig

    config = {
        "provider": {
            "id": provider_id,
            "kind": "openai_compatible",
            "base_url": url,
            "credential_ref": "FORGE_PROVIDER_KEY_SAMPLE",
            "supports_tools": True,
            "supports_json_schema": True,
            "allow_insecure_loopback": opt_in,
        },
        "router_model_id": "model",
        "router_context_limit": 32768,
        "model_token_limit": 50000,
        "model_concurrency_limit": 2,
        "network_hosts": [],
    }
    if accepted:
        assert OperatorConfig.model_validate(config).provider.allow_insecure_loopback
    else:
        with pytest.raises(ValueError):
            OperatorConfig.model_validate(config)


def test_model_edit_preserves_measurements_and_rejects_stale_version(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "model.sqlite3"))
    from forge_api import main

    importlib.reload(main)
    profile = ModelProfile(
        id="model",
        provider="provider",
        display_name="Original",
        capabilities={"coding": 42},
        provenance={"coding": {"source": "benchmark", "version": "v1"}},
    )
    main.store.save_model(profile)
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    challenge = client.get("/health").json()["session"]
    headers = {
        "Authorization": "Bearer "
        + hmac.new(bytes.fromhex(secret), challenge.encode(), hashlib.sha256).hexdigest()
    }
    version = client.get("/api/models/model", headers=headers).json()["updated_at"]
    payload = {"expected_updated_at": version, "display_name": "Edited", "enabled": False}
    assert client.patch("/api/models/model", headers=headers, json=payload).status_code == 200
    assert client.patch("/api/models/model", headers=headers, json=payload).status_code == 409
    saved = main.store.list_models()[0]
    assert saved.display_name == "Edited" and not saved.enabled
    assert saved.capabilities == {"coding": 42} and saved.provenance == profile.provenance
    assert (
        client.post(
            "/api/models", headers=headers, json=profile.model_dump(mode="json")
        ).status_code
        == 409
    )


def test_timeline_is_persisted_paginated_and_never_dumps_payload(tmp_path, monkeypatch):
    from forge_core.context_service import ContextService

    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("FORGE_DB_PATH", raising=False)
    from forge_api import main

    importlib.reload(main)
    for index in range(5):
        main.store.append_event(
            ForgeEvent(
                event_type="worker.blocked",
                payload={"private": "DO_NOT_EXPOSE", "run_id": f"run_{index}"},
            )
        )
    main.store.add_message(ConversationMessage(role="user", content="source"))
    ContextService(main.store).compact_router(keep_recent=0)
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    challenge = client.get("/health").json()["session"]
    headers = {
        "Authorization": "Bearer "
        + hmac.new(bytes.fromhex(secret), challenge.encode(), hashlib.sha256).hexdigest()
    }
    assert client.get("/api/timeline").status_code == 401
    first = client.get("/api/timeline?limit=3", headers=headers).json()
    second = client.get(
        "/api/timeline", params={"limit": 3, "before": first["next_before"]}, headers=headers
    ).json()
    combined = first["items"] + second["items"]
    assert len(combined) == 6 and len({(item["kind"], item["event_id"]) for item in combined}) == 6
    assert any(item["kind"] == "context_archive" for item in combined)
    assert "DO_NOT_EXPOSE" not in json.dumps(combined) and secret not in json.dumps(combined)


def test_user_message_timestamp_precedes_durable_activity_during_model_call(tmp_path):
    import asyncio

    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService
    from forge_core.runtime import ModelResponse
    from forge_core.settings import Workspace
    from forge_core.store import Store

    store = Store(tmp_path / "history.sqlite3", workspace=Workspace.from_path(tmp_path))

    class Delayed:
        execution_mode = "mock"

        async def generate(self, request):
            await asyncio.sleep(0.02)
            store.append_event(ForgeEvent(event_type="worker.blocked", payload={}))
            decision = {
                "intent": "chat",
                "reply": "ok",
                "title": None,
                "goal": None,
                "quest_id": None,
                "candidates": [],
            }
            return ModelResponse(
                text=json.dumps(decision), tool_calls=[], usage={}, finish_reason="mock"
            )

    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=Delayed(), model_id="mock"
    )
    result = asyncio.run(router.handle_user_message("hello", request_id="request-1"))
    event = store.list_events()[0]
    assert result["user_message"]["created_at"] < event["created_at"]
    assert result["user_message"]["metadata"]["request_id"] == "request-1"
    assert len(store.list_messages()) == 2
