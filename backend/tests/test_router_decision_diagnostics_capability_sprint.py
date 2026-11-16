"""Rejected decisions expose fixed contract diagnostics, never raw model text."""

import asyncio
import hashlib
import json

import pytest
from test_router_intelligence_phase2 import ScriptedMock, decision, router

from forge_core.domain import DomainError
from forge_core.runtime import ModelResponse
from forge_core.store import Store


def rejected_events(store):
    return [json.loads(row["payload"]) for row in store.list_events()
        if row["event_type"] == "router.decision_rejected"]


def test_rejected_initial_and_repair_have_field_paths_hashes_and_no_raw_content(tmp_path):
    store = Store(tmp_path / "invalid.sqlite3")
    private = "MODEL_PRIVATE_REPLY_NEVER_PERSIST"
    invalid = decision(reply=private, priority=99, **{private: "sensitive field value"})
    provider = ScriptedMock(invalid)
    instance = router(store, provider)
    before = store.ensure_router()
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(instance.handle_user_message("USER_PRIVATE_REQUEST_NEVER_PERSIST", request_id="invalid"))
    events = rejected_events(store)
    assert [item["stage"] for item in events] == ["initial", "repair"]
    expected = hashlib.sha256(json.dumps(invalid).encode()).hexdigest()
    assert all(item["response_sha256"] == expected for item in events)
    assert all("priority: less_than_equal" in item["errors"] for item in events)
    assert all("[unknown_field]: extra_forbidden" in item["errors"] for item in events)
    assert private not in json.dumps(store.list_events())
    assert "sensitive field value" not in json.dumps(store.list_events())
    assert "USER_PRIVATE_REQUEST" not in json.dumps(store.list_events())
    assert store.ensure_router() == before
    assert store.list_messages() == [] and store.list_quests() == []
    assert len(provider.requests) == 2


def test_successful_reflection_retains_initial_diagnostic_without_invalid_turn(tmp_path):
    store = Store(tmp_path / "repaired.sqlite3")
    invalid = decision(priority="PRIVATE_INVALID_VALUE")
    provider = ScriptedMock(invalid, decision())
    result = asyncio.run(router(store, provider).handle_user_message("Hello", request_id="repair"))
    assert result["intent"] == "chat" and len(provider.requests) == 2
    # Self-correction must see its rejected decision, not merely a masked error.
    reflected = provider.requests[1].messages
    assert reflected[-2] == {"role": "assistant", "content": json.dumps(invalid)}
    assert "recalled_requirement_ids=[]" in reflected[-1]["content"]
    events = rejected_events(store)
    assert len(events) == 1 and events[0]["stage"] == "initial"
    assert events[0]["errors"] == ["priority: int_type"]
    assert "PRIVATE_INVALID_VALUE" not in json.dumps(store.list_events())
    assert len(store.list_messages()) == 2
    assert "PRIVATE_INVALID_VALUE" not in json.dumps(store.list_messages())


def test_diagnostic_after_router_lookup_does_not_cause_a_model_retry(tmp_path):
    store = Store(tmp_path / "lookup-repair.sqlite3")
    lookup = ModelResponse(text="", tool_calls=[{
        "id": "lookup", "name": "list_quests", "arguments": {}
    }], usage={"input_tokens": 10, "output_tokens": 5})
    provider = ScriptedMock(lookup, decision(priority="invalid"), decision())
    result = asyncio.run(router(store, provider).handle_user_message("Hello", request_id="lookup"))
    assert result["intent"] == "chat"
    assert len(provider.requests) == 3
    assert [request.budget_context.purpose for request in provider.requests] == [
        "router", "router", "router_reflect"
    ]
    assert len(rejected_events(store)) == 1


def test_disabled_reflection_records_one_diagnostic_and_does_not_retry(tmp_path):
    store = Store(tmp_path / "no-repair.sqlite3")
    provider = ScriptedMock(decision(candidates="RAW_PRIVATE_BAD_LIST"))
    instance = router(store, provider)
    instance.max_repairs = 0
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(instance.handle_user_message("Hello", request_id="no-repair"))
    assert len(provider.requests) == 1
    assert rejected_events(store)[0]["errors"] == ["candidates: list_type"]
    assert "RAW_PRIVATE_BAD_LIST" not in json.dumps(store.list_events())


def test_recall_rejection_records_fixed_reason_without_source_statement(tmp_path):
    store = Store(tmp_path / "recall.sqlite3")
    provider = ScriptedMock(decision("create_quest",
        recalled_requirement_ids=["PRIVATE_NOT_PRESENTED_MEMORY"]))
    instance = router(store, provider)
    instance.max_repairs = 0
    with pytest.raises(DomainError):
        asyncio.run(instance.handle_user_message("Hello", request_id="bad-recall"))
    assert rejected_events(store)[0]["errors"] == ["recall_source_not_presented_or_ineligible"]
    assert "PRIVATE_NOT_PRESENTED_MEMORY" not in json.dumps(store.list_events())
