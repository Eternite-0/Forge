"""Mock reasoning reads actual Core metadata from real CPython test results."""

import json

import pytest
from test_python_unittest_superagent import corrected_worker
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response

from forge_core.domain import DomainError
from forge_core.worker_runtime import _worker_tool_output


def test_scripted_model_uses_visible_failed_and_successful_python_receipt_ids(tmp_path, monkeypatch):
    captured = {}
    generate = ProtocolModel.generate

    async def observe(self, request):
        for message in request.messages:
            if message["role"] != "tool":
                continue
            value = json.loads(message["content"])
            output = value.get("output", value)
            if output.get("runtime") != "cpython_unittest_v1":
                assert "core_receipt" not in output
                continue
            metadata = output["core_receipt"]
            assert set(metadata) == {"receipt_id", "intent_id", "execution_mode", "state"}
            assert metadata["execution_mode"] == "mock"
            captured[metadata["receipt_id"]] = output
        if len(self.requests) == 5:
            value = delivery("Cite the actual failed and successful test receipts")
            value["evidence_references"] = list(captured)
            self.responses[0] = response(value)
        return await generate(self, request)

    monkeypatch.setattr(ProtocolModel, "generate", observe)
    store, _manager, model, worker, outcome, _approvals = corrected_worker(tmp_path)
    assert outcome["state"] == "review_pending" and len(captured) == 2
    with store.connection() as c:
        for receipt_id, output in captured.items():
            receipt = c.execute(
                "SELECT r.*,i.state FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE r.id=?",
                (receipt_id,),
            ).fetchone()
            original = json.loads(receipt["result_json"])
            assert {key: value for key, value in output.items() if key != "core_receipt"} == original
            assert output["manifest_sha256"] == original["manifest_sha256"]
            assert output["core_receipt"] == {
                "receipt_id": receipt_id,
                "intent_id": receipt["intent_id"],
                "execution_mode": receipt["execution_mode"],
                "state": receipt["state"],
            }
        event = c.execute(
            "SELECT payload FROM events WHERE event_type='worker.result' AND worker_id=? ORDER BY seq DESC LIMIT 1",
            (worker["worker_id"],),
        ).fetchone()
    claimed = json.loads(json.loads(event[0])["claim"])
    assert set(claimed["evidence_references"]) == set(captured)
    assert len(model.requests) == 6


def test_python_projection_rejects_result_identity_change(tmp_path):
    store, _manager, _model, worker, _outcome, _approvals = corrected_worker(tmp_path)
    with store.connection() as c:
        call = c.execute(
            "SELECT * FROM worker_tool_calls WHERE tool_name='python_unittest' AND state='succeeded'"
        ).fetchone()
        run = c.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        result = json.loads(call["result_json"])
        result["receipt_id"] = "receipt_fabricated"
        with pytest.raises(DomainError, match="source-bound"):
            _worker_tool_output(c, run, call, result)
