"""Delivery error compatibility preserves bounded, value-free field locations."""

import json

import jsonschema
import pytest
from test_delivery_diagnostics_v22 import new_pending
from test_worker_intelligence_phase2 import delivery, response

from forge_core.worker_runtime import _delivery_schema_error_path


def test_approved_custom_field_names_do_not_enter_delivery_diagnostic_path():
    marker = "PRIVATE_FIELD_NAME_MARKER"
    schema = {"properties": {"summary": {"properties": {marker: {"type": "number"}}}}}
    with pytest.raises(jsonschema.ValidationError) as caught:
        jsonschema.validate({"summary": {marker: "PRIVATE_REJECTED_VALUE_MARKER"}}, schema)
    path = _delivery_schema_error_path(caught.value)
    assert path == ["summary", "field"]
    assert "PRIVATE" not in json.dumps(path)


def test_delivery_error_path_bounds_depth_and_rejects_unbounded_indexes():
    error = jsonschema.ValidationError(
        "Untrusted exception text", path=["findings", 9999] + ["summary"] * 20
    )
    assert _delivery_schema_error_path(error) == ["findings", "field"] + ["summary"] * 6


def test_capacity_worker_feedback_names_archive_reader_within_unchanged_bound(
    tmp_path, monkeypatch
):
    _store, _manager, model, _worker = new_pending(
        tmp_path, monkeypatch, responses=[response(delivery())], budget=100000
    )
    progress = next(
        message
        for message in model.requests[0].messages
        if message["content"].startswith("Core remaining execution allowance")
    )
    assert "worker_read_context_source" in progress["content"]
    assert "source_refs" in progress["content"] and "max_chars<=2048" in progress["content"]
    assert len(json.dumps(progress, ensure_ascii=False, separators=(",", ":")).encode()) <= 2048
