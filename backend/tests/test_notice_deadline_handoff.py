"""Real local artifact/verifier; test-only provenance and deterministic notice models.

Accelerate only the outer notice deadline, never perform external inference.
"""

import asyncio
import json

import httpx
import pytest
from test_normal_completion_handoff import completed as completed_fixture
from test_normal_completion_handoff import inbox, retry_now
from test_superagent_loop import MockProvider, response

from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderGateway


@pytest.fixture
def completed(tmp_path):
    return completed_fixture.__wrapped__(tmp_path)


@pytest.fixture
def deadline(monkeypatch):
    original = asyncio.wait_for

    async def shortened(awaitable, timeout):
        return await original(awaitable, 0.03 if timeout == 45 else timeout)

    monkeypatch.setattr(asyncio, "wait_for", shortened)


class Slow:
    execution_mode = "mock"

    def __init__(self, on_cancel=None):
        self.calls = 0
        self.cancelled = 0
        self.on_cancel = on_cancel

    async def generate(self, request):
        self.calls += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            if self.on_cancel:
                self.on_cancel()
            raise


def prime(store, event_id):
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_attempts=4 WHERE event_id=?", (event_id,)
        )


def test_deadline_last_attempt_handoff_is_exactly_once(completed, deadline):
    store, router, dispatcher, quest, _run, artifact, event_id = completed
    router.provider = Slow()
    before = len(store.list_messages())
    for index in range(5):
        retry_now(store, event_id)
        assert asyncio.run(router.handle_pending_events(dispatcher)) is (index == 4)
        assert len(store.list_messages()) == before + (index == 4)
    assert router.provider.calls == router.provider.cancelled == 5
    assert inbox(store, event_id)["processed_at"] and not inbox(store, event_id)["notice_dead_at"]
    metadata = json.loads(store.list_messages()[-1]["metadata"])
    assert metadata["quest_id"] == quest.id
    assert metadata["notice_phase"] == "completion_summary_unavailable"
    assert metadata["completion_result_sources"]
    assert artifact.read_text() == '{"ok":true}'
    retry_now(store, event_id)
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert router.provider.calls == 5 and len(store.list_messages()) == before + 1


def test_early_timeout_preserves_normal_later_summary(completed, deadline):
    store, router, dispatcher, _, _, _, event_id = completed
    router.provider = Slow()
    before = len(store.list_messages())
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == before
    router.provider = MockProvider(
        lambda *_: response({"action": "update_user", "reply": "文件通过本地检查。"})
    )
    retry_now(store, event_id)
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert store.list_messages()[-1]["content"] == "文件通过本地检查。"


@pytest.mark.parametrize("mutation", ["lease", "quest", "source", "revision", "provenance"])
def test_timeout_revalidation_rejects_changed_original_authority(completed, deadline, mutation):
    store, router, dispatcher, quest, _, _, event_id = completed
    prime(store, event_id)

    def mutate():
        with store.unit_of_work() as work:
            c = work.connection
            if mutation == "lease":
                c.execute(
                    "UPDATE event_inbox SET notice_token='peer' WHERE event_id=?", (event_id,)
                )
            elif mutation == "quest":
                c.execute(
                    "UPDATE quests SET status='running',version=version+1 WHERE id=?", (quest.id,)
                )
            elif mutation == "source":
                c.execute(
                    "UPDATE events SET payload=json_set(payload,'$.claim','{}') WHERE event_type='worker.result'"
                )
            elif mutation == "provenance":
                c.execute("UPDATE worker_runs SET execution_mode='mock'")
            else:
                c.execute("UPDATE router_state SET revision=revision+1")

    router.provider = Slow(mutate)
    before = len(store.list_messages())
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == before and not inbox(store, event_id)["processed_at"]
    assert router.provider.calls == 1
    if mutation == "lease":
        assert inbox(store, event_id)["notice_token"] == "peer"
        assert not inbox(store, event_id)["notice_dead_at"]


def test_external_cancellation_never_becomes_completion(completed):
    store, router, dispatcher, _, _, _, event_id = completed
    prime(store, event_id)
    provider = Slow()
    router.provider = provider
    before = len(store.list_messages())

    async def scenario():
        task = asyncio.create_task(router.handle_pending_events(dispatcher))
        while provider.calls == 0:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(store.list_messages()) == before and not inbox(store, event_id)["processed_at"]
    assert provider.calls == provider.cancelled == 1


def test_gateway_cancelled_attempt_accounting_is_not_refunded_or_replayed(completed, deadline):
    store, router, dispatcher, _, _, _, event_id = completed
    prime(store, event_id)
    calls = []

    async def slow_http(request):
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
        transport=httpx.MockTransport(slow_http),
        budget_ledger=ledger,
    )
    assert asyncio.run(router.handle_pending_events(dispatcher))
    with store.connection() as c:
        attempts = [dict(row) for row in c.execute("SELECT * FROM model_budget_attempts")]
    assert len(calls) == len(attempts) == 1
    assert attempts[0]["state"] == "reserved"
    assert attempts[0]["input_charged"] is None and attempts[0]["output_charged"] is None
    retry_now(store, event_id)
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    with store.connection() as c:
        assert [dict(row) for row in c.execute("SELECT * FROM model_budget_attempts")] == attempts
    assert len(calls) == 1
