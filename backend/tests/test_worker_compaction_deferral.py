"""No external traffic: real Gateway ledger, mocked HTTP and governed local reads."""

import asyncio
import json
import time

import pytest
from test_model_compaction_v29 import http_response, mock_gateway
from test_worker_intelligence_phase2 import delivery, prepared

from forge_core.context_service import _json
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderError
from forge_core.runtime import ModelCallContext
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def history_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store, manager, _, worker = prepared(tmp_path, [])
    with store.unit_of_work() as work:
        for ordinal in range(2, 7):
            message = {
                "role": "assistant",
                "content": f"Historical context {ordinal}:" + "x" * (25000 if ordinal < 5 else 5),
            }
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (worker["id"], ordinal, "assistant", _json(message)),
            )
    return store, manager, worker


def raw_history(store):
    with store.connection() as connection:
        return [tuple(row) for row in connection.execute("SELECT * FROM worker_messages")]


def test_busy_compaction_defers_then_reopens_and_performs_actual_read(tmp_path, monkeypatch):
    store, manager, worker = history_fixture(tmp_path, monkeypatch)
    original = raw_history(store)
    phases = []

    def handler(request):
        body = json.loads(request.content)
        if "context handoff" in body["messages"][0]["content"]:
            phases.append("compaction")
            return http_response("Keep original task and read its approved input before delivery.")
        if not any(item["role"] == "tool" for item in body["messages"]):
            phases.append("read")
            return http_response(
                None,
                finish="tool_calls",
                tool_calls=[
                    {
                        "id": "original-read",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "work/input.txt"}),
                        },
                    }
                ],
            )
        phases.append("delivery")
        return http_response(
            json.dumps(delivery("Original source inspected; independent review pending."))
        )

    gateway = mock_gateway(store, handler)
    manager.providers = ProviderPool({"protocol": gateway})
    now = time.time()
    monkeypatch.setattr(manager, "_now", lambda: now)
    ledger = gateway._budget_ledger
    # An admitted peer occupies the real shared slot; no HTTP request is sent.
    held = ledger.admit(
        ModelCallContext("peer", "router", store.workspace.id),
        "protocol",
        "fixture",
        input_tokens=1,
        output_tokens=1,
        remote_timeout=30,
    )
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "queued"
    assert result["steps"] == result["token_spent"] == 0
    assert result["next_attempt_at"] == now + 1
    assert not phases and raw_history(store) == original
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM context_archives").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 0
        assert [
            r[0] for r in connection.execute("SELECT state FROM worker_model_reservations")
        ] == ["refunded"]
        assert connection.execute("SELECT count(*) FROM model_budget_attempts").fetchone()[0] == 1
    ledger.settle(held, {"input_tokens": 0, "output_tokens": 0})
    reopened = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        reopened,
        ProviderPool({"protocol": gateway}),
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    monkeypatch.setattr(resumed, "_now", lambda: now)
    assert asyncio.run(resumed.run(worker["id"]))["state"] == "queued"
    assert not phases
    monkeypatch.setattr(resumed, "_now", lambda: now + 2)
    result = asyncio.run(resumed.run(worker["id"]))
    assert result["state"] == "review_pending"
    assert phases == ["compaction", "read", "delivery"]
    assert result["steps"] == 3 and result["token_spent"] == 120
    assert result["deadline"] == worker["deadline"]
    assert result["allowed_tools_json"] == worker["allowed_tools_json"]
    with reopened.connection() as connection:
        calls = connection.execute("SELECT * FROM worker_tool_calls").fetchall()
        assert (
            len(calls) == 1
            and calls[0]["tool_name"] == "file_read"
            and calls[0]["state"] == "succeeded"
        )
        assert "Original source" in calls[0]["result_json"]
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
        assert [
            r[0]
            for r in connection.execute(
                "SELECT state FROM worker_model_reservations ORDER BY rowid"
            )
        ] == ["refunded", "settled", "settled", "settled"]


@pytest.mark.parametrize(
    "case", ["submitted", "unknown", "other_error", "refund_denied", "paused", "cancelled"]
)
def test_compaction_deferral_requires_exact_unsent_current_owner_refund(
    tmp_path, monkeypatch, case
):
    store, manager, worker = history_fixture(tmp_path, monkeypatch)
    original = raw_history(store)
    gateway = mock_gateway(store, lambda _: pytest.fail("No physical HTTP permitted"))
    manager.providers = ProviderPool({"protocol": gateway})

    async def rejection(request):
        request._budget_post_started = {"submitted": True, "unknown": None}.get(case, False)
        if case in {"paused", "cancelled"}:
            manager.control(worker["id"], "pause" if case == "paused" else "cancel")
        if case == "refund_denied":
            monkeypatch.setattr(
                "forge_core.worker_model_compaction.refund_unsent_worker_model_call",
                lambda *args: False,
            )
        raise ProviderError("auth" if case == "other_error" else "concurrency_unavailable")

    gateway.generate = rejection
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == (case if case in {"paused", "cancelled"} else "blocked")
    assert raw_history(store) == original
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM context_archives").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM model_budget_attempts").fetchone()[0] == 0
        assert connection.execute("SELECT state FROM worker_model_reservations").fetchone()[0] == (
            "refunded" if case == "other_error" else "reserved"
        )


def test_compaction_cancellation_propagates_without_requeue_or_replay(tmp_path, monkeypatch):
    store, manager, worker = history_fixture(tmp_path, monkeypatch)
    gateway = mock_gateway(store, lambda _: pytest.fail("No physical HTTP permitted"))
    manager.providers = ProviderPool({"protocol": gateway})

    async def cancelled(request):
        request._budget_post_started = None
        raise asyncio.CancelledError()

    gateway.generate = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.run(worker["id"]))
    assert manager.get(worker["id"])["state"] != "queued"
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM worker_model_reservations").fetchone()[0]
            == "reserved"
        )
        assert connection.execute("SELECT count(*) FROM context_archives").fetchone()[0] == 0
