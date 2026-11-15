"""Actual single-workspace scope is additive and read-only in desktop projections."""

import hashlib
import hmac
import importlib
import json
import secrets

from fastapi.testclient import TestClient

from forge_core.domain import Quest
from forge_core.project_context import project_quest, workspace_project_context
from forge_core.settings import Workspace


def test_workspace_projection_does_not_promote_quest_metadata(tmp_path):
    workspace = Workspace.from_path(tmp_path / "Current", "stable-workspace")
    metadata = json.dumps({"workspace_id": "forged", "project": {"id": "label-only"}})
    quest = {"id": "quest-one", "title": "not-a-project", "metadata": metadata}
    before = dict(quest)
    projected = project_quest(quest, workspace)
    context = projected["project_context"]
    assert quest == before
    assert projected["metadata"] == metadata
    assert projected["workspace_id"] == workspace.id
    assert projected["workspace_root"] == str(workspace.root)
    assert context == workspace_project_context(workspace)
    assert context["scope"] == "current_workspace"
    assert context["default_project"]["key"] == "workspace:stable-workspace"
    assert context["default_project"]["source"] == "workspace_binding"
    assert not any(context["capabilities"].values())
    other = Workspace.from_path(tmp_path / "Other" / "Current", "other-workspace")
    assert workspace_project_context(other)["workspace"]["name"] == context["workspace"]["name"]
    assert (
        workspace_project_context(other)["default_project"]["key"]
        != context["default_project"]["key"]
    )


def test_authenticated_settings_and_quest_pages_share_current_scope(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FORGE_WORKSPACE_ID", "scope-contract")
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "scope.sqlite3"))
    from forge_api import main

    importlib.reload(main)
    quests = [
        Quest(title=f"Task {index}", goal="test", metadata={"project_id": "display-only"})
        for index in range(3)
    ]
    for quest in quests:
        main.store.save_quest(quest)
    with main.store.connection() as connection:
        snapshot = list(connection.iterdump())
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/quests").status_code == 401
    challenge = client.get("/health").json()["session"]
    bearer = hmac.new(bytes.fromhex(secret), challenge.encode(), hashlib.sha256).hexdigest()
    headers = {"Authorization": "Bearer " + bearer}
    summary = client.get("/api/settings", headers=headers).json()
    first = client.get("/api/quests?limit=2", headers=headers).json()
    second = client.get(
        f"/api/quests?limit=2&before={first['next_before']}", headers=headers
    ).json()
    legacy = client.get("/api/quests", headers=headers).json()
    detail = client.get(f"/api/quests/{quests[0].id}", headers=headers).json()
    assert first["has_more"] and not second["has_more"]
    assert len(legacy) == 3
    expected = summary["project_context"]
    assert expected["workspace"]["id"] == "scope-contract"
    for quest in [*first["items"], *second["items"], *legacy, detail["quest"]]:
        assert quest["project_context"] == expected
        assert quest["workspace_id"] == summary["workspace_id"]
        assert quest["workspace_root"] == summary["workspace_root"]
        assert json.loads(quest["metadata"]) == {"project_id": "display-only"}
    with main.store.connection() as connection:
        assert list(connection.iterdump()) == snapshot
