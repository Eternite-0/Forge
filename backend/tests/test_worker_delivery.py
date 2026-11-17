"""Delivery-stage contracts against durable SQLite; no live Provider calls."""

import copy
import hashlib
import json
import sqlite3

import pytest
from test_worker_intelligence_phase2 import delivery, prepared

from forge_core.domain import DomainError
from forge_core.worker_delivery import (
    DeliveryOutcome,
    commit_worker_response,
    validate_worker_delivery,
)
from forge_core.worker_schema import DELIVERY_OUTPUT_SCHEMA


def capsule():
    return {
        "output_schema": copy.deepcopy(DELIVERY_OUTPUT_SCHEMA),
        "success_criteria": ["delivery_review"],
    }


@pytest.mark.parametrize(
    ("text", "stage", "code"),
    [
        ('{"summary":', "json_syntax", "invalid_json_syntax"),
        ('{"summary":"ok","confidence":NaN}', "canonical_json", "invalid_json_value"),
        ('{"summary":12}', "output_schema", "output_schema"),
        ('{"summary":"ok"}', "delivery_review", "invalid_output_contract"),
        (
            json.dumps(delivery() | {"confidence": 1.2}),
            "delivery_review",
            "invalid_output_contract",
        ),
    ],
)
def test_validation_returns_redacted_typed_diagnostics(text, stage, code):
    value, diagnostics = validate_worker_delivery(capsule(), text)
    assert value is None
    assert diagnostics["validation_stage"] == stage
    assert diagnostics["code"] == code
    assert diagnostics["response_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert diagnostics["response_bytes"] == len(text.encode())
    assert "response" not in diagnostics


def test_validation_does_not_rewrite_approved_contract_or_fill_missing_fields():
    contract = capsule()
    before = copy.deepcopy(contract)
    value = delivery("完整😊")
    assert validate_worker_delivery(contract, json.dumps(value)) == (value, None)
    assert contract == before
    parsed, diagnostics = validate_worker_delivery(contract, '{"summary":"PRIVATE_CLAIM"}')
    assert parsed is None
    assert diagnostics["missing_fields"] == list(value)[1:]
    assert "PRIVATE_CLAIM" not in json.dumps(diagnostics)


@pytest.fixture
def claimed(tmp_path):
    store, manager, model, run = prepared(tmp_path, [])
    token = manager._claim(run["id"])
    row = manager.get(run["id"])
    assert token and row["state"] == "running"
    return store, manager, model, run, token, row


def commit(claimed, *, calls=None, token=None):
    _store, manager, _model, run, owner, row = claimed
    value = delivery()
    return commit_worker_response(
        manager,
        run_id=run["id"],
        token=owner if token is None else token,
        row=row,
        calls=calls or [],
        allowed=json.loads(row["allowed_tools_json"]),
        text=json.dumps(value),
        final_value=value if not calls else None,
    )


def snapshot(store):
    """Include all authoritative writes made by the extracted stage."""
    with store.connection() as connection:
        return {
            table: [
                tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            for table in (
                "worker_runs",
                "workers",
                "task_nodes",
                "worker_messages",
                "worker_capsules",
                "worker_tool_calls",
                "worker_inbox",
                "events",
                "outbox",
                "worker_budget_ledger",
                "action_intents",
                "action_receipts",
            )
        }


def test_final_commit_only_requests_review_and_does_not_execute_or_charge(claimed):
    store, manager, model, run, _token, row = claimed
    before = snapshot(store)
    assert commit(claimed) is DeliveryOutcome.REVIEW_PENDING
    after = snapshot(store)
    current = manager.get(run["id"])
    assert current["state"] == "review_pending"
    assert current["lease_token"] is None and current["lease_until"] is None
    assert current["steps"] == row["steps"] and current["token_spent"] == row["token_spent"]
    for table in ("worker_budget_ledger", "action_intents", "action_receipts"):
        assert after[table] == before[table]
    assert not model.requests
    assert store.get_quest(run["quest_id"])["status"] != "completed"
    assert not store.list_messages()
    assert [event["event_type"] for event in store.list_events()].count("worker.result") == 1


@pytest.mark.parametrize("control", ["pause", "cancel"])
def test_stale_generation_cannot_commit_or_publish_anything(claimed, control):
    store, manager, _model, run, _token, _row = claimed
    manager.control(run["id"], control)
    before = snapshot(store)
    with pytest.raises(DomainError):
        commit(claimed)
    assert snapshot(store) == before


def test_tool_commit_is_pending_not_execution_and_duplicate_rolls_back(claimed):
    store, manager, model, run, _token, _row = claimed
    calls = [{"id": "one", "name": "file_read", "arguments": {"path": "work/input.txt"}}]
    assert commit(claimed, calls=calls) is DeliveryOutcome.TOOLS_PENDING
    before = snapshot(store)
    with store.connection() as connection:
        call = dict(connection.execute("SELECT * FROM worker_tool_calls").fetchone())
    assert call["state"] == "pending" and call["receipt_id"] is None
    assert manager.get(run["id"])["tool_spent"] == 1
    assert not model.requests and not before["action_receipts"] and not before["action_intents"]
    with pytest.raises(sqlite3.IntegrityError):
        commit(claimed, calls=calls)
    assert snapshot(store) == before


def test_late_inbox_retains_charged_draft_without_publishing_result(claimed):
    store, manager, model, run, _token, row = claimed
    manager.deliver(run["id"], "late-context", {"message": "retain this requirement"})
    assert commit(claimed) is DeliveryOutcome.CONTEXT_PENDING
    current = manager.get(run["id"])
    assert current["state"] == "running"
    assert current["token_spent"] == row["token_spent"] and not model.requests
    with store.connection() as connection:
        messages = [
            json.loads(item[0])
            for item in connection.execute(
                "SELECT message_json FROM worker_messages WHERE run_id=? ORDER BY ordinal",
                (run["id"],),
            )
        ]
        assert connection.execute("SELECT consumed FROM worker_inbox").fetchone()[0] == 0
    assert messages[-2]["role"] == "assistant"
    assert messages[-1]["role"] == "user" and "Core delivery boundary" in messages[-1]["content"]
    assert not any(event["event_type"] == "worker.result" for event in store.list_events())
