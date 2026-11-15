"""Late task events survive a Worker entering review and can start fresh work."""

import asyncio
import hashlib
import json

import pytest
from test_superagent_events import worker
from test_superagent_loop import MockProvider, event_decision, response
from test_superagent_loop import rig as rig  # noqa: PLC0414 - shared SQLite/Router fixture.

from forge_core.domain import DomainError
from forge_core.memory import MemoryService
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.superagent_delegation import SuperagentDelegation
from forge_core.superagent_events import TriggerService
from forge_core.superagent_loop import process_triggers
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


@pytest.mark.parametrize("enters_review_during_inference", [False, True])
def test_late_attach_rolls_back_then_dispatches_new_hidden_worker_with_same_thread(
    rig, monkeypatch, enters_review_during_inference
):
    store, router, events = rig
    original = events.receive("external", "original", "same-topic", {"text": "original task"})
    previous = worker(store)
    events.mark_routed(original["id"], previous["quest_id"])

    def enter_review():
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE worker_runs SET state='review_pending',lease_token=NULL,lease_until=NULL "
                "WHERE id=?",
                (previous["id"],),
            )
            work.connection.execute(
                "UPDATE workers SET status='review_pending' WHERE id=?", (previous["worker_id"],)
            )
            work.connection.execute(
                "UPDATE quests SET status='running' WHERE id=?", (previous["quest_id"],)
            )

    if not enters_review_during_inference:
        events.subscribe(original["thread_id"], previous["id"])
        enter_review()
    # An existing subscription stops accepting events at review. A task with
    # no subscription can also enter review during a cold attachment decision.
    late = events.receive("external", "late", "same-topic", {"text": "new requirement"})
    assert late["thread_id"] == original["thread_id"]
    other = events.receive("external", "other", "unrelated", {"text": "independent draft"})
    expected_hash = hashlib.sha256(
        json.dumps({"text": "new requirement"}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    now = [100.0]
    controlled = TriggerService(store, clock=lambda: now[0])
    from forge_core import superagent_loop

    monkeypatch.setattr(superagent_loop, "TriggerService", lambda _: controlled)
    before = store.get_quest(previous["quest_id"])

    def attach_then_redispatch(request, count):
        context = json.loads(request.messages[1]["content"])
        if count == 1:
            target = next(task for task in context["tasks"] if task["id"] == previous["quest_id"])
            assert target["accepting_worker_count"] == int(enters_review_during_inference)
            assert "accepting_worker_count greater than zero" in request.messages[0]["content"]
            if enters_review_during_inference:
                enter_review()
            # An earlier independent dispatch in the same batch must roll back
            # with the now-invalid attachment, including its mandate and events.
            return response(
                {
                    "decisions": [
                        event_decision(other),
                        event_decision(late, "attach", quest_id=previous["quest_id"]),
                    ]
                }
            )
        assert all(task["id"] != previous["quest_id"] for task in context["tasks"])
        return response({"decisions": [event_decision(late), event_decision(other, "ignore")]})

    provider = router.provider = MockProvider(attach_then_redispatch)
    with pytest.raises(DomainError, match="no accepting Worker"):
        asyncio.run(process_triggers(router))
    assert len(store.list_quests()) == 1
    assert store.get_quest(previous["quest_id"])["metadata"] == before["metadata"]
    for trigger in (late, other):
        stored = events.read_trigger(trigger["id"])
        assert stored["status"] == "pending" and stored["quest_id"] is None
    assert events.read_trigger(late["id"])["payload_sha256"] == expected_hash
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM delegation_mandates").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='router.trigger_decided'"
            ).fetchone()[0]
            == 0
        )
    assert asyncio.run(process_triggers(router)) == 0
    assert len(provider.requests) == 1

    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='succeeded' WHERE id=?", (previous["id"],)
        )
        work.connection.execute(
            "UPDATE quests SET status='completed' WHERE id=?", (previous["quest_id"],)
        )
    now[0] += 121
    assert asyncio.run(process_triggers(router)) == 2
    fresh = next(quest for quest in store.list_quests() if quest["id"] != previous["quest_id"])
    metadata = json.loads(fresh["metadata"])
    assert metadata["thread_id"] == late["thread_id"]
    assert metadata["trigger_id"] == late["id"]
    graphs, teams = QuestGraphService(store), TeamCompositionService(store)
    assert SuperagentDelegation(store, graphs, teams).prepare(fresh["id"])
    with store.connection() as connection:
        mandate = connection.execute(
            "SELECT * FROM delegation_mandates WHERE quest_id=?", (fresh["id"],)
        ).fetchone()
        team = connection.execute(
            "SELECT proposal_digest FROM team_proposals WHERE quest_id=?", (fresh["id"],)
        ).fetchone()
    assert mandate["source_trigger_id"] == late["id"] and mandate["source_sha256"] == expected_hash
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": provider}), ToolEngine(store), team_authority=teams
    )
    session = manager.start(
        fresh["id"],
        graphs.get(fresh["id"])["id"],
        "task",
        team_digest=team["proposal_digest"],
        member_index=0,
    )
    delivered = events.read_trigger(late["id"], run_id=session["id"])
    assert delivered["payload_sha256"] == expected_hash
    assert delivered["thread_id"] == late["thread_id"]
    chain = MemoryService(store).recall_chain(
        [f"thread:{late['thread_id']}"], max_depth=4, max_nodes=64, max_bytes=65536
    )
    assert {
        f"trigger:{original['id']}",
        f"trigger:{late['id']}",
        f"quest:{previous['quest_id']}",
        f"quest:{fresh['id']}",
        f"run:{session['id']}",
    } <= {node["ref"] for node in chain["nodes"]}
    assert all(item["hidden"] for item in store.list_workers())
    assert store.list_messages() == []
