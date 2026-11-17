"""Real pending-notice loop over explicitly scripted completed SQLite fixtures.

Fixture live provenance is a storage contract specimen, never model execution.
The separate roundtrip test exercises actual file tools and the local verifier.
"""

import asyncio
import hashlib
import json

import pytest
from test_router_intake_notice_convergence import completed_specialists
from test_router_responsiveness_superagent import deliver_all
from test_superagent_loop import MockProvider, rig

from forge_core.dispatcher import Dispatcher
from forge_core.domain import DomainError


@pytest.fixture
def oversized(tmp_path):
    store, router, _ = rig.__wrapped__(tmp_path)
    quest, event, _ = completed_specialists(store)
    with store.unit_of_work() as work:
        for row in work.connection.execute(
            "SELECT id,payload FROM events WHERE event_type='worker.result'"
        ).fetchall():
            payload = json.loads(row["payload"])
            delivery = json.loads(payload["claim"])
            delivery["summary"] = "经过核查的中文结果。" * 80
            delivery["findings"] = ["任务证据与来源说明。" * 30] * 3
            payload["claim"] = json.dumps(delivery, ensure_ascii=False)
            work.connection.execute(
                "UPDATE events SET payload=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), row["id"]),
            )
        # Explicit test-only fixture: emulate the provenance of Core-completed runs.
        work.connection.execute("UPDATE worker_runs SET execution_mode='live'")
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET processed_at='other-fixture-events' WHERE event_id<>?",
            (event.id,),
        )
    router.provider = MockProvider(lambda *_: pytest.fail("No model call for a handoff"))
    return store, router, dispatcher, quest, event


def test_oversized_completion_is_delivered_once_without_consuming_retries(oversized):
    store, router, dispatcher, quest, event = oversized
    with store.connection() as connection:
        original = dict(
            connection.execute(
                "SELECT id,payload FROM events WHERE event_type='worker.result'"
            ).fetchall()
        )
    before = len(store.list_messages())
    for index in range(6):
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE event_inbox SET notice_next_attempt=0 WHERE event_id=?", (event.id,)
            )
        assert asyncio.run(router.handle_pending_events(dispatcher)) is (index == 0)
    assert not router.provider.requests
    assert len(store.list_messages()) == before + 1
    message = store.list_messages()[-1]
    assert message["conversation_id"] == "main"
    assert message["content"] == "任务已完成。结果较长，这里只通知完成，不概括内容。"
    assert quest.id not in message["content"] and event.id not in message["content"]
    metadata = json.loads(message["metadata"])
    assert metadata["source"] == "router_notice"
    assert metadata["notice_phase"] == "completion_summary_unavailable"
    assert metadata["execution_mode"] == "programmatic"
    assert metadata["completion_execution_mode"] == "live"
    assert metadata["quest_id"] == quest.id and metadata["source_event_ids"] == [event.id]
    assert len(metadata["completion_result_sources"]) == 3
    for source in metadata["completion_result_sources"]:
        assert (
            source["source_sha256"]
            == hashlib.sha256(original[source["event_id"]].encode()).hexdigest()
        )
    with store.connection() as connection:
        assert original == dict(
            connection.execute(
                "SELECT id,payload FROM events WHERE event_type='worker.result'"
            ).fetchall()
        )
        row = connection.execute(
            "SELECT * FROM event_inbox WHERE event_id=?", (event.id,)
        ).fetchone()
    assert row["processed_at"] and row["notice_attempts"] == 1
    assert row["notice_dead_at"] is None and row["notice_error"] is None


@pytest.mark.parametrize(
    "mutation", ["mock", "review_pending", "foreign", "missing_source", "inbox_mismatch"]
)
def test_unproved_completion_never_receives_programmatic_success(oversized, mutation):
    store, router, dispatcher, quest, event = oversized
    with store.unit_of_work() as work:
        if mutation == "mock":
            work.connection.execute("UPDATE worker_runs SET execution_mode='mock'")
        elif mutation == "review_pending":
            work.connection.execute("UPDATE quests SET status='running' WHERE id=?", (quest.id,))
        elif mutation == "foreign":
            work.connection.execute(
                "UPDATE events SET conversation_id='foreign' WHERE id=?", (event.id,)
            )
        elif mutation == "missing_source":
            work.connection.execute("UPDATE worker_runs SET state='review_pending'")
        else:
            work.connection.execute("UPDATE events SET payload='{}' WHERE id=?", (event.id,))
    # Keep the rejection path over the same fixed projection cap even when the
    # unavailable source is correctly omitted from the ordinary projection.
    original_context = router._context

    def context(*args):
        value, tools = original_context(*args)
        value["fixture_oversize"] = "x" * 16001
        return value, tools

    router._context = context
    before = len(store.list_messages())
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == before and not router.provider.requests


@pytest.mark.parametrize(
    "mutation", ["quest", "claim", "completion_event", "lease", "router_revision"]
)
def test_handoff_rechecks_state_sources_and_lease_before_commit(oversized, mutation):
    store, router, dispatcher, quest, event = oversized
    original_context = router._context

    def context(*args):
        value = original_context(*args)
        with store.unit_of_work() as work:
            if mutation == "quest":
                work.connection.execute(
                    "UPDATE quests SET status='running',version=version+1 WHERE id=?", (quest.id,)
                )
            elif mutation == "claim":
                work.connection.execute(
                    "UPDATE events SET payload=json_set(payload,'$.claim','{}') WHERE event_type='worker.result'"
                )
            elif mutation == "completion_event":
                work.connection.execute("UPDATE events SET payload='{}' WHERE id=?", (event.id,))
            elif mutation == "lease":
                work.connection.execute(
                    "UPDATE event_inbox SET notice_token='different-owner' WHERE event_id=?",
                    (event.id,),
                )
            else:
                work.connection.execute("UPDATE router_state SET revision=revision+1")
        return value

    router._context = context
    before = len(store.list_messages())
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == before and not router.provider.requests


def test_oversized_noncompletion_keeps_original_failure_semantics(oversized):
    store, router, _dispatcher, _quest, event = oversized
    with store.unit_of_work() as work:
        for table in ("events", "event_inbox"):
            key = "id" if table == "events" else "event_id"
            work.connection.execute(
                f"UPDATE {table} SET event_type='worker.blocked',payload='{{}}' WHERE {key}=?",
                (event.id,),
            )
    original = router._context

    def context(*args):
        value, tools = original(*args)
        value["fixture_oversize"] = "x" * 16001
        return value, tools

    router._context = context
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_token='test',notice_next_attempt=9999999999 WHERE event_id=?",
            (event.id,),
        )
    with pytest.raises(DomainError, match="projection exceeds budget"):
        asyncio.run(router._handle_notice([event.id], "test"))
    assert not router.provider.requests


@pytest.mark.parametrize("completion", [True, False])
def test_small_projection_over_effective_model_budget_uses_same_completion_gate(
    oversized, monkeypatch, completion
):
    import forge_core.router as router_module

    store, router, dispatcher, quest, event = oversized
    with store.unit_of_work() as work:
        for row in work.connection.execute(
            "SELECT id,payload FROM events WHERE event_type='worker.result'"
        ).fetchall():
            payload = json.loads(row["payload"])
            payload["claim"] = json.dumps({"summary": "bounded finding", "findings": []})
            work.connection.execute(
                "UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), row["id"])
            )
        if not completion:
            for table in ("events", "event_inbox"):
                key = "id" if table == "events" else "event_id"
                work.connection.execute(
                    f"UPDATE {table} SET event_type='worker.blocked',payload='{{}}' WHERE {key}=?",
                    (event.id,),
                )
    original_context = router._context
    seen = []

    def constrained_context(*args):
        context, tools = original_context(*args)
        # Isolate the existing final wire-admission boundary, not earlier intake
        # policy admission. The projection fits16k but cannot fit this budget.
        context["budget"]["effective_limit"] = router.output_reserve + 1024
        seen.append(context)
        return context, tools

    router._context = constrained_context
    wire_sizes = []
    original_json = router_module._json

    def observe_wire(value):
        text = original_json(value)
        if isinstance(value, dict) and "messages" in value and "schema" in value:
            wire_sizes.append(len(text.encode("utf-8")))
        return text

    monkeypatch.setattr(router_module, "_json", observe_wire)
    before = len(store.list_messages())
    assert asyncio.run(router.handle_pending_events(dispatcher)) is completion
    assert seen[-1]["budget"]["input_estimate"] < 16000
    assert wire_sizes[-1] > 1024
    assert not router.provider.requests
    if completion:
        assert len(store.list_messages()) == before + 1
        metadata = json.loads(store.list_messages()[-1]["metadata"])
        assert metadata["notice_phase"] == "completion_summary_unavailable"
        assert metadata["quest_id"] == quest.id
        assert metadata["completion_execution_mode"] == "live"
        assert not asyncio.run(router.handle_pending_events(dispatcher))
    else:
        assert len(store.list_messages()) == before
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE event_inbox SET notice_token='test',notice_next_attempt=9999999999 WHERE event_id=?",
                (event.id,),
            )
        with pytest.raises(DomainError, match="notice context budget exceeded"):
            asyncio.run(router._handle_notice([event.id], "test"))


def test_configured_18000_context_delivers_13973_byte_completion_without_inference(oversized):
    store, router, dispatcher, quest, _event = oversized
    with store.unit_of_work() as work:
        for row in work.connection.execute(
            "SELECT id,payload FROM events WHERE event_type='worker.result'"
        ).fetchall():
            payload = json.loads(row["payload"])
            payload["claim"] = json.dumps(
                {"summary": "核查结果" * 50, "findings": ["来源证据" * 50] * 3},
                ensure_ascii=False,
            )
            work.connection.execute(
                "UPDATE events SET payload=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), row["id"]),
            )
    router.context_limit = 18000
    original_context = router._context
    seen = []

    def observe(*args):
        context, tools = original_context(*args)
        seen.append(context)
        return context, tools

    router._context = observe
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert seen[-1]["budget"]["effective_limit"] == 18000
    assert seen[-1]["budget"]["input_estimate"] < 16000
    assert not router.provider.requests
    message = store.list_messages()[-1]
    metadata = json.loads(message["metadata"])
    assert metadata["quest_id"] == quest.id
    assert metadata["notice_phase"] == "completion_summary_unavailable"
    assert metadata["completion_execution_mode"] == "live"
