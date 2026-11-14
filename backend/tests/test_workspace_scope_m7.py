"""Workspace grant paths cannot alias protected Core files on Windows."""

import os

import pytest

from forge_core.domain import DomainError, Quest
from forge_core.governance import GovernanceService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.tool_engine import ToolEngine


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
@pytest.mark.parametrize(
    "scope",
    [
        ".env::$DATA",
        "data/operator-settings.json::$DATA",
        ".forge-restore-fence.json::$DATA",
        "work/NUL.txt",
        "work/trailing.",
        "work/trailing ",
        "\\\\.\\NUL",
    ],
)
def test_windows_aliases_cannot_gain_workspace_grant(tmp_path, scope):
    (tmp_path / ".env").write_text("DUMMY=fixture", encoding="utf-8")
    store = Store(tmp_path / "data" / "forge.sqlite3", workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="scope", goal="test")
    store.save_quest(quest)
    with pytest.raises(DomainError):
        GovernanceService(store).grant_workspace(quest.id, scope, "user")


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS alternate stream alias")
def test_ntfs_default_stream_alias_reads_dummy_but_tool_rejects(tmp_path):
    secret_file = tmp_path / ".env"
    secret_file.write_text("DUMMY=fixture", encoding="utf-8")
    alias = str(secret_file) + "::$DATA"
    with open(alias, encoding="utf-8") as stream:
        assert stream.read() == "DUMMY=fixture"
    store = Store(tmp_path / "data" / "forge.sqlite3", workspace=Workspace.from_path(tmp_path))
    with pytest.raises(DomainError):
        ToolEngine(store)._scope("file_read", {"path": alias})
