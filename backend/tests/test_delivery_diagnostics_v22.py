"""Typed delivery feedback with unchanged approval/budget gates; no network."""

import asyncio
import copy
import hashlib
import json

import pytest
from test_review_envelope_phase2 import envelope, new_pending
from test_worker_intelligence_phase2 import delivery, prepared, response

from forge_core.domain import DomainError
from forge_core.review_envelope import review_cycle_state
from forge_core.worker_envelope import (
    DeliveryCapacityError,
    canonical,
    cycle_allowance,
    validate_delivery,
)
from forge_core.worker_prompts import initial_worker_messages
from forge_core.worker_runtime import _delivery_schema_diagnostics
from forge_core.worker_schema import STRICT_DELIVERY_OUTPUT_SCHEMA


def rejection_events(store):
    return [
        json.loads(event["payload"])
        for event in store.list_events(limit=100)
        if event["event_type"] == "worker.model_response_rejected"
    ]


def feedback(model):
    return json.loads(model.requests[-1].messages[-1]["content"])["repair_request"]["detail"]


def test_delivery_capacity_error_counts_exact_canonical_utf8_including_escaping():
    value = delivery('中文 "quotes" \\ newline\n')
    actual = len(canonical(value).encode("utf-8"))
    contract = {"review_envelope": {"max_delivery_bytes": actual}}
    assert validate_delivery(contract, value) is None
    contract["review_envelope"]["max_delivery_bytes"] -= 1
    with pytest.raises(DeliveryCapacityError) as caught:
        validate_delivery(contract, value)
    assert isinstance(caught.value, ValueError)
    assert caught.value.code == "delivery_byte_bound"
    assert caught.value.actual_bytes == actual and caught.value.max_bytes == actual - 1
    assert "中文" not in str(caught.value)


def test_legacy_delivery_without_capacity_contract_retains_existing_behavior():
    assert validate_delivery({}, delivery("x" * 100000)) is None


def test_delivery_bound_feedback_reports_numbers_and_preserves_reserved_review_cycle(
    tmp_path, monkeypatch
):
    value = delivery("x" * 2500)
    env = envelope(max_delivery_bytes=1024)
    original = copy.deepcopy(env)
    store, manager, model, worker = new_pending(
        tmp_path,
        monkeypatch,
        env=env,
        responses=[response(value), response(delivery())],
        budget=100000,
    )
    detail = feedback(model)
    assert detail["code"] == "delivery_byte_bound"
    assert detail["actual_bytes"] == len(canonical(value).encode("utf-8"))
    assert detail["max_bytes"] == 1024 and detail["schema_errors"] == []
    assert "not valid JSON" not in detail["message"]
    assert detail["response_sha256"] == hashlib.sha256(response(value).text.encode()).hexdigest()
    assert env == original
    after = manager.get(worker["id"])
    assert after["repairs"] == 1 and after["steps"] == 2 and after["token_spent"] == 80
    assert after["token_limit"] == 100000 and after["state"] == "review_pending"
    with store.connection() as connection:
        cycle = review_cycle_state(connection, after["id"], env)
        # The initial self-repair consumed the shared correction call, while
        # both independent reviews remain reserved within the original limit.
        assert cycle["reviews_remaining"] == 2 and cycle["correction_calls_remaining"] == 0
        assert cycle["future_reserve_tokens"] == 34048 and cycle["correction_started"]
        with pytest.raises(DomainError, match="correction call count exhausted"):
            cycle_allowance(connection, after, env)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='review.result'"
            ).fetchone()[0]
            == 0
        )
    events = rejection_events(store)
    assert len(events) == 1 and events[0]["reason_code"] == "delivery_byte_bound"
    assert events[0]["diagnostics"] == detail


def test_size_and_schema_errors_are_reported_together_without_echoing_values(tmp_path, monkeypatch):
    marker = "PRIVATE_MODEL_VALUE_MARKER"
    value = delivery("x" * 2200)
    value["findings"] = marker
    store, _manager, model, _worker = new_pending(
        tmp_path,
        monkeypatch,
        env=envelope(max_delivery_bytes=1024),
        responses=[response(value), response(delivery())],
        budget=100000,
    )
    detail = feedback(model)
    assert detail["code"] == "delivery_byte_bound"
    assert {"validator": "type", "path": ["findings"]} in detail["schema_errors"]
    assert marker not in json.dumps(detail) and marker not in json.dumps(rejection_events(store))


@pytest.mark.parametrize("correction_calls", [1, 2])
def test_repair_feedback_reports_only_approved_remaining_correction_calls(
    tmp_path, monkeypatch, correction_calls
):
    contract = envelope(max_delivery_bytes=1024, correction_calls=correction_calls)
    before = copy.deepcopy(contract)
    _store, manager, model, worker = new_pending(
        tmp_path,
        monkeypatch,
        env=contract,
        responses=[response(delivery("x" * 2500)), response(delivery())],
        budget=150000,
    )
    repair = json.loads(model.requests[-1].messages[-1]["content"])["repair_request"]
    assert repair["remaining_attempts"] == correction_calls - 1
    assert "1024 canonical JSON UTF-8 bytes" in repair["instruction"]
    assert ("last approved correction call" in repair["instruction"]) is (correction_calls == 1)
    assert "output-token allowance does not enlarge" in repair["instruction"]
    assert contract == before and manager.get(worker["id"])["repairs"] == 1


@pytest.mark.parametrize("valid_correction", [True, False])
def test_prefetch_batch_repair_reuses_complete_sources_and_keeps_original_delivery_gate(
    tmp_path, monkeypatch, valid_correction
):
    from test_core_source_prefetch_v23 import prepared as prefetched
    from test_worker_intelligence_phase2 import tool

    store, manager, model, worker = prefetched(
        tmp_path,
        monkeypatch,
        source="source" * 350,
        responses=[
            tool("file_read", {"path": "inputs/source.txt"}),
            response(delivery("done" if valid_correction else "x" * 4000)),
            response(delivery("This response must not be requested.")),
        ],
    )
    manager.max_repairs = 4
    result = asyncio.run(manager.run(worker["id"]))
    repair = json.loads(model.requests[-1].messages[-1]["content"])["repair_request"]
    assert repair["reason_code"] == "review_envelope_tool_batch"
    assert repair["remaining_attempts"] == 0
    assert "2048 canonical JSON UTF-8 bytes" in repair["instruction"]
    assert "last approved correction call" in repair["instruction"]
    assert "complete Core declared source block" in repair["instruction"]
    assert len(model.requests) == result["steps"] == 2 and result["repairs"] == 1
    assert result["state"] == ("review_pending" if valid_correction else "blocked")
    with store.connection() as connection:
        capsule = json.loads(
            connection.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
        )
        assert capsule["review_envelope"]["max_delivery_bytes"] == 2048
        assert capsule["review_envelope"]["correction_calls"] == 1
        assert connection.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM events WHERE event_type='review.result'"
            ).fetchone()[0]
            == 0
        )
    if not valid_correction:
        assert rejection_events(store)[-1]["reason_code"] == "delivery_byte_bound"
        assert not any(
            event["event_type"] == "worker.result" for event in store.list_events(limit=100)
        )


def test_json_syntax_failure_has_position_without_capacity_misclassification(tmp_path, monkeypatch):
    store, _manager, model, _worker = new_pending(
        tmp_path, monkeypatch, responses=[response('{"summary":'), response(delivery())]
    )
    detail = feedback(model)
    assert detail["code"] == "invalid_json_syntax"
    assert detail["line"] == 1 and detail["column"] > 0 and detail["position"] > 0
    assert "actual_bytes" not in detail and "schema_errors" not in detail
    assert rejection_events(store)[0]["reason_code"] == "invalid_json_syntax"


def test_small_parsed_schema_failure_is_not_reported_as_invalid_json(tmp_path, monkeypatch):
    value = delivery()
    value["findings"] = "PRIVATE_MODEL_VALUE_MARKER"
    store, _manager, model, _worker = new_pending(
        tmp_path, monkeypatch, responses=[response(value), response(delivery())], budget=100000
    )
    detail = feedback(model)
    assert detail["code"] == "output_schema"
    assert detail["schema_errors"] == [{"validator": "type", "path": ["findings"]}]
    assert "PRIVATE_MODEL_VALUE_MARKER" not in json.dumps(detail)
    assert rejection_events(store)[0]["reason_code"] == "output_schema"


def test_nonfinite_parsed_value_is_distinguished_from_json_syntax(tmp_path, monkeypatch):
    value = delivery()
    value["confidence"] = float("nan")
    _store, _manager, model, _worker = new_pending(
        tmp_path, monkeypatch, responses=[response(value), response(delivery())], budget=100000
    )
    assert feedback(model)["code"] == "invalid_json_value"
    assert feedback(model)["validation_stage"] == "canonical_json"


@pytest.mark.parametrize("violation", ["missing_fields", "confidence"])
def test_legacy_schema_delivery_review_failures_are_contract_errors(tmp_path, violation):
    value = {"summary": "Incomplete"} if violation == "missing_fields" else delivery()
    if violation == "confidence":
        value["confidence"] = 2
    _store, manager, model, worker = prepared(tmp_path, [response(value), response(delivery())])
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    detail = feedback(model)
    assert detail["code"] == "invalid_output_contract"
    assert detail["validation_stage"] == "delivery_review"
    assert "finite canonical JSON" not in detail["message"]
    if violation == "missing_fields":
        assert set(detail["missing_fields"]) == set(delivery()) - {"summary"}
    else:
        assert detail["missing_fields"] == [] and detail["path"] == ["confidence"]


def test_post_canonical_value_error_is_not_reported_as_serialization_failure(tmp_path, monkeypatch):
    from forge_core import worker_envelope

    original = worker_envelope.validate_delivery
    calls = 0

    def check_contract(capsule, value):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("PRIVATE_CONTRACT_ERROR_MARKER")
        return original(capsule, value)

    monkeypatch.setattr(worker_envelope, "validate_delivery", check_contract)
    store, _manager, model, _worker = new_pending(
        tmp_path, monkeypatch, responses=[response(delivery()), response(delivery())], budget=100000
    )
    detail = feedback(model)
    assert detail["code"] == "invalid_output_contract"
    assert detail["validation_stage"] == "capacity_contract"
    assert "finite canonical JSON" not in detail["message"]
    assert "PRIVATE_CONTRACT_ERROR_MARKER" not in json.dumps(detail)
    assert rejection_events(store)[0]["reason_code"] == "invalid_output_contract"


def test_schema_diagnostics_are_bounded_and_omit_unknown_field_names():
    marker = "PRIVATE_MODEL_VALUE_MARKER"
    value = {marker: marker, "summary": 3, "findings": marker, "confidence": 3}
    diagnostics = _delivery_schema_diagnostics(value, STRICT_DELIVERY_OUTPUT_SCHEMA)
    assert len(diagnostics) == 3
    assert marker not in json.dumps(diagnostics)
    assert len(json.dumps(diagnostics).encode()) < 1024


def prompt_contract(*, bounded):
    contract = {
        "constraints": {"allowed_tools": []},
        "responsibility": "Inspect the source",
        "input_contract": ["Approved source"],
        "output_schema": STRICT_DELIVERY_OUTPUT_SCHEMA,
        "acceptance_criteria": ["delivery_review"],
        "objective": "Inspect the source",
        "success_criteria": ["delivery_review"],
        "quest_id": "quest",
        "plan_id": "plan",
        "task_node_key": "n",
        "required_input_paths": [],
    }
    if bounded:
        contract["review_envelope"] = envelope(max_delivery_bytes=1536)
    return contract


@pytest.mark.parametrize("has_successors", [False, True])
def test_exact_capacity_prompt_precedes_intermediate_size_guidance(has_successors):
    contract = prompt_contract(bounded=True)
    before = copy.deepcopy(contract)
    messages = initial_worker_messages(contract, has_successors=has_successors)
    policy = messages[0]["content"]
    assert "1536 UTF-8 bytes" in policy and "canonical JSON" in policy
    assert "4 KiB" not in policy and "6 KiB" not in policy
    assert "one shared evidence list" in policy and "Preserve all assigned requirements" in policy
    assert contract == before


def test_legacy_intermediate_prompt_keeps_its_existing_writing_guidance():
    policy = initial_worker_messages(prompt_contract(bounded=False), has_successors=True)[0][
        "content"
    ]
    assert "4 KiB" in policy and "6 KiB" in policy
