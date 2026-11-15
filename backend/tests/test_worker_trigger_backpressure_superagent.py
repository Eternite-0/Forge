"""A Worker finalizes only after its durable subscribed event debt is consumed."""

import asyncio
import json

from test_worker_runtime import (
    MockApprovedTeam,
    MockToolEngine,
    graph,  # noqa: F401 - real SQLite and approved legacy contract fixture.
)

from forge_core.domain import ModelProfile
from forge_core.provider_pool import ProviderPool
from forge_core.runtime import ModelResponse
from forge_core.superagent_events import TriggerService
from forge_core.worker_runtime import WorkerManager


def test_final_boundary_flushes_deferred_subscription_without_dispatcher(
    graph,  # noqa: F811 - pytest resolves the imported graph fixture.
    monkeypatch,
):
    store, quest, plan = graph
    # This regression exercises production's default cumulative token policy;
    # all context, step, tool and lease bounds remain in force.
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store.save_model(
        ModelProfile(
            id="backpressure-mock",
            display_name="Backpressure Mock",
            provider="mock-provider",
            model_id="mock-model",
            enabled=True,
            context_window=32768,
        )
    )
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "deferred", "topic", {"text": "deferred requirement"})

    class BackpressureMock:
        execution_mode = "mock"

        def __init__(self):
            self.requests = []

        async def generate(self, request):
            self.requests.append(request)
            prompt = json.dumps(request.messages)
            if len(self.requests) == 1:
                assert trigger["id"] not in prompt
            else:
                assert trigger["id"] in prompt and "deferred requirement" in prompt
                assert not any(
                    item["event_type"] == "worker.result" for item in store.list_events()
                )
            return ModelResponse(
                text=json.dumps({"summary": f"draft {len(self.requests)}"}),
                tool_calls=[],
                usage={"input_tokens": 20, "output_tokens": 10},
            )

    model = BackpressureMock()
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": model}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan, tools=["read"]),
    )
    session = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    for number in range(64):
        manager.deliver(session["id"], f"control-{number}", {"n": number})
    service.subscribe(trigger["thread_id"], session["id"])
    assert service.read_trigger(trigger["id"])["deliveries"] == []
    result = asyncio.run(manager.run(session["id"]))
    assert result["state"] == "review_pending", [
        json.loads(row["payload"])["reason_code"]
        for row in store.list_events()
        if row["event_type"] == "worker.blocked"
    ]
    assert len(model.requests) == 2
    assert service.flush_run(session["id"]) == {"delivered": 0, "pending": False}
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT consumed FROM worker_inbox WHERE run_id=? AND event_id=?",
                (session["id"], trigger["event_id"]),
            ).fetchone()[0]
            == 1
        )
    results = [row for row in store.list_events() if row["event_type"] == "worker.result"]
    assert (
        len(results) == 1 and json.loads(results[0]["payload"])["claim"] == '{"summary": "draft 2"}'
    )
    assert store.list_messages() == []
