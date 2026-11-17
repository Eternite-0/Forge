"""Independent offline intake review: real SQLite/paths, scripted Mock reasoning."""

import asyncio
import json

from test_superagent_loop import MockProvider, conversational_decision, event_decision, response
from test_superagent_loop import rig as base_rig

import forge_core.workspace_discovery as discovery_module
from forge_core.domain import ConversationMessage
from forge_core.project_continuity import project_context
from forge_core.superagent_loop import process_triggers
from forge_core.workspace_discovery import WorkspaceDiscovery


def setup(tmp_path, count=3):
    store, router, events = base_rig.__wrapped__(tmp_path)
    roots = [tmp_path / f"independent_project_{index:02d}" for index in range(count)]
    for root in roots:
        root.mkdir()
        (root / "pyproject.toml").write_text("[project]\nname='offline'\n", encoding="utf-8")
    return store, router, events, roots


def test_discovery_probes_only_eight_explicit_paths(tmp_path, monkeypatch):
    store, _router, _events, roots = setup(tmp_path, 10)
    observed = []
    detect = discovery_module.detect_project

    def record(path):
        observed.append(path)
        return detect(path)

    monkeypatch.setattr(discovery_module, "detect_project", record)
    with store.connection() as connection:
        connection.execute("BEGIN")
        WorkspaceDiscovery(store).register_user_request(
            "Compare " + " and ".join('"' + str(path) + '"' for path in roots),
            connection=connection,
        )
        connection.commit()
    assert observed == roots[:8]
    assert {project["root"] for project in WorkspaceDiscovery(store).list_projects()} == {
        str(root) for root in roots[:8]
    }


def test_first_request_discovers_current_user_only_not_prior_user_or_assistant(tmp_path):
    store, router, _events, roots = setup(tmp_path)
    store.add_message(ConversationMessage(role="user", content=f'Historical request "{roots[1]}"'))
    store.add_message(
        ConversationMessage(role="assistant", content=f'Historical reply "{roots[2]}"')
    )
    requests = []

    def decide(request, _ordinal):
        context = json.loads(request.messages[1]["content"])
        projects = context["project_context"]
        requests.append(context)
        assert {p["root"] for p in projects["projects"]} == {str(roots[0])}
        assert projects["focused_project_id"] == projects["projects"][0]["id"]
        assert projects["grants_tool_access"] is False
        return response(conversational_decision("chat"))

    router.provider = MockProvider(decide)
    asyncio.run(
        router.handle_user_message(f'Current request "{roots[0]}"', request_id="current-only")
    )
    assert len(requests) == 1
    assert {project["root"] for project in WorkspaceDiscovery(store).list_projects()} == {
        str(roots[0])
    }


def test_manual_mode_never_registers_named_projects(tmp_path, monkeypatch):
    store, router, _events, roots = setup(tmp_path)
    router.autonomous = False

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Manual Router must not use autonomous discovery")

    monkeypatch.setattr(WorkspaceDiscovery, "register_user_request", forbidden)
    router.provider = MockProvider(lambda *_: response(conversational_decision("chat")))
    asyncio.run(router.handle_user_message(f'Current request "{roots[0]}"', request_id="manual"))
    assert WorkspaceDiscovery(store).list_projects() == []


def test_background_trigger_payload_never_uses_authenticated_user_registration(
    tmp_path, monkeypatch
):
    store, router, events, roots = setup(tmp_path)
    trigger = events.receive(
        "external", "background", "path-bearing", {"text": f'Inspect "{roots[0]}"'}
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Background data is not a current authenticated user message")

    monkeypatch.setattr(WorkspaceDiscovery, "register_user_request", forbidden)
    router.provider = MockProvider(
        lambda *_: response({"decisions": [event_decision(trigger, "ignore")]})
    )
    assert asyncio.run(process_triggers(router)) == 1
    assert WorkspaceDiscovery(store).list_projects() == []


def test_explicit_revisit_of_old_project_is_presented_without_refreshing_other_projects(tmp_path):
    store, router, _events, roots = setup(tmp_path, 34)
    discovery = WorkspaceDiscovery(store)
    for offset in range(0, len(roots), 8):
        with store.connection() as connection:
            connection.execute("BEGIN")
            discovery.register_user_request(
                "Compare "
                + " and ".join('"' + str(root) + '"' for root in roots[offset : offset + 8]),
                connection=connection,
            )
            connection.commit()
    with store.connection() as connection:
        # Deterministic fixture ordering puts this project outside the bounded view.
        connection.execute(
            "UPDATE workspace_projects SET detected_at='2000-01-01' WHERE root=?", (str(roots[0]),)
        )
        connection.commit()
        before = {
            row[0]: row[1]
            for row in connection.execute("SELECT root,detected_at FROM workspace_projects")
        }
        old = project_context(connection, str(roots[0]))
        assert old["focused_project_id"] is None
    observations = []

    def decide(request, _ordinal):
        context = json.loads(request.messages[1]["content"])
        projects = context["project_context"]
        focused = projects["focused_project_id"]
        assert focused is not None
        assert next(
            project["root"] for project in projects["projects"] if project["id"] == focused
        ) == str(roots[0])
        observations.append(projects)
        return response(conversational_decision("chat"))

    router.provider = MockProvider(decide)
    asyncio.run(router.handle_user_message(f'Revisit "{roots[0]}"', request_id="old-project"))
    with store.connection() as connection:
        after = {
            row[0]: row[1]
            for row in connection.execute("SELECT root,detected_at FROM workspace_projects")
        }
    assert after[str(roots[0])] != before[str(roots[0])]
    assert all(after[str(root)] == before[str(root)] for root in roots[1:])
    assert len(observations) == 1
