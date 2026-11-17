"""Deterministic SQLite intake races and source-preserving notice admission."""

import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import closing

import pytest
from test_router_responsiveness_superagent import completed_task, deliver_all
from test_superagent_loop import MockProvider, conversational_decision, response
from test_superagent_loop import rig as superagent_rig

from forge_core.constraints import ConstraintService
from forge_core.dispatcher import Dispatcher
from forge_core.domain import ConversationMessage, ForgeEvent, WorkerSession, new_id
from forge_core.router import _json
from forge_core.workspace_discovery import WorkspaceDiscovery


@pytest.fixture
def rig(tmp_path):
    return superagent_rig.__wrapped__(tmp_path)


def test_project_registration_serializes_writers_then_releases_lock_before_inference(
    rig, tmp_path, monkeypatch
):
    store, router, _events = rig
    project = tmp_path / "second_project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='second'\n", encoding="utf-8")
    original = WorkspaceDiscovery.register_user_request
    conflicts = []

    def register(discovery, text, *, connection):
        # A second Core writer interleaves after request-id SELECT and before
        # registration INSERT. A deferred reader would admit it then fail to
        # upgrade its own stale WAL snapshot with SQLITE_BUSY_SNAPSHOT.
        with closing(sqlite3.connect(store.path, isolation_level=None)) as other:
            other.execute("PRAGMA busy_timeout=0")
            try:
                other.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                conflicts.append(exc.sqlite_errorcode)
            else:
                other.execute("UPDATE router_state SET revision=revision+1")
                other.commit()
        return original(discovery, text, connection=connection)

    monkeypatch.setattr(WorkspaceDiscovery, "register_user_request", register)

    def infer(_request, _count):
        # The short intake transaction must finish before awaiting any model.
        with closing(sqlite3.connect(store.path, isolation_level=None)) as other:
            other.execute("PRAGMA busy_timeout=0")
            other.execute("BEGIN IMMEDIATE")
            other.execute("UPDATE router_state SET event_cursor=event_cursor")
            other.commit()
        return response(conversational_decision("chat"))

    model = router.provider = MockProvider(infer)
    result = asyncio.run(router.handle_user_message(f'Inspect "{project}"', request_id="second"))
    assert result["intent"] == "chat"
    assert conflicts == [sqlite3.SQLITE_BUSY]
    assert len(model.requests) == 1
    assert WorkspaceDiscovery(store).list_projects()[0]["root"] == str(project)
    assert len(store.list_messages()) == 2


def completed_specialists(store):
    """Explicit scripted storage fixture; no external execution is claimed."""
    quest, first = completed_task(store, "initial scripted completion")
    with store.connection() as connection:
        template = dict(
            connection.execute("SELECT * FROM worker_runs WHERE quest_id=?", (quest.id,)).fetchone()
        )
        node = dict(
            connection.execute(
                "SELECT * FROM task_nodes WHERE plan_id=?", (template["plan_id"],)
            ).fetchone()
        )
    expected = []
    for index in range(3):
        if index:
            worker = WorkerSession(quest_id=quest.id, role="research")
            store.save_worker(worker)
            run = {
                **template,
                "id": new_id("run"),
                "worker_id": worker.id,
                "node_key": f"task_{index}",
            }
            task = {**node, "node_key": run["node_key"], "worker_id": worker.id}
            with store.unit_of_work() as work:
                for table, row in (("task_nodes", task), ("worker_runs", run)):
                    work.connection.execute(
                        f"INSERT INTO {table}("
                        + ",".join(row)
                        + ") VALUES ("
                        + ",".join("?" for _ in row)
                        + ")",
                        tuple(row.values()),
                    )
        else:
            run = template
        delivery = {
            "summary": f"Specialist {index}: " + "observed grounded finding " * 32,
            "findings": [
                f"Evidence {part}: " + "source-backed analysis " * 13 for part in range(3)
            ],
            "artifacts": [f"work/{quest.id}/report_{index}.json receipt:exact_{index}"],
        }
        event = ForgeEvent(
            event_type="worker.result",
            quest_id=quest.id,
            worker_id=run["worker_id"],
            payload={
                **first.payload,
                "run_id": run["id"],
                "task_node_key": run["node_key"],
                "claim": json.dumps(delivery),
            },
        )
        store.append_event(event)
        with store.connection() as connection:
            raw = connection.execute(
                "SELECT payload FROM events WHERE id=?", (event.id,)
            ).fetchone()[0]
        expected.append(
            {
                "source_event_id": event.id,
                "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                **delivery,
            }
        )
    constraints = ConstraintService(store)
    for index in range(4):
        statement = f"Required source constraint {index}: " + "preserve evidence " * 20
        message = ConversationMessage(role="user", content=statement)
        store.add_message(message)
        constraints.record_extraction(statement, source_message_id=message.id, quest_id=quest.id)
    completed = ForgeEvent(
        event_type="quest.status_changed",
        quest_id=quest.id,
        payload={"from": "running", "to": "completed"},
    )
    store.append_event(completed)
    return quest, completed, expected


def test_complete_notice_facts_fit_without_repeating_the_ordinary_router_policy(rig):
    store, router, _events = rig
    quest, completed, expected = completed_specialists(store)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)

    def summarize(request, _count):
        context = json.loads(request.messages[1]["content"])
        # Necessary source facts already fit the unchanged 16000-byte bound.
        assert len(_json(context).encode("utf-8")) <= 16000
        summaries = context["notice"]["completed_delivery_summaries"]
        assert {item["source_event_id"] for item in summaries} == {
            item["source_event_id"] for item in expected
        }
        for actual in summaries:
            original = next(
                item for item in expected if item["source_event_id"] == actual["source_event_id"]
            )
            for key in ("source_sha256", "summary", "findings", "artifacts"):
                assert actual[key] == original[key]
            assert "reviewed Worker claim" in actual["trust"]
        assert len(context["completed_context"][0]["deliveries"]) == 3
        assert len(context["constraints"]["items"]) == 4
        assert completed.id in context["notice"]["source_event_ids"]
        assert "never invent execution" in request.messages[0]["content"].lower()
        return response({"action": "update_user", "reply": "Completed the source-linked analysis."})

    model = router.provider = MockProvider(summarize)
    token = "offline-notice-projection"
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_token=?,notice_next_attempt=? "
            "WHERE consumer_id='router' AND event_id=?",
            (token, time.time() + 60, completed.id),
        )
    assert asyncio.run(router._handle_notice([completed.id], token))
    assert len(model.requests) == 1
    assert store.list_quests()[0]["id"] == quest.id
    assert store.list_quests()[0]["status"] == "completed"
    assert json.loads(store.list_messages()[-1]["metadata"])["source"] == "router_notice"
