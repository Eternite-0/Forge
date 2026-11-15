"""Offline protocol feedback, real receipts and original Worker admission."""

import asyncio

from test_worker_budget_feedback_phase2 import allowance
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.worker_runtime import _json


def test_review_preview_uses_settled_evidence_and_is_in_original_reservation(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery())],
    )
    generate = model.generate
    observations = []

    async def inspect(request):
        current = manager.get(worker["id"])
        feedback = allowance(request)
        preview = feedback["review_capacity"]
        assert preview["complete"] is False
        assert preview["raw_packet_bytes"] is None
        assert preview["native_input_reservation"] is None
        assert preview["remaining_cumulative_tokens"] == feedback["remaining_cumulative_tokens"]
        assert preview["settled_receipt_count"] == len(observations)
        with store.connection() as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type IN ('worker.result','review.result')"
                ).fetchone()[0]
                == 0
            )
        complete_input = len(
            _json(
                {"messages": request.messages, "tools": request.tools, "model": request.model_id}
            ).encode("utf-8")
        )
        reserve = complete_input + request.max_output_tokens + 64 + 16 * len(request.messages)
        assert current["token_spent"] == 40 * len(observations) + reserve
        assert current["token_spent"] <= current["token_limit"]
        observations.append(preview)
        return await generate(request)

    model.generate = inspect
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending"
    assert result["steps"] == 2 and result["token_spent"] == 80
    assert result["tool_spent"] == 1 and result["repairs"] == 0
    assert len(observations) == 2
    assert observations[1]["settled_receipt_bytes"] > 0
