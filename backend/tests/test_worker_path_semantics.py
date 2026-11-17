"""Programmatic path/context checks; no real model Provider is called."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from test_delivery_diagnostics_v22 import prompt_contract
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_tool_engine import mock_run as mock_run  # noqa: PLC0414
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response
from test_worker_request_framing_v17 import ObservedGateway

from forge_core.domain import DomainError, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.tool_engine import ToolEngine
from forge_core.worker_environment import worker_environment
from forge_core.worker_prompts import initial_worker_messages, measure_initial_worker_request
from forge_core.worker_runtime import WorkerManager
from forge_core.workspace_discovery import WorkspaceDiscovery


def test_environment_paths_follow_persisted_task_link_without_grant_or_creation(mock_run):
    store, quest, _worker, _plan = mock_run
    project_root = store.workspace.root / "work" / "alpha"
    project_root.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("[project]\nname='alpha'", encoding="utf-8")
    project = WorkspaceDiscovery(store).associate(quest.id, project_root)
    other_root = store.workspace.root / "work" / "beta"
    other_root.mkdir()
    other_quest = Quest(title="Another project", goal=str(other_root))
    store.save_quest(other_quest)
    WorkspaceDiscovery(store).associate(other_quest.id, other_root)
    with store.connection() as connection:
        before = connection.total_changes
        environment = worker_environment(store, "run", create=False, connection=connection)
        assert connection.total_changes == before
        assert connection.execute("SELECT COUNT(*) FROM task_grants").fetchone()[0] == 0
    assert environment["storage_root"] == str(store.workspace.root.resolve())
    assert environment["tool_path_base"] == environment["storage_root"]
    assert environment["project"] == {
        "id": project["id"],
        "root": str(project_root.resolve()),
        "reference": "project:" + project["id"],
        "source": "persisted_task_project_link",
        "grants_tool_access": False,
    }
    assert not Path(environment["root"]).exists()
    assert environment["cwd"] != environment["tool_path_base"]
    assert environment["project"]["root"] != environment["tool_path_base"]
    engine = ToolEngine(store)
    assert engine._scope("file_read", {"path": "source.py"}) == str(
        store.workspace.root / "source.py"
    )
    assert engine._scope("file_read", {"path": "work/alpha/source.py"}) == str(
        project_root / "source.py"
    )


def test_external_project_identity_does_not_change_tool_scope(mock_run, tmp_path):
    store, quest, _worker, _plan = mock_run
    project_root = tmp_path.parent / (tmp_path.name + "-external")
    project_root.mkdir()
    project = WorkspaceDiscovery(store).associate(quest.id, project_root)
    environment = worker_environment(store, "run", create=False)
    assert environment["project"]["id"] == project["id"]
    assert environment["tool_path_base"] == str(store.workspace.root.resolve())
    with pytest.raises(DomainError, match="outside workspace"):
        ToolEngine(store)._scope("file_read", {"path": str(project_root / "source.py")})


def test_missing_project_link_is_explicit_and_does_not_infer_from_task_text(mock_run):
    store, quest, _worker, _plan = mock_run
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET goal=? WHERE id=?", ("Project /invented/root", quest.id)
        )
    environment = worker_environment(store, "run", create=False)
    assert environment["project"] is None
    assert environment["tool_path_base"] == str(store.workspace.root.resolve())


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("source_policy", [None, "native-input-roundtrip-v1"])
def test_worker_prompt_separates_tool_paths_from_project_and_private_cwd(
    mock_run, bounded, source_policy
):
    store, _quest, _worker, _plan = mock_run
    contract = prompt_contract(bounded=bounded)
    contract["execution_environment"] = worker_environment(store, "run", create=False)
    contract["superagent"] = {"write_scope": "work/quest", "policy": "local-superagent-v2"}
    if source_policy is not None:
        contract["input_acquisition_policy"] = source_policy
    messages = initial_worker_messages(contract, has_successors=False)
    task = json.loads(messages[-1]["content"])
    assert task["execution_environment"] == contract["execution_environment"]
    assert "tool_path_base" in messages[0]["content"]
    assert "project root" in messages[0]["content"]
    assert "private cwd" in messages[0]["content"]
    assert "write_scope" in messages[0]["content"]


@pytest.mark.parametrize("kind", ["openai_compatible", "deepseek", "anthropic"])
def test_initial_measurement_includes_real_superagent_paths_and_bounds_runtime(delegated, kind):
    store, quest_id, _mandate, graph, teams, service = delegated
    project_root = store.workspace.root / "work" / "project"
    project_root.mkdir(parents=True)
    (project_root / "pyproject.toml").write_text("[project]\nname='project'", encoding="utf-8")
    WorkspaceDiscovery(store).associate(quest_id, project_root)
    assert service.prepare(quest_id)
    gateway = ObservedGateway(
        kind=kind,
        transport=httpx.MockTransport(lambda _: pytest.fail("Framing must not call HTTP")),
    )
    gateway.config = replace(gateway.config, id="mock-provider")
    plan = graph.get(quest_id)
    with store.connection() as connection:
        team = connection.execute("SELECT * FROM team_proposals").fetchone()
        member = json.loads(team["body_json"])["members"][0]
        before = connection.total_changes
        measured = measure_initial_worker_request(
            store,
            connection,
            store.get_quest(quest_id),
            plan,
            plan["nodes"][0],
            member,
            provider=gateway,
        )
        assert connection.total_changes == before
    preview_request = gateway.serialized[-1][0]
    preview_task = json.loads(preview_request.messages[-1]["content"])
    assert preview_task["superagent"]["quest_ref"] == "quest:" + quest_id
    assert preview_task["execution_environment"]["project"]["root"] == str(project_root)
    model = ProtocolModel([response(delivery())])
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    run = manager.start(
        quest_id,
        plan["id"],
        plan["nodes"][0]["key"],
        team_digest=team["proposal_digest"],
        member_index=0,
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    request = model.requests[0]
    task = json.loads(request.messages[-1]["content"])
    assert task["superagent"] == preview_task["superagent"]
    assert task["execution_environment"]["storage_root"] == str(store.workspace.root.resolve())
    assert (
        task["execution_environment"]["project"] == preview_task["execution_environment"]["project"]
    )
    # This helper measures persisted initial context, not the separately admitted
    # per-step execution/review preview appended by runtime just before sending.
    initial_request = replace(
        request,
        messages=[
            item
            for item in request.messages
            if not item["content"].startswith("Core remaining execution allowance")
        ],
    )
    assert {item["name"] for item in request.tools}.issubset(
        {item["name"] for item in preview_request.tools}
    )
    actual = (
        len(json.dumps(gateway._payload(initial_request, False), ensure_ascii=False).encode()) + 64
    )
    assert measured["native_input_reservation"] >= actual
    assert not Path(preview_task["execution_environment"]["root"]).exists()
