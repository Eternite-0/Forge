"""Offline Mock models and real SQLite; no live model execution is claimed."""

import asyncio
import json
from contextlib import closing

import httpx
import pytest

from forge_core.domain import Quest
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelResponse
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.worker_runtime import WorkerManager


def run(coro):
    return asyncio.run(coro)


def _remove_post_v13_tables(connection):
    """Construct a genuine older fixture rather than only editing its version.

    Current-schema copies contain later independent tables; these must also be
    removed so the migration under test can strictly create its own objects.
    """
    for table in (
        "superagent_trigger_deliveries",
        "superagent_subscriptions",
        "superagent_timers",
        "superagent_file_watches",
        "superagent_triggers",
        "superagent_threads",
        "superagent_sources",
        "delegation_contracts",
        "delegation_mandates",
        "task_project_links",
        "workspace_projects",
        "runtime_heartbeats",
    ):
        connection.execute(f"DROP TABLE {table}")


@pytest.fixture
def graph(tmp_path):
    store = Store(tmp_path / "worker.sqlite3", workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Task", goal="Implement task")
    store.save_quest(quest)
    service = QuestGraphService(store)
    plan = service.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": "Implement",
                    "success_criteria": ["verified"],
                    "budget_tokens": 4000,
                }
            ],
            "edges": [],
        },
        expected_version=0,
    )
    service.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    return store, quest, plan


class ScriptedMock:
    execution_mode = "mock"

    def __init__(self):
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                text="",
                tool_calls=[{"id": "call-1", "name": "read", "arguments": {"path": "input.txt"}}],
                usage={"input_tokens": 10, "output_tokens": 5},
            )
        assert request.messages[-1]["role"] == "tool"
        return ModelResponse(
            text='{"summary":"done"}', tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5}
        )


class MockToolEngine:
    def __init__(self):
        self.calls = []

    def definitions(self, names):
        return [
            {"name": name, "description": "mock", "input_schema": {"type": "object"}}
            for name in names
        ]

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "call_id": kwargs["call_id"],
            "state": "succeeded",
            "receipt_id": "mock-receipt",
            "output": {"content": "data"},
        }


class MockApprovedTeam:
    def __init__(self, plan, *, tokens=4000, tools=None, cost_cap=None):
        self.plan = plan
        self.tokens = tokens
        self.tools = tools or []
        self.cost_cap = cost_cap

    def get_approved(self, quest_id, plan_revision, proposal_digest, *, connection):
        assert plan_revision == self.plan["plan_revision"] and proposal_digest == "team"
        return {
            "plan_digest": self.plan["digest"],
            "proposal_digest": "team",
            "members": [
                {
                    "role_id": "coding",
                    "task_node_key": "n",
                    "depth": 0,
                    "provider_id": "mock-provider",
                    "model_id": "mock-model",
                    "tool_scope": self.tools,
                    "estimated_tokens": self.tokens,
                    "output_tokens": min(256, max(1, self.tokens // 4)),
                    "model_max_output_tokens": 4096,
                    "model_context_window": 32768,
                    "max_tool_calls": 2,
                    "max_steps": 3,
                    "max_duration_seconds": 30,
                    "capability_sources": {"tools": "operator"},
                    "tool_calling_support": {"supported": True, "source": "operator"},
                }
            ],
            "limits": {
                "max_depth": 1,
                "max_members": 1,
                "max_total_tokens": 4000,
                "max_cost_microunits": self.cost_cap,
                "currency": "USD" if self.cost_cap is not None else None,
            },
        }


def test_worker_model_tool_loop_is_hidden_and_never_completes_quest(graph):
    store, quest, plan = graph
    model, tool = ScriptedMock(), MockToolEngine()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": model}),
        tool,
        team_authority=MockApprovedTeam(plan, tools=["read"]),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    result = run(manager.run(session["id"]))
    assert result["state"] == "review_pending"
    assert result["execution_mode"] == "mock"
    assert model.calls == 2 and len(tool.calls) == 1
    assert store.get_quest(quest.id)["status"] != "completed"
    assert store.list_messages() == []
    assert any(event["event_type"] == "worker.result" for event in store.list_events())


def test_event_arriving_during_final_inference_is_consumed_before_delivery(graph):
    store, quest, plan = graph
    session_id = None

    class LateEventModel:
        execution_mode = "mock"
        calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                with store.unit_of_work() as work:
                    work.connection.execute(
                        "INSERT INTO worker_inbox(run_id,event_id,payload_json,consumed) "
                        "VALUES (?,? ,?,0)",
                        (session_id, "late-event", json.dumps({"followup": "include appendix"})),
                    )
                return ModelResponse(
                    text='{"summary":"draft"}',
                    tool_calls=[],
                    usage={"input_tokens": 10, "output_tokens": 5},
                )
            assert "include appendix" in json.dumps(request.messages)
            assert not any(e["event_type"] == "worker.result" for e in store.list_events())
            return ModelResponse(
                text='{"summary":"done with appendix"}',
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    model = LateEventModel()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": model}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session_id = manager.start(
        quest.id,
        plan["id"],
        "n",
        team_digest="team",
        member_index=0,
    )["id"]
    result = run(manager.run(session_id))
    assert result["state"] == "review_pending"
    assert model.calls == 2 and result["steps"] == 2 and result["token_spent"] == 30
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT consumed FROM worker_inbox WHERE run_id=?",
                (session_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_messages WHERE run_id=? "
                "AND message_json LIKE '%draft%'",
                (session_id,),
            ).fetchone()[0]
            >= 1
        )
    events = [e for e in store.list_events() if e["event_type"] == "worker.result"]
    assert len(events) == 1


def test_worker_output_limit_uses_approved_role_not_fixed_256(graph):
    store, quest, plan = graph

    class OutputTeam(MockApprovedTeam):
        def get_approved(self, *args, **kwargs):
            team = super().get_approved(*args, **kwargs)
            team["members"][0]["output_tokens"] = 700
            team["members"][0]["model_max_output_tokens"] = 800
            return team

    class CheckOutput:
        execution_mode = "mock"

        async def generate(self, request):
            assert request.max_output_tokens == 700
            return ModelResponse(
                text='{"summary":"done"}',
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": CheckOutput()}),
        MockToolEngine(),
        team_authority=OutputTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    assert run(manager.run(session["id"]))["state"] == "review_pending"


def test_gateway_worker_uses_approved_scopes_without_double_charging_opening(graph):
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway
    from forge_core.runtime import ModelCallContext

    store, quest, plan = graph
    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        budget_ledger=ledger,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"summary":"done"}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )
        ),
    )
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": gateway}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    assert run(manager.run(session["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        row = connection.execute(
            "SELECT input_charged,output_charged FROM model_budget_attempts"
        ).fetchone()
        assert tuple(row) == (10, 5)
        assert connection.execute("SELECT COUNT(*) FROM model_budget_opening").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT token_spent FROM worker_runs WHERE id=?", (session["id"],)
            ).fetchone()[0]
            == 15
        )
    restarted = ModelCallBudgetLedger(store.path, token_limit=25, concurrency_limit=1)
    restarted.admit(
        ModelCallContext("after-restart", "router", store.workspace.id),
        "p",
        "m",
        input_tokens=5,
        output_tokens=5,
        remote_timeout=30,
    )


def test_ledger_uses_only_current_approved_quest_revision(graph):
    from forge_core.model_call_budget import BudgetExceeded, ModelCallBudgetLedger
    from forge_core.runtime import ModelCallContext

    store, quest, _ = graph
    ledger = ModelCallBudgetLedger(store.path, token_limit=10_000_000, concurrency_limit=1)
    context = ModelCallContext("planner", "planner", store.workspace.id, quest_id=quest.id)
    with store.connection() as connection:
        cap = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE quest_id=? AND status='approved'",
                (quest.id,),
            ).fetchone()[0]
        )["max_total_tokens"]
    with pytest.raises(BudgetExceeded):
        ledger.admit(context, "p", "m", input_tokens=cap, output_tokens=1, remote_timeout=30)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (quest.id,)
        )
    ledger.admit(context, "p", "m", input_tokens=cap, output_tokens=1, remote_timeout=30)


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_two_real_gateway_workers_wait_without_spending_extra_steps(
    graph, cancel_waiter, monkeypatch
):
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway

    store, first_quest, first_plan = graph
    second_quest = Quest(title="Second", goal="Implement second task")
    store.save_quest(second_quest)
    service = QuestGraphService(store)
    second_plan = service.propose(
        second_quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": "Implement",
                    "success_criteria": ["verified"],
                    "budget_tokens": 4000,
                }
            ],
            "edges": [],
        },
        expected_version=0,
    )
    service.approve(
        second_quest.id,
        second_plan["digest"],
        second_plan["plan_revision"],
        expected_version=1,
        actor="local_user",
    )

    class Teams:
        def get_approved(self, quest_id, plan_revision, proposal_digest, *, connection):
            plan = first_plan if quest_id == first_quest.id else second_plan
            team = MockApprovedTeam(plan).get_approved(
                quest_id, plan_revision, "team", connection=connection
            )
            team["proposal_digest"] = proposal_digest
            return team

    active = 0
    peak = 0
    entered = None

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        await asyncio.sleep(0.2)
        active -= 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"summary":"done"}'}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            concurrency=2,
        ),
        credential_resolver=lambda _: "fixture",
        budget_ledger=ledger,
        transport=httpx.MockTransport(handler),
    )
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": gateway}), MockToolEngine(), team_authority=Teams()
    )
    first = manager.start(
        first_quest.id, first_plan["id"], "n", team_digest="first", member_index=0
    )
    second = manager.start(
        second_quest.id, second_plan["id"], "n", team_digest="second", member_index=0
    )

    async def both():
        nonlocal entered
        entered = asyncio.Event()
        first_task = asyncio.create_task(manager.run(first["id"]))
        await entered.wait()
        if cancel_waiter:
            waiting = asyncio.Event()

            async def wait_before_send(**_kwargs):
                waiting.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(gateway, "_pace", wait_before_send)
        second_task = asyncio.create_task(manager.run(second["id"]))
        if cancel_waiter:
            await waiting.wait()
            second_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second_task
            return [await first_task]
        first_result, queued = await asyncio.gather(first_task, second_task)
        assert queued["state"] == "queued"
        # Busy workers now persistently yield their lease; the scheduler retries later.
        await asyncio.sleep(1.05)
        return [first_result, await manager.run(second["id"])]

    assert [item["state"] for item in run(both())] == (
        ["review_pending"] if cancel_waiter else ["review_pending", "review_pending"]
    )
    assert peak == 1
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == (
            1 if cancel_waiter else 2
        )
        assert connection.execute(
            "SELECT steps,token_spent FROM worker_runs WHERE id=?", (second["id"],)
        ).fetchone()[:] == ((0, 0) if cancel_waiter else (1, 15))


def test_worker_busy_requeues_persistently_without_spend_across_restart(graph, monkeypatch):
    import time

    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway
    from forge_core.runtime import ModelCallContext

    store, quest, plan = graph
    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)
    hold = ledger.admit(
        ModelCallContext("hold", "test", store.workspace.id),
        "p",
        "m",
        input_tokens=10,
        output_tokens=10,
        remote_timeout=30,
    )
    admissions = 0
    original_admit = ledger.admit

    def count_admission(*args, **kwargs):
        nonlocal admissions
        admissions += 1
        return original_admit(*args, **kwargs)

    monkeypatch.setattr(ledger, "admit", count_admission)
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        budget_ledger=ledger,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"summary":"done"}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )
        ),
    )
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": gateway}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
        lease_seconds=1,
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    waiting = run(manager.run(session["id"]))
    assert admissions == 1
    assert (
        waiting["state"] == "queued"
        and waiting["steps"] == 0
        and waiting["token_spent"] == 0
        and waiting["lease_token"] is None
    )
    restarted_store = Store(store.path, workspace=store.workspace)
    resumed = WorkerManager(
        restarted_store,
        ProviderPool({"mock-provider": gateway}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
        lease_seconds=1,
    )
    time.sleep(1.05)  # Slot remains occupied beyond the original lease and retry delay.
    assert run(resumed.run(session["id"]))["state"] == "queued"
    assert admissions == 2
    with store.connection() as connection:
        assert connection.execute(
            "SELECT steps,token_spent,lease_token,lease_until FROM worker_runs WHERE id=?",
            (session["id"],),
        ).fetchone()[:] == (0, 0, None, None)
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 1

    async def complete():
        await asyncio.sleep(1.05)
        ledger.settle(hold, {"input_tokens": 1, "output_tokens": 1})
        return await resumed.run(session["id"])

    assert run(complete())["state"] == "review_pending"
    assert admissions == 3
    with restarted_store.connection() as connection:
        assert connection.execute(
            "SELECT steps,token_spent FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()[:] == (1, 15)
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 2


@pytest.mark.parametrize("blocked", ["semaphore", "rate", "credentials", "cancel"])
def test_gateway_unsent_worker_reservation_is_refunded(graph, blocked, monkeypatch):
    import time

    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway

    store, quest, plan = graph
    sent = []
    observations = []
    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            concurrency=1,
        ),
        credential_resolver=lambda _: None if blocked == "credentials" else "fixture",
        budget_ledger=ledger,
        observer=lambda *args: observations.append(args),
        transport=httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(500)),
    )
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": gateway}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
        lease_seconds=0.3,
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)

    async def exercise():
        if blocked == "semaphore":
            await gateway._semaphore.acquire()
        if blocked == "cancel":

            async def wait_before_send(**_kwargs):
                await asyncio.Event().wait()

            monkeypatch.setattr(gateway, "_pace", wait_before_send)
        if blocked == "rate":
            gateway._next_request = time.monotonic() + 2
        task = asyncio.create_task(manager.run(session["id"]))
        if blocked == "cancel":
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task

    run(exercise())
    assert not sent
    if blocked in {"semaphore", "rate"}:
        assert observations == []
        assert gateway.health()["requests"] == 0
    with store.connection() as connection:
        assert connection.execute(
            "SELECT steps,token_spent FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()[:] == (0, 0)
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 0
        if blocked in {"semaphore", "rate"}:
            row = connection.execute(
                "SELECT state,lease_token,lease_until FROM worker_runs WHERE id=?", (session["id"],)
            ).fetchone()
            assert row[:] == ("queued", None, None)


@pytest.mark.parametrize("blocked", ["semaphore", "rate"])
def test_local_pressure_resumes_after_restart_without_duplicate_charge(graph, blocked):
    import time

    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway

    store, quest, plan = graph
    sent = []
    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            concurrency=1,
        ),
        credential_resolver=lambda _: "fixture",
        budget_ledger=ledger,
        transport=httpx.MockTransport(
            lambda request: (
                sent.append(request)
                or httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": '{"summary":"done"}'}, "finish_reason": "stop"}
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    },
                )
            )
        ),
    )
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": gateway}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
        lease_seconds=2,
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)

    async def exercise():
        if blocked == "semaphore":
            await gateway._semaphore.acquire()
        else:
            gateway._next_request = time.monotonic() + 5
        assert (await manager.run(session["id"]))["state"] == "queued"
        assert not sent
        if blocked == "semaphore":
            gateway._semaphore.release()
        gateway._next_request = 0
        await asyncio.sleep(1.05)
        restarted = WorkerManager(
            Store(store.path, workspace=store.workspace),
            ProviderPool({"mock-provider": gateway}),
            MockToolEngine(),
            team_authority=MockApprovedTeam(plan),
            lease_seconds=2,
        )
        assert (await restarted.run(session["id"]))["state"] == "review_pending"

    run(exercise())
    assert len(sent) == 1
    with store.connection() as connection:
        assert connection.execute(
            "SELECT steps,token_spent FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()[:] == (1, 15)
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 1


def test_busy_response_after_same_lease_expiry_still_refunds_and_queues(graph, monkeypatch):
    import time

    from forge_core.model_call_budget import ConcurrencyUnavailable, ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway

    store, quest, plan = graph
    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)

    def slow_admit(*_args, **_kwargs):
        time.sleep(0.4)
        raise ConcurrencyUnavailable()

    monkeypatch.setattr(ledger, "admit", slow_admit)
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        budget_ledger=ledger,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": gateway}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
        lease_seconds=0.3,
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    result = run(manager.run(session["id"]))
    assert (result["state"], result["steps"], result["token_spent"], result["lease_token"]) == (
        "queued",
        0,
        0,
        None,
    )


def test_approval_resume_does_not_consume_retry_after_busy_and_pause(graph):
    import time

    from forge_core.domain import DomainError
    from forge_core.governance import GovernanceService
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway
    from forge_core.runtime import ModelCallContext
    from forge_core.tool_engine import ToolEngine

    store, quest, plan = graph
    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)
    hold = ledger.admit(
        ModelCallContext("hold", "test", store.workspace.id),
        "p",
        "m",
        input_tokens=1,
        output_tokens=1,
        remote_timeout=30,
    )
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        budget_ledger=ledger,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    clock = [time.time()]
    tools = ToolEngine(store, network_hosts=["example.test"])
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": gateway}),
        tools,
        team_authority=MockApprovedTeam(plan, tools=["browser_get"]),
        clock=lambda: clock[0],
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    for _ in range(4):
        assert run(manager.run(session["id"]))["state"] == "queued"
        clock[0] += 1.1
    manager.control(session["id"], "pause")
    manager.control(session["id"], "resume")
    params = {"url": "https://example.test/item"}
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='blocked',awaiting_authorization=1 WHERE id=?",
            (session["id"],),
        )
        work.connection.execute(
            "UPDATE workers SET status='blocked' WHERE id=?", (session["worker_id"],)
        )
        work.connection.execute(
            "UPDATE task_nodes SET status='blocked' WHERE plan_id=? AND node_key='n'", (plan["id"],)
        )
        work.connection.execute(
            "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) VALUES (?,?,?,?,?,'pending')",
            (session["id"], "r2-call", "r2-call", "browser_get", json.dumps(params)),
        )
    approval = GovernanceService(store).request_approval(
        quest.id, "browser_get", params, params["url"], "R2", "review"
    )
    with pytest.raises(DomainError):
        manager.retry(session["id"])
    GovernanceService(store).decide_approval(approval["id"], "APPROVED", "local_user")
    assert manager.retry(session["id"])["state"] == "queued"
    with store.connection() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM events WHERE event_type='worker.retry' ORDER BY seq DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert payload["retry_kind"] == "authorization"
    ledger.settle(hold, {"input_tokens": 1, "output_tokens": 1})


def test_real_r2_approval_after_four_busy_claims_resumes_once(tmp_path, monkeypatch):
    from forge_core.domain import ModelProfile, ProtocolSupport
    from forge_core.governance import GovernanceService
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway
    from forge_core.runtime import ModelCallContext
    from forge_core.team_composition import TeamCompositionService
    from forge_core.tool_engine import ToolEngine

    store = Store(tmp_path / "worker.sqlite3", workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="R2", goal="approved fetch")
    store.save_quest(quest)
    graph = QuestGraphService(store)
    plan = graph.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": "fetch",
                    "success_criteria": ["done"],
                    "budget_tokens": 16000,
                    "allowed_tools": ["browser_get"],
                }
            ],
            "edges": [],
            "max_total_tokens": 16000,
        },
        expected_version=0,
    )
    graph.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    store.save_model(
        ModelProfile(
            id="fixture",
            provider="mock-provider",
            model_id="mock-model",
            display_name="fixture",
            context_window=32000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    teams = TeamCompositionService(store)
    team = teams.propose(
        quest.id,
        [
            {
                "id": "coding",
                "task_node_key": "n",
                "tools": ["browser_get"],
                "input_tokens": 9000,
                "output_tokens": 3000,
                "max_steps": 4,
                "max_tool_calls": 2,
            }
        ],
        {
            "max_members": 1,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 12000,
            "max_total_tool_calls": 2,
            "allowed_tools": ["browser_get"],
        },
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(
        quest.id,
        plan["plan_revision"],
        team["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    ledger = ModelCallBudgetLedger(store.path, token_limit=50000, concurrency_limit=1)
    hold = ledger.admit(
        ModelCallContext("hold", "test", store.workspace.id),
        "p",
        "m",
        input_tokens=1,
        output_tokens=1,
        remote_timeout=30,
    )
    calls = []

    def model(request):
        calls.append(request)
        message = (
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "fetch",
                        "type": "function",
                        "function": {
                            "name": "browser_get",
                            "arguments": '{"url":"https://example.test/item"}',
                        },
                    }
                ],
            }
            if len(calls) == 1
            else {"content": '{"summary":"done"}'}
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls" if len(calls) == 1 else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        budget_ledger=ledger,
        transport=httpx.MockTransport(model),
    )
    tools = ToolEngine(store, network_hosts=["example.test"])
    monkeypatch.setattr(
        tools,
        "_network_request",
        lambda *_: {
            "status_code": 200,
            "body": "fixture",
            "sha256": __import__("hashlib").sha256(b"fixture").hexdigest(),
        },
    )
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": gateway}), tools, team_authority=teams
    )
    session = manager.start(
        quest.id, plan["id"], "n", team_digest=team["proposal_digest"], member_index=0
    )
    for _ in range(4):
        assert run(manager.run(session["id"]))["state"] == "queued"
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE worker_runs SET next_attempt_at=0 WHERE id=?", (session["id"],)
            )
    ledger.settle(hold, {"input_tokens": 1, "output_tokens": 1})
    assert run(manager.run(session["id"]))["state"] == "blocked"
    with store.connection() as connection:
        approval_id = connection.execute(
            "SELECT id FROM approvals WHERE status='PENDING'"
        ).fetchone()[0]
        assert (
            connection.execute(
                "SELECT state FROM worker_tool_calls WHERE run_id=?", (session["id"],)
            ).fetchone()[0]
            == "pending"
        )
    GovernanceService(store).decide_approval(approval_id, "APPROVED", "local_user")
    assert manager.retry(session["id"])["state"] == "queued"
    assert run(manager.run(session["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
        assert connection.execute(
            "SELECT steps,token_spent FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()[:] == (2, 30)
    assert len(calls) == 2


def test_failure_retry_count_is_bounded_without_resetting_spend(graph):
    from forge_core.domain import DomainError

    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    for _ in range(2):
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE worker_runs SET state='blocked',token_spent=7 WHERE id=?", (session["id"],)
            )
        assert manager.retry(session["id"])["token_spent"] == 7
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='blocked' WHERE id=?", (session["id"],)
        )
    with pytest.raises(DomainError):
        manager.retry(session["id"])


@pytest.mark.parametrize("case", ["stale_lease", "sent_timeout"])
def test_worker_reservation_is_not_refunded_after_ownership_or_post_changes(
    graph, case, monkeypatch
):
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.providers import ProviderConfig, ProviderGateway

    store, quest, plan = graph
    entered = None

    async def handler(_request):
        entered.set()
        await asyncio.sleep(2)
        return httpx.Response(500)

    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            concurrency=1,
        ),
        credential_resolver=lambda _: "fixture",
        budget_ledger=ledger,
        transport=httpx.MockTransport(handler),
    )
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": gateway}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
        lease_seconds=0.3,
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)

    async def exercise():
        nonlocal entered
        entered = asyncio.Event()
        if case == "stale_lease":

            async def wait_before_send(**_kwargs):
                entered.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(gateway, "_pace", wait_before_send)
        task = asyncio.create_task(manager.run(session["id"]))
        if case == "stale_lease":
            await entered.wait()
            with store.unit_of_work() as work:
                work.connection.execute(
                    "UPDATE worker_runs SET lease_token='new-owner' WHERE id=?", (session["id"],)
                )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await entered.wait()
            await task

    run(exercise())
    with store.connection() as connection:
        steps, spent = connection.execute(
            "SELECT steps,token_spent FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()
        assert steps == 1 and spent > 0
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == (
            case == "sent_timeout"
        )


@pytest.mark.parametrize("legacy", [False, True])
def test_gateway_worker_with_approved_currency_limit(graph, legacy):
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.provider_pool import PriceBound
    from forge_core.providers import ProviderConfig, ProviderGateway

    store, quest, plan = graph
    price = PriceBound("mock-provider", "mock-model", "USD", 1_000, 2_000, "v1", True, True)
    bounds = {("mock-provider", "mock-model"): price}
    ledger = ModelCallBudgetLedger(
        store.path,
        token_limit=10000,
        concurrency_limit=1,
        price_bounds=bounds,
        scope_cost_reconciliations={("quest", quest.id, "USD"): 3} if legacy else None,
    )
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        budget_ledger=ledger,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"summary":"done"}'}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )
        ),
    )
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": gateway}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan, cost_cap=100),
        price_bounds=bounds,
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    if legacy:
        with store.unit_of_work() as work:
            work.connection.execute(
                "INSERT INTO model_budget_legacy_quests VALUES (?)", (quest.id,)
            )
            work.connection.execute(
                "INSERT INTO model_budget_opening VALUES (?,?,?,?,?,?)",
                (session["id"], store.workspace.id, quest.id, 10, 2, "USD"),
            )
            work.connection.execute(
                "UPDATE worker_runs SET token_spent=10,cost_spent_microunits=2 WHERE id=?",
                (session["id"],),
            )
    assert run(manager.run(session["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        assert connection.execute(
            "SELECT currency,cost_charged FROM model_budget_attempts"
        ).fetchone()[:] == ("USD", 1)


def test_v7_worker_opening_migrates_once_with_known_reserves(graph, tmp_path):
    import sqlite3

    from forge_core.migrations import migrate
    from forge_core.provider_pool import PriceBound

    store, quest, plan = graph
    bound = PriceBound("mock-provider", "mock-model", "USD", 1000, 2000, "v1", True, True)
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan, cost_cap=100),
        price_bounds={("mock-provider", "mock-model"): bound},
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET token_spent=12,cost_spent_microunits=3 WHERE id=?",
            (session["id"],),
        )
    path = tmp_path / "v7-copy.sqlite"
    with store.connection() as source, sqlite3.connect(path, isolation_level=None) as target:
        source.backup(target)
        for table in (
            "worker_core_source_reads",
            "worker_model_reservations",
            "model_budget_attempts",
            "model_budget_metadata",
            "model_budget_opening",
            "model_provider_cooldowns",
            "model_budget_legacy_quests",
            "model_budget_scope_reconciliations",
            "context_archive_heads",
            "context_archive_sources",
            "context_archives",
            "memories",
            "revoked_memory_sources",
            "memory_source_index",
            "memory_source_cursor",
            "memory_terms",
            "restore_reviews",
            "model_budget_worker_teams",
            "semantic_test_runs",
            "constraint_records",
            "constraint_message_index",
            "constraint_index_cursor",
        ):
            target.execute(f"DROP TABLE {table}")
        for column in (
            "output_limit",
            "model_max_output_tokens",
            "model_context_window",
            "next_attempt_at",
            "repairs",
        ):
            target.execute(f"ALTER TABLE worker_runs DROP COLUMN {column}")
        _remove_post_v13_tables(target)
        target.execute("UPDATE schema_meta SET version=7")
        migrate(target)
        migrate(target)
        assert target.execute(
            "SELECT worker_run_id,quest_id,tokens,cost,currency FROM model_budget_opening"
        ).fetchall() == [(session["id"], quest.id, 12, 3, "USD")]
        assert target.execute("SELECT quest_id FROM model_budget_legacy_quests").fetchall() == [
            (quest.id,)
        ]


def test_v8_active_worker_enters_output_review_without_changing_team_or_budget(graph, tmp_path):
    import sqlite3

    from forge_core.domain import ModelProfile
    from forge_core.migrations import migrate
    from forge_core.team_composition import TeamCompositionService, _digest

    store, quest, plan = graph
    store.save_model(
        ModelProfile(
            id="legacy-profile",
            provider="mock-provider",
            model_id="mock-model",
            display_name="Legacy",
            context_window=16000,
            max_output_tokens=1000,
        )
    )
    teams = TeamCompositionService(store)
    team = teams.propose(
        quest.id,
        [{"id": "coding", "task_node_key": "n", "input_tokens": 2500, "output_tokens": 500}],
        {"max_members": 1, "max_depth": 1, "max_concurrency": 1, "max_total_tokens": 4000},
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(
        quest.id,
        plan["plan_revision"],
        team["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=teams,
    )
    session = manager.start(
        quest.id, plan["id"], "n", team_digest=team["proposal_digest"], member_index=0
    )
    path = tmp_path / "v8.sqlite"
    with (
        store.connection() as source,
        closing(sqlite3.connect(path, isolation_level=None)) as target,
    ):
        source.backup(target)
        original = target.execute(
            "SELECT id,body_json FROM team_proposals WHERE proposal_digest=?",
            (team["proposal_digest"],),
        ).fetchone()
        body = json.loads(original[1])
        for member in body["members"]:
            for key in ("output_tokens", "model_max_output_tokens", "model_context_window"):
                member.pop(key)
        digest = _digest(body)
        legacy_body = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        target.execute(
            "UPDATE team_proposals SET body_json=?,proposal_digest=? WHERE id=?",
            (legacy_body, digest, original[0]),
        )
        target.execute(
            "UPDATE worker_runs SET team_digest=?,token_spent=123 WHERE id=?",
            (digest, session["id"]),
        )
        for column in (
            "output_limit",
            "model_max_output_tokens",
            "model_context_window",
            "next_attempt_at",
            "repairs",
        ):
            target.execute(f"ALTER TABLE worker_runs DROP COLUMN {column}")
        target.execute("DROP TABLE restore_reviews")
        target.execute("DROP TABLE model_budget_worker_teams")
        target.execute("DROP TABLE semantic_test_runs")
        target.execute("DROP TABLE constraint_records")
        target.execute("DROP TABLE constraint_message_index")
        target.execute("DROP TABLE constraint_index_cursor")
        target.execute("DROP TABLE worker_model_reservations")
        target.execute("DROP TABLE worker_core_source_reads")
        _remove_post_v13_tables(target)
        target.execute("UPDATE schema_meta SET version=8")
        migrate(target)
        assert target.execute(
            "SELECT state,token_spent,output_limit FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone() == ("blocked", 123, None)
        assert target.execute(
            "SELECT body_json,proposal_digest,status FROM team_proposals WHERE id=?", (original[0],)
        ).fetchone() == (legacy_body, digest, "approved")
        assert any(
            "output_contract_review_required" in row[0]
            for row in target.execute(
                "SELECT payload FROM events WHERE event_type='worker.blocked'"
            )
        )


def test_restore_review_keeps_fence_for_unresolved_external_effect(graph, tmp_path, monkeypatch):
    from forge_core.workspace_backup import (
        BackupError,
        backup_workspace,
        restore_workspace,
        review_restore,
    )

    store, quest, plan = graph
    monkeypatch.setenv("FORGE_DB_PATH", store.path)
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO action_intents(id,call_id,quest_id,worker_id,run_id,plan_id,plan_revision,tool_name,parameters_json,parameter_digest,resource_scope,risk,execution_mode,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "intent-1",
                "call-1",
                quest.id,
                session["worker_id"],
                session["id"],
                plan["id"],
                plan["plan_revision"],
                "file_write",
                "{}",
                "a" * 64,
                "work/result",
                "R1",
                "mock",
                "uncertain",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
    archive = tmp_path.parent / f"{tmp_path.name}-backup"
    backup_workspace(tmp_path, archive)
    result = restore_workspace(tmp_path, archive)
    marker = json.loads((tmp_path / ".forge-restore-fence.json").read_text(encoding="utf-8"))
    with pytest.raises(BackupError, match="Unresolved"):
        review_restore(
            tmp_path,
            expected_manifest_sha256=marker["manifest_sha256"],
            expected_rollback_directory=result["rollback_directory"],
            reason="review requires external effect reconciliation",
        )
    assert (tmp_path / ".forge-restore-fence.json").exists()


def test_restore_review_keeps_post_snapshot_worker_team_usage_and_revision_watermark(
    graph, tmp_path, monkeypatch
):
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.runtime import ModelCallContext
    from forge_core.workspace_backup import backup_workspace, restore_workspace, review_restore

    store, quest, plan = graph
    monkeypatch.setenv("FORGE_DB_PATH", store.path)
    archive = tmp_path.parent / f"{tmp_path.name}-backup"
    backup_workspace(tmp_path, archive)
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    ledger = ModelCallBudgetLedger(store.path, token_limit=10000, concurrency_limit=1)
    call = ledger.admit(
        ModelCallContext(
            "worker-call",
            "worker",
            store.workspace.id,
            quest_id=quest.id,
            worker_run_id=session["id"],
        ),
        "mock-provider",
        "mock-model",
        input_tokens=100,
        output_tokens=50,
        remote_timeout=30,
    )
    ledger.settle(call, {"input_tokens": 90, "output_tokens": 20})
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET plan_revision=7,version=8 WHERE id=?", (quest.id,)
        )
    restored = restore_workspace(tmp_path, archive)
    marker = json.loads((tmp_path / ".forge-restore-fence.json").read_text(encoding="utf-8"))
    review_restore(
        tmp_path,
        expected_manifest_sha256=marker["manifest_sha256"],
        expected_rollback_directory=restored["rollback_directory"],
        reason="reviewed and retained later Worker usage",
    )
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM worker_runs WHERE id=?", (session["id"],)
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT team_digest FROM model_budget_worker_teams WHERE worker_run_id=?",
                (session["id"],),
            ).fetchone()[0]
            == "team"
        )
        assert (
            connection.execute(
                "SELECT SUM(input_charged+output_charged) FROM model_budget_attempts WHERE worker_run_id IN (SELECT worker_run_id FROM model_budget_worker_teams WHERE team_digest=?)",
                ("team",),
            ).fetchone()[0]
            == 110
        )
        assert connection.execute(
            "SELECT plan_revision,version FROM quests WHERE id=?", (quest.id,)
        ).fetchone()[:] == (8, 9)


def test_restore_review_cancels_snapshot_workers_and_requires_new_plan(
    graph, tmp_path, monkeypatch
):
    from forge_core.workspace_backup import backup_workspace, restore_workspace, review_restore

    store, quest, plan = graph
    monkeypatch.setenv("FORGE_DB_PATH", store.path)
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    archive = tmp_path.parent / f"{tmp_path.name}-backup"
    backup_workspace(tmp_path, archive)
    restored = restore_workspace(tmp_path, archive)
    marker = json.loads((tmp_path / ".forge-restore-fence.json").read_text(encoding="utf-8"))
    review_restore(
        tmp_path,
        expected_manifest_sha256=marker["manifest_sha256"],
        expected_rollback_directory=restored["rollback_directory"],
        reason="reviewed old approved Worker and plan",
    )
    with store.connection() as connection:
        assert connection.execute(
            "SELECT state,lease_token FROM worker_runs WHERE id=?", (session["id"],)
        ).fetchone()[:] == ("cancelled", None)
        assert (
            connection.execute(
                "SELECT status FROM quest_plans WHERE id=?", (plan["id"],)
            ).fetchone()[0]
            == "superseded"
        )
        assert (
            connection.execute("SELECT status FROM quests WHERE id=?", (quest.id,)).fetchone()[0]
            == "needs_input"
        )


def test_timeline_projects_failed_tool_receipt_without_private_output(graph):
    import hashlib

    from forge_core.timeline import read_timeline

    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    result = json.dumps({"exit_code": 1, "stderr": "private-output", "stdout": "private-output"})
    digest = hashlib.sha256(result.encode()).hexdigest()
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO action_intents(id,call_id,quest_id,worker_id,run_id,plan_id,plan_revision,tool_name,parameters_json,parameter_digest,resource_scope,risk,execution_mode,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "intent-1",
                "call-1",
                quest.id,
                session["worker_id"],
                session["id"],
                plan["id"],
                plan["plan_revision"],
                "shell_inspect",
                '{"operation":"python_version","private":"never-show"}',
                "a" * 64,
                "process:diagnostic",
                "R0",
                "mock",
                "failed",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        work.connection.execute(
            "INSERT INTO action_receipts VALUES (?,?,?,?,?,?)",
            ("receipt-1", "intent-1", "mock", result, digest, "2026-01-01T00:00:01+00:00"),
        )
    timeline = read_timeline(store)
    receipt = next(item for item in timeline["items"] if item["kind"] == "tool_receipt")
    assert receipt["status"] == "failed" and receipt["execution_mode"] == "mock"
    assert receipt["detail"] == {
        "tool": "shell_inspect",
        "result_sha256": digest,
        "exit_code": 1,
        "command": "python -I --version",
    }
    assert "private-output" not in json.dumps(timeline) and "never-show" not in json.dumps(timeline)


@pytest.mark.parametrize(
    "arguments,expected,tamper",
    [
        ({"ordinal": 2}, "review_pending", False),
        ({"ordinal": 999}, "blocked", False),
        ({"ordinal": True}, "blocked", False),
        ({"ordinal": 2, "run_id": "other"}, "blocked", False),
        ({"ordinal": 2, "path": "secret"}, "blocked", False),
        ({"ordinal": 2}, "blocked", True),
    ],
)
def test_worker_reads_only_archived_own_context_and_charges_tool(
    graph, arguments, expected, tamper, monkeypatch
):
    from forge_core.context_service import ContextService

    store, quest, plan = graph

    class Reader:
        execution_mode = "mock"

        def __init__(self):
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                assert any(tool["name"] == "worker_read_context_source" for tool in request.tools)
                return ModelResponse(
                    text="",
                    tool_calls=[
                        {
                            "id": "read-archive",
                            "name": "worker_read_context_source",
                            "arguments": arguments,
                        }
                    ],
                    usage={"input_tokens": 10, "output_tokens": 5},
                )
            assert request.messages[-1]["role"] == "tool"
            assert "archived-marker" in request.messages[-1]["content"]
            return ModelResponse(
                text='{"summary":"done"}',
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    provider = Reader()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": provider}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.unit_of_work() as work:
        for index in range(4):
            message = {
                "role": "assistant",
                "content": "archived-marker " + "x" * (2000 if index == 1 else 10),
            }
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (session["id"], index + 2, "assistant", json.dumps(message)),
            )
    ContextService(store).build_worker_context(session["id"], input_budget=2500)
    if tamper:
        import forge_core.worker_runtime as runtime

        original = runtime._read_worker_source_slice

        def race(store, run_id, args, *, connection=None):
            result = original(store, run_id, args, connection=connection)
            if connection is None:
                with store.unit_of_work() as work:
                    work.connection.execute(
                        "UPDATE worker_messages SET message_json='tampered' WHERE run_id=? AND ordinal=2",
                        (run_id,),
                    )
            return result

        monkeypatch.setattr(runtime, "_read_worker_source_slice", race)
    result = run(manager.run(session["id"]))
    assert result["state"] == expected, [event["payload"] for event in store.list_events()]
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT tool_spent FROM worker_runs WHERE id=?", (session["id"],)
            ).fetchone()[0]
            == 1
        )
        tool = connection.execute(
            "SELECT state,receipt_id FROM worker_tool_calls WHERE run_id=? AND model_call_id='read-archive'",
            (session["id"],),
        ).fetchone()
        assert tool[:] == (
            ("succeeded", None) if expected == "review_pending" else ("failed", None)
        )
        if expected == "review_pending":
            from forge_core.evidence import _verify_worker_context_call

            call = connection.execute(
                "SELECT * FROM worker_tool_calls WHERE run_id=? AND model_call_id='read-archive'",
                (session["id"],),
            ).fetchone()
            assert _verify_worker_context_call(store, connection, session["id"], call)
            connection.execute(
                "UPDATE worker_tool_calls SET result_json='{}' WHERE run_id=? AND model_call_id='read-archive'",
                (session["id"],),
            )
            call = connection.execute(
                "SELECT * FROM worker_tool_calls WHERE run_id=? AND model_call_id='read-archive'",
                (session["id"],),
            ).fetchone()
            assert not _verify_worker_context_call(store, connection, session["id"], call)


def test_worker_source_slices_reassemble_large_unicode_message(graph):
    import hashlib

    from forge_core.context_service import ContextService
    from forge_core.domain import DomainError
    from forge_core.worker_runtime import _read_worker_source_slice

    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    original = json.dumps({"role": "assistant", "content": "中文🙂" * 6000}, ensure_ascii=False)
    with store.unit_of_work() as work:
        for index, message in enumerate(
            (
                original,
                json.dumps({"role": "assistant", "content": "old"}),
                json.dumps({"role": "assistant", "content": "recent"}),
                json.dumps({"role": "assistant", "content": "recent"}),
            ),
            2,
        ):
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (session["id"], index, "assistant", message),
            )
    ContextService(store).build_worker_context(session["id"], input_budget=3500)
    parts = []
    offset = 0
    while True:
        result = _read_worker_source_slice(
            store, session["id"], {"ordinal": 2, "offset_chars": offset, "max_chars": 2048}
        )
        assert len(result["content"].encode("utf-8")) <= 8192
        assert result["source_sha256"] == hashlib.sha256(original.encode("utf-8")).hexdigest()
        parts.append(result["content"])
        if result["complete"]:
            break
        offset = result["next_offset_chars"]
    assert "".join(parts) == original and len(original.encode("utf-8")) > 16384
    with pytest.raises(DomainError):
        _read_worker_source_slice(store, "other-run", {"ordinal": 2})
    with pytest.raises(DomainError):
        _read_worker_source_slice(store, session["id"], {"ordinal": 5})


def test_unknown_usage_reserves_budget_fail_closed(graph):
    store, quest, plan = graph

    class Unknown:
        execution_mode = "mock"

        async def generate(self, request):
            return ModelResponse(text="", tool_calls=[], usage={})

    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": Unknown()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan, tokens=32),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    result = run(manager.run(session["id"]))
    assert result["state"] in {"blocked", "failed"}
    assert store.get_quest(quest.id)["status"] != "completed"


def test_cancel_fences_inflight_model_and_does_not_report_result(graph):
    store, quest, plan = graph

    class Slow:
        execution_mode = "mock"

        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def generate(self, request):
            self.started.set()
            await self.release.wait()
            return ModelResponse(
                text="done", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1}
            )

    async def scenario():
        provider = Slow()
        manager = WorkerManager(
            store,
            ProviderPool({"mock-provider": provider}),
            MockToolEngine(),
            team_authority=MockApprovedTeam(plan),
        )
        session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
        running = asyncio.create_task(manager.run(session["id"]))
        await provider.started.wait()
        manager.control(session["id"], "cancel")
        provider.release.set()
        result = await running
        assert result["state"] == "cancelled"

    run(scenario())
    assert not any(event["event_type"] == "worker.result" for event in store.list_events())


def test_cost_capped_worker_requires_operator_bound_and_reserves_atomically(graph):
    from forge_core.domain import DomainError
    from forge_core.provider_pool import PriceBound

    store, quest, plan = graph

    class Model:
        execution_mode = "mock"

        async def generate(self, request):
            assert request.max_retries == 0
            return ModelResponse(
                text='{"summary":"done"}',
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    authority = MockApprovedTeam(plan, cost_cap=5000)
    pool = ProviderPool({"mock-provider": Model()})
    with pytest.raises(DomainError, match="price bounds"):
        WorkerManager(store, pool, MockToolEngine(), team_authority=authority).start(
            quest.id, plan["id"], "n", team_digest="team", member_index=0
        )
    bound = PriceBound(
        provider_id="mock-provider",
        model_id="mock-model",
        currency="USD",
        input_microunits_per_million=1000000,
        output_microunits_per_million=2000000,
        version="test-v1",
        covers_all_input_cache_and_fees=True,
        covers_all_output_and_fees=True,
    )
    manager = WorkerManager(
        store,
        pool,
        MockToolEngine(),
        team_authority=authority,
        price_bounds={(bound.provider_id, bound.model_id): bound},
        global_cost_limits={"USD": 5000},
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    result = run(manager.run(session["id"]))
    assert result["state"] == "review_pending"
    assert result["cost_spent_microunits"] == 20
    with store.connection() as connection:
        ledger = connection.execute(
            "SELECT reserved_microunits FROM worker_cost_ledger ORDER BY scope"
        ).fetchall()
        assert [entry[0] for entry in ledger] == [20, 20]


def test_expired_running_tool_is_uncertain_and_never_replayed(graph):
    store, quest, plan = graph
    tool = MockToolEngine()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        tool,
        team_authority=MockApprovedTeam(plan, tools=["read"]),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='running',lease_until=0,lease_token='old' WHERE id=?",
            (session["id"],),
        )
        work.connection.execute(
            "INSERT INTO worker_tool_calls VALUES (?,?,?,?,?,'running',NULL,NULL)",
            (session["id"], "uncertain-call", "old-model-call", "read", '{"path":"input.txt"}'),
        )
    outcome = run(manager.run(session["id"]))
    assert outcome["state"] == "blocked"
    assert tool.calls == []
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM worker_tool_calls").fetchone()[0] == "uncertain"
        )
        assert (
            connection.execute(
                "SELECT status FROM workers WHERE id=?", (session["worker_id"],)
            ).fetchone()[0]
            == "blocked"
        )
        assert connection.execute("SELECT status FROM task_nodes").fetchone()[0] == "blocked"


def test_worker_durable_inbox_deduplicates_and_injects_before_model(graph):
    store, quest, plan = graph

    class InboxModel:
        execution_mode = "mock"

        async def generate(self, request):
            assert sum("dependency-receipt" in item["content"] for item in request.messages) == 1
            return ModelResponse(
                text='{"summary":"done"}',
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": InboxModel()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    assert manager.deliver(session["id"], "event-one", {"receipt": "dependency-receipt"}) is True
    assert manager.deliver(session["id"], "event-one", {"receipt": "dependency-receipt"}) is False
    assert run(manager.run(session["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        assert connection.execute("SELECT consumed FROM worker_inbox").fetchone()[0] == 1


def test_duplicate_model_tool_id_blocks_all_entities(graph):
    store, quest, plan = graph

    class Duplicate(ScriptedMock):
        async def generate(self, request):
            return ModelResponse(
                text="",
                tool_calls=[{"id": "same", "name": "read", "arguments": {}}],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    tools = MockToolEngine()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": Duplicate()}),
        tools,
        team_authority=MockApprovedTeam(plan, tools=["read"]),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    assert run(manager.run(session["id"]))["state"] == "blocked"
    assert len(tools.calls) == 1
    with store.connection() as connection:
        assert connection.execute("SELECT status FROM workers").fetchone()[0] == "blocked"
        assert connection.execute("SELECT status FROM task_nodes").fetchone()[0] == "blocked"


def test_pause_resume_recovers_messages_and_budget_without_reallocation(graph):
    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan, tools=["read"]),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    manager.control(session["id"], "pause")
    with store.connection() as connection:
        before = [tuple(row) for row in connection.execute("SELECT * FROM worker_budget_ledger")]
    manager.control(session["id"], "resume")
    assert run(manager.run(session["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM worker_budget_ledger")
        ] == before


def test_deadline_timeout_synchronizes_states_and_keeps_reservation(graph):
    store, quest, plan = graph
    clock = [100.0]

    class Expiring:
        execution_mode = "mock"

        async def generate(self, request):
            clock[0] = 200.0
            raise TimeoutError

    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": Expiring()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
        clock=lambda: clock[0],
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    result = run(manager.run(session["id"]))
    assert result["state"] == "blocked" and result["token_spent"] > 0
    with store.connection() as connection:
        assert connection.execute("SELECT status FROM workers").fetchone()[0] == "blocked"
        assert connection.execute("SELECT status FROM task_nodes").fetchone()[0] == "blocked"


def test_help_is_bounded_and_retry_cannot_replay_uncertain_tools(graph):
    from forge_core.domain import DomainError

    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    manager.request_help(session["id"], "reviewer", "Need independent check")
    manager.request_help(session["id"], "reviewer", "Need independent check")
    with pytest.raises(DomainError, match="limit"):
        manager.request_help(session["id"], "reviewer", "Need independent check")
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='blocked' WHERE id=?", (session["id"],)
        )
        work.connection.execute(
            "INSERT INTO worker_tool_calls VALUES (?,?,?,?,?,'uncertain',NULL,NULL)",
            (session["id"], "x", "x", "read", "{}"),
        )
    with pytest.raises(DomainError, match="Unresolved"):
        manager.retry(session["id"])


def test_scheduler_scans_past_32_invalid_teams_without_starving_ready_work(graph):
    import json

    from forge_core.worker_runtime import WorkerScheduler

    store, quest, plan = graph
    authority = MockApprovedTeam(plan, tools=["read"])
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=authority,
    )
    with store.unit_of_work() as work:
        for index in range(33):
            body = (
                "broken-json" if index < 32 else json.dumps({"members": [{"task_node_key": "n"}]})
            )
            digest = f"invalid-{index}" if index < 32 else "team"
            work.connection.execute(
                "INSERT INTO team_proposals VALUES (?,?,?,?,?,'approved',?,?,?,?)",
                (
                    f"team-{index:03}",
                    quest.id,
                    plan["plan_revision"],
                    plan["digest"],
                    digest,
                    body,
                    "now",
                    "local_user",
                    "now",
                ),
            )

    async def scenario():
        scheduler = WorkerScheduler(manager)
        assert await scheduler.tick() == 0
        assert scheduler.last_failure_code == "invalid_team_checkpoint"
        assert await scheduler.tick() == 1
        await asyncio.gather(*scheduler.tasks.values())
        await scheduler.shutdown()

    run(scenario())
    with store.connection() as connection:
        assert connection.execute("SELECT state FROM worker_runs").fetchone()[0] == "review_pending"


def test_model_can_request_bounded_help_without_spawning_or_gaining_tools(graph):
    store, quest, plan = graph

    class HelpModel:
        execution_mode = "mock"

        async def generate(self, request):
            assert any(tool["name"] == "worker_request_help" for tool in request.tools)
            return ModelResponse(
                text="",
                tool_calls=[
                    {
                        "id": "help-one",
                        "name": "worker_request_help",
                        "arguments": {"role": "reviewer", "reason": "Need independent review"},
                    }
                ],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    tools = MockToolEngine()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": HelpModel()}),
        tools,
        team_authority=MockApprovedTeam(plan),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    result = run(manager.run(session["id"]))
    assert result["state"] == "blocked" and tools.calls == []
    # Known help responses are settled to actual usage; the request remains
    # blocked for Router coordination without charging a stale reservation.
    assert result["token_spent"] == 15
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM worker_runs").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='worker.help_requested'"
            ).fetchone()[0]
            == 1
        )


def test_two_managers_contend_sqlite_lease_only_one_charges_model(graph):
    from forge_core.domain import DomainError
    from forge_core.provider_pool import PriceBound

    store, quest, plan = graph

    class Model:
        execution_mode = "mock"

        def __init__(self):
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def generate(self, request):
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return ModelResponse(
                text='{"summary":"done"}',
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    async def scenario():
        model = Model()
        bound = PriceBound(
            provider_id="mock-provider",
            model_id="mock-model",
            currency="USD",
            input_microunits_per_million=1000000,
            output_microunits_per_million=2000000,
            version="test",
            covers_all_input_cache_and_fees=True,
            covers_all_output_and_fees=True,
        )
        kwargs = {
            "team_authority": MockApprovedTeam(plan, cost_cap=5000),
            "price_bounds": {("mock-provider", "mock-model"): bound},
            "global_cost_limits": {"USD": 5000},
        }
        first = WorkerManager(
            store, ProviderPool({"mock-provider": model}), MockToolEngine(), **kwargs
        )
        second = WorkerManager(
            store, ProviderPool({"mock-provider": model}), MockToolEngine(), **kwargs
        )
        session = first.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
        task = asyncio.create_task(first.run(session["id"]))
        await model.entered.wait()
        with pytest.raises(DomainError, match="claimable"):
            await second.run(session["id"])
        model.release.set()
        assert (await task)["cost_spent_microunits"] == 20
        assert model.calls == 1
        with store.connection() as connection:
            assert [
                row[0]
                for row in connection.execute("SELECT reserved_microunits FROM worker_cost_ledger")
            ] == [20, 20]

    run(scenario())


def test_global_cost_reservation_blocks_second_worker_before_provider_call(graph):
    from forge_core.provider_pool import PriceBound

    store, quest, plan = graph
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO task_nodes(plan_id,node_key,objective,criteria_json,budget_tokens,required_capabilities,expected_artifact,max_attempts,assigned_agent,worker_id,evidence_refs,status) SELECT plan_id,'n2',objective,criteria_json,budget_tokens,required_capabilities,expected_artifact,max_attempts,NULL,NULL,'[]','proposed' FROM task_nodes WHERE node_key='n'"
        )

    class Authority(MockApprovedTeam):
        def get_approved(self, *args, **kwargs):
            body = super().get_approved(*args, **kwargs)
            body["members"][0]["estimated_tokens"] = 2000
            body["members"].append({**body["members"][0], "task_node_key": "n2"})
            body["limits"]["max_members"] = 2
            return body

    class Model:
        execution_mode = "mock"

        def __init__(self):
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def generate(self, request):
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return ModelResponse(
                text='{"summary":"done"}',
                tool_calls=[],
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    async def scenario():
        model = Model()
        bound = PriceBound(
            provider_id="mock-provider",
            model_id="mock-model",
            currency="USD",
            input_microunits_per_million=1000000,
            output_microunits_per_million=2000000,
            version="v",
            covers_all_input_cache_and_fees=True,
            covers_all_output_and_fees=True,
        )
        kwargs = {
            "team_authority": Authority(plan, cost_cap=5000),
            "price_bounds": {("mock-provider", "mock-model"): bound},
            "global_cost_limits": {"USD": 2000},
        }
        first = WorkerManager(
            store, ProviderPool({"mock-provider": model}), MockToolEngine(), **kwargs
        )
        second = WorkerManager(
            store, ProviderPool({"mock-provider": model}), MockToolEngine(), **kwargs
        )
        a = first.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
        b = second.start(quest.id, plan["id"], "n2", team_digest="team", member_index=1)
        task = asyncio.create_task(first.run(a["id"]))
        await model.entered.wait()
        result = await second.run(b["id"])
        model.release.set()
        await task
        assert result["state"] == "blocked"
        assert model.calls == 1
        assert any(
            json.loads(event["payload"]).get("reason_code") == "cost_budget"
            for event in store.list_events()
        )

    run(scenario())


def test_scheduler_quest_pause_resume_preserves_manual_pause_and_cancel(graph):
    from forge_core.worker_runtime import WorkerScheduler

    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan, tools=["read"]),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)

    async def scenario():
        scheduler = WorkerScheduler(manager)
        with store.unit_of_work() as work:
            work.connection.execute("UPDATE quests SET status='paused' WHERE id=?", (quest.id,))
        await scheduler.tick()
        assert manager.get(session["id"])["state"] == "paused"
        assert manager.get(session["id"])["quest_paused"] == 1
        with store.unit_of_work() as work:
            work.connection.execute("UPDATE quests SET status='queued' WHERE id=?", (quest.id,))
        # Keep scheduler from launching while inspecting automatic resume.
        scheduler.tasks["occupied"] = asyncio.create_task(asyncio.Event().wait())
        manager.max_concurrency = 1
        await scheduler.tick()
        assert manager.get(session["id"])["state"] == "queued"
        manager.control(session["id"], "pause")
        await scheduler.tick()
        assert manager.get(session["id"])["state"] == "paused"
        with store.unit_of_work() as work:
            work.connection.execute("UPDATE quests SET status='cancelled' WHERE id=?", (quest.id,))
        await scheduler.tick()
        assert manager.get(session["id"])["state"] == "cancelled"
        with store.unit_of_work() as work:
            work.connection.execute("UPDATE quests SET status='queued' WHERE id=?", (quest.id,))
        await scheduler.tick()
        assert manager.get(session["id"])["state"] == "cancelled"
        await scheduler.shutdown()

    run(scenario())
