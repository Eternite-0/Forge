"""Offline scripted model with real approved SQLite state and real workspace tools."""

import asyncio

from forge_core.domain import ModelProfile, ProtocolSupport, Quest
from forge_core.governance import GovernanceService
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelResponse
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def test_mock_model_uses_real_file_and_numeric_process_with_receipts(tmp_path):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    (tmp_path / "work").mkdir()
    quest = Quest(title="Experiment", goal="Evaluate an arithmetic expression")
    store.save_quest(quest)
    tool_names = ["file_write", "file_read", "numeric_ast"]
    graphs = QuestGraphService(store)
    plan = graphs.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": "Run test",
                    "success_criteria": ["verified"],
                    "budget_tokens": 16000,
                    "allowed_tools": tool_names,
                }
            ],
            "edges": [],
            "max_total_tokens": 16000,
        },
        expected_version=0,
    )
    graphs.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    store.save_model(
        ModelProfile(
            id="profile",
            provider="mock-provider",
            model_id="mock-model",
            display_name="Mock",
            context_window=32000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="user_override"),
        )
    )
    team_service = TeamCompositionService(store)
    team = team_service.propose(
        quest.id,
        [
            {
                "id": "coding",
                "task_node_key": "n",
                "tools": tool_names,
                "input_tokens": 9000,
                "output_tokens": 3000,
                "max_steps": 6,
                "max_tool_calls": 3,
                "max_duration_seconds": 60,
            }
        ],
        {
            "max_members": 1,
            "max_depth": 1,
            "max_concurrency": 1,
            "max_total_tokens": 12000,
            "max_total_tool_calls": 3,
            "allowed_tools": tool_names,
        },
        strategy="greedy",
        expected_version=2,
    )
    team_service.approve(
        quest.id,
        plan["plan_revision"],
        team["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")

    class Model:
        execution_mode = "mock"

        def __init__(self):
            self.count = 0

        async def generate(self, request):
            self.count += 1
            actions = [
                ("file_write", {"path": "work/input.txt", "content": "3"}),
                ("file_read", {"path": "work/input.txt"}),
                ("numeric_ast", {"source": "result = x * x + 1", "inputs": {"x": 3}}),
            ]
            calls = (
                []
                if self.count > len(actions)
                else [
                    {
                        "id": f"model-{self.count}",
                        "name": actions[self.count - 1][0],
                        "arguments": actions[self.count - 1][1],
                    }
                ]
            )
            return ModelResponse(
                text='{"summary":"claim"}' if not calls else "",
                tool_calls=calls,
                usage={"input_tokens": 50, "output_tokens": 10},
            )

    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": Model()}),
        ToolEngine(store),
        team_authority=team_service,
    )
    worker = manager.start(
        quest.id, plan["id"], "n", team_digest=team["proposal_digest"], member_index=0
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending"
    assert (tmp_path / "work" / "input.txt").read_text(encoding="utf-8") == "3"
    with store.connection() as connection:
        rows = connection.execute("SELECT * FROM action_receipts ORDER BY rowid").fetchall()
        assert len(rows) == 3 and all(row["execution_mode"] == "mock" for row in rows)
    assert store.get_quest(quest.id)["status"] != "completed"
