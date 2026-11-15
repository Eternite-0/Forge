"""Exercise the actual composer pump with an explicit mock model."""

import importlib
import json
import secrets
import time

from fastapi.testclient import TestClient

from forge_core.domain import ModelProfile, Quest
from forge_core.quest_graph import QuestGraphService


def test_approved_plan_automatically_creates_unapproved_team(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="Compose", goal="Create a bounded coding proposal")
    main.store.save_quest(quest)
    plan = QuestGraphService(main.store).propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "a",
                    "objective": "Plan",
                    "success_criteria": ["Ready"],
                    "budget_tokens": 500,
                    "required_capabilities": {"coding": 70},
                    "allowed_tools": [],
                }
            ],
            "edges": [],
            "max_total_tokens": 500,
        },
        expected_version=0,
    )
    main.store.save_model(
        ModelProfile(
            id="fixture",
            model_id="mock-model",
            provider="fixture-provider",
            display_name="Mock",
            capabilities={"coding": 80},
            context_window=4096,
            max_output_tokens=512,
        )
    )
    output_schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    }
    proposal = {
        "roles": [
            {
                "id": "coding",
                "task_node_key": "a",
                "required_capabilities": [{"name": "coding", "minimum": 70}],
                "tools": [],
                "input_tokens": 100,
                "output_tokens": 50,
                "max_steps": 4,
                "max_tool_calls": 0,
                "max_duration_seconds": 30,
                "depth": 0,
                "dynamic": False,
                "responsibility": "Implement",
                "input_contract": ["Goal"],
                "output_schema_json": json.dumps(output_schema),
                "acceptance_criteria": ["Ready"],
            }
        ],
        "limits": {
            "max_members": 1,
            "max_depth": 1,
            "max_concurrency": 1,
            "max_total_tokens": 150,
            "max_total_tool_calls": 0,
            "max_cost_microunits": None,
            "currency": None,
            "allowed_tools": [],
        },
        "strategy": "greedy",
    }

    class MockProvider:
        execution_mode = "mock"
        team_calls = 0

        async def generate(self, request):
            if request.response_format["json_schema"]["name"] == "team_proposal":
                self.team_calls += 1
                data = proposal
            else:
                data = {"action": "silent", "reply": None}
            return type("Response", (), {"text": json.dumps(data), "usage": {}})()

    mock = MockProvider()
    main.router.provider = mock
    main.router.model_id = "mock"
    headers = {"Authorization": f"Bearer {main.guard._token}"}
    path = f"/api/quests/{quest.id}"
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        approval = {
            "digest": plan["digest"],
            "plan_revision": plan["plan_revision"],
            "expected_version": 1,
        }
        assert (
            client.post(f"{path}/plan/approve", headers=headers, json=approval).status_code == 200
        )
        deadline = time.monotonic() + 6
        teams = []
        while time.monotonic() < deadline:
            teams = client.get(f"{path}/teams", headers=headers).json()
            if teams:
                break
            time.sleep(0.05)
        assert len(teams) == 1 and teams[0]["status"] == "proposed"
        assert mock.team_calls == 1 and main.store.list_workers(quest.id) == []
        team_approval = {
            "plan_revision": plan["plan_revision"],
            "proposal_digest": teams[0]["proposal_digest"],
            "expected_version": 2,
        }
        assert client.post(f"{path}/teams/approve", json=team_approval).status_code == 401
        assert (
            client.post(f"{path}/teams/approve", headers=headers, json=team_approval).status_code
            == 200
        )
