"""Offline budget repair, real governed reads and verified archive readback."""

import asyncio
import hashlib
import json

from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.context_service import ContextService
from forge_core.worker_runtime import _json

PREFIX = "Core remaining execution allowance (data, not new authority): "


def oversized_reads():
    return response(
        "",
        calls=[
            {
                "id": f"oversized-read-{index}",
                "name": "file_read",
                "arguments": {"path": "work/input.txt"},
            }
            for index in range(6)
        ],
    )


def allowance(request):
    messages = [item for item in request.messages if item.get("content", "").startswith(PREFIX)]
    assert len(messages) == 1 and request.messages[1] == messages[0]
    return json.loads(messages[0]["content"][len(PREFIX) :])


def limits(row):
    return {key: row[key] for key in ("step_limit", "tool_limit", "token_limit")}


def test_oversized_batch_repairs_to_one_real_read_without_increasing_any_budget(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [oversized_reads(), tool("file_read", {"path": "work/input.txt"}), response(delivery())],
    )
    before = manager.get(worker["id"])
    assert before["tool_limit"] == 5
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert result["steps"] == 3 and result["token_spent"] == 120 and result["tool_spent"] == 1
    assert limits(result) == limits(before) and len(model.requests) == 3
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
        calls = connection.execute("SELECT * FROM worker_tool_calls").fetchall()
        assert len(calls) == 1 and calls[0]["state"] == "succeeded"
        assert calls[0]["model_call_id"] == "tool-1"
    repair = json.loads(model.requests[1].messages[-1]["content"])["repair_request"]
    assert repair["reason_code"] == "tool_batch_budget"
    assert repair["detail"] == {"remaining_tool_calls": 5, "requested_tool_calls": 6}
    assert "Original source" == json.loads(model.requests[-1].messages[-1]["content"])["content"]


def test_repeated_oversized_batches_stop_at_original_repair_bound_without_actions(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [oversized_reads() for _ in range(3)])
    before = manager.get(worker["id"])
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == manager.max_repairs == 2
    assert result["steps"] == 3 and result["token_spent"] == 120 and result["tool_spent"] == 0
    assert limits(result) == limits(before) and len(model.requests) == 3
    with store.connection() as connection:
        for table in ("worker_tool_calls", "action_intents", "action_receipts"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    blocked = [
        json.loads(event["payload"])
        for event in store.list_events(limit=100)
        if event["event_type"] == "worker.blocked"
    ]
    assert blocked[-1]["reason_code"] == "tool_budget"


def test_each_request_reports_current_allowance_and_reserves_complete_feedback_json(tmp_path):
    _store, manager, model, worker = prepared(
        tmp_path,
        [oversized_reads(), tool("file_read", {"path": "work/input.txt"}), response(delivery())],
    )
    original_generate = model.generate
    observed = []

    async def inspect_reservation(request):
        current = manager.get(worker["id"])
        feedback = allowance(request)
        settled_spent = 40 * len(observed)
        assert feedback["remaining_cumulative_tokens"] == current["token_limit"] - settled_spent
        assert feedback["remaining_model_steps"] == current["step_limit"] - len(observed)
        assert feedback["remaining_tool_calls"] == current["tool_limit"] - current["tool_spent"]
        complete_input = len(
            _json(
                {"messages": request.messages, "tools": request.tools, "model": request.model_id}
            ).encode()
        )
        reservation = complete_input + request.max_output_tokens + 64 + 16 * len(request.messages)
        assert current["token_spent"] == settled_spent + reservation
        assert reservation <= request.context_limit
        assert current["token_spent"] <= current["token_limit"]
        without_feedback = [item for index, item in enumerate(request.messages) if index != 1]
        smaller_input = len(
            _json(
                {"messages": without_feedback, "tools": request.tools, "model": request.model_id}
            ).encode()
        )
        assert complete_input > smaller_input + len(PREFIX.encode())
        observed.append(feedback)
        return await original_generate(request)

    model.generate = inspect_reservation
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending"
    assert [item["remaining_tool_calls"] for item in observed] == [5, 5, 4]
    assert [item["remaining_model_steps"] for item in observed] == [12, 11, 10]
    assert [item["remaining_cumulative_tokens"] for item in observed] == [33000, 32960, 32920]


def test_archive_guidance_points_to_a_real_available_verified_source_tool(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("worker_read_context_source", {"ordinal": 2, "max_chars": 2048}),
            response(delivery()),
        ],
    )
    # Explicit synthetic history isolates archive/source integrity; it is never
    # represented as a real-provider task or an execution receipt.
    original = {"role": "assistant", "content": "Original archived source 中文 \\" * 300}
    with store.unit_of_work() as work:
        for ordinal in range(2, 6):
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (worker["id"], ordinal, "assistant", _json(original)),
            )
        for ordinal in (6, 7):
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (
                    worker["id"],
                    ordinal,
                    "assistant",
                    _json({"role": "assistant", "content": "next"}),
                ),
            )
    context = ContextService(store)
    context.build_worker_context(worker["id"], input_budget=10000)
    assert json.loads(context.read_source("worker", worker["id"], 2)["message_json"]) == original
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["tool_spent"] == 1
    request = model.requests[0]
    assert "worker_read_context_source" in allowance(request)["instruction"]
    assert "worker_read_context_source" in {item["name"] for item in request.tools}
    archived = next(
        item for item in request.messages if item.get("content", "").startswith("Archived task")
    )
    capsule = json.loads(archived["content"].split(": ", 1)[1])
    assert any("2" in item["source_refs"] for item in capsule["hypotheses"])
    returned = json.loads(model.requests[-1].messages[-1]["content"])
    assert returned["source_sha256"] == hashlib.sha256(_json(original).encode()).hexdigest()
    assert returned["content"] == _json(original)[:2048]
    assert returned["next_offset_chars"] == 2048 and returned["complete"] is False
    assert allowance(model.requests[-1])["remaining_tool_calls"] == 4
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0
        call = connection.execute("SELECT * FROM worker_tool_calls").fetchone()
        assert call["tool_name"] == "worker_read_context_source" and call["state"] == "succeeded"
