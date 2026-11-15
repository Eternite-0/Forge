"""Actual lifecycle pumps with scripted provenance fixtures and real local tools.

These are offline contracts, not evidence of live model capability.
"""

import asyncio
import importlib
import json
import secrets
import time

import pytest
from fastapi.testclient import TestClient

from forge_core.domain import ModelProfile, ProtocolSupport, Quest
from forge_core.governance import GovernanceService
from forge_core.provider_pool import ProviderPool
from forge_core.runtime import ModelResponse
from forge_core.team_composition import DEFAULT_OUTPUT_SCHEMA


def load_main(tmp_path, monkeypatch):
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
    (tmp_path / "work").mkdir()
    return main


def wait_for(check, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.03)
    assert check()


def test_actual_lifespan_schedules_parallel_dag_verifies_and_relays(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    quest = Quest(title="Offline lifecycle integration", goal="Create three valid JSON artifacts")
    main.store.save_quest(quest)
    keys = ["a", "b", "c"]
    nodes = [
        {
            "key": key,
            "objective": "Create " + key,
            "success_criteria": ["json_valid:" + str(tmp_path / "work" / (key + ".json"))],
            "budget_tokens": 16000,
            "allowed_tools": ["file_write"],
        }
        for key in keys
    ]
    plan = main.quest_graph.propose(
        quest.id,
        {
            "nodes": nodes,
            "edges": [{"source": "a", "target": "c", "condition": "success"}],
            "max_total_tokens": 48000,
        },
        expected_version=0,
    )
    main.quest_graph.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    for key in keys:
        main.store.save_model(
            ModelProfile(
                id=key,
                provider="fixture",
                model_id=key,
                display_name="Scripted test provenance stub",
                context_window=32000,
                max_output_tokens=4000,
                tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
            )
        )
    # A capability requirement per node distinguishes the otherwise identical models.
    from forge_core.domain import Measurement

    for key in keys:
        model = next(item for item in main.store.list_models() if item.id == key)
        model.capability_measurements = {key: Measurement(value=100, source="test_fixture")}
        main.store.save_model(model)
    roles = [
        {
            "id": "coding_" + key,
            "dynamic": True,
            "responsibility": "Create JSON",
            "input_contract": ["goal"],
            "output_schema": DEFAULT_OUTPUT_SCHEMA,
            "acceptance_criteria": ["json_valid:" + str(tmp_path / "work" / (key + ".json"))],
            "task_node_key": key,
            "required_capabilities": {key: 1},
            "tools": ["file_write"],
            "input_tokens": 9000,
            "output_tokens": 3000,
            "max_steps": 3,
            "max_tool_calls": 1,
            "max_duration_seconds": 60,
        }
        for key in keys
    ]
    team = main.team_service.propose(
        quest.id,
        roles,
        {
            "max_members": 3,
            "max_depth": 0,
            "max_concurrency": 2,
            "max_total_tokens": 36000,
            "max_total_tool_calls": 3,
            "allowed_tools": ["file_write"],
        },
        strategy="greedy",
        expected_version=2,
    )
    main.team_service.approve(
        quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user"
    )
    GovernanceService(main.store).grant_workspace(quest.id, "work", "local_user")

    class ScriptedWorker:
        execution_mode = "live"
        active = 0
        peak = 0

        def __init__(self):
            self.counts = {}

        async def generate(self, request):
            key = request.model_id
            self.counts[key] = self.counts.get(key, 0) + 1
            if key == "c":
                with main.store.connection() as connection:
                    assert (
                        connection.execute(
                            "SELECT status FROM task_nodes WHERE plan_id=? AND node_key='a'",
                            (plan["id"],),
                        ).fetchone()[0]
                        == "succeeded"
                    )
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.15)
            self.active -= 1
            calls = (
                [
                    {
                        "id": "write-" + key,
                        "name": "file_write",
                        "arguments": {
                            "path": "work/" + key + ".json",
                            "content": json.dumps({"fixture": key}),
                        },
                    }
                ]
                if self.counts[key] == 1
                else []
            )
            return ModelResponse(
                text="" if calls else '{"summary":"fixture"}',
                tool_calls=calls,
                usage={"input_tokens": 30, "output_tokens": 20},
                finish_reason="tool_calls" if calls else "stop",
            )

    class ScriptedRouter:
        execution_mode = "mock"

        async def generate(self, request):
            return ModelResponse(
                text='{"action":"update_user","reply":"Verified task update"}',
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 10},
            )

    worker = ScriptedWorker()
    monkeypatch.setattr(
        ProviderPool,
        "from_environment",
        classmethod(lambda cls, **kwargs: (ProviderPool({"fixture": worker}), {})),
    )
    main.router.provider = ScriptedRouter()
    main.router.model_id = "mock-router"
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):
        wait_for(lambda: main.store.get_quest(quest.id)["status"] == "completed")
        wait_for(lambda: bool(main.store.list_messages()))
        assert worker.peak == 2
        with main.store.connection() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM worker_runs WHERE state='succeeded'"
                ).fetchone()[0]
                == 3
            )
            assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 3
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM execution_runs WHERE kind='json_validation' AND exit_code=0"
                ).fetchone()[0]
                == 3
            )
        assert all(
            message["role"] == "assistant" and message["conversation_id"] == "main"
            for message in main.store.list_messages()
        )


def test_actual_lifespan_help_creates_only_pending_reassignment(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    quest = Quest(title="Offline help arbitration", goal="Request a replacement")
    main.store.save_quest(quest)
    plan = main.quest_graph.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": quest.goal,
                    "success_criteria": ["manual"],
                    "budget_tokens": 16000,
                    "allowed_tools": [],
                }
            ],
            "edges": [],
            "max_total_tokens": 16000,
        },
        expected_version=0,
    )
    main.quest_graph.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    main.store.save_model(
        ModelProfile(
            id="fixture",
            provider="fixture",
            model_id="fixture",
            display_name="Scripted mock",
            context_window=32000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    role = {
        "id": "coding",
        "task_node_key": "n",
        "tools": [],
        "input_tokens": 9000,
        "output_tokens": 3000,
        "max_steps": 3,
        "max_tool_calls": 1,
        "max_duration_seconds": 60,
    }
    limits = {
        "max_members": 1,
        "max_depth": 1,
        "max_concurrency": 1,
        "max_total_tokens": 12000,
        "max_total_tool_calls": 1,
        "allowed_tools": [],
    }
    team = main.team_service.propose(
        quest.id, [role], limits, strategy="greedy", expected_version=2
    )
    main.team_service.approve(
        quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user"
    )

    class HelpWorker:
        execution_mode = "mock"

        async def generate(self, request):
            return ModelResponse(
                text="",
                tool_calls=[
                    {
                        "id": "help",
                        "name": "worker_request_help",
                        "arguments": {"role": "reviewer", "reason": "Need another specialist"},
                    }
                ],
                usage={"input_tokens": 30, "output_tokens": 20},
            )

    wire_role = {
        **role,
        "required_capabilities": [],
        "depth": 0,
        "dynamic": False,
        "responsibility": "Implement",
        "input_contract": ["goal"],
        "output_schema_json": json.dumps(DEFAULT_OUTPUT_SCHEMA),
        "acceptance_criteria": ["manual"],
    }
    wire_limits = {**limits, "max_cost_microunits": None, "currency": None}

    class Arbitrator:
        execution_mode = "mock"
        calls = 0

        async def generate(self, request):
            if request.response_format["json_schema"]["name"] == "team_proposal":
                self.calls += 1
                text = json.dumps(
                    {"roles": [wire_role], "limits": wire_limits, "strategy": "greedy"}
                )
            else:
                text = '{"action":"silent","reply":null}'
            return ModelResponse(
                text=text, tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10}
            )

    monkeypatch.setattr(
        ProviderPool,
        "from_environment",
        classmethod(lambda cls, **kwargs: (ProviderPool({"fixture": HelpWorker()}), {})),
    )
    arbitrator = Arbitrator()
    original = main.team_service.propose_reassignment
    errors = []

    def capture(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            errors.append(repr(exc))
            raise

    monkeypatch.setattr(main.team_service, "propose_reassignment", capture)
    main.router.provider, main.router.model_id = arbitrator, "mock-router"
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        wait_for(lambda: len(main.list_teams(quest.id)) == 2 or bool(errors))
        assert not errors
        proposals = main.list_teams(quest.id)
        assert [item["status"] for item in proposals] == ["approved", "proposed"]
        assert proposals[1]["body"]["reassignment"]["run_id"]
        assert arbitrator.calls == 1
        with main.store.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 1
            assert connection.execute("SELECT state FROM worker_runs").fetchone()[0] == "blocked"
            notice = connection.execute(
                "SELECT processed_at FROM event_inbox WHERE consumer_id='composer' AND event_type='worker.help_requested'"
            ).fetchone()
            assert notice[0]
        response = client.post(
            f"/api/quests/{quest.id}/teams/reassign",
            json={
                "run_id": "forged",
                "reason": "forged",
                "proposal": {"roles": [wire_role], "limits": wire_limits, "strategy": "greedy"},
                "expected_version": 0,
            },
        )
        assert response.status_code == 401


@pytest.mark.parametrize("tool_name", ["file_write", "browser_get"])
def test_actual_lifespan_waits_for_late_user_authorization_and_resumes_same_call(
    tmp_path, monkeypatch, tool_name
):
    main = load_main(tmp_path, monkeypatch)
    quest = Quest(title="Late authorization", goal="Wait before effect")
    main.store.save_quest(quest)
    plan = main.quest_graph.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": quest.goal,
                    "success_criteria": ["manual review"],
                    "budget_tokens": 16000,
                    "allowed_tools": [tool_name],
                }
            ],
            "edges": [],
            "max_total_tokens": 16000,
        },
        expected_version=0,
    )
    main.quest_graph.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    main.store.save_model(
        ModelProfile(
            id="fixture",
            provider="fixture",
            model_id="fixture",
            display_name="Scripted mock",
            context_window=32000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    role = {
        "id": "coding",
        "task_node_key": "n",
        "tools": [tool_name],
        "input_tokens": 9000,
        "output_tokens": 3000,
        "max_steps": 3,
        "max_tool_calls": 1,
        "max_duration_seconds": 60,
    }
    team = main.team_service.propose(
        quest.id,
        [role],
        {
            "max_members": 1,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 12000,
            "max_total_tool_calls": 1,
            "allowed_tools": [tool_name],
        },
        strategy="greedy",
        expected_version=2,
    )
    main.team_service.approve(
        quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user"
    )
    params = (
        {"path": "work/result.json", "content": "{}"}
        if tool_name == "file_write"
        else {"url": "https://example.test/item"}
    )

    class Model:
        execution_mode = "mock"
        count = 0

        async def generate(self, request):
            self.count += 1
            return ModelResponse(
                text="" if self.count == 1 else '{"summary":"done"}',
                tool_calls=[{"id": "same-call", "name": tool_name, "arguments": params}]
                if self.count == 1
                else [],
                usage={"input_tokens": 30, "output_tokens": 20},
            )

    monkeypatch.setattr(
        ProviderPool,
        "from_environment",
        classmethod(lambda cls, **kwargs: (ProviderPool({"fixture": Model()}), {})),
    )
    monkeypatch.setenv("FORGE_TOOL_NETWORK_HOSTS_JSON", '["example.test"]')
    from forge_core.tool_engine import ToolEngine

    network_calls = []

    def fake_network(*args):
        network_calls.append(args)
        return {"status_code": 200, "body": "ok", "sha256": "0" * 64}

    monkeypatch.setattr(ToolEngine, "_network_request", fake_network)
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)):

        def parked():
            with main.store.connection() as connection:
                return connection.execute(
                    "SELECT 1 FROM worker_runs WHERE state='blocked' AND awaiting_authorization=1"
                ).fetchone()

        wait_for(parked)
        with main.store.connection() as connection:
            call_id = connection.execute("SELECT call_id FROM worker_tool_calls").fetchone()[0]
            assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0
            assert (
                connection.execute("SELECT state FROM worker_tool_calls").fetchone()[0] == "pending"
            )
        assert not (tmp_path / "work/result.json").exists() and not network_calls
        if tool_name == "file_write":
            main.governance.grant_workspace(quest.id, "work", "local_user")
        else:
            approvals = main.governance.list_approvals(quest.id)
            assert len(approvals) == 1 and approvals[0]["status"] == "PENDING"
            main.governance.decide_approval(approvals[0]["id"], "APPROVED", "local_user")

        def finished():
            with main.store.connection() as connection:
                return connection.execute(
                    "SELECT 1 FROM worker_runs WHERE state='review_pending'"
                ).fetchone()

        wait_for(finished)
        with main.store.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
            assert connection.execute("SELECT call_id,state FROM worker_tool_calls").fetchone()[
                :
            ] == (call_id, "succeeded")
        assert main.store.get_quest(quest.id)["status"] != "completed"
