"""Native offline HTTP/ledger regression for one shared correction allowance."""

import asyncio
import json
import sqlite3

import httpx
import pytest
from test_core_source_prefetch_v23 import prepared
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_worker_intelligence_phase2 import delivery

from forge_core.domain import DomainError
from forge_core.evidence import CompletionGate, NodeVerifier
from forge_core.independent_review import IndependentReviewService
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.review_envelope import review_cycle_state
from forge_core.worker_envelope import cycle_allowance


@pytest.mark.parametrize("refunded", [False, True])
def test_fixed_correction_frontier_counts_unknown_but_excludes_refunded_after_clock_rollback(
    refunded,
):
    # A minimal independent ledger fixture fixes the chronological frontier;
    # earlier initial usage can share a timestamp and is never a correction.
    with sqlite3.connect(":memory:") as c:
        c.row_factory = sqlite3.Row
        c.executescript("""
            CREATE TABLE events(seq INTEGER PRIMARY KEY,event_type TEXT,payload TEXT,created_at TEXT);
            CREATE TABLE worker_model_reservations(run_id TEXT,created_at TEXT,state TEXT);
        """)
        c.execute("INSERT INTO worker_model_reservations VALUES ('r','2030','settled')")
        c.execute(
            "INSERT INTO events VALUES (1,'worker.progress',?,'2040')",
            (
                json.dumps(
                    {"run_id": "r", "phase": "self_repair", "correction_reservation_cursor": 1}
                ),
            ),
        )
        c.execute(
            "INSERT INTO worker_model_reservations VALUES ('r','2001',?)",
            ("refunded" if refunded else "unknown",),
        )
        policy = {
            "version": "worker-capacity-v1",
            "max_source_bytes": 1000,
            "max_artifact_bytes": 1000,
            "max_delivery_bytes": 1000,
            "correction_input_tokens": 4000,
            "correction_output_tokens": 1000,
            "correction_calls": 1,
        }
        run = {"id": "r", "token_limit": 20000, "token_spent": 500}
        if refunded:
            state = cycle_allowance(c, run, policy)
            assert state["correction_calls_remaining"] == 1
            assert state["current_request_allowance"] == 19500
        else:
            with pytest.raises(DomainError, match="correction call count exhausted"):
                cycle_allowance(c, run, policy)


@pytest.mark.parametrize("verdict", ["PASS", "NEEDS_REVISION"])
def test_oversized_source_batch_uses_one_shared_native_correction(tmp_path, monkeypatch, verdict):
    store, manager, scripted, worker = prepared(tmp_path, monkeypatch, source="原" * 400)
    # A clock rollback/equal timestamps must not replenish a persisted call.
    monkeypatch.setattr(
        "forge_core.worker_model_usage.now_iso", lambda: "2001-01-01T00:00:00+00:00"
    )
    posted = []

    def handle(request):
        body = json.loads(request.content)
        posted.append(body)
        if len(posted) == 1:
            # This duplicate is a real model-selected request. Admission rejects
            # it before any new intent/receipt or tool counter can be created.
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "duplicate-source",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "inputs/source.txt"}),
                        },
                    }
                ],
            }
        elif len(posted) == 2:
            assert any("repair_request" in item.get("content", "") for item in body["messages"])
            with store.connection() as c:
                run = c.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
                capsule = json.loads(
                    c.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
                )
                stages = review_cycle_state(c, worker["id"], capsule["review_envelope"])
                # The current admitted correction already consumes the only call.
                assert stages["correction_started"] and stages["correction_calls_remaining"] == 0
                assert run["steps"] == 2 and run["repairs"] == 1
            message = {
                "content": json.dumps(delivery("Complete analysis from the original source."))
            }
        elif len(posted) == 3:
            message = {
                "content": json.dumps(
                    {
                        "verdict": verdict,
                        "findings": ["Complete source-bound analysis."],
                        "revision_goals": ["Clarify the finding."]
                        if verdict == "NEEDS_REVISION"
                        else [],
                        "confidence": 0.8,
                    }
                )
            }
        else:
            pytest.fail("The original single correction allowance cannot produce another call")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "tool_calls" if len(posted) == 1 else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 40},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            supports_json_schema=True,
            max_retries=0,
        ),
        credential_resolver=lambda _: "offline-placeholder",
        transport=httpx.MockTransport(handle),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=180000, concurrency_limit=1),
    )
    manager.providers = ProviderPool({"protocol": gateway})
    result = asyncio.run(manager.run(worker["id"]))
    with store.connection() as c:
        reasons = [
            json.loads(r[0]).get("reason_code")
            for r in c.execute(
                "SELECT payload FROM events WHERE event_type IN ('worker.blocked','worker.failed')"
            )
        ]
    assert result["state"] == "review_pending", (len(posted), reasons)
    assert result["steps"] == 2 and result["repairs"] == 1
    assert result["tool_spent"] == 1 and result["token_spent"] == 480
    assert not scripted.requests
    assert "additional action-evidence allowance is ZERO" in posted[0]["messages"][0]["content"]
    review = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert review["verdict"] == verdict
    assert review["review_context"] == "independent_same_model"
    assert review.get("revision_applied", False) is False
    final = manager.get(worker["id"])
    assert final["steps"] == 3 and final["token_spent"] == 720 and final["repairs"] == 1
    with store.connection() as c:
        capsule = json.loads(c.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0])
        stage = review_cycle_state(c, worker["id"], capsule["review_envelope"])
        assert stage["correction_started"] and stage["correction_calls_remaining"] == 0
        with pytest.raises(DomainError, match="correction call count exhausted"):
            cycle_allowance(c, final, capsule["review_envelope"])
        assert c.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT count(*) FROM worker_core_source_reads WHERE state='succeeded'"
            ).fetchone()[0]
            == 1
        )
        calls = c.execute(
            "SELECT purpose,state,input_charged,output_charged FROM model_budget_attempts ORDER BY created_at"
        ).fetchall()
        assert [(r["purpose"], r["state"]) for r in calls] == [
            ("worker", "settled"),
            ("worker", "settled"),
            ("independent_reviewer", "settled"),
        ]
        assert sum(r["input_charged"] + r["output_charged"] for r in calls) == 720
        assert (
            c.execute(
                "SELECT count(*) FROM events WHERE event_type='worker.revision_requested'"
            ).fetchone()[0]
            == 0
        )
    assert len(posted) == 3


def test_local_http_shared_correction_finishes_with_original_source_and_completion_gate(
    tmp_path, monkeypatch
):
    """Real loopback HTTP, scripted responses; not an actual model acceptance."""
    store, manager, scripted, worker = prepared(
        tmp_path, monkeypatch, source="原" * 400, mode="live"
    )
    monkeypatch.setattr(
        "forge_core.worker_model_usage.now_iso", lambda: "2001-01-01T00:00:00+00:00"
    )

    def responder(body, ordinal):
        if ordinal == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "duplicate-native-read",
                        "type": "function",
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "inputs/source.txt"}),
                        },
                    }
                ],
            }
        if ordinal == 2:
            assert any("repair_request" in m.get("content", "") for m in body["messages"])
            return {"content": json.dumps(delivery("The original source has been analyzed."))}
        assert ordinal == 3 and not body.get("tools")
        return {
            "content": json.dumps(
                {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
            )
        }

    with offline_loopback_gateway(store, responder) as (gateway, requests):
        gateway._budget_ledger = ModelCallBudgetLedger(
            store.path, token_limit=180000, concurrency_limit=1
        )
        manager.providers = ProviderPool({"protocol": gateway})
        assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
        assert (
            asyncio.run(IndependentReviewService(manager).review(worker["id"]))["verdict"] == "PASS"
        )
        assert NodeVerifier(store).verify(worker["quest_id"], "n")["status"] == "completed"
        final = manager.get(worker["id"])
        assert final["state"] == "succeeded" and final["steps"] == 3 and final["repairs"] == 1
        assert final["token_limit"] == worker["token_limit"] and final["token_spent"] == 120
        assert final["tool_spent"] == 1 and not scripted.requests and len(requests) == 3
        with store.connection() as c:
            c.execute("PRAGMA query_only=ON")
            c.execute("BEGIN")
            CompletionGate(store).check(
                type("ReadWork", (), {"connection": c})(),
                c.execute("SELECT * FROM quests WHERE id=?", (worker["quest_id"],)).fetchone(),
            )
            assert c.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
            assert c.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 0
            attempts = c.execute(
                "SELECT state,input_charged,output_charged FROM model_budget_attempts"
            ).fetchall()
            assert len(attempts) == 3 and all(r["state"] == "settled" for r in attempts)
            assert sum(r["input_charged"] + r["output_charged"] for r in attempts) == 120
            assert c.total_changes == 0
