"""Normal-size completion cannot disappear on valid silence or final transport failure.

Existing test-only provenance stub; ToolEngine writes and local JSON verification
are real. Notice models are deterministic in-process functions, never providers.
"""

import asyncio
import json

import pytest
from test_node_verifier_m5 import reviewed_artifact
from test_router_responsiveness_superagent import deliver_all
from test_superagent_loop import MockProvider, response

from forge_core.dispatcher import Dispatcher
from forge_core.domain import ForgeEvent, Quest
from forge_core.evidence import NodeVerifier
from forge_core.providers import ProviderError
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService


@pytest.fixture
def completed(tmp_path):
    store, quest, _, run, artifact = reviewed_artifact(tmp_path)
    assert NodeVerifier(store).verify(quest.id, "n")["status"] == "completed"
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        model_id="scripted",
        provider_id="fixture",
        context_limit=32000,
        autonomous=True,
    )
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    with store.unit_of_work() as work:
        event_id = work.connection.execute(
            "SELECT id FROM events WHERE quest_id=? AND event_type='quest.status_changed' "
            "AND json_extract(payload,'$.to')='completed'",
            (quest.id,),
        ).fetchone()[0]
        work.connection.execute(
            "UPDATE event_inbox SET processed_at='other-fixture-events' WHERE event_id<>?",
            (event_id,),
        )
    return store, router, dispatcher, quest, run, artifact, event_id


def retry_now(store, event_id):
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_next_attempt=0 WHERE event_id=?",
            (event_id,),
        )


def inbox(store, event_id):
    with store.connection() as connection:
        return dict(
            connection.execute(
                "SELECT * FROM event_inbox WHERE consumer_id='router' AND event_id=?",
                (event_id,),
            ).fetchone()
        )


@pytest.mark.parametrize(
    "behavior", ["silent", "transport", "timeout", "provider_error", "summary"]
)
def test_real_verified_small_completion_delivers_exactly_once(completed, behavior):
    store, router, dispatcher, quest, run, artifact, event_id = completed
    sizes = []

    def decide(request, _count):
        sizes.append(json.loads(request.messages[1]["content"])["budget"]["input_estimate"])
        if behavior == "transport":
            raise RuntimeError("fixture transport failure")
        if behavior == "timeout":
            raise TimeoutError("fixture provider timeout")
        if behavior == "provider_error":
            raise ProviderError("network_error")
        return response(
            {"action": "silent", "reply": None}
            if behavior == "silent"
            else {
                "action": "update_user",
                "reply": "JSON文件已通过本地检查。",
            }
        )

    router.provider = MockProvider(decide)
    before = len(store.list_messages())
    with store.connection() as connection:
        original = list(
            connection.execute("SELECT id,payload FROM events WHERE event_type='worker.result'")
        )
    final_attempt = 5 if behavior in {"transport", "timeout", "provider_error"} else 1
    for attempt in range(1, final_attempt + 1):
        retry_now(store, event_id)
        assert asyncio.run(router.handle_pending_events(dispatcher)) is (attempt == final_attempt)
        if attempt < final_attempt:
            assert len(store.list_messages()) == before
            assert inbox(store, event_id)["processed_at"] is None
    assert max(sizes) < 16000
    assert len(store.list_messages()) == before + 1
    message = store.list_messages()[-1]
    metadata = json.loads(message["metadata"])
    assert message["conversation_id"] == "main"
    if behavior == "summary":
        assert message["content"] == "JSON文件已通过本地检查。"
        assert "notice_phase" not in metadata and metadata["execution_mode"] == "mock"
    else:
        assert message["content"] == "任务已完成。结果摘要暂不可用。"
        assert metadata["notice_phase"] == "completion_summary_unavailable"
        assert metadata["quest_id"] == quest.id
        assert metadata["completion_execution_mode"] == "live"
        assert metadata["execution_mode"] == "programmatic"
        assert len(metadata["completion_result_sources"]) == 1
    assert inbox(store, event_id)["processed_at"]
    assert inbox(store, event_id)["notice_dead_at"] is None
    for _ in range(2):
        retry_now(store, event_id)
        assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(router.provider.requests) == final_attempt
    assert len(store.list_messages()) == before + 1
    assert artifact.read_text() == '{"ok":true}'
    with store.connection() as connection:
        assert (
            list(
                connection.execute("SELECT id,payload FROM events WHERE event_type='worker.result'")
            )
            == original
        )
        assert (
            connection.execute("SELECT state FROM worker_runs WHERE id=?", (run["id"],)).fetchone()[
                0
            ]
            == "succeeded"
        )


@pytest.mark.parametrize("behavior", ["silent", "transport"])
@pytest.mark.parametrize("mutation", ["quest", "claim", "lease", "revision"])
def test_new_fallback_preserves_source_and_commit_fences(completed, behavior, mutation):
    store, router, dispatcher, quest, _, _, event_id = completed
    if behavior == "transport":
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE event_inbox SET notice_attempts=4 WHERE event_id=?", (event_id,)
            )

    def decide(*_):
        with store.unit_of_work() as work:
            if mutation == "quest":
                work.connection.execute(
                    "UPDATE quests SET status='running',version=version+1 WHERE id=?", (quest.id,)
                )
            elif mutation == "claim":
                work.connection.execute(
                    "UPDATE events SET payload=json_set(payload,'$.claim','{}') WHERE event_type='worker.result'"
                )
            elif mutation == "lease":
                work.connection.execute(
                    "UPDATE event_inbox SET notice_token='different' WHERE event_id=?", (event_id,)
                )
            else:
                work.connection.execute("UPDATE router_state SET revision=revision+1")
        if behavior == "transport":
            raise RuntimeError("fixture transport failure")
        return response({"action": "silent", "reply": None})

    router.provider = MockProvider(decide)
    before = len(store.list_messages())
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == before and inbox(store, event_id)["processed_at"] is None


@pytest.mark.parametrize("case", ["busy", "cancelled", "malformed", "invalid_silent"])
def test_nontransport_and_invalid_outputs_do_not_gain_fallback(completed, case):
    store, router, dispatcher, _, _, _, event_id = completed
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_attempts=4 WHERE event_id=?", (event_id,)
        )

    def decide(*_):
        if case == "busy":
            raise ProviderError("concurrency_unavailable")
        if case == "cancelled":
            raise asyncio.CancelledError()
        if case == "malformed":
            value = response({})
            value.text = "not-json"
            return value
        return response({"action": "silent", "reply": "invalid nonempty silent reply"})

    router.provider = MockProvider(decide)
    before = len(store.list_messages())
    if case == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(router.handle_pending_events(dispatcher))
    else:
        assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert len(store.list_messages()) == before
    assert inbox(store, event_id)["processed_at"] is None
    if case == "busy":
        assert inbox(store, event_id)["notice_attempts"] == 4
        assert inbox(store, event_id)["notice_dead_at"] is None


@pytest.mark.parametrize("completion_attempts", [0, 4])
def test_coalesced_attempts_use_completed_event_not_exhausted_peer(completed, completion_attempts):
    store, router, dispatcher, quest, _, _, event_id = completed
    unrelated = Quest(title="Unrelated manual task", goal="Keep this notice pending")
    store.save_quest(unrelated)
    other_event = ForgeEvent(
        event_type="quest.status_changed",
        quest_id=unrelated.id,
        payload={"from": "draft", "to": "planning"},
    )
    store.append_event(other_event)
    deliver_all(dispatcher)
    with store.unit_of_work() as work:
        peer = work.connection.execute(
            "SELECT id FROM events WHERE quest_id=? AND event_type='worker.result'", (quest.id,)
        ).fetchone()[0]
        work.connection.execute(
            "UPDATE event_inbox SET notice_attempts=?,notice_next_attempt=0 WHERE event_id=?",
            (completion_attempts, event_id),
        )
        work.connection.execute(
            "UPDATE event_inbox SET processed_at=NULL,notice_attempts=?,notice_next_attempt=0 WHERE event_id=?",
            (4 if completion_attempts == 0 else 0, peer),
        )

    def fail(*_):
        raise RuntimeError("fixture transport failure")

    router.provider = MockProvider(fail)
    before = len(store.list_messages())
    assert asyncio.run(router.handle_pending_events(dispatcher)) is (completion_attempts == 4)
    assert inbox(store, other_event.id)["processed_at"] is None
    assert inbox(store, other_event.id)["notice_attempts"] == 0
    if completion_attempts == 0:
        assert inbox(store, event_id)["notice_attempts"] == 1
        assert inbox(store, event_id)["processed_at"] is None
        assert inbox(store, peer)["processed_at"] is None
        assert len(store.list_messages()) == before
    else:
        assert inbox(store, event_id)["processed_at"] and inbox(store, peer)["processed_at"]
        assert len(store.list_messages()) == before + 1


@pytest.mark.parametrize("case", ["mock", "noncompletion"])
@pytest.mark.parametrize("behavior", ["silent", "transport"])
def test_ineligible_event_retains_silent_and_provider_error_semantics(completed, case, behavior):
    store, router, dispatcher, _, _, _, event_id = completed
    with store.unit_of_work() as work:
        if case == "mock":
            work.connection.execute("UPDATE worker_runs SET execution_mode='mock'")
        else:
            for table in ("events", "event_inbox"):
                key = "id" if table == "events" else "event_id"
                work.connection.execute(
                    f"UPDATE {table} SET event_type='worker.blocked',payload='{{}}' WHERE {key}=?",
                    (event_id,),
                )
        if behavior == "transport":
            work.connection.execute(
                "UPDATE event_inbox SET notice_attempts=4 WHERE event_id=?", (event_id,)
            )

    def decide(*_):
        if behavior == "transport":
            raise RuntimeError("fixture failure on ineligible final attempt")
        return response({"action": "silent", "reply": None})

    router.provider = MockProvider(decide)
    before = len(store.list_messages())
    assert asyncio.run(router.handle_pending_events(dispatcher)) is (behavior == "silent")
    assert len(store.list_messages()) == before
    if behavior == "transport":
        assert inbox(store, event_id)["processed_at"] is None
        assert inbox(store, event_id)["notice_dead_at"]
