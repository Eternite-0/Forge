"""Programmatic fixed diagnostic codes; no Provider request or retry."""

import asyncio
import json

import pytest
from test_worker_intelligence_phase2 import prepared

from forge_core.context_service import ContextService
from forge_core.domain import DomainError


@pytest.mark.parametrize(
    "message,code",
    [
        ("Worker context budget exceeded", "request_input_allowance_exceeded"),
        ("Worker context budget exceeded after compaction", "compacted_input_allowance_exceeded"),
        ("Worker token budget exhausted", "cumulative_allocation_exhausted"),
        ("Worker compaction token allocation exhausted", "cumulative_allocation_exhausted"),
        ("Invalid Worker compaction handoff", "invalid_compaction_handoff"),
        ("Invalid source-bound Worker model handoff", "invalid_compaction_handoff"),
        ("Complete earliest Worker exchange does not fit model context", "complete_exchange_context_limit"),
        ("Cannot archive an incomplete Worker tool exchange", "incomplete_compaction_exchange"),
        ("Worker compaction Provider failed; original context retained", "compaction_provider_failure"),
        ("PRIVATE REMOTE MODEL TEXT", "unclassified_context_rejection"),
    ],
)
@pytest.mark.parametrize("enforced", [False, True])
def test_context_rejection_distinguishes_capacity_protocol_and_unknown_without_text(
    tmp_path, monkeypatch, message, code, enforced
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", str(enforced).lower())
    store, manager, model, worker = prepared(tmp_path, [])
    observed = {}

    def reject(*_args, **kwargs):
        observed.update(kwargs)
        raise DomainError(message)

    monkeypatch.setattr(ContextService, "build_worker_context", reject)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and not model.requests and result["repairs"] == 0
    with store.connection() as c:
        events = c.execute(
            "SELECT payload FROM events WHERE event_type='worker.context_rejected'"
        ).fetchall()
        assert len(events) == 1
        event = json.loads(events[0][0])
        assert event == {
            "run_id": worker["id"],
            "reason_code": code,
            "model_context_window": result["model_context_window"],
            "effective_model_context_limit": result["model_context_window"],
            "request_token_allowance": (
                result["token_limit"] if enforced else result["model_context_window"]
            ),
            "input_allowance": observed["input_budget"],
            "input_allowance_limited_by": "cumulative_tokens" if enforced else "model_context",
            "cumulative_token_limit": result["token_limit"],
            "cumulative_token_remaining": result["token_limit"] - result["token_spent"],
            "cumulative_token_limits_enforced": enforced,
        }
        assert "PRIVATE" not in events[0][0]
        blocked = c.execute("SELECT payload FROM events WHERE event_type='worker.blocked'").fetchone()
        assert json.loads(blocked[0])["reason_code"] == "context_budget"
