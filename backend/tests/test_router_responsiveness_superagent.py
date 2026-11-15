"""Router responsiveness with real SQLite mutations and explicitly scripted models."""

import asyncio
import json

import pytest
from test_router_recall_binding_v28 import prepared
from test_superagent_loop import MockProvider, conversational_decision, response
from test_superagent_loop import rig as superagent_rig

from forge_core.context_service import ContextService
from forge_core.dispatcher import Dispatcher
from forge_core.domain import ConversationMessage, DomainError, ForgeEvent, Quest, WorkerSession
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderGateway
from forge_core.providers.config import ProviderConfig
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelResponse
from forge_core.superagent_delegation import SuperagentDelegation, issue_mandate
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


@pytest.fixture
def rig(tmp_path):
    return superagent_rig.__wrapped__(tmp_path)


def background_task(store, *, mandated=True):
    quest = Quest(title="Background analysis", goal="Inspect the background source")
    source = ConversationMessage(role="user", content="Inspect the background source")
    with store.unit_of_work() as work:
        work.add_message(source)
        work.save_quest(quest)
        if mandated:
            issue_mandate(work, quest.id, source.id)
    worker = WorkerSession(quest_id=quest.id, role="research")
    store.save_worker(worker)
    return quest, worker


def deliver_all(dispatcher):
    dispatcher.register("router")
    for _ in range(100):
        lease = dispatcher.claim("router")
        if lease is None:
            return
        dispatcher.deliver(lease)
    raise AssertionError("Fixture event backlog did not drain")


@pytest.mark.parametrize(
    "event_type,status",
    [
        ("worker.progress", None),
        *[("quest.status_changed", s) for s in ("approved", "queued", "running")],
    ],
)
def test_autonomous_routine_progress_is_durably_acknowledged_without_inference(
    rig, event_type, status
):
    store, router, _events = rig
    quest, worker = background_task(store)
    event = ForgeEvent(
        event_type=event_type,
        quest_id=quest.id,
        worker_id=worker.id,
        payload={"to": status} if status else {"phase": "model"},
    )
    store.append_event(event)
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    model = router.provider = MockProvider(
        lambda *_: pytest.fail("Routine progress needs no model")
    )
    state = store.ensure_router()
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert not asyncio.run(router.handle_pending_events(dispatcher))
    assert model.requests == [] and store.ensure_router() == state
    with store.connection() as c:
        assert c.execute("SELECT 1 FROM events WHERE id=?", (event.id,)).fetchone()
        inbox = c.execute("SELECT * FROM event_inbox WHERE event_id=?", (event.id,)).fetchone()
    assert inbox["processed_at"] and inbox["notice_attempts"] == 0


@pytest.mark.parametrize("autonomous,mandated", [(False, True), (True, False)])
def test_manual_progress_keeps_its_existing_router_notice_behavior(rig, autonomous, mandated):
    store, router, _events = rig
    router.autonomous = autonomous
    quest, worker = background_task(store, mandated=mandated)
    store.append_event(
        ForgeEvent(event_type="worker.progress", quest_id=quest.id, worker_id=worker.id)
    )
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    model = router.provider = MockProvider(lambda *_: response({"action": "silent", "reply": None}))
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(model.requests) == 1


@pytest.mark.parametrize(
    "event_type,status",
    [
        ("worker.result", None),
        ("worker.blocked", None),
        ("worker.failed", None),
        ("worker.help_requested", None),
        ("approval.requested", None),
        *[
            ("quest.status_changed", s)
            for s in ("completed", "failed", "cancelled", "needs_input", "paused")
        ],
    ],
)
def test_actionable_and_terminal_notifications_still_reach_router(rig, event_type, status):
    store, router, _events = rig
    quest, worker = background_task(store)
    store.append_event(
        ForgeEvent(
            event_type=event_type,
            quest_id=quest.id,
            worker_id=worker.id,
            payload={"to": status} if status else {},
        )
    )
    dispatcher = Dispatcher(store)
    deliver_all(dispatcher)
    model = router.provider = MockProvider(lambda *_: response({"action": "silent", "reply": None}))
    assert asyncio.run(router.handle_pending_events(dispatcher))
    assert len(model.requests) == 1


@pytest.mark.parametrize("status", ["approved", "queued", "running", "completed", "failed"])
@pytest.mark.parametrize("archived", [False, True])
def test_new_independent_task_is_not_reinferred_for_unrelated_worker_lifecycle(
    rig, status, archived
):
    store, router, _events = rig
    quest, _worker = background_task(store)
    if archived:
        ContextService(store).compact_router(keep_recent=0)

    def advance(request, count):
        if count == 1:
            with store.unit_of_work() as work:
                metadata = json.loads(work.get_quest(quest.id)["metadata"])
                metadata["worker_role"] = "research"
                work.connection.execute(
                    "UPDATE quests SET status=?,version=version+1,plan_revision=plan_revision+1,"
                    "updated_at='later',metadata=? WHERE id=?",
                    (status, json.dumps(metadata), quest.id),
                )
        return response(conversational_decision())

    model = router.provider = MockProvider(advance)
    result = asyncio.run(
        router.handle_user_message("A separate independent task", request_id="new")
    )
    assert result["quest_id"] != quest.id and len(model.requests) == 1
    with store.connection() as c:
        mandate = c.execute(
            "SELECT * FROM delegation_mandates WHERE quest_id=?", (result["quest_id"],)
        ).fetchone()
    assert mandate["source_message_id"] == result["user_message"]["id"]


@pytest.mark.parametrize("mutation", ["goal", "metadata", "state", "message", "model", "archive"])
def test_independent_create_preserves_semantic_and_conversation_cas(rig, mutation):
    store, router, _events = rig
    quest, _worker = background_task(store)

    def change(request, count):
        if count == 1:
            with store.unit_of_work() as work:
                if mutation == "goal":
                    work.connection.execute(
                        "UPDATE quests SET goal='Different source contract' WHERE id=?", (quest.id,)
                    )
                elif mutation == "metadata":
                    work.connection.execute(
                        "UPDATE quests SET metadata=? WHERE id=?",
                        (json.dumps({"execution_constraints": ["new limit"]}), quest.id),
                    )
                elif mutation == "state":
                    work.connection.execute("UPDATE router_state SET revision=revision+1")
                elif mutation == "message":
                    work.add_message(
                        ConversationMessage(role="user", content="Another instruction")
                    )
                elif mutation == "model":
                    work.connection.execute("UPDATE models SET updated_at='changed'")
            if mutation == "archive":
                ContextService(store).compact_router(keep_recent=0)
        return response(conversational_decision())

    model = router.provider = MockProvider(change)
    assert asyncio.run(router.handle_user_message("A separate task", request_id="cas"))["quest_id"]
    assert len(model.requests) == 2


def test_continuation_still_reinfers_after_the_existing_task_changes(rig):
    store, router, _events = rig
    quest, _worker = background_task(store)

    def change(request, count):
        if count == 1:
            with store.unit_of_work() as work:
                work.connection.execute(
                    "UPDATE quests SET status='running',version=version+1 WHERE id=?", (quest.id,)
                )
        return response(conversational_decision("continue_quest", quest_id=quest.id))

    model = router.provider = MockProvider(change)
    result = asyncio.run(router.handle_user_message(f"Continue {quest.id}", request_id="continue"))
    assert result["intent"] == "continue_quest" and len(model.requests) == 2


def test_create_after_explicit_task_lookup_keeps_full_context_fencing(rig):
    store, router, _events = rig
    quest, _worker = background_task(store)

    def lookup(request, count):
        if count == 1:
            with store.unit_of_work() as work:
                work.connection.execute(
                    "UPDATE quests SET status='running',version=version+1 WHERE id=?", (quest.id,)
                )
        if count in {1, 3}:
            return ModelResponse(
                text="",
                tool_calls=[{"id": f"lookup-{count}", "name": "list_quests", "arguments": {}}],
                usage={},
            )
        return response(conversational_decision())

    model = router.provider = MockProvider(lookup)
    assert asyncio.run(router.handle_user_message("An independent task", request_id="lookup"))[
        "quest_id"
    ]
    assert len(model.requests) == 4


def test_independent_create_cannot_carry_a_changed_selected_requirement(tmp_path):
    store, router, _source, identities = prepared(tmp_path)
    router.autonomous = True
    before = len(store.list_messages())

    def revoke(request, _count):
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE memories SET statement='Changed' WHERE id=?", (identities[0],)
            )
        decision = conversational_decision()
        decision["recalled_requirement_ids"] = [identities[0]]
        return response(decision)

    router.provider = MockProvider(revoke)
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(router.handle_user_message("New Aster task", request_id="changed-recall"))
    assert len(store.list_messages()) == before and store.list_quests() == []


def completed_task(store, summary):
    """Persist a scripted completed fixture; this does not claim external execution."""
    quest, _unused_worker = background_task(store)
    graph, teams = QuestGraphService(store), TeamCompositionService(store)
    assert SuperagentDelegation(store, graph, teams).prepare(quest.id)
    plan = graph.get(quest.id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest.id,)).fetchone()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": MockProvider(lambda *_: pytest.fail("No inference"))}),
        ToolEngine(store),
        team_authority=teams,
    )
    run = manager.start(
        quest.id, plan["id"], "task", team_digest=team["proposal_digest"], member_index=0
    )
    event = ForgeEvent(
        event_type="worker.result",
        quest_id=quest.id,
        worker_id=run["worker_id"],
        payload={
            "run_id": run["id"],
            "plan_id": plan["id"],
            "task_node_key": "task",
            "claim": json.dumps({"summary": summary}),
        },
    )
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='succeeded' WHERE id=?", (run["id"],))
        work.connection.execute(
            "UPDATE task_nodes SET status='succeeded' WHERE plan_id=?", (plan["id"],)
        )
        work.connection.execute(
            "UPDATE quests SET status='completed',version=version+1 WHERE id=?", (quest.id,)
        )
        work.append_event(event)
    return quest, event


def test_completed_task_uses_a_source_linked_natural_router_recap(rig):
    store, router, _events = rig
    quest, event = completed_task(
        store, "Compared the two source tables and retained the discrepancy list."
    )
    recap = "已完成两份来源表格的比较，并保留了差异清单。"

    def summarize(request, _count):
        context = json.loads(request.messages[1]["content"])
        assert context["completed_context"][0]["deliveries"][0]["summary"].startswith("Compared")
        return response(
            {**conversational_decision("status_query", quest_id=quest.id), "reply": recap}
        )

    router.provider = MockProvider(summarize)
    result = asyncio.run(router.handle_user_message(f"回顾 {quest.id}", request_id="recap"))
    content = result["assistant_message"]["content"]
    assert (
        content.startswith(recap)
        and "quest:" + quest.id in content
        and "event:" + event.id in content
    )
    metadata = result["assistant_message"]["metadata"]
    assert metadata["status_sources"] == ["quest:" + quest.id + " [completed]", "event:" + event.id]


@pytest.mark.parametrize("autonomous,status", [(False, "completed"), (True, "running")])
def test_legacy_or_unfinished_status_never_adopts_a_model_completion_claim(rig, autonomous, status):
    store, router, _events = rig
    quest, _event = completed_task(store, "Observed source comparison")
    router.autonomous = autonomous
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status=? WHERE id=?", (status, quest.id))
    router.provider = MockProvider(
        lambda *_: response(
            {
                **conversational_decision("status_query", quest_id=quest.id),
                "reply": "已完成虚构结果，所有输入均已删除。",
            }
        )
    )
    result = asyncio.run(router.handle_user_message(f"回顾 {quest.id}", request_id="fixed-status"))
    assert "虚构" not in result["assistant_message"]["content"]
    assert f"[{status}]" in result["assistant_message"]["content"]
    assert "status_sources" not in result["assistant_message"]["metadata"]


def test_completed_status_without_a_succeeded_source_delivery_keeps_core_facts(rig):
    store, router, _events = rig
    quest, _worker = background_task(store)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status='completed' WHERE id=?", (quest.id,))
    router.provider = MockProvider(
        lambda *_: response(
            {
                **conversational_decision("status_query", quest_id=quest.id),
                "reply": "已完成一份不存在的报告。",
            }
        )
    )
    result = asyncio.run(router.handle_user_message(f"回顾 {quest.id}", request_id="no-delivery"))
    assert "不存在" not in result["assistant_message"]["content"]
    assert "[completed]" in result["assistant_message"]["content"]


def test_completed_recap_reinfers_if_its_delivery_source_changes(rig):
    store, router, _events = rig
    quest, event = completed_task(store, "Original comparison")

    def change(request, count):
        if count == 1:
            with store.unit_of_work() as work:
                payload = json.loads(
                    work.connection.execute(
                        "SELECT payload FROM events WHERE id=?", (event.id,)
                    ).fetchone()[0]
                )
                payload["claim"] = json.dumps({"summary": "Changed comparison"})
                work.connection.execute(
                    "UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), event.id)
                )
        return response(
            {**conversational_decision("status_query", quest_id=quest.id), "reply": "已完成对比。"}
        )

    model = router.provider = MockProvider(change)
    result = asyncio.run(
        router.handle_user_message(f"回顾 {quest.id}", request_id="changed-delivery")
    )
    assert result["intent"] == "status_query" and len(model.requests) == 2


@pytest.mark.parametrize("include_unfinished", [False, True])
def test_historical_recap_handles_multiple_completed_tasks_and_keeps_mixed_states_factual(
    rig, include_unfinished
):
    store, router, _events = rig
    first, _first_event = completed_task(store, "Compared source tables")
    second, _second_event = completed_task(store, "Checked independent totals")
    if include_unfinished:
        background_task(store)
    recap = "已完成来源表比较和独立总数校验。"

    def summarize(request, _count):
        context = json.loads(request.messages[1]["content"])
        assert {item["reference"] for item in context["completed_context"]} == {
            "quest:" + first.id,
            "quest:" + second.id,
        }
        return response({**conversational_decision("status_query"), "reply": recap})

    router.provider = MockProvider(summarize)
    result = asyncio.run(
        router.handle_user_message("Background analysis 历史回顾", request_id="history")
    )
    content = result["assistant_message"]["content"]
    assert result["intent"] == "status_query"
    if include_unfinished:
        assert recap not in content and "[proposed]" in content
    else:
        assert content.startswith(recap)
        assert "quest:" + first.id in content and "quest:" + second.id in content


def test_autonomous_nested_source_backlog_fits_native_gateway_without_losing_recap_sources(rig):
    store, router, _events = rig
    quest, _event = completed_task(store, "Observed table totals: alpha=17; marker=alpha-source.")
    router.context_limit = 65536
    router.output_reserve = 4096
    # Stored event payloads add another JSON escaping layer inside Router context.
    # This is real persisted source content, not an enlarged model/window ceiling.
    for index in range(16):
        store.append_event(
            ForgeEvent(
                event_type="source.observed",
                quest_id=quest.id,
                payload={"index": index, "quoted_source": '\\"line\n' * 250},
            )
        )
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.test/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            supports_json_schema=True,
        )
    )
    observed = []

    def respond(request, _count):
        # The actual gateway's full wire validator is the acceptance oracle.
        payload = gateway._payload(request, False)
        observed.append(payload)
        context = json.loads(request.messages[1]["content"])
        assert context["input"].startswith("回顾")
        assert context["completed_context"][0]["deliveries"][0]["summary"].startswith("Observed")
        assert context["spilled"]
        for spill in context["spilled"].values():
            for reference in spill["references"]:
                assert router.memory_service.recall_object(reference)["ref"] == reference
        assert (
            len(json.dumps(payload, ensure_ascii=False).encode()) + 64 + request.max_output_tokens
            <= 65536
        )
        return response(
            {
                **conversational_decision("status_query", quest_id=quest.id),
                "reply": "已完成来源总数核验，alpha 为 17。",
            }
        )

    router.provider = MockProvider(respond)
    result = asyncio.run(router.handle_user_message(f"回顾 {quest.id}", request_id="nested-source"))
    assert len(observed) == 1 and result["intent"] == "status_query"
    with store.connection() as c:
        cursor = c.execute("SELECT event_cursor FROM router_state").fetchone()[0]
        maximum = c.execute("SELECT MAX(seq) FROM events").fetchone()[0]
    assert cursor < maximum  # spilled events remain available to the next observation
