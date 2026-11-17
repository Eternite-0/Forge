"""No-provider recovery after an interrupted final completion-notice attempt."""

import asyncio
import json

import pytest
from test_normal_completion_handoff import completed as completed_fixture
from test_normal_completion_handoff import inbox, retry_now
from test_notice_deadline_handoff import Slow, prime
from test_superagent_loop import MockProvider, response


@pytest.fixture
def completed(tmp_path):
    return completed_fixture.__wrapped__(tmp_path)


def cancel_last(router, dispatcher):
    async def scenario():
        task = asyncio.create_task(router.handle_pending_events(dispatcher))
        while router.provider.calls == 0:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def interrupted(completed):
    store, router, dispatcher, _, _, _, event_id = completed
    prime(store, event_id)
    router.provider = Slow()
    count = len(store.list_messages())
    cancel_last(router, dispatcher)
    assert len(store.list_messages()) == count
    assert not inbox(store, event_id)["processed_at"]
    return count


def test_later_poll_waits_for_lease_then_delivers_once_without_replay(completed):
    store, router, dispatcher, _, _, artifact, event_id = completed
    count = interrupted(completed)
    old_token = inbox(store, event_id)["notice_token"]
    # Lease is 60 seconds, longer than the ordinary 45-second deadline.
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert inbox(store, event_id)["notice_token"] == old_token
    retry_now(store, event_id)
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert router.provider.calls == router.provider.cancelled == 1
    assert len(store.list_messages()) == count + 1
    assert json.loads(store.list_messages()[-1]["metadata"])["execution_mode"] == "programmatic"
    assert inbox(store, event_id)["notice_attempts"] == 5
    assert inbox(store, event_id)["notice_dead_at"] is None
    assert not asyncio.run(router._handle_notice([event_id], old_token))
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert artifact.read_bytes() == b'{"ok":true}'


@pytest.mark.parametrize("mutation", ["cancelled", "provenance", "claim"])
def test_stale_verified_result_does_not_recover(completed, mutation):
    store, router, dispatcher, quest, _, _, event_id = completed
    count = interrupted(completed)
    with store.unit_of_work() as work:
        if mutation == "cancelled":
            work.connection.execute(
                "UPDATE quests SET status='cancelled',version=version+1 WHERE id=?", (quest.id,)
            )
        elif mutation == "provenance":
            work.connection.execute("UPDATE worker_runs SET execution_mode='mock'")
        else:
            work.connection.execute(
                "UPDATE events SET payload=json_set(payload,'$.claim','{}') WHERE event_type='worker.result'"
            )
    retry_now(store, event_id)
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == count
    assert router.provider.calls == 1
    assert inbox(store, event_id)["notice_dead_at"]


def test_terminal_malformed_output_is_never_resurrected(completed):
    store, router, dispatcher, _, _, _, event_id = completed
    prime(store, event_id)
    router.provider = MockProvider(lambda *_: response({"action": "silent", "reply": "invalid"}))
    count = len(store.list_messages())
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert inbox(store, event_id)["notice_token"] is None
    assert inbox(store, event_id)["notice_dead_at"]
    retry_now(store, event_id)
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == count
    assert len(router.provider.requests) == 1


def test_reclaimed_lease_blocks_old_owner_and_duplicate_consumer(completed, monkeypatch):
    store, router, dispatcher, _, _, _, event_id = completed
    count = interrupted(completed)
    old_token = inbox(store, event_id)["notice_token"]
    retry_now(store, event_id)
    original = router._handle_notice

    async def race(event_ids, token, **kwargs):
        if kwargs.get("orphaned_completion"):
            assert token != old_token
            assert not await original(event_ids, old_token)
            assert not await router.handle_pending_events(dispatcher)
        return await original(event_ids, token, **kwargs)

    monkeypatch.setattr(router, "_handle_notice", race)
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == count + 1
    assert router.provider.calls == 1


def test_new_router_recovers_without_touching_unknown_gateway_reservation(completed):
    import httpx

    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store, router, dispatcher, _, _, _, event_id = completed
    prime(store, event_id)
    calls = []

    async def transport(request):
        calls.append(request)
        await asyncio.Event().wait()

    ledger = ModelCallBudgetLedger(store.path, token_limit=1000000, concurrency_limit=1)
    router.provider = ProviderGateway(
        ProviderConfig(
            id="fixture",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_json_schema=True,
            timeout_seconds=60,
        ),
        credential_resolver=lambda _: "disposable-fixture",
        transport=httpx.MockTransport(transport),
        budget_ledger=ledger,
    )

    async def cancel():
        task = asyncio.create_task(router.handle_pending_events(dispatcher))
        while not calls:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    count = len(store.list_messages())
    asyncio.run(cancel())
    with store.connection() as c:
        original = [dict(row) for row in c.execute("SELECT * FROM model_budget_attempts")]
    assert len(original) == len(calls) == 1
    assert original[0]["state"] == "reserved"
    assert len(store.list_messages()) == count
    # A new Router has no cancellation snapshot or response in memory.
    recovered = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        model_id="scripted",
        provider_id="fixture",
        context_limit=32000,
        autonomous=True,
    )
    recovered.provider = router.provider
    retry_now(store, event_id)
    assert asyncio.run(recovered.handle_pending_events(dispatcher))
    with store.connection() as c:
        assert [dict(row) for row in c.execute("SELECT * FROM model_budget_attempts")] == original
    assert len(calls) == 1
    assert len(store.list_messages()) == count + 1


def test_malformed_orphan_payload_does_not_break_recovery_scan(completed):
    store, router, dispatcher, _, _, _, event_id = completed
    interrupted(completed)
    retry_now(store, event_id)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE event_inbox SET payload='{' WHERE event_id=?", (event_id,))
    assert asyncio.run(router._recover_interrupted_completion()) is None
    # Exercise the exhaustion predicate independently of autonomous progress
    # acknowledgement, whose pre-existing parser expects valid durable JSON.
    router.autonomous = False
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert inbox(store, event_id)["notice_dead_at"]


def test_unconfigured_router_does_not_recover(completed):
    store, router, dispatcher, _, _, _, event_id = completed
    count = interrupted(completed)
    retry_now(store, event_id)
    original = inbox(store, event_id)
    router.model_id = None
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert inbox(store, event_id) == original
    assert len(store.list_messages()) == count


def test_expiry_between_recovery_probe_and_sweep_preserves_next_poll(completed, monkeypatch):
    store, router, dispatcher, _, _, _, event_id = completed
    count = interrupted(completed)
    original = router._recover_interrupted_completion

    async def expire_after_probe():
        result = await original()
        assert result is None  # The 60-second lease has not expired at the probe.
        retry_now(store, event_id)  # Expiry crosses before the ordinary sweep.
        return result

    monkeypatch.setattr(router, "_recover_interrupted_completion", expire_after_probe)
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert inbox(store, event_id)["notice_dead_at"] is None
    assert inbox(store, event_id)["notice_token"]
    assert len(store.list_messages()) == count
    monkeypatch.setattr(router, "_recover_interrupted_completion", original)
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == count + 1
    assert router.provider.calls == 1


def test_multiple_expired_orphans_recover_one_per_poll(completed):
    from test_router_responsiveness_superagent import deliver_all

    from forge_core.domain import ForgeEvent

    store, router, dispatcher, quest, _, _, first = completed
    count = interrupted(completed)
    with store.connection() as c:
        payload = json.loads(
            c.execute("SELECT payload FROM events WHERE id=?", (first,)).fetchone()[0]
        )
    second = ForgeEvent(event_type="quest.status_changed", quest_id=quest.id, payload=payload)
    store.append_event(second)
    deliver_all(dispatcher)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_attempts=5,notice_next_attempt=0,notice_token='interrupted-second' "
            "WHERE consumer_id='router' AND event_id=?",
            (second.id,),
        )
    retry_now(store, first)
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == count + 1
    assert inbox(store, second.id)["notice_dead_at"] is None
    assert inbox(store, second.id)["processed_at"] is None
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == count + 2
    assert inbox(store, second.id)["processed_at"]
    assert router.provider.calls == 1
