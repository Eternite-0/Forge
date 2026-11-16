"""Lossless review inputs, real receipts and explicitly offline model protocols."""

import asyncio
import hashlib
import json

import httpx
import pytest
import test_worker_intelligence_phase2 as worker_fixture
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.constraints import ConstraintService
from forge_core.domain import ConversationMessage, DomainError, ModelProfile, ProtocolSupport
from forge_core.independent_review import (
    REVIEW_SCHEMA,
    IndependentReviewService,
    _hash,
    _json,
    _packet,
    _review_messages,
    _review_reservation,
    _ReviewBoundaryError,
    review_max_rounds_from_env,
)
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.review_envelope import restore_review_text
from forge_core.runtime import ModelRequest

PASS = {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}


def reconstruct(messages):
    projected = json.loads(messages[-1]["content"])
    if "reference_key" in projected and "packet" in projected:
        texts = [item["content"].split("\n", 1)[1] for item in messages[1:-1]]
        return restore_review_text(projected, texts)
    if "review_representation" not in projected:
        return projected
    for receipt in projected["receipts"]:
        reference = receipt.get("output", {}).get("content")
        if not isinstance(reference, dict) or "review_source_ref" not in reference:
            continue
        prefix = f"Complete untrusted source {reference['review_source_ref']}:\n"
        source = next(
            item["content"][len(prefix) :]
            for item in messages
            if item["content"].startswith(prefix)
        )
        receipt["output"]["content"] = source
    constraints = projected["user_constraints"]
    shared = constraints.pop("shared_fields", {})
    constraints["items"] = [{**shared, **item} for item in constraints["items"]]
    for item in constraints["items"]:
        reference = item.pop("statement_ref", None)
        if reference is not None:
            key, start, end = reference
            item["statement"] = projected[key]["content"][start:end]
    del projected["review_representation"]
    return projected


def test_large_review_preserves_every_source_and_constraint_without_changing_packet(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            response(delivery("evidence " * 500)),
            response(PASS),
        ],
    )
    source = json.dumps(
        {"lines": [f'{index}: original source "quoted" \\ 中文' for index in range(400)]},
        ensure_ascii=False,
        indent=2,
    )
    clauses = [f"必须保留约束 {index}: original evidence " + "x" * 100 for index in range(8)]
    message = ConversationMessage(role="user", content="\n".join(clauses))
    store.add_message(message)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET metadata=? WHERE id=?",
            (
                json.dumps(
                    {
                        "original_request_message_id": message.id,
                        "original_request_sha256": hashlib.sha256(
                            message.content.encode()
                        ).hexdigest(),
                    }
                ),
                worker["quest_id"],
            ),
        )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    for statement in clauses:
        ConstraintService(store).record_extraction(
            statement,
            source_message_id=message.id,
            quest_id=worker["quest_id"],
            origin="explicit_user",
        )
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        actual_packet = _packet(store, connection, row)
    # Synthetic large payload exercises the representation only; it is never
    # submitted as Core receipt evidence or used to manufacture a live PASS.
    packet = json.loads(_json(actual_packet))
    packet["receipts"][0]["output"]["content"] = source
    before = _hash(packet)
    assert len(_json(packet).encode()) > 32768
    messages = _review_messages(packet, {"role": "system", "content": "Independent review"})
    assert len(messages) > 2 and reconstruct(messages) == packet
    assert _hash(packet) == before
    raw = [
        {"role": "system", "content": "Independent review"},
        {"role": "user", "content": _json(packet)},
    ]
    assert len(_json(messages).encode()) < len(_json(raw).encode())
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["verdict"] == "PASS" and result["packet_sha256"] == _hash(actual_packet)
    assert json.loads(model.requests[-1].messages[-1]["content"]) == actual_packet


def test_compact_source_refs_restore_all_evidence_and_recover_native_verdict_room():
    system = {"role": "system", "content": "Independent review"}
    sources = [
        f"Complete untrusted source {index}:\n"
        + ('original "quoted" \\ source 中文\n' * 350)
        + f"\nexact ending {index}  \n"
        for index in range(3)
    ]
    packet = {
        "receipts": [
            {
                "id": f"receipt_original_id_{index}",
                "tool_name": "file_read",
                "output": {
                    "content": source,
                    "sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "other_original_value": {"review_source_ref": "untrusted original data"},
                },
            }
            for index, source in enumerate(sources)
        ],
        "user_constraints": {"items": []},
        "original_request": None,
        "updated_request": None,
    }
    original = _json(packet)
    original_hash = _hash(packet)
    assert 32768 < len(original.encode()) < 65536
    messages = _review_messages(packet, system)
    assert reconstruct(messages) == packet
    assert _json(packet) == original and _hash(packet) == original_hash
    projected = json.loads(messages[-1]["content"])
    for index, receipt in enumerate(projected["receipts"]):
        assert receipt["output"]["content"] == {"review_source_ref": index}
        assert receipt["output"]["sha256"] == hashlib.sha256(sources[index].encode()).hexdigest()

    # Rebuild the previous lossless representation solely to measure the
    # boundary regression; both views contain the same complete evidence.
    previous = json.loads(_json(messages))
    previous_packet = json.loads(previous[-1]["content"])
    for index, receipt in enumerate(previous_packet["receipts"]):
        ref = f"receipt:{receipt['id']}:content"
        receipt["output"]["content"] = {
            "review_source_ref": ref,
            "sha256": hashlib.sha256(sources[index].encode()).hexdigest(),
            "utf8_bytes": len(sources[index].encode()),
        }
        previous[index + 1]["content"] = f"Complete untrusted source {ref}:\n{sources[index]}"
    previous[-1]["content"] = _json(previous_packet)
    assert reconstruct(previous) == packet
    gateway = native_review_gateway()

    def request(view):
        return ModelRequest(
            provider_id="protocol",
            model_id="fixture",
            messages=view,
            max_output_tokens=8192,
            response_schema=REVIEW_SCHEMA,
            model_capabilities={"json_schema": True},
            model_capability_sources={"json_schema": "test_fixture"},
        )

    old_request, new_request = request(previous), request(messages)
    old_input = (
        len(json.dumps(gateway._payload(old_request, False), ensure_ascii=False).encode()) + 64
    )
    new_input = (
        len(json.dumps(gateway._payload(new_request, False), ensure_ascii=False).encode()) + 64
    )
    assert old_input - new_input >= 358
    row = {
        "model_context_window": old_input + 666,
        "token_limit": old_input + 8192,
        "token_spent": 0,
    }
    with pytest.raises(_ReviewBoundaryError) as failure:
        _review_reservation(gateway, old_request, row, 0)
    assert failure.value.code == "review_context_limit"
    reserve = _review_reservation(gateway, new_request, row, 0)
    assert new_request.max_output_tokens >= 1024
    assert reserve <= row["model_context_window"] and reserve <= row["token_limit"]
    assert gateway.health()["requests"] == 0


@pytest.mark.parametrize("value", ["1", "2", "3", "4"])
def test_review_round_setting_accepts_only_bounded_integers(value):
    assert review_max_rounds_from_env({"FORGE_REVIEW_MAX_ROUNDS": value}) == int(value)


@pytest.mark.parametrize("value", ["", "0", "5", "-1", "2.0", "many", True, 3])
def test_invalid_review_round_setting_fails_closed(value):
    with pytest.raises(ValueError):
        review_max_rounds_from_env({"FORGE_REVIEW_MAX_ROUNDS": value})


def test_review_round_default_remains_two():
    assert review_max_rounds_from_env({}) == 2


def test_exhausted_review_round_blocks_current_result_without_extra_revision(tmp_path):
    revision = {
        "verdict": "NEEDS_REVISION",
        "findings": ["Clarify evidence"],
        "revision_goals": ["Clarify the finding"],
        "confidence": 0.7,
    }
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            response(delivery()),
            response(revision),
        ],
    )
    service = IndependentReviewService(manager, max_rounds=1)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    result = asyncio.run(service.review(worker["id"]))
    assert result["verdict"] == "NEEDS_REVISION" and not result["revision_applied"]
    assert not asyncio.run(service.tick())
    blocked = [
        json.loads(row["payload"])
        for row in store.list_events(limit=1000)
        if row["event_type"] == "worker.blocked"
    ]
    assert len(blocked) == 1 and blocked[0]["reason_code"] == "review_revision_exhausted"
    assert blocked[0]["exhausted_bounds"] == ["review_round_limit"]
    assert len(model.requests) == 3 and manager.get(worker["id"])["state"] == "blocked"


def test_read_only_node_review_retains_original_goal_without_assigning_downstream_writes(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery()), response(PASS)],
    )
    original = ConversationMessage(
        role="user",
        content="Research the source first. A separate downstream writing node must create work/report.json.",
    )
    store.add_message(original)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET metadata=? WHERE id=?",
            (
                json.dumps(
                    {
                        "original_request_message_id": original.id,
                        "original_request_sha256": hashlib.sha256(
                            original.content.encode()
                        ).hexdigest(),
                    }
                ),
                worker["quest_id"],
            ),
        )
    asyncio.run(manager.run(worker["id"]))
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        before = _packet(store, connection, row)
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    request = model.requests[-1]
    packet = json.loads(request.messages[-1]["content"])
    assert packet == before and result["packet_sha256"] == _hash(before)
    assert packet["original_request"]["content"] == original.content
    assert packet["objective"] == "Inspect the original source with evidence"
    assert packet["success_criteria"] == ["delivery_review"] and not packet["artifacts"]
    instruction = request.messages[0]["content"]
    assert "CURRENT background task node" in instruction
    assert "downstream deliverables are not additional requirements" in instruction
    assert "Every requirement assigned to the current node remains mandatory" in instruction
    assert "Revision goals must correct this node's work within its approved scope" in instruction


def test_missing_assigned_artifact_still_blocks_before_model_judgment(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [tool("file_read", {"path": "work/input.txt"}), response(delivery()), response(PASS)],
        criteria=["json_review:work/report.json"],
    )
    asyncio.run(manager.run(worker["id"]))
    assert asyncio.run(IndependentReviewService(manager).tick())
    assert len(model.requests) == 2 and manager.get(worker["id"])["state"] == "review_pending"
    assert not any(row["event_type"] == "review.result" for row in store.list_events(limit=1000))


def native_review_gateway(*, transport=None, ledger=None):
    return ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_json_schema=True,
            reasoning_effort="none",
        ),
        credential_resolver=lambda _: "fixture",
        transport=transport,
        budget_ledger=ledger,
    )


@pytest.mark.parametrize(
    "context_room,budget_room,expected",
    [(2048, 8192, 2048), (8192, 1536, 1536), (1024, 1024, 1024), (8192, 8192, 8192)],
)
def test_native_review_cap_fits_complete_payload_and_original_remaining_allowance(
    context_room, budget_room, expected
):
    gateway = native_review_gateway()
    request = ModelRequest(
        provider_id="protocol",
        model_id="fixture",
        messages=[{"role": "user", "content": 'complete evidence: "quoted" \\ 中文 ' * 300}],
        max_output_tokens=8192,
        response_schema=REVIEW_SCHEMA,
        model_capabilities={"json_schema": True},
        model_capability_sources={"json_schema": "test_fixture"},
    )
    original = _json(request.messages)
    full_payload = gateway._payload(request, False)
    input_tokens = len(json.dumps(full_payload, ensure_ascii=False).encode()) + 64
    row = {
        "model_context_window": input_tokens + context_room,
        "token_spent": 4300,
        "token_limit": 4300 + input_tokens + budget_room,
    }
    reserve = _review_reservation(gateway, request, row, 0)
    actual = gateway._payload(request, False)
    assert request.max_output_tokens == expected and _json(request.messages) == original
    assert reserve == len(json.dumps(actual, ensure_ascii=False).encode()) + 64 + expected
    assert reserve <= row["model_context_window"]
    assert row["token_spent"] + reserve <= row["token_limit"]
    assert actual["reasoning_effort"] == "none"
    assert actual["response_format"]["json_schema"]["schema"] == REVIEW_SCHEMA
    assert any("Schema:" in message["content"] for message in actual["messages"])


@pytest.mark.parametrize("exhausted", ["context", "budget"])
def test_review_refuses_less_than_1024_verdict_tokens_without_sending(exhausted):
    gateway = native_review_gateway()
    request = ModelRequest(
        provider_id="protocol",
        model_id="fixture",
        messages=[{"role": "user", "content": "evidence"}],
        max_output_tokens=8192,
    )
    size = len(json.dumps(gateway._payload(request, False), ensure_ascii=False).encode()) + 64
    row = {"model_context_window": size + 8192, "token_limit": size + 8192, "token_spent": 0}
    row["model_context_window" if exhausted == "context" else "token_limit"] = size + 1023
    with pytest.raises(_ReviewBoundaryError if exhausted == "context" else DomainError) as failure:
        _review_reservation(gateway, request, row, 0)
    if exhausted == "context":
        assert failure.value.code == "review_context_limit"
    assert gateway.health()["requests"] == 0


def test_original_small_review_output_contract_is_preserved():
    gateway = native_review_gateway()
    request = ModelRequest(
        provider_id="protocol",
        model_id="fixture",
        messages=[{"role": "user", "content": "complete source"}],
        max_output_tokens=512,
    )
    row = {"model_context_window": 10000, "token_limit": 10000, "token_spent": 0}
    _review_reservation(gateway, request, row, 0)
    assert request.max_output_tokens == 512


def test_native_review_reservation_matches_actual_gateway_admission_and_settlement(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        worker_fixture,
        "ModelProfile",
        lambda **fields: ModelProfile(
            **fields,
            structured_output_support=ProtocolSupport(supported=True, source="test_fixture"),
        ),
    )
    store, manager, _model, worker = prepared(
        tmp_path, [tool("file_read", {"path": "work/input.txt"}), response(delivery())]
    )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    before = manager.get(worker["id"])
    seen = []

    def handler(http_request):
        body = json.loads(http_request.content)
        seen.append(body)
        with store.connection() as connection:
            attempt = connection.execute("SELECT * FROM model_budget_attempts").fetchone()
            claim = json.loads(
                connection.execute(
                    "SELECT payload FROM events WHERE event_type='review.started' ORDER BY seq DESC LIMIT 1"
                ).fetchone()[0]
            )
        assert attempt["input_reserved"] == len(json.dumps(body, ensure_ascii=False).encode()) + 64
        assert attempt["output_reserved"] == body["max_tokens"]
        assert claim["reserved_tokens"] == attempt["input_reserved"] + attempt["output_reserved"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(PASS)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 15},
            },
        )

    gateway = native_review_gateway(
        transport=httpx.MockTransport(handler),
        ledger=ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1),
    )
    manager.providers = ProviderPool({"protocol": gateway})
    assert asyncio.run(IndependentReviewService(manager).review(worker["id"]))["verdict"] == "PASS"
    assert len(seen) == 1 and seen[0]["response_format"]["json_schema"]["schema"] == REVIEW_SCHEMA
    assert manager.get(worker["id"])["token_spent"] == before["token_spent"] + 40
    with store.connection() as connection:
        attempt = connection.execute("SELECT * FROM model_budget_attempts").fetchone()
        assert attempt["state"] == "settled"
        assert attempt["input_charged"] == 25 and attempt["output_charged"] == 15
