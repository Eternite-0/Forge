"""Offline HTTP → admitted Worker → real files/receipts → user-readable result.

Scripted models never contact providers. Mock execution is not live review proof.
The separate local-verifier case uses the established test-only provenance stub.
"""

import asyncio
import hashlib
import hmac
import importlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient
from test_router_responsiveness_superagent import deliver_all
from test_superagent_loop import MockProvider, conversational_decision, response, rig

from forge_core.dispatcher import Dispatcher
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.store import Store
from forge_core.superagent_delegation import SuperagentDelegation
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def api_client(monkeypatch, tmp_path, store, router):
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path / "http-bootstrap"))
    from forge_api import main

    importlib.reload(main)
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "router", router)
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    session = client.get("/health").json()["session"]
    client.headers["Authorization"] = (
        "Bearer " + hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
    )
    return main, client


def test_natural_request_real_write_read_receipt_http_and_replay(tmp_path, monkeypatch):
    store, router, _ = rig.__wrapped__(tmp_path)
    router.provider = MockProvider(lambda *_: response(conversational_decision()))
    main, client = api_client(monkeypatch, tmp_path, store, router)
    request = {"content": "请生成一份说明文档，并读回确认内容。", "request_id": "task-report-once"}
    first = client.post("/api/conversation/message", json=request)
    assert first.status_code == 200
    quest_id = first.json()["quest_id"]
    trigger_id = json.loads(store.get_quest(quest_id)["metadata"])["trigger_id"]
    graph, teams = QuestGraphService(store), TeamCompositionService(store)
    assert SuperagentDelegation(store, graph, teams).prepare(quest_id)
    path = f"work/{quest_id}/report.md"
    content = "# 离线验收报告\n这份文件由真实受管工具写入，并已读回。\n"

    def execute(request, count):
        from forge_core.runtime import ModelResponse

        if count == 1:
            return ModelResponse(
                text="",
                tool_calls=[
                    {
                        "id": "read-request",
                        "name": "read_trigger",
                        "arguments": {"trigger_id": trigger_id},
                    }
                ],
                usage={"input_tokens": 20, "output_tokens": 10},
            )
        if count <= 3:
            if count == 2:
                observed = [m for m in request.messages if m["role"] == "tool"]
                assert "请生成一份说明文档" in json.loads(observed[-1]["content"])["text"]
            if count == 3:
                observed = [m for m in request.messages if m["role"] == "tool"]
                assert (
                    json.loads(observed[-1]["content"])["sha256"]
                    == hashlib.sha256(content.encode()).hexdigest()
                )
            return ModelResponse(
                text="",
                tool_calls=[
                    {
                        "id": "write-report" if count == 2 else "read-report",
                        "name": "file_write" if count == 2 else "file_read",
                        "arguments": {"path": path, **({"content": content} if count == 2 else {})},
                    }
                ],
                usage={"input_tokens": 20, "output_tokens": 10},
            )
        observed = [m for m in request.messages if m["role"] == "tool"]
        assert count == 4
        assert content == json.loads(observed[-1]["content"])["content"]
        return response(
            {
                "summary": "说明文档已写入并读回，仍需独立审查。",
                "findings": ["内容与预期一致"],
                "decisions": [],
                "artifacts": [path],
                "evidence_references": [],
                "unresolved_issues": [],
                "confidence": 0.8,
                "next_actions": [],
            }
        )

    model = MockProvider(execute)
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    plan = graph.get(quest_id)
    with store.connection() as connection:
        team = connection.execute(
            "SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)
        ).fetchone()
    run = manager.start(
        quest_id, plan["id"], "task", team_digest=team["proposal_digest"], member_index=0
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending", [
        [m for m in request.messages if m["role"] == "tool"] for request in model.requests
    ]
    assert (tmp_path / path).read_bytes() == content.encode("utf-8")
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    for _ in range(10):
        if not asyncio.run(router.handle_pending_events(dispatcher)):
            break
    assert any("尚未确认任务完成" in message["content"] for message in store.list_messages())
    route = f"/api/conversations/main/subagents/{run['id']}/artifacts"
    artifacts = client.get(route).json()
    assert artifacts["state"] == "review_pending" and artifacts["execution_mode"] == "mock"
    assert not artifacts["current_files_verified"] and len(artifacts["items"]) == 1
    receipt = artifacts["items"][0]
    diff = client.get(f"/api/artifacts/receipts/{receipt['receipt_id']}/diff")
    assert diff.status_code == 200
    assert diff.json()["run_id"] == run["id"] and diff.json()["path"] == path
    assert "+# 离线验收报告" in diff.json()["text"]
    assert diff.json()["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    # New Store instance models durable replay reads, not a full process restart.
    reopened = Store(store.path, workspace=store.workspace)
    monkeypatch.setattr(main, "store", reopened)
    assert (
        client.get("/api/conversation/turn", params={"request_id": request["request_id"]}).json()[
            "state"
        ]
        == "committed"
    )
    assert client.post("/api/conversation/message", json=request).json() == first.json()
    assert len(model.requests) == 4 and len(router.provider.requests) == 1
    assert len(store.list_quests()) == 1
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM action_intents WHERE tool_name='file_write'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 1
    client.close()


@pytest.mark.parametrize("oversized", [False, True])
def test_local_verified_file_result_is_persisted_to_user_with_openable_reference(
    tmp_path, monkeypatch, oversized
):
    import test_node_verifier_m5 as local_verifier_fixture

    from forge_core.evidence import NodeVerifier
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    # Established test-only live provenance stub, not a provider or model-quality claim.
    # ToolEngine writes the real file; NodeVerifier actually runs its fixed JSON check.
    original_response = local_verifier_fixture.ModelResponse

    def with_artifact_delivery(**kwargs):
        if kwargs.get("text") == '{"summary":"fixture"}':
            kwargs["text"] = json.dumps(
                {
                    "summary": "JSON文件已经写入，等待独立本地校验。",
                    "artifacts": ["work/result.json"]
                    + (["不应直接展示" * 134] * 7 if oversized else []),
                },
                ensure_ascii=False,
            )
        return original_response(**kwargs)

    monkeypatch.setattr(local_verifier_fixture, "ModelResponse", with_artifact_delivery)
    store, quest, _, run, artifact = local_verifier_fixture.reviewed_artifact(tmp_path)
    assert artifact.read_text() == '{"ok":true}'
    assert NodeVerifier(store).verify(quest.id, "n")["status"] == "completed"
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        model_id="scripted",
        provider_id="fixture",
        context_limit=32000,
        autonomous=True,
    )
    with store.connection() as connection:
        receipt_id = connection.execute(
            "SELECT r.id FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id "
            "WHERE i.run_id=? AND i.tool_name='file_write' AND i.state='succeeded'",
            (run["id"],),
        ).fetchone()[0]
    reply = (
        "任务已完成。结果较长，这里只通知完成，不概括内容。"
        if oversized
        else f"JSON任务已通过本地验证：work/result.json；receipt:{receipt_id}"
    )

    def summarize(request, _):
        assert not oversized, "Oversized completion must not invoke a model"
        context = json.loads(request.messages[1]["content"])
        assert context["completed_context"] == []
        deliveries = context["notice"]["completed_delivery_summaries"]
        assert len(deliveries) == 1 and deliveries[0]["artifacts"] == ["work/result.json"]
        assert "reviewed Worker claim" in deliveries[0]["trust"]
        return response({"action": "update_user", "reply": reply})

    router.provider = MockProvider(summarize)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    assert asyncio.run(router.handle_pending_events(dispatcher))
    notices = [
        m
        for m in store.list_messages()
        if json.loads(m["metadata"]).get("source") == "router_notice"
    ]
    assert len(notices) == 1 and notices[0]["content"] == reply
    assert notices[0]["conversation_id"] == "main"
    metadata = json.loads(notices[0]["metadata"])
    if oversized:
        assert metadata["notice_phase"] == "completion_summary_unavailable"
        assert metadata["quest_id"] == quest.id
        assert metadata["execution_mode"] == "programmatic"
        assert metadata["completion_execution_mode"] == "live"
        assert len(metadata["completion_result_sources"]) == 1
        assert "不应直接展示" not in notices[0]["content"]
    with store.connection() as connection:
        completed = connection.execute(
            "SELECT id FROM events WHERE quest_id=? AND event_type='quest.status_changed' "
            "AND json_extract(payload,'$.to')='completed'",
            (quest.id,),
        ).fetchone()[0]
    assert completed in metadata["source_event_ids"]
    _, client = api_client(monkeypatch, tmp_path, store, router)
    result = client.get(f"/api/artifacts/receipts/{receipt_id}/diff")
    assert result.status_code == 200 and '+{"ok":true}' in result.json()["text"]
    assert result.json()["run_id"] == run["id"] and result.json()["path"] == "work/result.json"
    assert not asyncio.run(router._handle_notice([completed], "already-processed"))
    assert len(router.provider.requests) == (0 if oversized else 1)
    client.close()
