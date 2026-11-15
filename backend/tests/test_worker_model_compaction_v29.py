"""Offline whole-exchange handoff, fencing and exact accounting verification."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_model_compaction_v29 import http_response, mock_gateway
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_worker_context_tail_phase2 import oversized_tail
from test_worker_intelligence_phase2 import ProtocolModel, delivery, prepared

from forge_core.context_service import ContextService, _json
from forge_core.domain import DomainError
from forge_core.independent_review import IndependentReviewService
from forge_core.provider_pool import ProviderPool
from forge_core.worker_model_compaction import WorkerModelCompactor
from forge_core.worker_recovery import unresolved_calls

pytest_plugins = ["test_worker_runtime"]


def seeded(tmp_path, *, chars=1500):
    store, manager, _model, worker = prepared(tmp_path, [])
    token = manager._claim(worker["id"])
    originals = {}
    with store.unit_of_work() as work:
        for ordinal in range(2, 10):
            message = {
                "role": "assistant",
                "content": f"original-{ordinal}:"
                + "x" * (chars if ordinal < 8 else 10)
                + "中文\r\n",
            }
            originals[ordinal] = message
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (worker["id"], ordinal, message["role"], _json(message)),
            )
    return store, manager, worker, token, originals


def archives(store):
    with store.connection() as connection:
        return [tuple(row) for row in connection.execute("SELECT * FROM context_archives")]


def test_worker_batches_use_complete_previous_handoff_and_immutable_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store, manager, worker, token, originals = seeded(tmp_path)
    summaries, selected = [], []

    def handler(request):
        body = json.loads(request.content)
        assert not body.get("tools") and not body.get("response_format")
        assert len(json.dumps(body, ensure_ascii=False).encode()) + 64 + 128 <= 7000
        packet = json.loads(body["messages"][1]["content"])
        assert packet["previous_context"] == (summaries[-1] if summaries else {})
        for item in packet["messages"]:
            assert item["message"] == originals[item["ordinal"]]
            selected.append(item["ordinal"])
        summaries.append(f"Handoff {len(summaries) + 1}: preserve original analysis and next step.")
        return http_response(summaries[-1])

    gateway = mock_gateway(store, handler)
    manager.providers = ProviderPool({"protocol": gateway})
    compactor = WorkerModelCompactor(
        manager, gateway, worker["id"], token, context_limit=7000, output_limit=128
    )
    while True:
        result = asyncio.run(compactor.compact(input_budget=6000, source_readback_available=True))
        if not result["source_count"]:
            break
    assert len(summaries) >= 2 and selected == sorted(set(selected))
    assert selected[0] == 2 and selected[-1] <= 7
    service = ContextService(store)
    for ordinal in selected:
        assert (
            json.loads(service.read_source("worker", worker["id"], ordinal)["message_json"])
            == originals[ordinal]
        )
    context = service.build_worker_context(worker["id"], input_budget=6000)
    capsule = json.loads(
        next(
            item["content"] for item in context if item["content"].startswith("Archived task")
        ).split(": ", 1)[1]
    )
    assert capsule["model_handoff"]["text"] == summaries[-1]
    assert capsule["model_handoff"]["status"] == "unverified"
    assert capsule["task"]["task_node_key"] == "n"
    assert manager.get(worker["id"])["steps"] == len(summaries)
    assert manager.get(worker["id"])["token_spent"] == 40 * len(summaries)
    with store.connection() as c:
        local = c.execute("SELECT * FROM worker_model_reservations").fetchall()
        attempts = c.execute("SELECT * FROM model_budget_attempts").fetchall()
        assert len(local) == len(attempts) == len(summaries)
        assert all(
            row["state"] == "settled"
            and json.loads(row["context_json"])["purpose"] == "context_compaction"
            for row in local
        )
        assert all(
            row["purpose"] == "context_compaction" and row["worker_run_id"] == worker["id"]
            for row in attempts
        )
        assert c.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_messages SET message_json='{}' WHERE run_id=? AND ordinal=2",
            (worker["id"],),
        )
    with pytest.raises(DomainError, match="integrity"):
        service.build_worker_context(worker["id"], input_budget=6000)


@pytest.mark.parametrize("race", ["expired", "new_owner", "paused", "plan", "capsule", "message"])
def test_worker_model_handoff_cannot_checkpoint_after_fence_or_frontier_changes(
    tmp_path, race, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store, manager, worker, token, _originals = seeded(tmp_path)
    before = archives(store)

    def handler(_request):
        with store.unit_of_work() as work:
            if race == "expired":
                work.connection.execute(
                    "UPDATE worker_runs SET lease_until=0 WHERE id=?", (worker["id"],)
                )
            elif race == "new_owner":
                work.connection.execute(
                    "UPDATE worker_runs SET lease_token='new',generation=generation+1 WHERE id=?",
                    (worker["id"],),
                )
            elif race == "paused":
                work.connection.execute(
                    "UPDATE quests SET status='paused' WHERE id=?", (worker["quest_id"],)
                )
            elif race == "plan":
                work.connection.execute(
                    "UPDATE quest_plans SET status='superseded' WHERE id=?", (worker["plan_id"],)
                )
            elif race == "capsule":
                work.connection.execute(
                    "UPDATE worker_capsules SET revision=revision+1,next_action='changed' WHERE run_id=?",
                    (worker["id"],),
                )
            else:
                work.connection.execute(
                    "UPDATE worker_messages SET message_json=? WHERE run_id=? AND ordinal=2",
                    (_json({"role": "assistant", "content": "changed"}), worker["id"]),
                )
        return http_response()

    gateway = mock_gateway(store, handler)
    with pytest.raises(DomainError):
        asyncio.run(
            WorkerModelCompactor(
                manager, gateway, worker["id"], token, context_limit=9000, output_limit=128
            ).compact(input_budget=6000, source_readback_available=True)
        )
    assert archives(store) == before
    with store.connection() as c:
        # Received usage is settled even when it grants no checkpoint authority.
        assert c.execute("SELECT state FROM model_budget_attempts").fetchone()[0] == "settled"
        assert c.execute("SELECT state FROM worker_model_reservations").fetchone()[0] == "settled"
    assert manager.get(worker["id"])["token_spent"] == 40


def test_unknown_worker_compaction_retains_reservation_and_cannot_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store, manager, worker, token, _originals = seeded(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("offline fixture timeout")

    gateway = mock_gateway(store, handler)
    compactor = WorkerModelCompactor(
        manager, gateway, worker["id"], token, context_limit=9000, output_limit=128
    )
    for _ in range(2):
        with pytest.raises(DomainError):
            asyncio.run(compactor.compact(input_budget=6000, source_readback_available=True))
    assert len(requests) == 1 and not archives(store)
    with store.connection() as c:
        assert c.execute("SELECT state FROM model_budget_attempts").fetchone()[0] == "reserved"
        assert c.execute("SELECT state FROM worker_model_reservations").fetchone()[0] == "reserved"
        assert unresolved_calls(c, worker["id"])
    manager._fail(worker["id"], token, "usage_unknown")
    with pytest.raises(DomainError, match="Unresolved"):
        manager.retry(worker["id"])


def test_worker_whole_settled_tool_exchange_is_archived_together_and_readable(graph, monkeypatch):
    from test_worker_runtime import MockApprovedTeam, MockToolEngine, ScriptedMock

    from forge_core.worker_runtime import WorkerManager

    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    approved = MockApprovedTeam.get_approved

    def enough_steps(self, *args, **kwargs):
        team = approved(self, *args, **kwargs)
        team["members"][0]["max_steps"] = 12
        return team

    monkeypatch.setattr(MockApprovedTeam, "get_approved", enough_steps)
    store, worker, originals = oversized_tail(graph, existing_archive=False)
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(graph[2]),
    )
    token = manager._claim(worker["id"])
    seen_batches = []

    def handler(request):
        packet = json.loads(json.loads(request.content)["messages"][1]["content"])
        batch = {item["ordinal"] for item in packet["messages"]}
        assert not batch.intersection({8, 9, 10}) or {8, 9, 10} <= batch
        seen_batches.append(batch)
        return http_response("Preserve the two read results and verify the original task.")

    gateway = mock_gateway(store, handler)
    # The fixture already approves mock-provider; configure that same identity.
    gateway.config = replace(gateway.config, id="mock-provider")
    compactor = WorkerModelCompactor(
        manager, gateway, worker["id"], token, context_limit=64000, output_limit=128
    )
    while asyncio.run(compactor.compact(input_budget=6000, source_readback_available=True))[
        "source_count"
    ]:
        pass
    assert any({8, 9, 10} <= batch for batch in seen_batches)
    for ordinal, expected in originals.items():
        assert (
            json.loads(
                ContextService(store).read_source("worker", worker["id"], ordinal)["message_json"]
            )
            == expected
        )


def test_runtime_native_http_compacts_then_executes_and_reviews_with_exact_usage(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    monkeypatch.setattr(ProtocolModel, "execution_mode", "live")
    store, manager, _scripted, worker = prepared(tmp_path, [])
    # Stored raw messages are fixture conversation data, not fabricated receipts.
    with store.unit_of_work() as work:
        for ordinal in range(2, 7):
            message = {
                "role": "assistant",
                "content": f"historical analysis {ordinal}:" + "x" * (25000 if ordinal < 5 else 5),
            }
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (worker["id"], ordinal, "assistant", _json(message)),
            )
    phases = []

    def responder(body, _ordinal):
        if "context handoff" in body["messages"][0]["content"]:
            phases.append("compaction")
            assert not body.get("tools")
            return {
                "content": "Earlier analyses retained. Read the approved original source before delivery."
            }
        if body.get("tools"):
            if not any(item["role"] == "tool" for item in body["messages"]):
                phases.append("read")
                return {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "original-read",
                            "type": "function",
                            "function": {
                                "name": "file_read",
                                "arguments": json.dumps({"path": "work/input.txt"}),
                            },
                        }
                    ],
                }
            phases.append("worker")
            return {"content": json.dumps(delivery("Complete original analysis."))}
        phases.append("review")
        return {
            "content": json.dumps(
                {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
            )
        }

    with offline_loopback_gateway(store, responder) as (gateway, _requests):
        manager.providers = ProviderPool({"protocol": gateway})
        result = asyncio.run(manager.run(worker["id"]))
        assert result["state"] == "review_pending"
        assert (
            asyncio.run(IndependentReviewService(manager).review(worker["id"]))["verdict"] == "PASS"
        )
    assert phases == ["compaction", "read", "worker", "review"]
    assert manager.get(worker["id"])["token_spent"] == 160
    assert manager.get(worker["id"])["steps"] == 4  # Summary, read, delivery and review.


@pytest.mark.parametrize("invalid", ["empty", "oversize", "tools", "truncated"])
def test_invalid_worker_summary_retains_raw_frontier_and_settles_received_usage(
    tmp_path, monkeypatch, invalid
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store, manager, worker, token, _originals = seeded(tmp_path)

    def handler(_request):
        if invalid == "tools":
            return http_response(
                "",
                finish="tool_calls",
                tool_calls=[
                    {
                        "id": "forged",
                        "type": "function",
                        "function": {"name": "file_write", "arguments": "{}"},
                    }
                ],
            )
        if invalid == "truncated":
            return http_response("partial", finish="length")
        return http_response(" " if invalid == "empty" else "x" * 3000)

    gateway = mock_gateway(store, handler)
    with pytest.raises(DomainError):
        asyncio.run(
            WorkerModelCompactor(
                manager, gateway, worker["id"], token, context_limit=9000, output_limit=128
            ).compact(input_budget=6000, source_readback_available=True)
        )
    assert not archives(store)
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM model_budget_attempts").fetchone()[0] == "settled"
        )
        assert (
            connection.execute("SELECT state FROM worker_model_reservations").fetchone()[0]
            == "settled"
        )
        assert connection.execute("SELECT count(*) FROM action_intents").fetchone()[0] == 0
    assert manager.get(worker["id"])["token_spent"] == 40


def test_worker_summary_preserves_finite_step_admission_before_http(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store, manager, worker, token, _originals = seeded(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET steps=step_limit-1 WHERE id=?", (worker["id"],)
        )
    requests = []
    gateway = mock_gateway(store, lambda request: requests.append(request))
    with pytest.raises(DomainError, match="retain an execution step"):
        asyncio.run(
            WorkerModelCompactor(
                manager, gateway, worker["id"], token, context_limit=9000, output_limit=128
            ).compact(input_budget=6000, source_readback_available=True)
        )
    assert not requests and not archives(store)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": 25},
        {"prompt_tokens": -1, "completion_tokens": 15},
        {"prompt_tokens": 25, "completion_tokens": 9999},
    ],
)
def test_worker_unknown_or_invalid_usage_cannot_checkpoint_or_replay(tmp_path, monkeypatch, usage):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store, manager, worker, token, _originals = seeded(tmp_path)
    seen = []

    def handler(request):
        seen.append(request)
        response = {
            "choices": [{"message": {"content": "Unaccounted summary"}, "finish_reason": "stop"}]
        }
        if usage is not None:
            response["usage"] = usage
        return httpx.Response(200, json=response)

    gateway = mock_gateway(store, handler)
    compactor = WorkerModelCompactor(
        manager, gateway, worker["id"], token, context_limit=9000, output_limit=128
    )
    for _ in range(2):
        with pytest.raises(DomainError):
            asyncio.run(compactor.compact(input_budget=6000, source_readback_available=True))
    assert len(seen) == 1 and not archives(store)
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM worker_model_reservations").fetchone()[0]
            == "reserved"
        )


def test_worker_gateway_summary_cannot_use_response_only_settlement_fallback(tmp_path, monkeypatch):
    from forge_core.runtime import ModelResponse

    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store, manager, worker, token, _originals = seeded(tmp_path)
    gateway = mock_gateway(store, lambda _request: http_response())

    async def no_attempt(_request):
        return ModelResponse(
            text="Unproven summary",
            tool_calls=[],
            usage={"input_tokens": 25, "output_tokens": 15},
            provider_id="protocol",
            model_id="fixture",
            finish_reason="stop",
        )

    gateway.generate = no_attempt
    with pytest.raises(DomainError, match="exact settled Gateway usage"):
        asyncio.run(
            WorkerModelCompactor(
                manager, gateway, worker["id"], token, context_limit=9000, output_limit=128
            ).compact(input_budget=6000, source_readback_available=True)
        )
    assert not archives(store)
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM worker_model_reservations").fetchone()[0]
            == "reserved"
        )
