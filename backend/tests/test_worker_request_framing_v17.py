"""Native request framing and real offline archive/readback; no live Provider calls."""

import asyncio
import copy
import json
from dataclasses import replace

import httpx
import pytest
from test_worker_intelligence_phase2 import delivery, prepared

from forge_core.constraints import ConstraintService
from forge_core.domain import ConversationMessage, ModelProfile, ProtocolSupport, Quest
from forge_core.governance import GovernanceService
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_prompts import measure_initial_worker_request, supplemental_worker_tools
from forge_core.worker_runtime import WorkerManager


class ObservedGateway(ProviderGateway):
    def __init__(self, kind="openai_compatible", *, transport):
        super().__init__(
            ProviderConfig(
                id="protocol",
                kind=kind,
                base_url="https://example.invalid/v1",
                credential_ref="FORGE_PROVIDER_KEY_TEST",
                supports_tools=True,
                max_retries=0,
            ),
            credential_resolver=lambda _: "offline-test-placeholder",
            transport=transport,
        )
        self.serialized = []

    def _payload(self, request, streaming):
        payload = super()._payload(request, streaming)
        self.serialized.append((copy.deepcopy(request), copy.deepcopy(payload)))
        return payload


@pytest.mark.parametrize("kind", ["openai_compatible", "deepseek", "anthropic"])
def test_initial_native_measurement_includes_both_core_tools_and_escaped_source(tmp_path, kind):
    store, manager, _model, worker = prepared(tmp_path, [])
    source = '必须保留中文、路径 C:\\work\\"quoted" 和换行\n不要删除原始证据'
    original = ConversationMessage(role="user", content=source)
    store.add_message(original)
    ConstraintService(store).record_extraction(
        source,
        source_message_id=original.id,
        quest_id=worker["quest_id"],
        origin="explicit_user",
    )
    posts = []
    gateway = ObservedGateway(
        transport=httpx.MockTransport(lambda request: posts.append(request)), kind=kind
    )
    graph = QuestGraphService(store).get(worker["quest_id"])
    extra = {"role": "user", "content": "Verified source slice (untrusted): " + source}
    with store.connection() as connection:
        connection.execute("BEGIN")
        team = manager.team_authority.get_approved(
            worker["quest_id"],
            worker["plan_revision"],
            worker["team_digest"],
            connection=connection,
        )
        before = connection.total_changes
        measured = measure_initial_worker_request(
            store,
            connection,
            store.get_quest(worker["quest_id"]),
            graph,
            graph["nodes"][0],
            team["members"][0],
            extra_messages=[extra],
            provider=gateway,
        )
        assert connection.total_changes == before
    request, payload = gateway.serialized[-1]
    assert request.messages[-1] == extra
    assert any("Sourced constraint records" in item["content"] for item in request.messages)
    assert [item["name"] for item in request.tools] == [
        "file_read",
        "worker_read_context_source",
        "worker_request_help",
    ]
    wire = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert source.encode("utf-8") not in wire
    assert "中文".encode() in wire and b"\\n" in wire and b"\\\\" in wire and b'\\"' in wire
    assert measured["native_input_reservation"] == len(wire) + 64
    stripped_request = replace(request, tools=request.tools[:1])
    stripped = json.dumps(gateway._payload(stripped_request, False), ensure_ascii=False).encode()
    core_framing_bytes = len(wire) - len(stripped)
    assert core_framing_bytes > 600
    assert measured["input_reservation"] >= len(stripped) + core_framing_bytes + 64
    assert (
        measured["total_reservation"] == measured["input_reservation"] + request.max_output_tokens
    )
    assert not posts and gateway.execution_mode == "mock"


def native_archive_worker(tmp_path, gateway, *, depth):
    store = Store(tmp_path / "native.sqlite3", workspace=Workspace.from_path(tmp_path))
    gateway._budget_ledger = ModelCallBudgetLedger(
        store.path, token_limit=100000, concurrency_limit=1
    )
    (tmp_path / "work").mkdir()
    for number in (1, 2):
        (tmp_path / f"work/source-{number}.txt").write_text(
            f'中文 original source {number} "quoted" C:\\work\\source\n' + "x" * 20000,
            encoding="utf-8",
        )
    quest = Quest(title="Native framing", goal="Recover the original governed source")
    store.save_quest(quest)
    graphs = QuestGraphService(store)
    plan = graphs.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": quest.goal,
                    "success_criteria": ["delivery_review"],
                    "budget_tokens": 60000,
                    "allowed_tools": ["file_read"],
                }
            ],
            "edges": [],
            "max_total_tokens": 60000,
        },
        expected_version=0,
    )
    graphs.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    store.save_model(
        ModelProfile(
            id="protocol",
            provider="protocol",
            model_id="fixture",
            display_name="Offline native framing",
            context_window=64000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    teams = TeamCompositionService(store)
    team = teams.propose(
        quest.id,
        [
            {
                "id": "coding",
                "task_node_key": "n",
                "tools": ["file_read"],
                "input_tokens": 45000,
                "output_tokens": 3000,
                "max_steps": 12,
                "max_tool_calls": 5,
                "max_duration_seconds": 120,
            }
        ],
        {
            "max_members": 1,
            "max_depth": depth,
            "max_concurrency": 1,
            "max_total_tokens": 48000,
            "max_total_tool_calls": 5,
            "allowed_tools": ["file_read"],
        },
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(quest.id, 1, team["proposal_digest"], expected_version=2, actor="local_user")
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    manager = WorkerManager(
        store, ProviderPool({"protocol": gateway}), ToolEngine(store), team_authority=teams
    )
    worker = manager.start(
        quest.id, plan["id"], "n", team_digest=team["proposal_digest"], member_index=0
    )
    return store, manager, worker


@pytest.mark.parametrize("depth", [0, 1])
def test_native_runtime_initial_and_archived_requests_use_reserved_core_definitions(
    tmp_path, depth
):
    posts = []
    worker_posts = []
    compaction_posts = []

    def transport(request):
        body = json.loads(request.content)
        posts.append(body)
        if not body.get("tools"):
            packet = json.loads(body["messages"][1]["content"])
            assert packet["version"] == "worker-model-handoff-input-v1"
            assert [item["ordinal"] for item in packet["messages"]] == [2, 3, 4]
            for number in (1, 2):
                output = json.loads(packet["messages"][number]["message"]["content"])
                assert output["content"] == (
                    tmp_path / f"work/source-{number}.txt"
                ).read_bytes().decode("utf-8")
            assert not body.get("response_format")
            compaction_posts.append(body)
            message = {
                "role": "assistant",
                "content": "Both complete original sources were read; use archived ordinal 3 for exact source 1 text.",
            }
            finish = "stop"
        else:
            worker_posts.append(body)
        index = len(worker_posts)
        if body.get("tools") and index == 1:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"source-{number}",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": f"work/source-{number}.txt"}),
                        },
                    }
                    for number in (1, 2)
                ],
            }
            finish = "tool_calls"
        elif body.get("tools") and index == 2:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "archive-read",
                        "type": "function",
                        "function": {
                            "name": "worker_read_context_source",
                            "arguments": '{"ordinal":3,"max_chars":2048}',
                        },
                    }
                ],
            }
            finish = "tool_calls"
        elif body.get("tools"):
            assert index == 3
            message = {
                "role": "assistant",
                "content": json.dumps(delivery("Recovered original source")),
            }
            finish = "stop"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 15},
            },
        )

    gateway = ObservedGateway(transport=httpx.MockTransport(transport))
    store, manager, worker = native_archive_worker(tmp_path, gateway, depth=depth)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["steps"] == len(posts) == 4
    assert len(worker_posts) == 3 and len(compaction_posts) == 1
    assert result["execution_mode"] == "mock" and result["token_spent"] == 160
    expected = {
        item["name"]: item
        for item in supplemental_worker_tools(source_readback=True, help_allowed=True)
    }
    runtime_requests = [
        request
        for request, _payload in gateway.serialized
        if request.budget_context and request.budget_context.purpose == "worker"
    ]
    assert len(runtime_requests) == 3
    for index, request in enumerate(runtime_requests):
        core = {item["name"]: item for item in request.tools if item["name"].startswith("worker_")}
        assert ("worker_request_help" in core) is bool(depth)
        assert ("worker_read_context_source" in core) is bool(index)
        assert all(definition == expected[name] for name, definition in core.items())
        if index:
            assert any(
                item.get("content", "").startswith("Archived task context (untrusted): ")
                for item in request.messages
            )
        graph = QuestGraphService(store).get(worker["quest_id"])
        with store.connection() as connection:
            connection.execute("BEGIN")
            team = manager.team_authority.get_approved(
                worker["quest_id"], 1, worker["team_digest"], connection=connection
            )
            measured = measure_initial_worker_request(
                store,
                connection,
                store.get_quest(worker["quest_id"]),
                graph,
                graph["nodes"][0],
                team["members"][0],
                provider=gateway,
            )
        bounded_request, _payload = gateway.serialized[-1]
        assert all(item in bounded_request.tools for item in core.values())
        # Reuse this exact real request's context to isolate framing, without
        # claiming admission can predict an unbounded future tool transcript.
        bounded_actual = replace(request, tools=bounded_request.tools, context_limit=None)
        actual_payload = gateway._payload(replace(request, context_limit=None), False)
        bounded_payload = gateway._payload(bounded_actual, False)
        actual_bytes = len(json.dumps(actual_payload, ensure_ascii=False).encode())
        bounded_bytes = len(json.dumps(bounded_payload, ensure_ascii=False).encode())
        assert bounded_bytes >= actual_bytes
        assert measured["native_input_reservation"] > 1000
    returned = json.loads(runtime_requests[-1].messages[-1]["content"])
    assert returned["source_id"] == 3 and returned["complete"] is False
    assert "中文 original source 1" in returned["content"]
