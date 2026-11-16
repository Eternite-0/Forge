"""Conversational control contracts, real transactions and explicit Mock models."""

import asyncio
import json

import pytest
from test_superagent_delegation import delegated, set_assignments  # noqa: F401
from test_superagent_loop import MockProvider, conversational_decision, response
from test_superagent_loop import rig as router_rig  # noqa: F401

from forge_core.dispatcher import Dispatcher, LeaseError
from forge_core.domain import ConversationMessage, DomainError, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.router_control import control_task
from forge_core.runtime import ModelResponse
from forge_core.tool_engine import ToolEngine
from forge_core.worker_model_usage import reserve_worker_model_call
from forge_core.worker_runtime import WorkerManager


class NeverCalled:
    execution_mode = "mock"

    def __init__(self):
        self.calls = 0
        self.call_ids = []

    async def generate(self, request):
        self.calls += 1
        self.call_ids.append(request.budget_context.call_id)
        raise AssertionError("This offline control test must not call a model")


def controlled_workers(delegation_fixture, *, count=1, provider=None):
    store, quest_id, mandate, graph, teams, service = delegation_fixture
    if count > 1:
        set_assignments(store, quest_id, [
            {"objective": "Inspect " + str(index), "role": "research"} for index in range(count)
        ])
    service.prepare(quest_id)
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)).fetchone()
        members = json.loads(team["body_json"])["members"]
        digest = team["proposal_digest"]
    provider = provider or NeverCalled()
    manager = WorkerManager(store, ProviderPool({"mock-provider": provider}),
        ToolEngine(store), team_authority=teams)
    runs = [manager.start(quest_id, plan["id"], member["task_node_key"],
        team_digest=digest, member_index=index) for index, member in enumerate(members)]
    return store, quest_id, mandate, graph, manager, provider, runs


def apply_control(store, quest_id, action):
    with store.unit_of_work() as work:
        source = ConversationMessage(role="user", content=action)
        work.add_message(source)
        control_task(work, work.get_quest(quest_id), action, source_message_id=source.id)
    return source.id


def worker_dispatcher(store, run):
    dispatcher = Dispatcher(store)
    with store.connection() as c:
        seq = c.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
    dispatcher.register("worker:" + run["worker_id"], kind="worker",
        worker_id=run["worker_id"], replay_from_seq=seq)
    return dispatcher


def drain(dispatcher, worker_id):
    delivered = []
    for _ in range(20):
        lease = dispatcher.claim("worker:" + worker_id)
        if lease is None:
            return delivered
        delivered.append((lease, dispatcher.deliver(lease)))
    raise AssertionError("Bounded test consumer did not drain")


def test_pause_resume_cancel_commits_task_plan_and_durable_commands_together(delegated):  # noqa: F811
    store, quest_id, mandate, graph, manager, provider, runs = controlled_workers(delegated)
    run = runs[0]
    dispatcher = worker_dispatcher(store, run)
    original_version = store.get_quest(quest_id)["version"]
    budgets = (run["token_limit"], run["tool_limit"], run["step_limit"], run["deadline"])
    with store.connection() as c:
        capsule = c.execute("SELECT capsule_json FROM worker_capsules WHERE run_id=?", (run["id"],)).fetchone()[0]
    for action, target, plan_state in [("pause", "paused", "paused"),
        ("resume", "queued", "approved"), ("cancel", "cancelled", "cancelled")]:
        before = manager.get(run["id"])
        source_id = apply_control(store, quest_id, action)
        assert store.get_quest(quest_id)["status"] == target
        assert graph.get(quest_id)["status"] == plan_state
        # The outbox command is durable before its independent consumer runs.
        assert manager.get(run["id"])["state"] == before["state"]
        events = [event for event in store.list_events() if event["event_type"] == "worker.command." + action]
        assert len(events) == 1
        payload = json.loads(events[0]["payload"])
        assert payload["source_message_id"] == source_id
        assert payload["actor"] == "router_mandate:" + mandate
        delivered = drain(dispatcher, run["worker_id"])
        assert len(delivered) == 1
        current = manager.get(run["id"])
        assert current["state"] == target and current["generation"] == before["generation"] + 1
        assert current["lease_token"] is None and current["lease_until"] is None
        assert (current["token_limit"], current["tool_limit"], current["step_limit"], current["deadline"]) == budgets
        # A delivered lease cannot apply a second generation transition.
        with pytest.raises(LeaseError):
            dispatcher.deliver(delivered[0][0])
        assert manager.get(run["id"])["generation"] == current["generation"]
    assert store.get_quest(quest_id)["version"] == original_version + 3
    assert provider.calls == 0
    with store.connection() as c:
        assert c.execute("SELECT capsule_json FROM worker_capsules WHERE run_id=?", (run["id"],)).fetchone()[0] == capsule
        assert c.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_router_control_and_its_message_roll_back_as_one_transaction(delegated):  # noqa: F811
    store, quest_id, _, graph, manager, _, runs = controlled_workers(delegated)
    before = store.get_quest(quest_id)
    events_before = len(store.list_events())
    messages_before = len(store.list_messages())
    with pytest.raises(RuntimeError, match="abort"), store.unit_of_work() as work:
        source = ConversationMessage(role="user", content="pause")
        work.add_message(source)
        control_task(work, work.get_quest(quest_id), "pause", source_message_id=source.id)
        raise RuntimeError("abort")
    assert store.get_quest(quest_id) == before
    assert graph.get(quest_id)["status"] == "approved"
    assert manager.get(runs[0]["id"])["state"] == "queued"
    assert len(store.list_events()) == events_before and len(store.list_messages()) == messages_before


def test_stale_quest_cas_emits_no_command_and_preserves_winning_control(delegated):  # noqa: F811
    store, quest_id, _, _, _, _, _ = controlled_workers(delegated)
    stale = store.get_quest(quest_id)
    apply_control(store, quest_id, "pause")
    event_count = len(store.list_events())
    with pytest.raises(DomainError, match="revision"), store.unit_of_work() as work:
        control_task(work, stale, "pause", source_message_id="stale-request")
    assert store.get_quest(quest_id)["status"] == "paused"
    assert len(store.list_events()) == event_count


@pytest.mark.parametrize("tamper", ["revoked", "source", "policy"])
def test_unavailable_or_changed_mandate_never_authorizes_control(delegated, tamper):  # noqa: F811
    store, quest_id, mandate, _, _, _, _ = controlled_workers(delegated)
    with store.unit_of_work() as work:
        if tamper == "revoked":
            work.connection.execute("UPDATE delegation_mandates SET state='revoked' WHERE id=?", (mandate,))
        elif tamper == "source":
            work.connection.execute("UPDATE messages SET content='changed source' WHERE id="
                "(SELECT source_message_id FROM delegation_mandates WHERE id=?)", (mandate,))
        else:
            work.connection.execute("UPDATE delegation_mandates SET policy_json='{}' WHERE id=?", (mandate,))
    before = store.get_quest(quest_id)
    event_count = len(store.list_events())
    with pytest.raises(DomainError):
        apply_control(store, quest_id, "cancel")
    assert store.get_quest(quest_id) == before and len(store.list_events()) == event_count


def test_manual_task_cannot_borrow_another_tasks_mandate(delegated):  # noqa: F811
    store, _, _, _, _, _ = delegated
    manual = Quest(title="manual", goal="separate manually governed task")
    store.save_quest(manual)
    with pytest.raises(DomainError, match="autonomous task mandate"):
        apply_control(store, manual.id, "cancel")
    assert store.get_quest(manual.id)["status"] == "proposed"


def test_quest_resume_preserves_worker_manual_pause(delegated):  # noqa: F811
    store, quest_id, _, _, manager, provider, runs = controlled_workers(delegated, count=2)
    dispatchers = [worker_dispatcher(store, run) for run in runs]
    manager.control(runs[1]["id"], "pause")
    apply_control(store, quest_id, "pause")
    for dispatcher, run in zip(dispatchers, runs, strict=True):
        drain(dispatcher, run["worker_id"])
    assert manager.get(runs[0]["id"])["quest_paused"] == 1
    assert manager.get(runs[1]["id"])["quest_paused"] == 0
    apply_control(store, quest_id, "resume")
    for dispatcher, run in zip(dispatchers, runs, strict=True):
        drain(dispatcher, run["worker_id"])
    assert manager.get(runs[0]["id"])["state"] == "queued"
    assert manager.get(runs[1]["id"])["state"] == "paused"
    assert provider.calls == 0


@pytest.mark.parametrize("unknown", ["tool", "model"])
def test_pause_resume_never_replays_unknown_call_or_effect(delegated, unknown):  # noqa: F811
    store, quest_id, _, _, manager, provider, runs = controlled_workers(delegated)
    run = runs[0]
    dispatcher = worker_dispatcher(store, run)
    if unknown == "model":
        assert manager._claim(run["id"])  # A durable in-flight call needs a real owner lease.
    with store.unit_of_work() as work:
        if unknown == "tool":
            work.connection.execute("INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,"
                "tool_name,arguments_json,state) VALUES (?,'unknown-call','prior-model','file_write',?,'running')",
                (run["id"], json.dumps({"path": "unknown.txt", "content": "must not replay"})))
        else:
            row = work.connection.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone()
            reserve_worker_model_call(work.connection, row, "unknown-call", 100, 10, None, [])
            work.connection.execute("UPDATE worker_runs SET token_spent=token_spent+100,steps=steps+1 WHERE id=?", (run["id"],))
    apply_control(store, quest_id, "pause")
    drain(dispatcher, run["worker_id"])
    apply_control(store, quest_id, "resume")
    drain(dispatcher, run["worker_id"])
    result = asyncio.run(manager.run(run["id"]))
    assert result["state"] == "blocked"
    assert "unknown-call" not in provider.call_ids
    # An ordinary model call has no external tool side effect. A fresh inference
    # may be attempted; the unknown historical reservation remains charged.
    assert provider.calls == (0 if unknown == "tool" else 1)
    assert not (store.workspace.root / "unknown.txt").exists()
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
        if unknown == "tool":
            assert c.execute("SELECT state FROM worker_tool_calls WHERE call_id='unknown-call'").fetchone()[0] == "uncertain"
        else:
            assert c.execute("SELECT state FROM worker_model_reservations WHERE call_id='unknown-call'").fetchone()[0] == "reserved"
            assert manager.get(run["id"])["token_spent"] >= 100


def test_router_request_replay_is_idempotent_for_task_control(router_rig):  # noqa: F811
    store, router, _ = router_rig
    provider = MockProvider(lambda request, _: response(conversational_decision()))
    router.provider = provider
    created = asyncio.run(router.handle_user_message("Analyze this project", request_id="create"))
    quest_id = created["quest_id"]
    from forge_core.quest_graph import QuestGraphService
    from forge_core.superagent_delegation import SuperagentDelegation
    from forge_core.team_composition import TeamCompositionService

    SuperagentDelegation(store, QuestGraphService(store), TeamCompositionService(store)).prepare(quest_id)
    provider.respond = lambda request, _: response({**conversational_decision("continue_quest",
        quest_id=quest_id), "task_control": "pause"})
    first = asyncio.run(router.handle_user_message("Pause this task", request_id="pause-once"))
    version = store.get_quest(quest_id)["version"]
    calls = len(provider.requests)
    events = len(store.list_events())
    assert store.get_quest(quest_id)["status"] == "paused"
    assert asyncio.run(router.handle_user_message("Pause this task", request_id="pause-once")) == first
    assert store.get_quest(quest_id)["version"] == version
    assert len(provider.requests) == calls and len(store.list_events()) == events
    with pytest.raises(DomainError, match="different input"):
        asyncio.run(router.handle_user_message("Cancel this task", request_id="pause-once"))


def test_conversational_cancel_fences_running_model_result_and_preserves_usage(delegated):  # noqa: F811
    class Slow:
        execution_mode = "mock"

        def __init__(self):
            self.started, self.release = asyncio.Event(), asyncio.Event()
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return ModelResponse(text="", tool_calls=[{"id": "late-call", "name": "file_write",
                "arguments": {"path": "late.txt", "content": "must not execute"}}],
                usage={"input_tokens": 10, "output_tokens": 5})

    async def scenario():
        provider = Slow()
        store, quest_id, _, _, manager, _, runs = controlled_workers(delegated, provider=provider)
        run = runs[0]
        dispatcher = worker_dispatcher(store, run)
        task = asyncio.create_task(manager.run(run["id"]))
        await asyncio.wait_for(provider.started.wait(), timeout=5)
        apply_control(store, quest_id, "cancel")
        drain(dispatcher, run["worker_id"])
        provider.release.set()
        result = await task
        assert result["state"] == "cancelled" and provider.calls == 1
        assert not any(event["event_type"] == "worker.result" for event in store.list_events())
        assert not (store.workspace.root / "late.txt").exists()
        with store.connection() as c:
            rows = c.execute("SELECT * FROM worker_model_reservations WHERE run_id=?", (run["id"],)).fetchall()
            assert len(rows) == 1  # Accounting evidence survives the lost authorization.
            assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0

    asyncio.run(scenario())
