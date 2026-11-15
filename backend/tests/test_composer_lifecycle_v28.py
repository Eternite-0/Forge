"""Production V28 Planner/Composer events through an offline native HTTP transport."""

import asyncio
import hashlib
import importlib
import json
import secrets

import httpx
import pytest
from test_composer_resources_v21 import semantic_team
from test_event_dispatcher import drain_dispatcher
from test_planner_protocol_v23 import graph
from test_planner_protocol_v26 import v26_node

from forge_core.composition_capacity import remaining_composition_capacity
from forge_core.domain import ConversationMessage, ForgeEvent, ModelProfile, ProtocolSupport, Quest
from forge_core.model_intelligence import ObservationStore
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway


def stage(tmp_path, monkeypatch, *, outcome="valid", node_tools=("file_read",)):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FORGE_MODEL_TOKEN_LIMIT", "0")
    monkeypatch.setenv("FORGE_MODEL_SCOPE_LIMITS_JSON", "[]")
    for key in (
        "FORGE_DB_PATH",
        "FORGE_ROUTER_MODEL_ID",
        "FORGE_ROUTER_CONTEXT_LIMIT",
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    from forge_api import main

    importlib.reload(main)
    original = (
        "Inspect the complete original request. Task execution limits: "
        '{"max_total_tokens":1000,"max_nodes":1,"max_members":1,"max_concurrency":1}'
    )
    message = ConversationMessage(role="user", content=original)
    main.store.add_message(message)
    quest = Quest(
        title="Native offline V28 lifecycle",
        goal="Preserve this entire objective without a cumulative token cap",
        metadata={
            "original_request_message_id": message.id,
            "original_request_sha256": hashlib.sha256(original.encode()).hexdigest(),
        },
    )
    main.store.save_quest(quest)
    main.store.save_model(
        ModelProfile(
            id="native",
            provider="p",
            model_id="native",
            display_name="Explicit offline native model",
            context_window=65536,
            max_output_tokens=4096,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
            structured_output_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    selected = graph(v26_node("analysis", tools=list(node_tools)))
    native_requests = []

    def reply(request):
        payload = json.loads(request.content)
        native_requests.append(payload)
        name = payload["response_format"]["json_schema"]["name"]
        if name == "quest_graph":
            proposal = selected
        else:
            composer_count = sum(
                item["response_format"]["json_schema"]["name"] == "team_proposal"
                for item in native_requests
            )
            if outcome == "timeout":
                raise httpx.ReadTimeout("Offline timeout fixture", request=request)
            proposal = semantic_team(main.quest_graph.get(quest.id))
            if outcome == "invalid" or (outcome == "repair" and composer_count == 1):
                proposal["roles"][0]["input_contract"] = [" "]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(proposal)}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            },
        )

    provider = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_OFFLINE_FIXTURE",
            supports_tools=True,
            supports_json_schema=True,
            max_retries=0,
        ),
        credential_resolver=lambda _: "offline-placeholder",
        transport=httpx.MockTransport(reply),
        observer=ObservationStore(main.store).record,
        budget_ledger=main.model_budget,
    )
    main.provider = main.router.provider = provider
    main.router.provider_id = "p"
    main.router.model_id = "native"
    main.router.context_limit = 65536
    pool = ProviderPool({"p": provider})
    main.quest_graph.capacity_providers = pool
    main.team_service.capacity_providers = pool
    main.store.append_event(ForgeEvent(event_type="quest.proposed", quest_id=quest.id, payload={}))
    drain_dispatcher(main.dispatcher)
    planned = asyncio.run(main.process_pending_plan())
    assert planned, [item["notice_error"] for item in main.dispatcher.inbox("planner")]
    planner_context = json.loads(
        next(item["content"] for item in native_requests[0]["messages"] if item["role"] == "user")
    )
    assert planner_context["token_budgets_enforced"] is False
    assert "max_total_tokens" not in planner_context["limits"]
    assert "residual_model_budget_before_this_planner_call" not in planner_context
    plan = main.quest_graph.get(quest.id)
    assert main.model_budget.token_limit is None
    assert sum(node["budget_tokens"] for node in plan["nodes"]) > plan["max_total_tokens"] == 1000
    main.quest_graph.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    drain_dispatcher(main.dispatcher)
    return main, quest, plan, native_requests


@pytest.mark.parametrize("outcome,expected", [("valid", 1), ("repair", 2)])
def test_unlimited_tokens_preserve_native_context_binding_and_normal_team_approval(
    tmp_path, monkeypatch, outcome, expected
):
    main, quest, plan, requests = stage(tmp_path, monkeypatch, outcome=outcome)
    assert asyncio.run(main.process_pending_team())
    with main.store.connection() as connection:
        proposal = connection.execute(
            "SELECT * FROM team_proposals WHERE quest_id=?", (quest.id,)
        ).fetchone()
        stored = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE quest_id=?", (quest.id,)
            ).fetchone()[0]
        )
        attempts = connection.execute(
            "SELECT * FROM model_budget_attempts WHERE quest_id=? AND purpose='team_composer' "
            "ORDER BY rowid",
            (quest.id,),
        ).fetchall()
        remaining = remaining_composition_capacity(connection, quest.id, stored)
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0
    record = stored["capacity_contract"]["composition_capacity"]
    assert stored["capacity_contract"]["source_wire_format"] == "core-source-raw-text-v1"
    assert len(requests) == expected + 1
    assert len(attempts) == expected
    for index, attempt in enumerate(attempts):
        key = "initial_call_bound" if index == 0 else "repair_call_bound"
        assert attempt["parent_call_id"] == record["semantic_request_sha256"]
        assert attempt["state"] == "settled"
        assert attempt["input_reserved"] + attempt["output_reserved"] <= record[key] <= 65536
    assert remaining["future_composer_tokens"] == 0
    assert remaining["accepted_semantic_team"] is True
    assert proposal["status"] == "proposed"
    body = json.loads(proposal["body_json"])
    assert body["members"][0]["source_wire_format"] == "core-source-raw-text-v1"
    assert body["capacity_budget_snapshot"]["token_budgets_enforced"] is False
    main.team_service.approve(
        quest.id,
        plan["plan_revision"],
        proposal["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    with main.store.connection() as connection:
        assert (
            connection.execute(
                "SELECT status FROM team_proposals WHERE id=?", (proposal["id"],)
            ).fetchone()[0]
            == "approved"
        )


@pytest.mark.parametrize("outcome,expected", [("invalid", 2), ("timeout", 1)])
def test_a_failed_or_unresolved_composer_cycle_cannot_replay_http_on_notice_retry(
    tmp_path, monkeypatch, outcome, expected
):
    main, quest, _plan, requests = stage(tmp_path, monkeypatch, outcome=outcome)
    assert not asyncio.run(main.process_pending_team())
    assert len(requests) == expected + 1
    with main.store.connection() as connection:
        connection.execute(
            "UPDATE event_inbox SET notice_next_attempt=0,notice_dead_at=NULL "
            "WHERE consumer_id='composer' AND quest_id=?",
            (quest.id,),
        )
        connection.commit()
    assert not asyncio.run(main.process_pending_team())
    assert len(requests) == expected + 1


def test_a_node_with_no_domain_tools_still_binds_native_core_tool_capability_provenance(
    tmp_path, monkeypatch
):
    main, _quest, plan, requests = stage(tmp_path, monkeypatch, node_tools=())
    assert plan["nodes"][0]["allowed_tools"] == []
    assert asyncio.run(main.process_pending_team())
    assert len(requests) == 2
