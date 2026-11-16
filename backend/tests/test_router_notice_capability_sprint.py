"""Core notification regressions: scripted Mock and SQLite, no real Provider calls."""

import asyncio
import json

import pytest
from test_router_responsiveness_superagent import background_task, completed_task, deliver_all
from test_superagent_loop import MockProvider, response
from test_superagent_loop import rig as superagent_rig

from forge_core.dispatcher import Dispatcher
from forge_core.domain import ForgeEvent
from forge_core.governance import GovernanceService


@pytest.fixture
def rig(tmp_path):
    return superagent_rig.__wrapped__(tmp_path)


def request_approval(store, quest):
    return GovernanceService(store).request_approval(
        quest.id,
        "workspace_write",
        {"path": "work/output.txt"},
        str(store.workspace.root / "work"),
        "R1",
        "Write only the specified output",
    )


def authorization_state(store):
    with store.connection() as connection:
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("approvals", "task_grants", "delegation_mandates", "delegation_contracts")
        }


@pytest.mark.parametrize("closed", ["APPROVED", "REJECTED", "EXPIRED", "elapsed"])
def test_closed_automatic_approval_request_is_acknowledged_without_model_or_authority_change(
    rig, closed
):
    store, router, _events = rig
    quest, _worker = background_task(store)
    approval = request_approval(store, quest)
    with store.unit_of_work() as work:
        if closed == "elapsed":
            work.connection.execute(
                "UPDATE approvals SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (approval["id"],),
            )
        else:
            work.connection.execute(
                "UPDATE approvals SET status=? WHERE id=?", (closed, approval["id"])
            )
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    before = authorization_state(store)
    state = store.ensure_router()
    model = router.provider = MockProvider(lambda *_: pytest.fail("Closed approval needs no model"))
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert model.requests == [] and authorization_state(store) == before
    assert store.ensure_router() == state
    inbox = next(
        item for item in dispatcher.inbox("router") if item["event_type"] == "approval.requested"
    )
    assert inbox["processed_at"] and inbox["notice_attempts"] == 0 and not inbox["notice_dead_at"]


@pytest.mark.parametrize("autonomous,mandated", [(False, True), (True, False)])
def test_legacy_or_manual_closed_approval_keeps_existing_notice_behavior(rig, autonomous, mandated):
    store, router, _events = rig
    router.autonomous = autonomous
    quest, _worker = background_task(store, mandated=mandated)
    approval = request_approval(store, quest)
    GovernanceService(store).decide_approval(approval["id"], "APPROVED", "local_user")
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    model = router.provider = MockProvider(lambda *_: response({"action": "silent", "reply": None}))
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(model.requests) == 1


def test_same_authorization_checkpoint_batches_and_preserves_all_source_ids(rig):
    store, router, _events = rig
    quest, worker = background_task(store)
    approval = request_approval(store, quest)
    requested = next(
        item
        for item in store.list_events()
        if json.loads(item["payload"]).get("approval_id") == approval["id"]
    )
    blocked = ForgeEvent(
        event_type="worker.blocked",
        quest_id=quest.id,
        worker_id=worker.id,
        payload={"reason_code": "approval_required", "failure_category": "authorization"},
    )
    store.append_event(blocked)
    unrelated = request_approval(store, quest)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)

    def notify(request, _count):
        notice = json.loads(request.messages[1]["content"])["notice"]
        assert notice["source_event_ids"] == [requested["id"], blocked.id]
        assert notice["events"][0]["payload"]["approval_id"] == approval["id"]
        return response(
            {"action": "update_user", "reply": "Waiting for the exact requested authorization."}
        )

    before = authorization_state(store)
    model = router.provider = MockProvider(notify)
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(model.requests) == 1 and authorization_state(store) == before
    message = store.list_messages()[-1]
    assert json.loads(message["metadata"])["source_event_ids"] == [requested["id"], blocked.id]
    inbox = dispatcher.inbox("router")
    assert all(
        item["processed_at"] for item in inbox if item["event_id"] in (requested["id"], blocked.id)
    )
    assert not next(
        item for item in inbox if item["payload"].get("approval_id") == unrelated["id"]
    )["processed_at"]


def test_approval_decided_during_inference_cannot_publish_an_outdated_request(rig):
    store, router, _events = rig
    quest, _worker = background_task(store)
    approval = request_approval(store, quest)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)

    def decide(_request, _count):
        GovernanceService(store).decide_approval(approval["id"], "APPROVED", "local_user")
        return response({"action": "update_user", "reply": "STALE_AUTHORIZATION_REQUEST"})

    model = router.provider = MockProvider(decide)
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(model.requests) == 1
    assert "STALE_AUTHORIZATION_REQUEST" not in json.dumps(store.list_messages())
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_next_attempt=0 WHERE consumer_id='router'"
        )
    router.provider = MockProvider(
        lambda *_: pytest.fail("Resolved request must not be reinferred")
    )
    assert asyncio.run(router.handle_pending_events(dispatcher))


def pending_review_task(store):
    quest, event = completed_task(store, "PRIVATE_UNREVIEWED_MODEL_CLAIM")
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status='running' WHERE id=?", (quest.id,))
        work.connection.execute(
            "UPDATE task_nodes SET status='running' WHERE plan_id=?", (event.payload["plan_id"],)
        )
        work.connection.execute(
            "UPDATE worker_runs SET state='review_pending' WHERE quest_id=?", (quest.id,)
        )
    return quest, event


def test_pending_review_relays_only_core_status_without_a_provider_call(rig):
    store, router, _events = rig
    quest, event = pending_review_task(store)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    model = router.provider = MockProvider(
        lambda *_: pytest.fail("Unreviewed delivery needs no inference")
    )
    before = authorization_state(store)
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert model.requests == [] and authorization_state(store) == before
    message = store.list_messages()[-1]
    assert "等待独立审查" in message["content"] and "尚未确认任务完成" in message["content"]
    assert "PRIVATE_UNREVIEWED_MODEL_CLAIM" not in message["content"]
    metadata = json.loads(message["metadata"])
    assert metadata["execution_mode"] == "programmatic"
    assert metadata["notice_phase"] == "review_pending"
    assert metadata["source_event_ids"] == [event.id]
    assert f"quest:{quest.id}" in message["content"] and f"event:{event.id}" in message["content"]
    assert (
        len(
            [
                m
                for m in store.list_messages()
                if json.loads(m["metadata"]).get("source") == "router_notice"
            ]
        )
        == 1
    )


@pytest.mark.parametrize("field", ["run_id", "plan_id", "task_node_key"])
def test_pending_review_fact_requires_exact_source_bound_run(rig, field):
    store, router, _events = rig
    _quest, event = pending_review_task(store)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    with store.unit_of_work() as work:
        payload = dict(event.payload)
        payload[field] = "unknown-reference"
        # Dispatcher already verified the native event; a corrupted durable
        # inbox still cannot fabricate a Core pending-review fact.
        work.connection.execute(
            "UPDATE event_inbox SET payload=? WHERE consumer_id='router' AND event_id=?",
            (json.dumps(payload), event.id),
        )
    model = router.provider = MockProvider(lambda *_: response({"action": "silent", "reply": None}))
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(model.requests) == 1
    assert not any(json.loads(m["metadata"]).get("notice_phase") for m in store.list_messages())


def test_pending_review_state_change_is_fenced_before_status_publication(rig, monkeypatch):
    store, router, _events = rig
    quest, _event = pending_review_task(store)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    original = router._context

    def finish_during_projection(snapshot, text):
        result = original(snapshot, text)
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE worker_runs SET state='succeeded' WHERE quest_id=?", (quest.id,)
            )
        return result

    monkeypatch.setattr(router, "_context", finish_during_projection)
    model = router.provider = MockProvider(lambda *_: pytest.fail("No model for pending review"))
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert model.requests == []
    assert not any(
        json.loads(m["metadata"]).get("source") == "router_notice" for m in store.list_messages()
    )


def test_completion_batch_retains_terminal_source_even_when_result_is_first(rig):
    store, router, _events = rig
    quest, result = completed_task(store, "A reviewed result")
    completed = ForgeEvent(
        event_type="quest.status_changed",
        quest_id=quest.id,
        payload={"from": "running", "to": "completed"},
    )
    store.append_event(completed)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    model = router.provider = MockProvider(
        lambda *_: response({"action": "update_user", "reply": "The reviewed task is completed."})
    )
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(model.requests) == 1
    metadata = json.loads(store.list_messages()[-1]["metadata"])
    assert metadata["event_id"] == result.id
    assert metadata["source_event_ids"] == [result.id, completed.id]


def test_completion_summary_contains_only_latest_successful_current_plan_delivery(rig):
    store, router, _events = rig
    quest, original = completed_task(store, "SUPERSEDED_DELIVERY")
    with store.unit_of_work() as work:
        current = dict(
            work.connection.execute(
                "SELECT * FROM worker_runs WHERE quest_id=?", (quest.id,)
            ).fetchone()
        )
        for index, (state, revision) in enumerate(
            (
                ("blocked", current["plan_revision"]),
                ("succeeded", current["plan_revision"] - 1),
            )
        ):
            stale = {
                **current,
                "id": f"run_stale_{index}",
                "state": state,
                "plan_revision": revision,
            }
            work.connection.execute(
                "INSERT INTO worker_runs("
                + ",".join(stale)
                + ") VALUES ("
                + ",".join("?" for _ in stale)
                + ")",
                tuple(stale.values()),
            )
            work.append_event(
                ForgeEvent(
                    event_type="worker.result",
                    quest_id=quest.id,
                    worker_id=original.worker_id,
                    payload={
                        **original.payload,
                        "run_id": stale["id"],
                        "claim": json.dumps({"summary": f"REJECTED_OR_OLD_REVISION_{index}"}),
                    },
                )
            )
    accepted = ForgeEvent(
        event_type="worker.result",
        quest_id=quest.id,
        worker_id=original.worker_id,
        payload={
            **original.payload,
            "claim": json.dumps(
                {
                    "summary": "CURRENT_REVIEWED_DELIVERY",
                    "findings": ["Current finding"],
                    "artifacts": ["work/final-report.json"],
                }
            ),
        },
    )
    store.append_event(accepted)
    completed = ForgeEvent(
        event_type="quest.status_changed",
        quest_id=quest.id,
        payload={"from": "running", "to": "completed"},
    )
    store.append_event(completed)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)

    def summarize(request, _count):
        summaries = json.loads(request.messages[1]["content"])["notice"][
            "completed_delivery_summaries"
        ]
        assert len(summaries) == 1
        assert summaries[0]["source_event_id"] == accepted.id
        assert summaries[0]["summary"] == "CURRENT_REVIEWED_DELIVERY"
        assert summaries[0]["artifacts"] == ["work/final-report.json"]
        assert summaries[0]["source_sha256"]
        assert "reviewed Worker claim" in summaries[0]["trust"]
        return response(
            {"action": "update_user", "reply": "Completed using the current reviewed delivery."}
        )

    model = router.provider = MockProvider(summarize)
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(model.requests) == 1


def test_completion_delivery_source_change_fences_summary_commit(rig):
    store, router, _events = rig
    quest, accepted = completed_task(store, "CURRENT_REVIEWED_DELIVERY")
    store.append_event(
        ForgeEvent(
            event_type="quest.status_changed",
            quest_id=quest.id,
            payload={"from": "running", "to": "completed"},
        )
    )
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)

    def tamper_source(_request, _count):
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE events SET payload=? WHERE id=?",
                (
                    json.dumps(
                        {**accepted.payload, "claim": json.dumps({"summary": "CHANGED_DELIVERY"})}
                    ),
                    accepted.id,
                ),
            )
        return response({"action": "update_user", "reply": "STALE_COMPLETION_SUMMARY"})

    router.provider = MockProvider(tamper_source)
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert "STALE_COMPLETION_SUMMARY" not in json.dumps(store.list_messages())
