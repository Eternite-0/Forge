"""Offline Gateway admission fencing; no live Provider requests are performed."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.domain import ForgeEvent
from forge_core.independent_review import IndependentReviewService
from forge_core.model_call_budget import BudgetExceeded, ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.runtime import ModelCallContext, ModelRequest


def claimed(tmp_path):
    store, manager, _model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            response(delivery()),
        ],
    )
    asyncio.run(manager.run(worker["id"]))
    result = next(
        item
        for item in reversed(store.list_events(limit=1000))
        if item["event_type"] == "worker.result"
    )
    # This test concerns admission/claim ordering; no review reservation was spent.
    started = {
        "plan_id": worker["plan_id"],
        "task_node_key": worker["node_key"],
        "run_id": worker["id"],
        "call_id": "current-review",
        "result_event_id": result["id"],
        "lease_until": manager._now() - 1,
        "reserved_tokens": 0,
        "reserved_cost_microunits": None,
    }
    store.append_event(
        ForgeEvent(
            event_type="review.started",
            quest_id=worker["quest_id"],
            worker_id=worker["worker_id"],
            payload=started,
        )
    )
    ledger = ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1)
    return store, manager, worker, ledger, started


@pytest.mark.parametrize("case", ["stale", "refunded"])
def test_stale_or_refunded_reviewer_claim_never_reaches_http(tmp_path, case):
    store, _manager, worker, ledger, started = claimed(tmp_path)
    identifier = "old-review" if case == "stale" else started["call_id"]
    if case == "refunded":
        store.append_event(
            ForgeEvent(
                event_type="review.released",
                quest_id=worker["quest_id"],
                worker_id=worker["worker_id"],
                payload={**started, "reservation_refunded": True, "unsent": True},
            )
        )
    posts = []
    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(lambda request: posts.append(request)),
        budget_ledger=ledger,
    )
    request = ModelRequest(
        provider_id="protocol",
        model_id="fixture",
        messages=[{"role": "user", "content": "Review"}],
        max_output_tokens=64,
        budget_context=ModelCallContext(
            identifier, "independent_reviewer", store.workspace.id, worker["quest_id"], worker["id"]
        ),
    )
    with pytest.raises(ProviderError):
        asyncio.run(gateway.generate(request))
    assert not posts and request._budget_post_started is False
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 0


def test_current_claim_can_be_admitted_under_original_worker_budget(tmp_path):
    store, _manager, worker, ledger, started = claimed(tmp_path)
    identifier = ledger.admit(
        ModelCallContext(
            started["call_id"],
            "independent_reviewer",
            store.workspace.id,
            worker["quest_id"],
            worker["id"],
        ),
        "protocol",
        "fixture",
        input_tokens=100,
        output_tokens=64,
        remote_timeout=30,
    )
    with store.connection() as connection:
        row = connection.execute(
            "SELECT * FROM model_budget_attempts WHERE attempt_id=?", (identifier,)
        ).fetchone()
        assert row["worker_run_id"] == worker["id"] and row["state"] == "reserved"


@pytest.mark.parametrize("case", ["worker_cancelled", "quest_paused", "plan_stale"])
def test_review_admission_rechecks_active_assignment_status(tmp_path, case):
    store, _manager, worker, ledger, started = claimed(tmp_path)
    with store.unit_of_work() as work:
        if case == "worker_cancelled":
            work.connection.execute(
                "UPDATE worker_runs SET state='cancelled' WHERE id=?", (worker["id"],)
            )
        elif case == "quest_paused":
            work.connection.execute(
                "UPDATE quests SET status='paused' WHERE id=?", (worker["quest_id"],)
            )
        else:
            work.connection.execute(
                "UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (worker["quest_id"],)
            )
    with pytest.raises(BudgetExceeded):
        ledger.admit(
            ModelCallContext(
                started["call_id"],
                "independent_reviewer",
                store.workspace.id,
                worker["quest_id"],
                worker["id"],
            ),
            "protocol",
            "fixture",
            input_tokens=100,
            output_tokens=64,
            remote_timeout=30,
        )
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 0


def test_expired_claim_recovery_and_gateway_admission_are_atomic(tmp_path):
    store, manager, worker, ledger, started = claimed(tmp_path)
    barrier = threading.Barrier(2)

    def admit():
        barrier.wait(timeout=10)
        try:
            ledger.admit(
                ModelCallContext(
                    started["call_id"],
                    "independent_reviewer",
                    store.workspace.id,
                    worker["quest_id"],
                    worker["id"],
                ),
                "protocol",
                "fixture",
                input_tokens=100,
                output_tokens=64,
                remote_timeout=30,
            )
            return "admitted"
        except BudgetExceeded:
            return "denied"

    def recover():
        barrier.wait(timeout=10)
        with store.unit_of_work() as work:
            row = work.connection.execute(
                "SELECT * FROM worker_runs WHERE id=?", (worker["id"],)
            ).fetchone()
            return IndependentReviewService(manager)._ready(work, row, started["result_event_id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(admit), pool.submit(recover)
        admitted, recovered = first.result(timeout=15), second.result(timeout=15)
    with store.connection() as connection:
        released = connection.execute(
            "SELECT payload FROM events WHERE event_type='review.released'"
        ).fetchone()
        attempts = connection.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0]
    if admitted == "admitted":
        assert attempts == 1 and released is None and not recovered
    else:
        assert (
            attempts == 0
            and released is not None
            and json.loads(released[0])["unsent"]
            and recovered
        )
