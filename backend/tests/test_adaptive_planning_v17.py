"""Offline feedback orchestration and source fences; no live/model quality claims."""

import asyncio
import copy
import importlib
import json
import secrets
import time
from types import SimpleNamespace

import pytest
from test_remaining_dag_v16 import prepared
from test_router_intelligence_phase2 import ScriptedMock, router

from forge_core.adaptive_planning import remaining_replan_context
from forge_core.dispatcher import Dispatcher
from forge_core.domain import DomainError, ForgeEvent
from forge_core.quest_graph import QuestGraphService


def feedback(tmp_path):
    store, quest, plan, raw, *_rest, runs = prepared(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status='blocked' WHERE id=?", (quest.id,))
    event = ForgeEvent(
        event_type="worker.blocked",
        quest_id=quest.id,
        worker_id=runs[1]["worker_id"],
        payload={
            "run_id": runs[1]["id"],
            "plan_id": plan["id"],
            "task_node_key": runs[1]["node_key"],
            "reason_code": "needs_specialist",
        },
    )
    store.append_event(event)
    dispatcher = Dispatcher(store)
    for _ in range(10):
        dispatcher.tick()
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET processed_at='fixture-older-events' WHERE event_id!=?",
            (event.id,),
        )
    return store, quest, plan, raw, runs, event, dispatcher


def test_router_feedback_stages_a_source_bound_replan_request_without_approval(tmp_path):
    store, quest, parent, raw, runs, source, dispatcher = feedback(tmp_path)
    with store.connection() as connection:
        original = [
            dict(row) for row in connection.execute("SELECT * FROM worker_runs ORDER BY id")
        ]
        ledger = [dict(row) for row in connection.execute("SELECT * FROM worker_budget_ledger")]

    def inspect(request):
        context = json.loads(request.messages[-1]["content"])
        offered = context["notice"]["remaining_planning_available"]
        assert offered["completed_node_keys"] == ["a"]
        assert offered["source_event_id"] == source.id
        return {"action": "replan_remaining", "reply": "Replan unfinished work with a specialist."}

    model = ScriptedMock(inspect)
    assert asyncio.run(router(store, model).handle_pending_events(dispatcher))
    for _ in range(4):
        dispatcher.tick()
    request = next(
        row for row in store.list_events() if row["event_type"] == "quest.replan_requested"
    )
    payload = json.loads(request["payload"])
    assert payload["parent_plan_id"] == parent["id"]
    assert payload["source_event_id"] == source.id
    assert store.get_quest(quest.id)["plan_revision"] == parent["revision"]
    with store.connection() as connection:
        assert [
            dict(row) for row in connection.execute("SELECT * FROM worker_runs ORDER BY id")
        ] == original
        assert [
            dict(row) for row in connection.execute("SELECT * FROM worker_budget_ledger")
        ] == ledger
        assert connection.execute("SELECT count(*) FROM quest_plans").fetchone()[0] == 1
        notice = connection.execute(
            "SELECT * FROM event_inbox WHERE consumer_id='planner' AND event_id=?", (request["id"],)
        ).fetchone()
        assert notice is not None and notice["processed_at"] is None
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_token='planner-token',notice_next_attempt=? "
            "WHERE consumer_id='planner' AND event_id=?",
            (time.time() + 60, request["id"]),
        )
    current = store.get_quest(quest.id)
    raw["nodes"][1]["objective"] = "Use a bounded specialist for the remaining work."
    staged = QuestGraphService(store).propose_remaining(
        quest.id,
        raw,
        expected_version=current["version"],
        source_event_id=request["id"],
        source_event_token="planner-token",
    )
    assert staged["status"] == "proposed"
    assert staged["revision_lineage"]["completed_nodes"][0]["run_id"] == runs[0]["id"]
    assert store.get_quest(quest.id)["plan_revision"] == parent["revision"]
    with store.connection() as connection:
        assert connection.execute(
            "SELECT processed_at FROM event_inbox WHERE consumer_id='planner' AND event_id=?",
            (request["id"],),
        ).fetchone()[0]
        assert [
            dict(row) for row in connection.execute("SELECT * FROM worker_runs ORDER BY id")
        ] == original


@pytest.mark.parametrize(
    "mutation", ["running", "no_completed", "different_source", "proposed_plan"]
)
def test_replan_offer_rejects_unsafe_or_duplicate_feedback(tmp_path, mutation):
    store, quest, plan, raw, runs, event, _dispatcher = feedback(tmp_path)
    source_id = event.id
    with store.unit_of_work() as work:
        if mutation == "running":
            work.connection.execute(
                "UPDATE worker_runs SET state='running' WHERE id=?", (runs[1]["id"],)
            )
        elif mutation == "no_completed":
            work.connection.execute(
                "UPDATE task_nodes SET status='blocked' WHERE plan_id=?", (plan["id"],)
            )
        elif mutation == "different_source":
            source_id = "event_absent"
    if mutation == "proposed_plan":
        QuestGraphService(store).propose_remaining(
            quest.id, copy.deepcopy(raw), expected_version=store.get_quest(quest.id)["version"]
        )
    with store.connection() as connection:
        assert remaining_replan_context(store, connection, quest.id, source_id) is None


def test_expired_adaptive_planner_cannot_stage_or_ack_a_new_attempt(tmp_path):
    store, quest, _parent, raw, _runs, event, _dispatcher = feedback(tmp_path)
    with store.connection() as connection:
        context = remaining_replan_context(store, connection, quest.id, event.id)
    requested = ForgeEvent(event_type="quest.replan_requested", quest_id=quest.id, payload=context)
    store.append_event(requested)
    dispatcher = Dispatcher(store)
    for _ in range(6):
        dispatcher.tick()
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_token='new-token',notice_next_attempt=? "
            "WHERE consumer_id='planner' AND event_id=?",
            (time.time() + 60, requested.id),
        )
    before = store.get_quest(quest.id)
    with pytest.raises(DomainError, match="no longer pending"):
        QuestGraphService(store).propose_remaining(
            quest.id,
            raw,
            expected_version=before["version"],
            source_event_id=requested.id,
            source_event_token="expired-token",
        )
    assert store.get_quest(quest.id) == before
    assert len(QuestGraphService(store).history(quest.id)) == 1


def test_background_planner_dispatches_feedback_in_remaining_mode(tmp_path, monkeypatch):
    store, quest, _parent, _raw, _runs, event, dispatcher = feedback(tmp_path / "source")
    with store.connection() as connection:
        context = remaining_replan_context(store, connection, quest.id, event.id)
    request = ForgeEvent(event_type="quest.replan_requested", quest_id=quest.id, payload=context)
    store.append_event(request)
    for _ in range(4):
        dispatcher.tick()
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path / "api"))
    from forge_api import main

    importlib.reload(main)
    calls = []

    class CapturePlanner:
        async def generate(self, quest_id, **kwargs):
            calls.append((quest_id, kwargs))

    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "router", router(store, ScriptedMock({})))
    monkeypatch.setattr(main, "quest_graph", CapturePlanner())
    monkeypatch.setattr(main, "model_budget", SimpleNamespace(token_limit=2000))
    assert asyncio.run(main.process_pending_plan())
    assert len(calls) == 1 and calls[0][0] == quest.id
    assert calls[0][1]["remaining_dag"] is True
    assert calls[0][1]["require_capacity_contract"] is True
    assert calls[0][1]["source_event_id"] == request.id
    assert calls[0][1]["source_event_token"]
