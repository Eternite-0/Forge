"""Local project identity is durable and confers no execution rights."""

import pytest

from forge_core.domain import Quest
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.workspace_discovery import WorkspaceDiscovery, detect_project, migrate


def ready_store(root):
    store = Store(root / "discovery.sqlite3", workspace=Workspace.from_path(root))
    with store.unit_of_work() as work:
        if not work.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='workspace_projects'"
        ).fetchone():
            migrate(work.connection)
    return store


@pytest.mark.parametrize("marker", ["pyproject.toml", "package.json", "Cargo.toml", ".git"])
def test_nested_path_finds_manifest_without_reading_content(tmp_path, marker):
    root = tmp_path / "project"
    child = root / "src" / "nested"
    child.mkdir(parents=True)
    (root / marker).write_bytes(b"not valid manifest content; identity does not parse it")
    project = detect_project(child, boundary=root)
    assert project["root"] == str(root)
    assert project["markers"] == [marker]
    assert not project["grants_tool_access"]
    assert detect_project(root)["id"] == project["id"]


def test_plain_folder_and_nearest_nested_project(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    assert detect_project(root, boundary=root)["source"] == "local_directory"
    (root / "package.json").touch()
    nested = root / "nested"
    nested.mkdir()
    (nested / "pyproject.toml").touch()
    assert detect_project(nested, boundary=root)["root"] == str(nested)


def test_cross_project_association_survives_restart_without_new_grants(tmp_path):
    storage, external = tmp_path / "forge", tmp_path / "research"
    external.mkdir()
    (external / "pyproject.toml").touch()
    store = ready_store(storage)
    quest = Quest(title="Compare projects", goal="Work in the named project")
    store.save_quest(quest)
    discovery = WorkspaceDiscovery(store)
    first = discovery.associate(quest.id, external)
    assert discovery.associate(quest.id, external) == first
    restarted = Store(store.path, workspace=store.workspace)
    assert WorkspaceDiscovery(restarted).for_quest(quest.id) == first
    assert WorkspaceDiscovery(restarted).list_projects() == [first]
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM task_grants").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        assert connection.execute("SELECT root FROM workspace").fetchone()[0] == str(storage)
    assert (
        len(
            [
                event
                for event in store.list_events()
                if event["event_type"] == "workspace.task_associated"
            ]
        )
        == 1
    )


def test_missing_task_does_not_leave_catalog_rows(tmp_path):
    store = ready_store(tmp_path)
    with pytest.raises(KeyError):
        WorkspaceDiscovery(store).associate("missing", tmp_path)
    assert WorkspaceDiscovery(store).list_projects() == []
    assert store.list_events() == []


def test_catalog_link_and_outbox_roll_back_together(tmp_path, monkeypatch):
    from forge_core.store import UnitOfWork

    store = ready_store(tmp_path)
    quest = Quest(title="Project", goal="Associate locally")
    store.save_quest(quest)

    def fail_event(*args, **kwargs):
        raise RuntimeError("transaction failure")

    monkeypatch.setattr(UnitOfWork, "append_event", fail_event)
    with pytest.raises(RuntimeError, match="transaction failure"):
        WorkspaceDiscovery(store).associate(quest.id, tmp_path)
    assert WorkspaceDiscovery(store).list_projects() == []
    assert WorkspaceDiscovery(store).for_quest(quest.id) is None
    assert store.list_outbox() == []


def test_detection_rejects_relative_missing_and_escaped_boundary(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        detect_project("relative")
    with pytest.raises(FileNotFoundError):
        detect_project(tmp_path / "missing")
    (tmp_path / "boundary").mkdir()
    with pytest.raises(ValueError, match="outside"):
        detect_project(tmp_path, boundary=tmp_path / "boundary")


def test_request_automatically_associates_named_project_with_spaces(tmp_path):
    from forge_core.domain import ConversationMessage
    from forge_core.superagent_delegation import issue_mandate

    external = tmp_path / "external research"
    external.mkdir()
    (external / "package.json").touch()
    store = ready_store(tmp_path / "storage")
    message = ConversationMessage(role="user", content=f"Inspect `{external}` and report here.")
    store.add_message(message)
    quest = Quest(title="Work", goal="Summarized goal without the original path")
    store.save_quest(quest)
    with store.unit_of_work() as work:
        issue_mandate(work, quest.id, message.id)
    result = WorkspaceDiscovery(store).associate_request(quest.id)
    assert result["root"] == str(external)
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM task_grants").fetchone()[0] == 0


def test_multiple_named_projects_do_not_silently_choose_one(tmp_path):
    store = ready_store(tmp_path / "storage")
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
        (root / "Cargo.toml").touch()
    quest = Quest(title="Compare", goal=f"Compare `{roots[0]}` and `{roots[1]}`")
    store.save_quest(quest)
    result = WorkspaceDiscovery(store).associate_request(quest.id)
    assert result["root"] == str(store.workspace.root)
