"""Actual application startup, retry and shutdown contracts."""

import importlib
import secrets
import time

from fastapi.testclient import TestClient


def test_lifespan_recovers_one_tick_failure_and_closes_all_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
        "FORGE_PROVIDERS_JSON",
        "FORGE_PRICE_BOUNDS_JSON",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    calls = 0

    async def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient test fault")

    monkeypatch.setattr(main, "process_pending_plan", fail_once)
    main.router.model_id = "test-enable"
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        deadline = time.monotonic() + 4
        while calls < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert calls >= 2
        assert main.app.state.background_health["planner"] == "ok"
        assert main.app.state.worker_manager is not None
        assert "worker" in main.app.state.background_health
        assert main.app.state.background_health["verifier"] == "ok"
    assert all(task.done() for task in main.app.state.background_tasks)
    assert main.app.state.worker_scheduler.stopping
    assert not main.app.state.worker_scheduler.tasks


def test_network_allowlist_is_operator_only_and_invalid_config_fails_closed(tmp_path, monkeypatch):
    import pytest

    from forge_core.store import Store
    from forge_core.tool_engine import ToolEngine

    store = Store(tmp_path / "config.sqlite3")
    monkeypatch.setenv("FORGE_TOOL_NETWORK_HOSTS_JSON", '["example.test"]')
    monkeypatch.setenv(
        "FORGE_MCP_SERVERS_JSON",
        '{"research":{"url":"https://example.test/rpc","tools":["search"]}}',
    )
    engine = ToolEngine.from_environment(store)
    assert engine._allowed_url("https://example.test/paper").hostname == "example.test"
    monkeypatch.setenv(
        "FORGE_MCP_SERVERS_JSON", '{"bad":{"url":"https://outside.test/rpc","tools":["read"]}}'
    )
    with pytest.raises(ValueError, match="operator tool"):
        ToolEngine.from_environment(store)
