"""Adversarial lifecycle tests; scripted mock models and synthetic ledger fixtures.

No Provider network requests run and no fixture is reported as live evidence.
"""

import asyncio
import json

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.independent_review import IndependentReviewService
from forge_core.providers import ProviderError
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager

PASS = {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}


def ready(tmp_path):
    store, manager, model, run = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery()), response(PASS)],
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    clock = [manager._now()]
    manager.clock = lambda: clock[0]
    return store, manager, model, run, clock


def events(store, kind):
    return [
        json.loads(row["payload"])
        for row in store.list_events(limit=1000)
        if row["event_type"] == kind
    ]


def attempt_fixture(store, request, *, state, recover_after):
    context = request.budget_context
    with store.unit_of_work() as work:
        work.connection.execute(
            """INSERT INTO model_budget_attempts(
            attempt_id,call_id,purpose,workspace_id,quest_id,worker_run_id,provider_id,model_id,
            input_reserved,output_reserved,state,created_at,recover_after,input_charged,output_charged)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "fixture-" + context.call_id,
                context.call_id,
                context.purpose,
                context.workspace_id,
                context.quest_id,
                context.worker_run_id,
                request.provider_id,
                request.model_id,
                1000,
                request.max_output_tokens,
                state,
                1.0,
                recover_after,
                25 if state == "settled" else None,
                15 if state == "settled" else None,
            ),
        )


def reopen(manager, clock):
    store = Store(manager.store.path, workspace=manager.store.workspace)
    return WorkerManager(
        store,
        manager.providers,
        ToolEngine(store),
        team_authority=TeamCompositionService(store),
        clock=lambda: clock[0],
    )


def test_unsent_reviewer_capacity_is_queued_without_consuming_semantic_round(tmp_path):
    store, manager, model, run, clock = ready(tmp_path)
    generate = model.generate
    busy = [True]

    async def capacity(request):
        if request.budget_context.purpose == "independent_reviewer" and busy[0]:
            request._budget_post_started = False
            busy[0] = False
            raise ProviderError("concurrency_unavailable")
        return await generate(request)

    model.generate = capacity
    before = manager.get(run["id"])
    reviewer = IndependentReviewService(manager, claim_seconds=10, retry_seconds=1)
    assert asyncio.run(reviewer.tick())
    after = manager.get(run["id"])
    assert after["state"] == "review_pending"
    assert (after["steps"], after["token_spent"]) == (before["steps"], before["token_spent"])
    assert events(store, "review.released")[-1]["unsent"] is True
    assert not asyncio.run(reviewer.tick())
    clock[0] += 2
    assert asyncio.run(reviewer.tick())
    assert events(store, "review.result")[-1]["verdict"] == "PASS"
    assert events(store, "review.started")[-1]["round"] == 1


@pytest.mark.parametrize("state,recover_after", [("settled", None), ("reserved", 0.0)])
def test_interrupted_review_restarts_only_after_settlement_or_trusted_remote_bound(
    tmp_path, state, recover_after
):
    store, manager, model, run, clock = ready(tmp_path)
    generate = model.generate
    first = [True]

    async def interrupted(request):
        if request.budget_context.purpose == "independent_reviewer" and first[0]:
            first[0] = False
            request._budget_post_started = True
            attempt_fixture(store, request, state=state, recover_after=recover_after)
            raise asyncio.CancelledError
        return await generate(request)

    model.generate = interrupted
    reviewer = IndependentReviewService(manager, claim_seconds=10, retry_seconds=1)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(reviewer.review(run["id"]))
    started = events(store, "review.started")[-1]
    assert started["lease_until"] > clock[0]
    reserved = manager.get(run["id"])["token_spent"]
    resumed = reopen(manager, clock)
    assert not asyncio.run(IndependentReviewService(resumed, claim_seconds=10).tick())
    clock[0] += 11
    assert asyncio.run(IndependentReviewService(resumed, claim_seconds=10).tick())
    assert events(store, "review.result")[-1]["verdict"] == "PASS"
    assert manager.get(run["id"])["token_spent"] > reserved
    assert len(events(store, "review.started")) == 2


def test_interrupted_review_with_unknown_remote_outcome_keeps_reservation_and_does_not_replay(
    tmp_path,
):
    store, manager, model, run, clock = ready(tmp_path)

    async def unknown(request):
        request._budget_post_started = True
        attempt_fixture(store, request, state="reserved", recover_after=None)
        raise asyncio.CancelledError

    model.generate = unknown
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(IndependentReviewService(manager, claim_seconds=10).review(run["id"]))
    reserved = manager.get(run["id"])
    clock[0] += 11
    resumed = reopen(manager, clock)
    service = IndependentReviewService(resumed, claim_seconds=10)
    assert not asyncio.run(service.tick())
    assert not asyncio.run(service.tick())
    assert len(events(store, "review.started")) == 1
    assert len(events(store, "review.recovery_blocked")) == 1
    current = resumed.get(run["id"])
    assert current["state"] == "review_pending"
    assert (current["token_spent"], current["steps"]) == (
        reserved["token_spent"],
        reserved["steps"],
    )
    with store.connection() as connection:
        assert (
            connection.execute("SELECT state FROM model_budget_attempts").fetchone()[0]
            == "reserved"
        )


def test_late_review_after_refunded_expired_claim_cannot_undercharge_or_revise_current_pass(
    tmp_path,
):
    store, manager, model, run, clock = ready(tmp_path)
    before = manager.get(run["id"])

    async def race():
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def delayed(request):
            requests.append(request)
            if len(requests) == 1:
                # Admission is delayed beyond the first claim's lease. No ledger
                # row exists when the new owner reclaims the expired claim.
                request._budget_post_started = False
                entered.set()
                await release.wait()
                request._budget_post_started = True
                return response(
                    {
                        "verdict": "NEEDS_REVISION",
                        "findings": ["stale result"],
                        "revision_goals": ["Do not apply this obsolete revision"],
                        "confidence": 0.8,
                    }
                )
            return response(PASS)

        model.generate = delayed
        service = IndependentReviewService(manager, claim_seconds=10)
        old = asyncio.create_task(service.review(run["id"]))
        await entered.wait()
        clock[0] += 11
        assert await IndependentReviewService(manager, claim_seconds=10).tick()
        assert events(store, "review.result")[-1]["verdict"] == "PASS"
        release.set()
        old_result = await old
        assert old_result["error_code"] == "stale_review_claim"

    asyncio.run(race())
    current = manager.get(run["id"])
    assert current["state"] == "review_pending"
    assert current["token_spent"] == before["token_spent"] + 80
    assert current["steps"] == before["steps"] + 2
    assert len(events(store, "review.stale_result")) == 1
    assert not events(store, "worker.revision_requested")
