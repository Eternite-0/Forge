"""Offline protocol models with real SQLite, governed tools and subprocesses.

These tests do not claim a real model or a real-provider quality measurement.
"""

import asyncio
import json

import httpx
import pytest

from forge_core.agent_protocol import AgentMessage, AgentMessageType
from forge_core.dispatcher import Dispatcher
from forge_core.domain import DomainError, ForgeEvent, ModelProfile, ProtocolSupport, Quest
from forge_core.governance import GovernanceService
from forge_core.independent_review import IndependentReviewService, review_pass_matches
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelResponse
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_recovery import settled_read_failure, unresolved_calls
from forge_core.worker_runtime import WorkerManager


def response(value, *, calls=None):
    return ModelResponse(
        text=json.dumps(value) if not isinstance(value, str) else value,
        tool_calls=calls or [],
        usage={"input_tokens": 25, "output_tokens": 15},
        finish_reason="tool_calls" if calls else "stop",
    )


def delivery(summary="done"):
    return {
        "summary": summary,
        "findings": [],
        "decisions": [],
        "artifacts": [],
        "evidence_references": [],
        "unresolved_issues": [],
        "confidence": 0.8,
        "next_actions": [],
    }


class ProtocolModel:
    execution_mode = "mock"

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    async def generate(self, request):
        self.requests.append(request)
        assert self.responses, "Unexpected model call"
        return self.responses.pop(0)


def prepared(tmp_path, responses, *, tools=None, criteria=None, max_repairs=2):
    tools = tools or ["file_read"]
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    (tmp_path / "work").mkdir()
    (tmp_path / "work/input.txt").write_text("Original source", encoding="utf-8")
    quest = Quest(title="Protocol task", goal="Inspect the original source with evidence")
    store.save_quest(quest)
    graphs = QuestGraphService(store)
    plan = graphs.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": quest.goal,
                    "success_criteria": criteria or ["delivery_review"],
                    "budget_tokens": 60000,
                    "allowed_tools": tools,
                }
            ],
            "edges": [],
            "max_total_tokens": 60000,
        },
        expected_version=0,
    )
    graphs.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    store.save_model(
        ModelProfile(
            id="protocol",
            provider="protocol",
            model_id="fixture",
            display_name="Offline protocol fixture",
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
                "tools": tools,
                "input_tokens": 30000,
                "output_tokens": 3000,
                "max_steps": 12,
                "max_tool_calls": 5,
                "max_duration_seconds": 120,
            }
        ],
        {
            "max_members": 1,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 33000,
            "max_total_tool_calls": 5,
            "allowed_tools": tools,
        },
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(
        quest.id,
        plan["plan_revision"],
        team["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    model = ProtocolModel(responses)
    manager = WorkerManager(
        store,
        ProviderPool({"protocol": model}),
        ToolEngine(store),
        team_authority=teams,
        max_repairs=max_repairs,
    )
    worker = manager.start(
        quest.id, plan["id"], "n", team_digest=team["proposal_digest"], member_index=0
    )
    return store, manager, model, worker


def tool(name, arguments, ordinal=1):
    return response("", calls=[{"id": f"tool-{ordinal}", "name": name, "arguments": arguments}])


def test_delivery_self_repair_is_bounded_and_charges_actual_usage(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path, [response({"summary": "incomplete"}), response(delivery())]
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert result["token_spent"] == 80 and result["steps"] == 2
    assert "repair_request" in model.requests[-1].messages[-1]["content"]
    events = store.list_events(limit=100)
    accepted = next(event for event in events if event["event_type"] == "worker.accepted")
    assert (
        AgentMessage.from_event(accepted, json.loads(accepted["payload"])).message_type
        == AgentMessageType.TASK_ACCEPTED
    )


def test_invalid_delivery_stops_at_finite_repair_bound(tmp_path):
    _store, manager, model, worker = prepared(tmp_path, [response("bad JSON")] * 3)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == 2
    assert result["token_spent"] == 120 and len(model.requests) == 3


def test_failed_numeric_diagnostic_is_observed_and_never_replayed(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("numeric_ast", {"source": "result = 1 / 0", "inputs": {}}),
            tool("numeric_ast", {"source": "result = 2 + 2", "inputs": {}}, 2),
            response(delivery()),
        ],
        tools=["numeric_ast"],
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert len(model.requests) == 3 and result["tool_spent"] == 2
    with store.connection() as connection:
        calls = connection.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        assert [call["state"] for call in calls] == ["failed", "succeeded"]
        assert settled_read_failure(connection, worker["id"], calls[0])
        assert not unresolved_calls(connection, worker["id"])
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
    feedback = model.requests[1].messages
    assert any(
        item["role"] == "tool" and '"state":"failed"' in item["content"] for item in feedback
    )


def test_mock_review_isolated_context_cannot_prove_live_completion(tmp_path):
    verdict = {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
    store, manager, model, worker = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery()), response(verdict)],
    )
    asyncio.run(manager.run(worker["id"]))
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["verdict"] == "PASS" and result["execution_mode"] == "mock"
    request = model.requests[-1]
    assert request.budget_context.purpose == "independent_reviewer"
    assert request.budget_context.worker_run_id == worker["id"]
    assert len(request.messages) == 2 and request.tools is None
    packet = json.loads(request.messages[-1]["content"])
    assert packet["receipts"][0]["output"]["content"] == "Original source"
    assert "worker_messages" not in packet
    with store.connection() as connection:
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        assert not review_pass_matches(store, connection, row, "delivery_review")
    assert manager.get(worker["id"])["state"] == "review_pending"


def test_review_revision_retains_budget_receipts_and_survives_core_reopen(tmp_path):
    verdict = {
        "verdict": "NEEDS_REVISION",
        "findings": ["Explain source finding"],
        "revision_goals": ["State the actual source finding"],
        "confidence": 0.7,
    }
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            response(delivery()),
            response(verdict),
            response(delivery("Corrected finding")),
        ],
    )
    asyncio.run(manager.run(worker["id"]))
    prior = manager.get(worker["id"])
    service = IndependentReviewService(manager)
    result = asyncio.run(service.review(worker["id"]))
    assert result["revision_applied"] and manager.get(worker["id"])["state"] == "queued"
    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    final = asyncio.run(resumed.run(worker["id"]))
    assert final["state"] == "review_pending" and final["repairs"] == 1
    assert (
        final["token_spent"] > prior["token_spent"] and final["tool_spent"] == prior["tool_spent"]
    )
    with reopened.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='worker.result'"
            ).fetchone()[0]
            == 2
        )
    assert len(model.requests) == 4


def test_model_claim_without_tool_receipt_blocks_review_and_is_durable(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [response(delivery())])
    asyncio.run(manager.run(worker["id"]))
    service = IndependentReviewService(manager)
    assert asyncio.run(service.tick())
    assert not asyncio.run(service.tick())
    assert len(model.requests) == 1
    assert any(event["event_type"] == "review.blocked" for event in store.list_events(limit=100))
    with pytest.raises(DomainError):
        manager.request_revision(worker["id"], "forged-review-event")


def test_artifact_change_during_review_is_recorded_and_rejects_pass(tmp_path):
    verdict = {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_write", {"path": "work/output.json", "content": '{"answer":4}'}),
            response(delivery()),
            response(verdict),
        ],
        tools=["file_write"],
        criteria=["json_review:work/output.json"],
    )
    asyncio.run(manager.run(worker["id"]))
    generate = model.generate

    async def mutate(request):
        if request.budget_context.purpose == "independent_reviewer":
            (tmp_path / "work/output.json").write_text('{"answer":5}', encoding="utf-8")
        return await generate(request)

    model.generate = mutate
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["verdict"] == "BLOCKED" and result["error_code"] == "review_source_changed"
    assert manager.get(worker["id"])["state"] == "review_pending"
    assert any(event["event_type"] == "review.result" for event in store.list_events(limit=100))


def test_review_rejects_missing_original_user_contract(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            response(delivery()),
        ],
    )
    asyncio.run(manager.run(worker["id"]))
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET metadata=? WHERE id=?",
            (
                json.dumps(
                    {
                        "original_request_message_id": "missing",
                        "original_request_sha256": "0" * 64,
                    }
                ),
                worker["quest_id"],
            ),
        )
    assert asyncio.run(IndependentReviewService(manager).tick())
    assert len(model.requests) == 2
    assert any(event["event_type"] == "review.blocked" for event in store.list_events(limit=100))


def test_empty_revision_goals_fail_closed_without_resuming_worker(tmp_path):
    verdict = {
        "verdict": "NEEDS_REVISION",
        "findings": ["bad"],
        "revision_goals": [],
        "confidence": 0.8,
    }
    _store, manager, _model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            response(delivery()),
            response(verdict),
        ],
    )
    asyncio.run(manager.run(worker["id"]))
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["verdict"] == "BLOCKED" and result["error_code"] == "invalid_review"
    assert manager.get(worker["id"])["state"] == "review_pending"


def test_preexecution_scope_rejection_is_observed_without_fake_receipt(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [
            response(
                "",
                calls=[
                    {"id": "root-list", "name": "directory_list", "arguments": {"path": "."}},
                    {"id": "project-list", "name": "directory_list", "arguments": {"path": "work"}},
                ],
            ),
            response(delivery()),
        ],
        tools=["directory_list"],
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    with store.connection() as connection:
        calls = connection.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        assert [call["state"] for call in calls] == ["failed", "succeeded"]
        assert calls[0]["receipt_id"] is None
        assert settled_read_failure(connection, worker["id"], calls[0])
        assert not unresolved_calls(connection, worker["id"])
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
    # OpenAI-style batched tool replies remain contiguous after the assistant call.
    messages = model.requests[-1].messages
    assistant = next(index for index, item in enumerate(messages) if item.get("tool_calls"))
    assert [item["role"] for item in messages[assistant + 1 :]] == ["tool", "tool"]


def test_missing_local_file_has_real_failure_receipt_and_bounded_correction(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/missing.txt"}),
            tool("file_read", {"path": "work/input.txt"}, 2),
            response(delivery()),
        ],
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert len(model.requests) == 3
    with store.connection() as connection:
        calls = connection.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        assert calls[0]["receipt_id"] is not None and calls[0]["state"] == "failed"
        assert settled_read_failure(connection, worker["id"], calls[0])
        failure = json.loads(calls[0]["result_json"])
        assert failure["output"]["error_code"] == "resource_unavailable"
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2


def test_native_agent_protocol_validates_assignment_and_deadletters_forgery(tmp_path):
    store, _manager, _model, worker = prepared(tmp_path, [])
    after = store.list_events(limit=1000)[-1]["seq"]
    dispatcher = Dispatcher(store, max_attempts=1)
    dispatcher.register("protocol-audit", replay_from_seq=after)
    valid = ForgeEvent(
        event_type="worker.accepted",
        quest_id=worker["quest_id"],
        worker_id=worker["worker_id"],
        payload={
            "run_id": worker["id"],
            "plan_id": worker["plan_id"],
            "task_node_key": worker["node_key"],
        },
    )
    store.append_event(valid)
    item = dispatcher.deliver(dispatcher.claim("protocol-audit"))
    assert item["execution_protocol"] == "native"
    assert item["agent_message"]["message_type"] == "TASK_ACCEPTED"
    malformed = ForgeEvent(
        event_type="worker.accepted",
        quest_id=worker["quest_id"],
        worker_id=worker["worker_id"],
        payload={
            "run_id": worker["id"],
            "plan_id": "forged-plan",
            "task_node_key": worker["node_key"],
        },
    )
    store.append_event(malformed)
    lease = dispatcher.claim("protocol-audit")
    with pytest.raises(ValueError):
        dispatcher.deliver(lease)
    dispatcher.fail(lease, "invalid_agent_protocol")
    assert dispatcher.dead_letters("protocol-audit")[0]["event_id"] == malformed.id
    assert len(dispatcher.inbox("protocol-audit")) == 1


def test_legacy_worker_proposal_is_labelled_nonexecution(tmp_path):
    store = Store(tmp_path / "legacy.sqlite3", workspace=Workspace.from_path(tmp_path))
    store.append_event(ForgeEvent(event_type="worker.proposed", payload={"role": "research"}))
    dispatcher = Dispatcher(store)
    dispatcher.register("router")
    result = dispatcher.deliver(dispatcher.claim("router"))
    assert result["execution_protocol"] == "legacy_nonexecution" and result["agent_message"] is None


@pytest.mark.parametrize(
    "code,recorded",
    [
        ("budget_exhausted", "budget_exhausted"),
        ("untrusted-secret-marker", "unexpected_provider_error"),
    ],
)
def test_provider_failure_records_only_controlled_codes(tmp_path, code, recorded):
    from forge_core.providers import ProviderError

    store, manager, model, worker = prepared(tmp_path, [])

    async def reject(request):
        request._budget_post_started = False
        raise ProviderError(code)

    model.generate = reject
    assert asyncio.run(manager.run(worker["id"]))["state"] == "blocked"
    payload = json.loads(
        next(
            event["payload"]
            for event in reversed(store.list_events(limit=100))
            if event["event_type"] == "worker.blocked"
        )
    )
    assert payload["provider_error_code"] == recorded
    assert "untrusted-secret-marker" not in json.dumps(payload)


def test_committed_review_revision_survives_restart_before_requeue(tmp_path):
    verdict = {
        "verdict": "NEEDS_REVISION",
        "findings": ["Missing detail"],
        "revision_goals": ["Add the source detail"],
        "confidence": 0.7,
    }
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            response(delivery()),
            response(verdict),
            response(delivery("Fixed")),
        ],
    )
    asyncio.run(manager.run(worker["id"]))

    def crash_after_review(*_):
        raise RuntimeError("Simulated Core stop between result and requeue")

    manager.request_revision = crash_after_review
    with pytest.raises(RuntimeError, match="Simulated Core stop"):
        asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert manager.get(worker["id"])["state"] == "review_pending"
    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    assert asyncio.run(IndependentReviewService(resumed).tick())
    assert resumed.get(worker["id"])["state"] == "queued"
    assert len(model.requests) == 3
    assert asyncio.run(resumed.run(worker["id"]))["state"] == "review_pending"
    with reopened.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='worker.revision_requested'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("always_truncate", [False, True])
def test_settled_gateway_truncation_repairs_compact_delivery_without_partial_tool_effects(
    tmp_path, always_truncate
):
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway

    store, manager, _model, worker = prepared(tmp_path, [], tools=["file_write"])
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if always_truncate or len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"summary":"partial',
                                "tool_calls": [
                                    {
                                        "id": "partial-write",
                                        "type": "function",
                                        "function": {
                                            "name": "file_write",
                                            "arguments": json.dumps(
                                                {
                                                    "path": "work/partial.txt",
                                                    "content": "must not execute",
                                                }
                                            ),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {"prompt_tokens": 25, "completion_tokens": body["max_tokens"]},
                },
            )
        assert "repair_request" in body["messages"][-1]["content"]
        assert "compact complete JSON delivery" in body["messages"][-1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(delivery())}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 25, "completion_tokens": 15},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(handler),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1),
    )
    manager.providers = ProviderPool({"protocol": gateway})
    result = asyncio.run(manager.run(worker["id"]))
    expected = (
        sum(25 + call["max_tokens"] for call in calls)
        if always_truncate
        else 25 + calls[0]["max_tokens"] + 40
    )
    assert result["token_spent"] == expected and result["steps"] == len(calls)
    assert result["repairs"] == (2 if always_truncate else 1)
    assert result["state"] == ("blocked" if always_truncate else "review_pending")
    assert len(calls) == (3 if always_truncate else 2)
    assert not (tmp_path / "work/partial.txt").exists()
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM worker_tool_calls").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
        attempts = connection.execute(
            "SELECT state,input_charged,output_charged FROM model_budget_attempts"
        ).fetchall()
        assert all(item["state"] == "settled" for item in attempts)
        assert sum(item["input_charged"] + item["output_charged"] for item in attempts) == expected
    rejected = [
        json.loads(event["payload"])
        for event in store.list_events(limit=1000)
        if event["event_type"] == "worker.model_response_rejected"
    ]
    assert len(rejected) == (3 if always_truncate else 1)
    assert all(event["tools_executed_from_response"] == 0 for event in rejected)


def test_unknown_provider_timeout_does_not_enter_truncation_repair(tmp_path):
    from forge_core.providers import ProviderError

    store, manager, model, worker = prepared(tmp_path, [])
    calls = []

    async def timeout(request):
        calls.append(request)
        request._budget_post_started = True
        raise ProviderError("timeout", usage={"input_tokens": 25, "output_tokens": 15})

    model.generate = timeout
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == 0
    assert len(calls) == 1 and result["token_spent"] > 40
    assert not any(
        event["event_type"] == "worker.model_response_rejected"
        for event in store.list_events(limit=100)
    )


def test_unsettled_live_provenance_stub_cannot_authorize_truncation_repair(tmp_path):
    """Test-only live label exercises rejection; there is no real Provider call."""
    from forge_core.providers import ProviderError

    store, manager, model, worker = prepared(tmp_path, [])
    model.execution_mode = "live"
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET execution_mode='live' WHERE id=?", (worker["id"],)
        )

    async def incomplete(request):
        request._budget_post_started = True
        raise ProviderError("truncated", usage={"input_tokens": 25, "output_tokens": 15})

    model.generate = incomplete
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == 0
    assert result["token_spent"] > 40
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 0


def test_authorized_worker_renews_between_settled_steps_beyond_initial_lease(tmp_path):
    import time

    store, manager, model, worker = prepared(
        tmp_path, [tool("file_read", {"path": "work/input.txt"}), response(delivery())]
    )
    started = time.time()
    clock = [started]
    manager.clock = lambda: clock[0]
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET deadline=? WHERE id=?", (started + 120, worker["id"])
        )
    generate = model.generate
    leases = []

    async def slow_steps(request):
        leases.append(manager.get(worker["id"])["lease_until"])
        result = await generate(request)
        clock[0] += 55
        return result

    model.generate = slow_steps
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["generation"] == 1
    assert leases == [started + 60, started + 115] and clock[0] == started + 110
    assert result["steps"] == 2 and result["token_spent"] == 80
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1


@pytest.mark.parametrize("invalid", ["expired", "competing_owner", "paused", "plan_changed"])
def test_worker_lease_renewal_never_revives_stale_or_unauthorized_claim(tmp_path, invalid):
    store, manager, _model, worker = prepared(tmp_path, [])
    clock = [100.0]
    manager.clock = lambda: clock[0]
    token = manager._claim(worker["id"])
    with store.unit_of_work() as work:
        if invalid == "expired":
            clock[0] = 161.0
        elif invalid == "competing_owner":
            work.connection.execute(
                "UPDATE worker_runs SET lease_token='new-owner',generation=generation+1,lease_until=200 WHERE id=?",
                (worker["id"],),
            )
        elif invalid == "paused":
            work.connection.execute(
                "UPDATE quests SET status='paused' WHERE id=?", (worker["quest_id"],)
            )
        else:
            work.connection.execute(
                "UPDATE quest_plans SET status='superseded' WHERE id=?", (worker["plan_id"],)
            )
    before = manager.get(worker["id"])
    from forge_core.domain import DomainError

    with pytest.raises(DomainError), store.unit_of_work() as work:
        manager._renew_lease(work.connection, worker["id"], token)
    assert manager.get(worker["id"]) == before


def test_worker_lease_renewal_stays_inside_original_task_deadline(tmp_path):
    store, manager, _model, worker = prepared(tmp_path, [])
    clock = [100.0]
    manager.clock = lambda: clock[0]
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET deadline=170 WHERE id=?", (worker["id"],))
    token = manager._claim(worker["id"])
    clock[0] = 155.0
    with store.unit_of_work() as work:
        renewed = manager._renew_lease(work.connection, worker["id"], token)
        assert renewed["lease_until"] == renewed["deadline"] == 170.0


@pytest.mark.parametrize(
    "failure,expected",
    [(TimeoutError("secret-marker"), "TimeoutError"), (TypeError("secret-marker"), "TypeError")],
)
def test_model_failure_records_fixed_exception_label_without_raw_text(tmp_path, failure, expected):
    store, manager, model, worker = prepared(tmp_path, [])

    async def reject(request):
        request._budget_post_started = True
        raise failure

    model.generate = reject
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["token_spent"] > 0
    event = next(
        json.loads(event["payload"])
        for event in reversed(store.list_events(limit=100))
        if event["event_type"] == "worker.blocked"
    )
    assert event["exception_type"] == expected
    assert event["provider_error_code"] == (
        "timeout" if expected == "TimeoutError" else "unexpected_provider_error"
    )
    assert "secret-marker" not in json.dumps(event)


def test_outer_worker_timeout_keeps_actual_gateway_attempt_unknown(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from forge_core import worker_runtime
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway

    store, manager, _model, worker = prepared(tmp_path, [])
    seen = []
    post_started, post_cancelled = asyncio.Event(), asyncio.Event()

    async def delayed(request):
        seen.append(request)
        post_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            post_cancelled.set()

    async def expire_after_post(awaitable, *, timeout):
        # Trigger the actual outer asyncio timeout only after HTTP transport
        # entry. Setup/SQLite speed cannot turn this into an unsent request.
        pending = asyncio.create_task(awaitable)
        try:
            await asyncio.wait_for(post_started.wait(), timeout=5)
            assert timeout > 0 and not pending.done()
            return await asyncio.wait_for(pending, timeout=0)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    monkeypatch.setattr(
        worker_runtime,
        "asyncio",
        SimpleNamespace(wait_for=expire_after_post, CancelledError=asyncio.CancelledError),
    )

    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(delayed),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1),
    )
    manager.providers = ProviderPool({"protocol": gateway})
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == 0
    assert post_started.is_set() and post_cancelled.is_set()
    assert len(seen) == 1 and seen[0].method == "POST" and result["token_spent"] > 0
    with store.connection() as connection:
        attempts = connection.execute("SELECT * FROM model_budget_attempts").fetchall()
        assert len(attempts) == 1 and attempts[0]["state"] == "reserved"
        assert attempts[0]["input_charged"] is None and attempts[0]["output_charged"] is None
        assert attempts[0]["input_reserved"] > 0 and attempts[0]["output_reserved"] > 0
        local = connection.execute("SELECT * FROM worker_model_reservations").fetchall()
        assert len(local) == 1 and local[0]["state"] == "reserved"
        assert local[0]["call_id"] == attempts[0]["call_id"]
        assert local[0]["reserved_tokens"] == result["token_spent"]
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM events WHERE event_type='worker.blocked'"
            ).fetchone()[0]
        )
    assert payload["provider_error_code"] == "timeout"
    assert payload["exception_type"] == "TimeoutError"
