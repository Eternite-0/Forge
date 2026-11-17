"""Local identity regressions; no model or Provider is called."""

import hashlib
import json

import pytest

from forge_core.domain import ConversationMessage, Quest
from forge_core.project_continuity import project_context, project_record_allowed
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.superagent_delegation import issue_mandate
from forge_core.workspace_discovery import WorkspaceDiscovery


def project_setup(tmp_path, content):
    paths = []
    for name in ("alpha_project", "beta_project"):
        path = tmp_path / name
        path.mkdir()
        (path / "pyproject.toml").write_text("[project]\nname='" + name + "'\n", encoding="utf-8")
        paths.append(path)
    store = Store(tmp_path / "data" / "forge.sqlite3", workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Inspect local project", goal="A model-derived summary")
    source = ConversationMessage(role="user", content=content(*paths))
    with store.unit_of_work() as work:
        work.add_message(source)
        work.save_quest(quest)
        mandate = issue_mandate(work, quest.id, source.id)
    return store, quest, source, mandate, paths


def test_cjk_adjacent_ascii_project_matches_without_partial_english_names(tmp_path):
    store, quest, _, _, paths = project_setup(tmp_path, lambda *_: "Inspect the project")
    project = WorkspaceDiscovery(store).associate(quest.id, paths[0])
    with store.connection() as c:
        assert project_context(c, "继续alpha_project的任务")["focused_project_id"] == project["id"]
        assert project_context(c, "alpha_project项目进度")["focused_project_id"] == project["id"]
        for partial in (
            "myalpha_project",
            "alpha_project_extra",
            "alpha_project-plugin",
            "alpha_project2",
        ):
            assert project_context(c, partial)["selected_project_ids"] == []


def test_single_immutable_user_root_corrects_model_association_without_permissions(tmp_path):
    store, quest, source, mandate, paths = project_setup(
        tmp_path, lambda alpha, _: "请分析这个本地项目 `" + str(alpha) + "` 并提供建议"
    )
    discovery = WorkspaceDiscovery(store)
    wrong = discovery.associate(quest.id, paths[1])
    assistant = ConversationMessage(
        role="assistant",
        content="I will inspect the named project",
        metadata={"project_id": wrong["id"], "cycle": {"quest_id": quest.id}},
    )
    with store.unit_of_work() as work:
        work.add_message(assistant)
    with store.connection() as c:
        original_mandate = dict(
            c.execute("SELECT * FROM delegation_mandates WHERE id=?", (mandate,)).fetchone()
        )
        grants = [
            dict(row)
            for row in c.execute("SELECT * FROM task_grants WHERE quest_id=?", (quest.id,))
        ]
    corrected = discovery.associate_request(quest.id)
    assert corrected["root"] == str(paths[0]) and corrected["id"] != wrong["id"]
    assert corrected["grants_tool_access"] is False
    assert discovery.associate_request(quest.id)["id"] == corrected["id"]
    with store.connection() as c:
        assert (
            dict(c.execute("SELECT * FROM delegation_mandates WHERE id=?", (mandate,)).fetchone())
            == original_mandate
        )
        assert [
            dict(row)
            for row in c.execute("SELECT * FROM task_grants WHERE quest_id=?", (quest.id,))
        ] == grants
        assert (
            c.execute("SELECT content FROM messages WHERE id=?", (source.id,)).fetchone()[0]
            == source.content
        )
        reply = dict(c.execute("SELECT * FROM messages WHERE id=?", (assistant.id,)).fetchone())
        assert project_record_allowed(c, "message", reply, corrected["id"])
        assert not project_record_allowed(c, "message", reply, wrong["id"])
    changes = [
        row for row in store.list_events() if row["event_type"] == "workspace.task_associated"
    ]
    assert len(changes) == 2 and json.loads(changes[-1]["payload"])["project_id"] == corrected["id"]
    assert original_mandate["source_sha256"] == hashlib.sha256(source.content.encode()).hexdigest()


@pytest.mark.parametrize("request_shape", ["none", "multiple"])
def test_absent_or_multiple_explicit_roots_preserve_existing_identity(tmp_path, request_shape):
    store, quest, _, _, paths = project_setup(
        tmp_path,
        lambda alpha, beta: (
            "Inspect the selected project"
            if request_shape == "none"
            else "Compare `" + str(alpha) + "` and `" + str(beta) + "`"
        ),
    )
    discovery = WorkspaceDiscovery(store)
    existing = discovery.associate(quest.id, paths[1])
    event_count = len(store.list_events())
    assert discovery.associate_request(quest.id)["id"] == existing["id"]
    assert len(store.list_events()) == event_count


def test_mutable_goal_never_overrides_existing_identity(tmp_path):
    store, _, _, _, paths = project_setup(tmp_path, lambda *_: "Keep the original task")
    manual = Quest(title="Manual task", goal="Inspect `" + str(paths[0]) + "`")
    store.save_quest(manual)
    discovery = WorkspaceDiscovery(store)
    existing = discovery.associate(manual.id, paths[1])
    assert discovery.associate_request(manual.id)["id"] == existing["id"]
