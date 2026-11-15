import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from forge_core.store import Store


@pytest.fixture
def clean_forge_env(monkeypatch):
    for name in (
        "FORGE_WORKSPACE_ROOT",
        "FORGE_WORKSPACE_ID",
        "FORGE_DB_PATH",
        "FORGE_ENV_FILE",
        "FORGE_HOST",
        "FORGE_PORT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_same_workspace_has_same_router_from_different_startup_directories(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".env").write_text("FORGE_DB_PATH=state/shared.sqlite3\n", encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("FORGE_")}
    environment["FORGE_WORKSPACE_ROOT"] = str(root)
    environment["FORGE_CORE_SECRET"] = secrets.token_hex(32)
    command = [
        sys.executable,
        "-c",
        "import json; from pathlib import Path; import forge_api.main as m; print(json.dumps({'db':str(Path(m.db_path).resolve()),'router':m.store.ensure_router()['router_session_id']}))",
    ]
    observations = []
    for name in ("cli-cwd", "desktop-cwd"):
        cwd = tmp_path / name
        cwd.mkdir()
        (cwd / ".env").write_text("FORGE_DB_PATH=wrong.sqlite3\n", encoding="utf-8")
        child = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        observations.append(json.loads(child.stdout))
    assert observations[0] == observations[1]
    assert observations[0]["db"] == str((root / "state" / "shared.sqlite3").resolve())


def test_settings_precedence_and_absolute_paths(tmp_path, monkeypatch, clean_forge_env):
    from forge_core.settings import Settings

    (tmp_path / ".env").write_text(
        "FORGE_DB_PATH=from-file.sqlite3\nFORGE_PORT=9001\n", encoding="utf-8"
    )
    from_file = Settings.load(workspace_root=tmp_path)
    assert from_file.db_path == (tmp_path / "from-file.sqlite3").resolve()
    assert from_file.port == 9001
    monkeypatch.setenv("FORGE_DB_PATH", "from-environment.sqlite3")
    monkeypatch.setenv("FORGE_PORT", "9002")
    from_environment = Settings.load(workspace_root=tmp_path)
    assert from_environment.db_path == (tmp_path / "from-environment.sqlite3").resolve()
    assert from_environment.port == 9002


def test_relative_workspace_is_rejected(clean_forge_env):
    from forge_core.settings import Settings

    with pytest.raises(ValueError, match="absolute"):
        Settings.load(workspace_root=Path("relative-workspace"))


def test_invalid_settings_do_not_fall_back_silently(tmp_path, monkeypatch, clean_forge_env):
    from forge_core.settings import Settings

    monkeypatch.setenv("FORGE_PORT", "not-a-port")
    with pytest.raises(ValueError, match="FORGE_PORT"):
        Settings.load(workspace_root=tmp_path)


def test_workspace_binding_prevents_accidental_database_reuse(tmp_path, clean_forge_env):
    from forge_core.settings import Workspace

    first_workspace = Workspace.from_path(tmp_path / "first")
    second_workspace = Workspace.from_path(tmp_path / "second")
    path = tmp_path / "shared.sqlite3"
    first = Store(path, workspace=first_workspace)
    identity = first.ensure_router()["router_session_id"]
    with pytest.raises(ValueError, match="(?i)workspace"):
        Store(path, workspace=second_workspace)
    assert Store(path, workspace=first_workspace).ensure_router()["router_session_id"] == identity


def test_relative_store_path_is_workspace_relative(tmp_path, monkeypatch, clean_forge_env):
    from forge_core.settings import Workspace

    workspace = Workspace.from_path(tmp_path / "root")
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    store = Store("state/relative.sqlite3", workspace=workspace)
    assert Path(store.path) == (workspace.root / "state" / "relative.sqlite3").resolve()


def test_default_workspace_is_user_scoped_not_installation_scoped(clean_forge_env):
    from forge_core.settings import Settings

    settings = Settings.load()
    if os.name == "nt":
        expected = Path(os.environ["LOCALAPPDATA"]) / "FORGE" / "workspaces" / "default"
    elif sys.platform == "darwin":
        expected = (
            Path.home() / "Library" / "Application Support" / "FORGE" / "workspaces" / "default"
        )
    else:
        expected = (
            Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
            / "forge"
            / "workspaces"
            / "default"
        )
    assert settings.workspace.root == expected.resolve()


def test_direct_workspace_constructor_cannot_bypass_absolute_path_rule(clean_forge_env):
    from forge_core.settings import Workspace

    with pytest.raises(ValueError, match="absolute"):
        Workspace(id="manual", root=Path("relative"))


def test_explicit_empty_workspace_identity_is_rejected(tmp_path, clean_forge_env):
    from forge_core.settings import Workspace

    with pytest.raises(ValueError, match="empty"):
        Workspace.from_path(tmp_path, workspace_id="")
