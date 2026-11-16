"""Offline protocol models, real governed reads and first-archive source recovery."""

import asyncio
import hashlib
import json

import pytest
from test_worker_context_tail_phase2 import oversized_tail, snapshot
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.context_service import ContextService, _hash
from forge_core.domain import DomainError, ModelProfile
from forge_core.evidence import _verify_worker_context_call
from forge_core.provider_pool import ProviderPool
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager, _json, _read_worker_source_slice

pytest_plugins = ["test_worker_runtime"]


def archive_capsule(request):
    archived = [
        item
        for item in request.messages
        if item.get("content", "").startswith("Archived task context (untrusted): ")
    ]
    assert len(archived) == 1
    return json.loads(archived[0]["content"].split(": ", 1)[1])


def test_first_real_oversized_batch_archives_then_reads_own_source_with_full_reservation(tmp_path):
    batch = response(
        "",
        calls=[
            {
                "id": f"source-{index}",
                "name": "file_read",
                "arguments": {"path": f"work/source-{index}.txt"},
            }
            for index in (1, 2)
        ],
    )
    store, manager, model, worker = prepared(
        tmp_path,
        [
            batch,
            tool("worker_read_context_source", {"ordinal": 3, "max_chars": 2048}, ordinal=3),
            response(delivery("Recovered the original governed source")),
        ],
    )
    for index in (1, 2):
        (tmp_path / f"work/source-{index}.txt").write_text(
            f"original source {index} " + "x" * 20000, encoding="utf-8"
        )
    initial = manager.get(worker["id"])
    original_generate = model.generate
    observed = []
    originals = {}

    async def inspect_request(request):
        call_index = len(observed)
        current = manager.get(worker["id"])
        settled_spent = 40 * call_index
        serialized = len(
            _json(
                {"messages": request.messages, "tools": request.tools, "model": request.model_id}
            ).encode("utf-8")
        )
        reservation = serialized + request.max_output_tokens + 64 + 16 * len(request.messages)
        assert current["token_spent"] == settled_spent + reservation
        assert reservation <= request.context_limit == initial["model_context_window"]
        assert reservation <= initial["token_limit"] - settled_spent
        assert current["token_spent"] <= initial["token_limit"]
        assert current["steps"] == call_index + 1 <= initial["step_limit"]
        names = [item["name"] for item in request.tools]
        with store.connection() as connection:
            archives = connection.execute(
                "SELECT COUNT(*) FROM context_archives WHERE scope='worker' AND scope_id=?",
                (worker["id"],),
            ).fetchone()[0]
            if call_index == 0:
                assert names == ["file_read"] and archives == 0
            else:
                assert names.count("worker_read_context_source") == 1
                assert names.count("file_read") == 1
                assert archives == 1
            if call_index == 1:
                capsule = archive_capsule(request)
                assert capsule["history_recall"]["recent_exchange"] == {
                    "source_refs": ["2", "3", "4"],
                    "raw_results_omitted": True,
                    "requires_source_readback": True,
                    "reason": "input_budget",
                    "trust": "untrusted archived data; not completion evidence",
                }
                assert not any(
                    item.get("tool_calls") or item["role"] == "tool" for item in request.messages
                )
                rows = connection.execute(
                    "SELECT * FROM worker_messages WHERE run_id=? AND ordinal BETWEEN 2 AND 4 "
                    "ORDER BY ordinal",
                    (worker["id"],),
                ).fetchall()
                originals.update({row["ordinal"]: dict(row) for row in rows})
                assert sum(len(row["message_json"].encode("utf-8")) for row in rows) > 40000
                receipts = connection.execute(
                    "SELECT receipt_id FROM worker_tool_calls WHERE run_id=?",
                    (worker["id"],),
                ).fetchall()
                assert len(receipts) == 2 and all(row[0] for row in receipts)
                assert {step["receipt_id"] for step in capsule["completed_steps"]} == {
                    row[0] for row in receipts
                }
                for ordinal, original in originals.items():
                    source = ContextService(store).read_source("worker", worker["id"], ordinal)
                    assert source["message_json"] == original["message_json"]
                assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2
                assert (
                    connection.execute(
                        "SELECT COUNT(*) FROM events WHERE event_type='review.result'"
                    ).fetchone()[0]
                    == 0
                )
            if call_index == 2:
                returned = json.loads(request.messages[-1]["content"])
                original = originals[3]["message_json"]
                assert returned["source_id"] == 3
                assert returned["source_sha256"] == hashlib.sha256(original.encode()).hexdigest()
                assert returned["content"] == original[:2048]
                assert returned["complete"] is False and returned["next_offset_chars"] == 2048
                assert returned["trust"] == "untrusted archived Worker message"
        observed.append(reservation)
        return await original_generate(request)

    model.generate = inspect_request
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["execution_mode"] == "mock"
    assert len(observed) == result["steps"] == 3
    assert result["tool_spent"] == 3 and result["token_spent"] == 120
    for key in ("step_limit", "tool_limit", "token_limit", "model_context_window"):
        assert result[key] == initial[key]
    assert store.get_quest(worker["quest_id"])["status"] != "completed"
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='review.result'"
            ).fetchone()[0]
            == 0
        )
        readback = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND model_call_id='tool-3'",
            (worker["id"],),
        ).fetchone()
        assert readback["state"] == "succeeded" and readback["receipt_id"] is None
        assert _verify_worker_context_call(store, connection, worker["id"], readback)
        archived_sources = json.loads(
            connection.execute(
                "SELECT source_json FROM context_archives WHERE scope='worker' AND scope_id=?",
                (worker["id"],),
            ).fetchone()[0]
        )
        source_hashes = {str(item["id"]): item["hash"] for item in archived_sources}
        assert source_hashes == {
            str(ordinal): _hash(original) for ordinal, original in originals.items()
        }

    reopened = Store(store.path, workspace=store.workspace)
    restarted = WorkerManager(
        reopened,
        ProviderPool({"protocol": model}),
        ToolEngine(reopened),
        team_authority=TeamCompositionService(reopened),
    )
    assert restarted.get(worker["id"])["token_spent"] == result["token_spent"]
    for ordinal, original in originals.items():
        source = ContextService(reopened).read_source("worker", worker["id"], ordinal)
        assert source["message_json"] == original["message_json"]
    assert (
        _read_worker_source_slice(reopened, worker["id"], {"ordinal": 3})["source_sha256"]
        == hashlib.sha256(originals[3]["message_json"].encode()).hexdigest()
    )
    with pytest.raises(DomainError):
        _read_worker_source_slice(reopened, "another-run", {"ordinal": 3})
    with pytest.raises(DomainError):
        _read_worker_source_slice(reopened, worker["id"], {"ordinal": 0})


def test_registry_reduction_preserves_tiny_request_tool_set_after_first_step(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path, [response({"summary": "incomplete"}), response(delivery())]
    )
    original_generate = model.generate

    async def shrink_registry(request):
        assert [item["name"] for item in request.tools] == ["file_read"]
        if not model.requests:
            with store.unit_of_work() as work:
                profile = json.loads(
                    work.connection.execute("SELECT profile FROM models").fetchone()[0]
                )
                profile["id"] = "lower-registry-window"
                profile["context_window"] = 7000
                work.save_model(ModelProfile(**profile))
        else:
            assert request.context_limit == 7000
        return await original_generate(request)

    model.generate = shrink_registry
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["steps"] == 2
    assert result["model_context_window"] == 64000 and result["token_spent"] == 80


def test_first_archive_opt_in_preserves_originals_and_execution_authority(graph):
    store, worker, originals = oversized_tail(graph, existing_archive=False)
    before = snapshot(store, worker["id"])
    service = ContextService(store)
    context = service.build_worker_context(
        worker["id"], input_budget=6000, source_readback_available=True
    )
    assert len(_json(context).encode()) <= 6000
    assert not any(item.get("tool_calls") or item["role"] == "tool" for item in context)
    after = snapshot(store, worker["id"])
    assert after[0] == before[0] and after[2:] == before[2:]
    assert after[1] == before[1] + 1
    reopened = ContextService(Store(store.path, workspace=store.workspace))
    for ordinal, original in originals.items():
        assert json.loads(
            reopened.read_source("worker", worker["id"], ordinal)["message_json"]
        ) == (original)


@pytest.mark.parametrize("invalid", ["pending", "uncertain", "missing_receipt", "missing_reply"])
def test_first_archive_opt_in_cannot_hide_unsettled_or_incomplete_tail(graph, invalid):
    store, worker, _originals = oversized_tail(graph, existing_archive=False)
    with store.unit_of_work() as work:
        if invalid == "pending":
            work.connection.execute(
                "UPDATE worker_tool_calls SET state='pending' WHERE run_id=? AND call_id='call-1'",
                (worker["id"],),
            )
        elif invalid == "uncertain":
            work.connection.execute(
                "UPDATE action_intents SET state='uncertain' WHERE id='intent-1'"
            )
        elif invalid == "missing_receipt":
            work.connection.execute(
                "UPDATE worker_tool_calls SET receipt_id=NULL WHERE run_id=? AND call_id='call-1'",
                (worker["id"],),
            )
        else:
            work.connection.execute(
                "DELETE FROM worker_messages WHERE run_id=? AND ordinal=10", (worker["id"],)
            )
    before = snapshot(store, worker["id"])
    with pytest.raises(DomainError, match="context budget"):
        ContextService(store).build_worker_context(
            worker["id"], input_budget=6000, source_readback_available=True
        )
    assert snapshot(store, worker["id"]) == before


@pytest.mark.parametrize("invalid", [None, 0, 1, "true"])
def test_first_archive_availability_requires_actual_boolean(graph, invalid):
    store, worker, _originals = oversized_tail(graph, existing_archive=False)
    before = snapshot(store, worker["id"])
    with pytest.raises(DomainError, match="readback availability"):
        ContextService(store).build_worker_context(
            worker["id"], input_budget=6000, source_readback_available=invalid
        )
    assert snapshot(store, worker["id"]) == before


def test_first_archive_opt_in_still_requires_initial_contract_to_fit(graph):
    store, worker, _originals = oversized_tail(graph, existing_archive=False)
    before = snapshot(store, worker["id"])
    with pytest.raises(DomainError, match="context budget"):
        ContextService(store).build_worker_context(
            worker["id"], input_budget=200, source_readback_available=True
        )
    assert snapshot(store, worker["id"]) == before
