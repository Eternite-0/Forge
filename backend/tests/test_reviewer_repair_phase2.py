"""Offline review repair with real SQLite, local receipts and HTTP MockTransport."""

import asyncio
import json

import httpx
import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.independent_review import IndependentReviewService, review_pass_matches
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager

PASS = {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}


def events(store, kind):
    return [
        json.loads(row["payload"])
        for row in store.list_events(limit=1000)
        if row["event_type"] == kind
    ]


def invalid_artifact(tmp_path, *, max_repairs=2):
    store, manager, model, run = prepared(
        tmp_path,
        [
            tool("file_write", {"path": "work/result.json", "content": '{"answer":'}),
            response(delivery()),
        ],
        tools=["file_write"],
        criteria=["json_review:work/result.json"],
        max_repairs=max_repairs,
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    return store, manager, model, run


def test_receipt_bound_json_syntax_error_requests_revision_without_model_claim(tmp_path):
    store, manager, model, run = invalid_artifact(tmp_path)
    before = manager.get(run["id"])
    result = asyncio.run(IndependentReviewService(manager).review(run["id"]))
    after = manager.get(run["id"])
    assert result["verdict"] == "NEEDS_REVISION" and result["revision_applied"]
    assert result["review_context"] == "deterministic_artifact_validation"
    assert result["validation_source"] == "core_json_parser"
    assert "call_id" not in result and "provider_id" not in result
    assert after["state"] == "queued" and after["repairs"] == 1
    assert after["steps"] == before["steps"] and after["token_spent"] == before["token_spent"]
    assert after["allowed_tools_json"] == before["allowed_tools_json"] == '["file_write"]'
    assert len(model.requests) == 2 and not events(store, "review.started")
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 0
        assert not review_pass_matches(store, connection, after, "json_review:work/result.json")
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1


def test_json_syntax_feedback_stops_at_original_repair_bound(tmp_path):
    store, manager, model, run = invalid_artifact(tmp_path, max_repairs=0)
    service = IndependentReviewService(manager)
    result = asyncio.run(service.review(run["id"]))
    assert result["verdict"] == "BLOCKED" and not result["revision_applied"]
    assert result["error_code"] == "artifact_json_repair_exhausted"
    assert manager.get(run["id"])["state"] == "review_pending"
    assert not asyncio.run(service.tick())
    assert len(events(store, "review.result")) == 1 and len(model.requests) == 2


def test_changed_invalid_json_artifact_does_not_get_trusted_revision_feedback(tmp_path):
    store, manager, model, run = invalid_artifact(tmp_path)
    (tmp_path / "work/result.json").write_text('{"different":', encoding="utf-8")
    assert asyncio.run(IndependentReviewService(manager).tick())
    assert not events(store, "review.result") and events(store, "review.blocked")
    assert manager.get(run["id"])["state"] == "review_pending" and len(model.requests) == 2


def test_committed_deterministic_feedback_survives_restart_before_requeue(tmp_path):
    store, manager, model, run = invalid_artifact(tmp_path)

    def stopped(*_):
        raise RuntimeError("Test-only crash between parser feedback and requeue")

    manager.request_revision = stopped
    with pytest.raises(RuntimeError, match="Test-only crash"):
        asyncio.run(IndependentReviewService(manager).review(run["id"]))
    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        manager.providers,
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    assert asyncio.run(IndependentReviewService(resumed).tick())
    assert resumed.get(run["id"])["state"] == "queued"
    assert len(events(reopened, "worker.revision_requested")) == 1
    assert len(events(reopened, "review.result")) == 1 and len(model.requests) == 2


def test_json_syntax_revision_can_fix_owned_artifact_then_receive_independent_pass(tmp_path):
    store, manager, model, run = prepared(
        tmp_path,
        [],
        tools=["file_write", "file_revise"],
        criteria=["json_review:work/result.json"],
    )
    calls = []

    async def script(request):
        calls.append(request)
        if len(calls) == 1:
            return tool("file_write", {"path": "work/result.json", "content": '{"answer":'})
        if len(calls) == 2:
            return response(delivery("Initial artifact"))
        if len(calls) == 3:
            references = next(
                message["content"]
                for message in reversed(request.messages)
                if message.get("content", "").startswith("Core artifact receipt references")
            )
            prior = json.loads(references.split(": ", 1)[1])[0]
            return tool(
                "file_revise",
                {
                    "path": "work/result.json",
                    "prior_receipt_id": prior["receipt_id"],
                    "expected_sha256": prior["sha256"],
                    "content": '{"answer":4}',
                },
                ordinal=2,
            )
        if len(calls) == 4:
            return response(delivery("Corrected JSON artifact"))
        packet = json.loads(request.messages[-1]["content"])
        assert packet["artifacts"][0]["content"] == {"answer": 4}
        return response(PASS)

    model.generate = script
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    service = IndependentReviewService(manager)
    assert asyncio.run(service.review(run["id"]))["revision_applied"]
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    result = asyncio.run(service.review(run["id"]))
    assert result["verdict"] == "PASS" and result["review_context"] == "independent_same_model"
    assert result["execution_mode"] == "mock" and len(calls) == 5
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
        history = [
            json.loads(row[0])["content"]
            for row in connection.execute(
                "SELECT parameters_json FROM action_intents ORDER BY rowid"
            )
        ]
        assert history == ['{"answer":', '{"answer":4}']


@pytest.mark.parametrize("always_truncate", [False, True])
def test_settled_reviewer_truncation_keeps_actual_usage_and_bounded_rounds(
    tmp_path, always_truncate
):
    store, manager, _model, run = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery())],
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    prior = manager.get(run["id"])
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if always_truncate or len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"verdict":"PA'}, "finish_reason": "length"}
                    ],
                    "usage": {"prompt_tokens": 25, "completion_tokens": body["max_tokens"]},
                },
            )
        assert "previous review reached its output limit" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(PASS)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 15},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(handler),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1),
    )
    manager.providers = ProviderPool({"protocol": gateway})
    service = IndependentReviewService(manager, max_rounds=2)
    first = asyncio.run(service.review(run["id"]))
    assert first["status"] == "deferred" and first["reason_code"] == "truncated"
    result = asyncio.run(service.review(run["id"]))
    assert result["verdict"] == ("BLOCKED" if always_truncate else "PASS")
    assert result["error_code"] == ("truncated" if always_truncate else None)
    assert len(calls) == 2 and not asyncio.run(service.tick())
    actual = (
        sum(25 + body["max_tokens"] for body in calls)
        if always_truncate
        else 25 + calls[0]["max_tokens"] + 40
    )
    assert manager.get(run["id"])["token_spent"] == prior["token_spent"] + actual
    assert manager.get(run["id"])["steps"] == prior["steps"] + 2
    assert manager.get(run["id"])["repairs"] == prior["repairs"]
    rejected = events(store, "review.response_rejected")
    assert len(rejected) == (2 if always_truncate else 1)
    assert all(item["tools_executed_from_response"] == 0 for item in rejected)
    with store.connection() as connection:
        attempts = connection.execute("SELECT * FROM model_budget_attempts").fetchall()
        assert all(item["state"] == "settled" for item in attempts)
        assert sum(item["input_charged"] + item["output_charged"] for item in attempts) == actual
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 1


def test_unknown_reviewer_timeout_with_usage_never_enters_truncation_repair(tmp_path):
    store, manager, model, run = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery())],
    )
    asyncio.run(manager.run(run["id"]))
    prior = manager.get(run["id"])
    seen = []

    async def timeout(request):
        seen.append(request)
        request._budget_post_started = True
        raise ProviderError("timeout", usage={"input_tokens": 25, "output_tokens": 15})

    model.generate = timeout
    service = IndependentReviewService(manager)
    result = asyncio.run(service.review(run["id"]))
    assert result["verdict"] == "BLOCKED" and result["error_code"] == "timeout"
    assert result["exception_type"] == "ProviderError"
    assert len(seen) == 1 and not asyncio.run(service.tick())
    assert manager.get(run["id"])["token_spent"] > prior["token_spent"] + 40
    assert not events(store, "review.released") and not events(store, "review.response_rejected")


def test_unsettled_live_provenance_stub_does_not_authorize_reviewer_truncation_retry(tmp_path):
    """Test-only live labels exercise rejection; no real Provider call occurs."""
    store, manager, model, run = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery())],
    )
    model.execution_mode = "live"
    model._budget_ledger = object()
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET execution_mode='live' WHERE id=?", (run["id"],)
        )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    prior = manager.get(run["id"])
    seen = []

    async def unsettled(request):
        seen.append(request)
        request._budget_post_started = True
        raise ProviderError("truncated", usage={"input_tokens": 25, "output_tokens": 15})

    model.generate = unsettled
    service = IndependentReviewService(manager)
    result = asyncio.run(service.review(run["id"]))
    assert result["verdict"] == "BLOCKED" and result["error_code"] == "truncated"
    assert len(seen) == 1 and not asyncio.run(service.tick())
    assert manager.get(run["id"])["token_spent"] > prior["token_spent"] + 40
    assert not events(store, "review.released") and not events(store, "review.response_rejected")
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 0


def test_oversized_review_native_request_emits_context_diagnostic_without_truncation(tmp_path):
    store, manager, model, run = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery("x" * 58000))],
    )
    (tmp_path / "work/input.txt").write_text("source " * 1700, encoding="utf-8")
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    service = IndependentReviewService(manager)
    assert asyncio.run(service.tick())
    assert not asyncio.run(service.tick())
    assert events(store, "review.blocked")[-1]["reason_code"] == "review_context_limit"
    assert len(model.requests) == 2 and not events(store, "review.started")


def test_reviewer_respects_configured_provider_timeout(tmp_path):
    from types import SimpleNamespace

    _store, manager, model, run = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery()), response(PASS)],
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    model.config = SimpleNamespace(
        kind="openai_compatible", supports_json_schema=False, timeout_seconds=0.25
    )
    assert asyncio.run(IndependentReviewService(manager).review(run["id"]))["verdict"] == "PASS"
    assert model.requests[-1].timeout_seconds == 0.25
