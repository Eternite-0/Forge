"""Scripted Router decisions against real multi-project state, not live models."""

import asyncio
import json

import pytest
from test_router_responsiveness_superagent import background_task
from test_superagent_loop import MockProvider, conversational_decision, response
from test_superagent_loop import rig as base_rig

from forge_core.context_service import ContextService
from forge_core.domain import ConversationMessage, DomainError, ForgeEvent
from forge_core.project_continuity import project_context
from forge_core.router_domain import ExecutionTeam, RouterDecision
from forge_core.workspace_discovery import WorkspaceDiscovery


@pytest.mark.parametrize("suffix", ["bet", "-plugin", "_plugin", ".backup"])
def test_path_prefix_is_not_a_project_boundary(tmp_path, suffix):
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE workspace_projects(id,root,name,detected_at)")
    connection.execute(
        "INSERT INTO workspace_projects VALUES ('p1',?,'alpha','now')", (str(tmp_path / "alpha"),)
    )
    context = project_context(connection, f"Continue `{tmp_path / ('alpha' + suffix)}`")
    assert context["selected_project_ids"] == []
    assert context["grants_tool_access"] is False
    if suffix in ("bet", "-plugin", "_plugin"):
        assert project_context(connection, "Continue alpha" + suffix)["selected_project_ids"] == []


def test_parsed_paths_disambiguate_nested_and_same_name_projects(tmp_path):
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE workspace_projects(id,root,name,detected_at)")
    roots = [tmp_path / "alpha", tmp_path / "other" / "alpha", tmp_path / "alpha" / "child"]
    for index, root in enumerate(roots):
        connection.execute(
            "INSERT INTO workspace_projects VALUES (?,?,?,'now')",
            (f"project_{index}", str(root), root.name),
        )
    for index, root in enumerate(roots):
        for path in (root, root / "file with spaces.py"):
            result = project_context(connection, f'Continue "{path}".')
            assert result["selected_project_ids"] == [f"project_{index}"]
            assert result["focused_project_id"] == f"project_{index}"
    assert project_context(connection, "继续alpha项目")["ambiguous"] is True
    assert project_context(connection, f"{roots[0]} and {roots[1]}")["ambiguous"] is True


@pytest.fixture
def rig(tmp_path):
    store, router, triggers = base_rig.__wrapped__(tmp_path)
    projects = []
    for name in ("alpha_project", "beta_project"):
        path = tmp_path / name
        path.mkdir()
        (path / "pyproject.toml").write_text("[project]\nname='" + name + "'\n", encoding="utf-8")
        quest, _worker = background_task(store)
        projects.append((quest, WorkspaceDiscovery(store).associate(quest.id, path)))
    return store, router, triggers, projects


def test_focused_recall_excludes_other_project_constraints(rig):
    store, router, _triggers, projects = rig
    for (quest, project), marker in zip(projects, ("ALPHA_PRIVATE", "BETA_PRIVATE"), strict=True):
        source = ConversationMessage(role="user", content="shared-rule " + marker)
        with store.unit_of_work() as work:
            work.add_message(source)
        router.memory_service.remember(
            "shared-rule " + marker,
            kind="constraint",
            layer="L1",
            source_refs=["message:" + source.id],
            related_quest_ids=[quest.id],
        )
    router._sync_memory()
    result = router.memory_service.retrieve_context("shared-rule", project_id=projects[0][1]["id"])
    text = json.dumps(result)
    assert "ALPHA_PRIVATE" in text and "BETA_PRIVATE" not in text
    with store.connection() as c:
        snapshot = router._snapshot(c, "alpha_project progress")
    assert snapshot["project_context"]["focused_project_id"] == projects[0][1]["id"]
    assert {q["id"] for q in snapshot["quests"]} == {projects[0][0].id}


def test_focused_project_continuation_never_targets_other_project(rig):
    _store, router, _triggers, projects = rig
    wrong = projects[1][0]
    router.provider = MockProvider(
        lambda *_: response(conversational_decision("continue_quest", quest_id=wrong.id))
    )
    result = asyncio.run(
        router.handle_user_message("Continue alpha_project work", request_id="wrong-project")
    )
    assert result["intent"] == "ambiguous" and result["quest_id"] is None


@pytest.mark.parametrize("alias", [False, True], ids=["dot-segments", "symlink-alias"])
def test_canonical_named_path_preserves_project_fence(rig, tmp_path, alias):
    store, router, _triggers, projects = rig
    wrong, target = projects[0], projects[1]
    if alias:
        link = tmp_path / "alias"
        link.symlink_to(tmp_path, target_is_directory=True)
        named = link / "beta_project"
    else:
        named = tmp_path / "alpha_project" / ".." / "beta_project"
    text = f"Continue `{named}`"
    with store.connection() as c:
        discovered = WorkspaceDiscovery(store).register_user_request(text, connection=c)
        snapshot = router._snapshot(c, text)
    assert [item["id"] for item in discovered] == [target[1]["id"]]
    assert snapshot["project_context"]["focused_project_id"] == target[1]["id"]
    assert {q["id"] for q in snapshot["quests"]} == {target[0].id}
    router.provider = MockProvider(
        lambda *_: response(conversational_decision("continue_quest", quest_id=wrong[0].id))
    )
    result = asyncio.run(router.handle_user_message(text, request_id="canonical-project"))
    assert result["intent"] == "ambiguous" and result["quest_id"] is None


def test_project_selection_persists_without_becoming_tool_authority(rig):
    store, router, _triggers, projects = rig
    project = projects[0][1]
    router.provider = MockProvider(
        lambda *_: response({**conversational_decision(), "project_id": project["id"]})
    )
    result = asyncio.run(
        router.handle_user_message("New alpha_project analysis", request_id="new-alpha")
    )
    assert WorkspaceDiscovery(store).for_quest(result["quest_id"])["id"] == project["id"]
    assert WorkspaceDiscovery(store).associate_request(result["quest_id"])["id"] == project["id"]
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT 1 FROM task_grants WHERE quest_id=?", (result["quest_id"],)
            ).fetchone()
            is None
        )


def test_long_context_compaction_keeps_project_chain(rig):
    store, router, _triggers, projects = rig
    for i in range(12):
        with store.unit_of_work() as work:
            work.add_message(ConversationMessage(role="user", content="background turn " + str(i)))
    ContextService(store).compact_router(keep_recent=2)
    with store.connection() as c:
        snapshot = router._snapshot(c, "alpha_project progress")
    assert snapshot["archive_head"]
    assert snapshot["task_projects"] == {projects[0][0].id: projects[0][1]["id"]}
    chain = router.memory_service.recall_chain(["project:" + projects[0][1]["id"]], max_depth=2)
    assert "quest:" + projects[0][0].id in {item["ref"] for item in chain["nodes"]}


def test_focused_context_excludes_mixed_transcript_and_archive(rig):
    store, router, _triggers, projects = rig
    for (quest, project), marker in zip(projects, ("ALPHA_PRIVATE", "BETA_PRIVATE"), strict=True):
        for _ in range(6):
            with store.unit_of_work() as work:
                work.add_message(
                    ConversationMessage(
                        role="user", content=marker, metadata={"project_id": project["id"]}
                    )
                )
                work.append_event(
                    ForgeEvent(
                        event_type="worker.progress", quest_id=quest.id, payload={"marker": marker}
                    )
                )
    ContextService(store).compact_router(keep_recent=2)
    with store.connection() as c:
        focused = router._snapshot(c, "alpha_project progress")
        global_view = router._snapshot(c, "alpha_project and beta_project progress")
    serialized = json.dumps(
        {key: focused[key] for key in ("messages", "events", "evidence", "context_capsule")}
    )
    assert "BETA_PRIVATE" not in serialized
    assert focused["context_capsule"]["mixed_history_spilled"] is True
    assert "BETA_PRIVATE" in json.dumps(global_view)


def test_project_scoped_recall_fences_objects_and_next_hops(rig):
    _store, router, _triggers, projects = rig
    project_id = projects[0][1]["id"]
    with pytest.raises(DomainError, match="project context"):
        router.memory_service.recall_object("quest:" + projects[1][0].id, project_id=project_id)
    with pytest.raises(DomainError, match="project context"):
        router.memory_service.recall_object("conversation:main", project_id=project_id)
    result = router.memory_service.recall_chain(
        ["project:" + project_id], project_id=project_id, max_depth=3
    )
    references = {item["ref"] for item in result["nodes"]}
    assert "quest:" + projects[0][0].id in references
    assert "quest:" + projects[1][0].id not in references
    assert "conversation:main" not in references


def test_model_cannot_relabel_continuation_as_another_project(rig):
    store, router, _triggers, projects = rig
    quest, project = projects[0]
    wrong_project = projects[1][1]
    router.provider = MockProvider(
        lambda *_: response(
            {
                **conversational_decision("continue_quest", quest_id=quest.id),
                "project_id": wrong_project["id"],
            }
        )
    )
    result = asyncio.run(
        router.handle_user_message(
            "Continue alpha_project ALPHA_PRIVATE_NEW_DATA", request_id="relabel"
        )
    )
    assert result["intent"] == "continue_quest"
    with store.connection() as c:
        user = c.execute(
            "SELECT metadata FROM messages WHERE json_extract(metadata,'$.request_id')='relabel'"
        ).fetchone()
        beta = router._snapshot(c, "beta_project progress")
    assert json.loads(user[0])["project_id"] == project["id"]
    assert "ALPHA_PRIVATE_NEW_DATA" not in json.dumps(beta["messages"])


@pytest.mark.parametrize(
    "strategy,deps", [("parallel", [[], [0]]), ("sequential", [[], []]), ("hierarchical", [[], []])]
)
def test_invalid_expert_topologies_are_rejected(strategy, deps):
    with pytest.raises(ValueError):
        ExecutionTeam.model_validate(
            {
                "strategy": strategy,
                "assignments": [
                    {"objective": str(i), "role": "research", "depends_on": dep}
                    for i, dep in enumerate(deps)
                ],
            }
        )


def test_team_and_independent_dispatches_are_mutually_exclusive():
    with pytest.raises(ValueError):
        RouterDecision.model_validate(
            {
                **conversational_decision(),
                "independent_dispatches": [{"objective": "x", "role": "research"}],
                "execution_team": {
                    "strategy": "single",
                    "assignments": [{"objective": "x", "role": "research"}],
                },
            }
        )
