"""Synthetic settled ledgers; context source integrity, never live task evidence."""

import hashlib
import json

import pytest
from test_worker_runtime import MockApprovedTeam, MockToolEngine, ScriptedMock

from forge_core.context_service import ContextService, _json
from forge_core.domain import DomainError
from forge_core.provider_pool import ProviderPool
from forge_core.store import Store
from forge_core.worker_runtime import WorkerManager

pytest_plugins = ["test_worker_runtime"]


def oversized_tail(graph, *, existing_archive=True):
    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    run = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.unit_of_work() as work:
        for ordinal in range(2, 6):
            message = {"role": "assistant", "content": "old source " * 700}
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (run["id"], ordinal, "assistant", _json(message)),
            )
        for ordinal in (6, 7):
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (run["id"], ordinal, "assistant", _json({"role": "assistant", "content": "next"})),
            )
    if existing_archive:
        ContextService(store).build_worker_context(run["id"], input_budget=6000)
    request = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"model-{index}",
                "type": "function",
                "function": {"name": "read", "arguments": _json({"path": f"source-{index}"})},
            }
            for index in (1, 2)
        ],
    }
    originals = {8: request}
    with store.unit_of_work() as work:
        c = work.connection
        for index in (1, 2):
            output = {"content": (f"original {index} " * 1000)}
            result = _json(output)
            c.execute(
                "INSERT INTO action_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"intent-{index}",
                    f"call-{index}",
                    quest.id,
                    run["worker_id"],
                    run["id"],
                    plan["id"],
                    plan["plan_revision"],
                    "read",
                    _json({"path": f"source-{index}"}),
                    "fixture-parameter-digest",
                    f"project/source-{index}",
                    "R0",
                    None,
                    "mock",
                    "succeeded",
                    "now",
                    "now",
                ),
            )
            c.execute(
                "INSERT INTO action_receipts VALUES (?,?,?,?,?,?)",
                (
                    f"receipt-{index}",
                    f"intent-{index}",
                    "mock",
                    result,
                    hashlib.sha256(result.encode()).hexdigest(),
                    "now",
                ),
            )
            c.execute(
                "INSERT INTO worker_tool_calls VALUES (?,?,?,?,?,?,?,?)",
                (
                    run["id"],
                    f"call-{index}",
                    f"model-{index}",
                    "read",
                    _json({"path": f"source-{index}"}),
                    "succeeded",
                    f"receipt-{index}",
                    result,
                ),
            )
            originals[8 + index] = {
                "role": "tool",
                "tool_call_id": f"model-{index}",
                "content": result,
            }
        for ordinal, message in originals.items():
            c.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (run["id"], ordinal, message["role"], _json(message)),
            )
    return store, run, originals


def snapshot(store, run_id):
    with store.connection() as c:
        return (
            tuple(c.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone()),
            c.execute("SELECT COUNT(*) FROM context_archives").fetchone()[0],
            c.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
            c.execute("SELECT COUNT(*) FROM events WHERE event_type='review.result'").fetchone()[0],
        )


def test_settled_tail_is_wholly_archived_with_original_readback_and_no_authority(graph):
    store, run, originals = oversized_tail(graph)
    before = snapshot(store, run["id"])
    service = ContextService(store)
    result = service.build_worker_context(run["id"], input_budget=6000)
    assert len(_json(result).encode()) <= 6000
    assert not any(item.get("tool_calls") or item["role"] == "tool" for item in result)
    archived = next(item for item in result if item.get("content", "").startswith("Archived task"))
    capsule = json.loads(archived["content"].split(": ", 1)[1])
    assert capsule["history_recall"]["recent_exchange"] == {
        "source_refs": ["8", "9", "10"],
        "raw_results_omitted": True,
        "requires_source_readback": True,
        "reason": "input_budget",
        "trust": "untrusted archived data; not completion evidence",
    }
    assert {item["receipt_id"] for item in capsule["completed_steps"]} == {
        "receipt-1",
        "receipt-2",
    }
    for ordinal, message in originals.items():
        assert (
            json.loads(service.read_source("worker", run["id"], ordinal)["message_json"]) == message
        )
    after = snapshot(store, run["id"])
    assert after[0] == before[0] and after[2:] == before[2:]
    reopened = ContextService(Store(store.path, workspace=store.workspace))
    assert reopened.build_worker_context(run["id"], input_budget=6000) == result
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_messages SET message_json='{}' WHERE run_id=? AND ordinal=9",
            (run["id"],),
        )
    with pytest.raises(DomainError, match="integrity"):
        reopened.build_worker_context(run["id"], input_budget=6000)


@pytest.mark.parametrize("invalid", ["pending", "uncertain", "missing_receipt", "missing_reply"])
def test_unsettled_or_incomplete_tail_stays_live_and_compaction_rolls_back(graph, invalid):
    store, run, _originals = oversized_tail(graph)
    with store.unit_of_work() as work:
        if invalid == "pending":
            work.connection.execute(
                "UPDATE worker_tool_calls SET state='pending' WHERE run_id=? AND call_id='call-1'",
                (run["id"],),
            )
        elif invalid == "uncertain":
            work.connection.execute(
                "UPDATE action_intents SET state='uncertain' WHERE id='intent-1'"
            )
        elif invalid == "missing_receipt":
            work.connection.execute(
                "UPDATE worker_tool_calls SET receipt_id=NULL WHERE run_id=? AND call_id='call-1'",
                (run["id"],),
            )
        else:
            work.connection.execute(
                "DELETE FROM worker_messages WHERE run_id=? AND ordinal=10", (run["id"],)
            )
    before = snapshot(store, run["id"])
    with pytest.raises(DomainError, match="context budget"):
        ContextService(store).build_worker_context(run["id"], input_budget=6000)
    assert snapshot(store, run["id"]) == before


def test_first_archive_cannot_hide_tail_before_source_read_tool_is_available(graph):
    store, run, _originals = oversized_tail(graph, existing_archive=False)
    before = snapshot(store, run["id"])
    with pytest.raises(DomainError, match="context budget"):
        ContextService(store).build_worker_context(run["id"], input_budget=6000)
    assert snapshot(store, run["id"]) == before


def test_required_initial_contract_still_blocks_when_it_cannot_fit(graph):
    store, run, _originals = oversized_tail(graph)
    before = snapshot(store, run["id"])
    with pytest.raises(DomainError, match="context budget"):
        ContextService(store).build_worker_context(run["id"], input_budget=200)
    assert snapshot(store, run["id"]) == before
