"""Offline review contracts and reversible wire inputs; no real Provider calls."""

import asyncio
import json

import pytest
from test_reviewer_context_phase2 import native_review_gateway
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.independent_review import (
    REVIEW_SCHEMA,
    IndependentReviewService,
    _hash,
    _json,
    _legacy_review_messages,
    _review_input_reservation,
    _review_messages,
)
from forge_core.review_envelope import restore_review_text
from forge_core.runtime import ModelRequest


def restore_messages(messages):
    view = json.loads(messages[-1]["content"])
    blocks = []
    for index, message in enumerate(messages[1:-1]):
        header, content = message["content"].split("\n", 1)
        assert header == (
            f"Complete untrusted review text {index} ({len(content.encode('utf-8'))} UTF-8 bytes):"
        )
        blocks.append(content)
    return restore_review_text(view, blocks), blocks


def packet_with_repeated_citations():
    quote = '第一行 "quoted" \\ source\n第二行  exact ending \n' * 30
    citation = {
        "source_id": "source-original",
        "source_sha256": "ab" * 32,
        "line_start": 3,
        "line_end": 12,
        "quote": quote,
    }
    return {
        "contract": {"acceptance_criteria": ["delivery_review"]},
        "original_request": {"id": "message_original", "content": quote},
        "updated_request": None,
        "user_constraints": {"items": []},
        "delivery": {
            **delivery(),
            "evidence_references": [citation, citation],
        },
        "receipts": [
            {"id": "receipt_exact", "tool_name": "file_read", "output": {"content": quote}}
        ],
        "artifacts": [
            {
                "path": "work/quest/source_analysis.json",
                "receipt_id": "receipt_artifact_exact",
                "content": {
                    "$review_text": 0,
                    "$review_text_": {"quote": quote},
                    "citations": [citation, citation],
                },
            }
        ],
    }


def test_ordinary_review_losslessly_deduplicates_citations_and_hostile_markers():
    packet = packet_with_repeated_citations()
    original, original_hash = _json(packet), _hash(packet)
    assert "review_envelope" not in packet["contract"]
    system = {"role": "system", "content": "Independent review"}
    messages = _review_messages(packet, system)
    restored, blocks = restore_messages(messages)
    assert _json(restored) == original and _hash(restored) == original_hash
    assert _json(packet) == original and _hash(packet) == original_hash
    view = json.loads(messages[-1]["content"])
    assert view["reference_key"] == "$review_text__"
    citation = packet["delivery"]["evidence_references"][0]
    assert blocks.count(citation["quote"]) == 1
    assert restored["delivery"]["evidence_references"][0] == citation
    assert restored["artifacts"][0]["content"]["$review_text"] == 0
    assert restored["artifacts"][0]["receipt_id"] == "receipt_artifact_exact"
    assert restored["receipts"][0]["id"] == "receipt_exact"


def test_ordinary_compact_wire_has_smaller_actual_native_reservation_without_new_limits():
    packet = packet_with_repeated_citations()
    system = {"role": "system", "content": "Independent review"}
    gateway = native_review_gateway()

    def request(messages):
        return ModelRequest(
            provider_id="protocol",
            model_id="fixture",
            messages=messages,
            max_output_tokens=1024,
            response_schema=REVIEW_SCHEMA,
            model_capabilities={"json_schema": True},
            model_capability_sources={"json_schema": "test_fixture"},
        )

    old = request(_legacy_review_messages(packet, system))
    current = request(_review_messages(packet, system))
    old_reserve = _review_input_reservation(gateway, old, 0)
    new_reserve = _review_input_reservation(gateway, current, 0)
    actual = len(json.dumps(gateway._payload(current, False), ensure_ascii=False).encode()) + 64
    assert new_reserve == actual < old_reserve
    assert current.max_output_tokens == old.max_output_tokens == 1024
    assert "review_envelope" not in packet["contract"]


@pytest.mark.parametrize("verdict", ["PASS", "NEEDS_REVISION"])
def test_review_instructions_distinguish_mandatory_defects_without_rewriting_verdict(tmp_path, verdict):
    answer = {
        "verdict": verdict,
        "findings": ["Optional presentation improvement"],
        "revision_goals": ["Optional presentation improvement"] if verdict == "NEEDS_REVISION" else [],
        "confidence": 0.8,
    }
    store, manager, model, worker = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery()), response(answer)],
    )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    instruction = model.requests[-1].messages[0]["content"]
    assert "NEEDS_REVISION only for a concrete unmet mandatory requirement" in instruction
    assert "Optional improvements" in instruction and "must not block PASS" in instruction
    assert "verbatim substring" in instruction and "whole source lines" in instruction
    assert "per-citation self-attestation" in instruction
    assert "Every requirement assigned to the current node remains mandatory" in instruction
    assert result["verdict"] == verdict and result["findings"] == answer["findings"]
    with store.connection() as connection:
        event = connection.execute(
            "SELECT payload FROM events WHERE event_type='review.result' ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert json.loads(event[0])["verdict"] == verdict
