"""Serial reservations with real Gateway admission over offline HTTP transport."""

import asyncio
import json

import httpx
from test_reviewer_capacity_phase2 import PASS, events, pending

from forge_core.independent_review import IndependentReviewService, _hash, _json, _packet
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway


def reservation_floors(store, manager, row, diagnostic):
    with store.connection() as connection:
        initial = [
            json.loads(item[0])
            for item in connection.execute(
                "SELECT message_json FROM worker_messages WHERE run_id=? AND ordinal IN (0,1)",
                (row["id"],),
            )
        ]
    worker = (
        len(
            _json(
                {
                    "messages": initial,
                    "tools": manager.tools.definitions(json.loads(row["allowed_tools_json"])),
                    "model": row["model_id"],
                }
            ).encode()
        )
        + min(row["output_limit"], row["model_max_output_tokens"])
        + 128
    )
    review = (
        diagnostic["fixed_input_floor"]
        + diagnostic["revision_receipt_allowance"]
        + diagnostic["minimum_verdict_tokens"]
    )
    return worker, review


def test_serial_capacity_repair_keeps_every_native_admission_within_original_allowance(
    tmp_path, monkeypatch
):
    # The complete review packet is oversized, while its whole tool exchanges
    # can still fit an accounted model handoff inside the original 59k approval.
    store, manager, _model, worker, packet, content = pending(
        tmp_path, monkeypatch, source_bytes=30000, explanation_repeats=1500
    )
    # This fixture records prior settled consumption inside the approved 59k
    # allowance; the limit, contract and evidence remain unchanged.
    remaining = 45000
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET token_spent=token_limit-? WHERE id=?",
            (remaining, worker["id"]),
        )
    before = manager.get(worker["id"])
    assert before["token_limit"] == 59000
    artifact = packet["artifacts"][0]
    compact = {**content, "explanation": "Repeated explanation."}
    posted = []
    worker_posts = []
    compaction_posts = []
    reservations = []

    def handler(http_request):
        body = json.loads(http_request.content)
        posted.append(body)
        with store.connection() as connection:
            current = connection.execute(
                "SELECT * FROM worker_runs WHERE id=?", (worker["id"],)
            ).fetchone()
            attempt = connection.execute(
                "SELECT * FROM model_budget_attempts WHERE worker_run_id=? AND state='reserved'",
                (worker["id"],),
            ).fetchone()
            opening = connection.execute(
                "SELECT COALESCE(SUM(tokens),0) FROM model_budget_opening WHERE worker_run_id=?",
                (worker["id"],),
            ).fetchone()[0]
            prior_usage = connection.execute(
                "SELECT COALESCE(SUM(input_charged+output_charged),0) FROM model_budget_attempts "
                "WHERE worker_run_id=? AND state='settled'",
                (worker["id"],),
            ).fetchone()[0]
        input_reserved = len(json.dumps(body, ensure_ascii=False).encode()) + 64
        assert attempt["input_reserved"] == input_reserved
        assert attempt["output_reserved"] == body["max_tokens"]
        total = input_reserved + body["max_tokens"]
        assert opening + prior_usage + total <= before["token_limit"]
        assert total <= body.get("context_limit", current["model_context_window"])
        assert current["token_limit"] == before["token_limit"]
        assert current["token_spent"] <= before["token_limit"]
        reservations.append(total)
        if attempt["purpose"] == "context_compaction":
            assert not body.get("tools") and not body.get("response_format")
            packet = json.loads(body["messages"][1]["content"])
            assert packet["version"] == "worker-model-handoff-input-v1"
            compaction_posts.append(body)
            message = {
                "content": "The complete prior artifact needs concise revision; preserve its finding and citation."
            }
            finish = "stop"
        elif attempt["purpose"] == "worker" and not worker_posts:
            worker_posts.append(body)
            message = {
                "content": "",
                "tool_calls": [
                    {
                        "id": "compact-owned-artifact",
                        "type": "function",
                        "function": {
                            "name": "file_revise",
                            "arguments": _json(
                                {
                                    "path": "work/findings.json",
                                    "prior_receipt_id": artifact["receipt_id"],
                                    "expected_sha256": artifact["sha256"],
                                    "content": _json(compact),
                                }
                            ),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        elif attempt["purpose"] == "worker":
            worker_posts.append(body)
            assert len(worker_posts) == 2
            message = {"content": _json({"summary": "Complete compact artifact is ready."})}
            finish = "stop"
        else:
            assert attempt["purpose"] == "independent_reviewer"
            assert "CURRENT background task node" in body["messages"][0]["content"]
            assert not body.get("tools")
            message, finish = {"content": _json(PASS)}, "stop"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message, "finish_reason": finish}],
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
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=200000, concurrency_limit=1),
    )
    manager.providers = ProviderPool({"protocol": gateway})
    service = IndependentReviewService(manager)
    diagnostic = asyncio.run(service.review(worker["id"]))
    worker_floor, review_floor = reservation_floors(
        store, manager, before, diagnostic["capacity_diagnostics"]
    )
    assert max(worker_floor, review_floor) < remaining < 2 * worker_floor + review_floor
    assert diagnostic["verdict"] == "NEEDS_REVISION" and diagnostic["revision_applied"]
    assert not posted and diagnostic["packet_sha256"] == _hash(packet)
    assert manager.get(worker["id"])["token_spent"] == before["token_spent"]
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    revised = manager.get(worker["id"])
    assert len(worker_posts) == 2 and len(compaction_posts) == 1
    assert len(posted) == 3 and len(events(store, "review.result")) == 1
    assert revised["token_spent"] == before["token_spent"] + 120
    assert revised["steps"] == before["steps"] + 3
    assert revised["tool_spent"] == before["tool_spent"] + 1
    assert revised["repairs"] == before["repairs"] + 1
    verdict = asyncio.run(service.review(worker["id"]))
    after = manager.get(worker["id"])
    assert verdict["verdict"] == "PASS" and verdict["review_context"] == "independent_same_model"
    assert verdict["execution_mode"] == "mock" and len(posted) == 4
    assert after["token_spent"] == before["token_spent"] + 160
    for key in ("token_limit", "tool_limit", "step_limit", "allowed_tools_json"):
        assert after[key] == before[key]
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        current = _packet(store, connection, row)
        assert current["artifacts"][0]["content"] == compact
        assert current["receipts"][0] == packet["receipts"][0]
        assert verdict["packet_sha256"] == _hash(current) != _hash(packet)
        attempts = connection.execute("SELECT * FROM model_budget_attempts").fetchall()
        assert len(attempts) == 4 and all(item["state"] == "settled" for item in attempts)
        assert sum(item["input_charged"] + item["output_charged"] for item in attempts) == 160
    assert all(reserve < remaining for reserve in reservations)


def test_remaining_budget_below_review_reservation_floor_still_blocks(tmp_path, monkeypatch):
    store, manager, model, worker, packet, _ = pending(tmp_path, monkeypatch)
    remaining = 26000
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET token_spent=token_limit-? WHERE id=?",
            (remaining, worker["id"]),
        )
    before = manager.get(worker["id"])
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    _, review_floor = reservation_floors(store, manager, before, result["capacity_diagnostics"])
    assert remaining < review_floor
    assert result["verdict"] == "BLOCKED" and not result["revision_applied"]
    assert result["packet_sha256"] == _hash(packet)
    assert len(model.requests) == 3 and not events(store, "review.started")
    assert manager.get(worker["id"])["token_spent"] == before["token_spent"]
    assert not events(store, "worker.revision_requested")
