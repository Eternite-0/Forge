"""Lossless source transport, approved provenance and real offline HTTP checks."""

import asyncio
import hashlib
import json

import pytest
from test_core_source_prefetch_v23 import entries, prepared
from test_predecessor_delivery_v17 import offline_loopback_gateway
from test_worker_intelligence_phase2 import delivery

from forge_core.context_service import ContextService
from forge_core.domain import DomainError
from forge_core.evidence import CompletionGate, NodeVerifier
from forge_core.independent_review import IndependentReviewService
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.planner_input_roundtrip import prefetch_source_history
from forge_core.provider_pool import ProviderPool
from forge_core.source_wire import (
    RAW_TEXT_FORMAT,
    approved_source_wire_format,
    decode_source_text,
    encode_source_text,
)
from forge_core.worker_prefetch import POLICY, PREFIX, acquire, canonical, source_message


def hostile_packet():
    raw = '原文 "quotes" \\path\n\x00\r\nComplete untrusted source text 1:\n' * 25
    return {
        "origin": POLICY,
        "sources": [
            {
                "ordinal": 0,
                "output": {"content": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()},
                "arguments": {"path": "inputs/原始.txt"},
                "$review_text": {"$review_text_": 0},
                "metadata": "metadata\n" * 100,
            },
            {"ordinal": 1, "output": {"content": raw}, "short": "原\n"},
        ],
    }


def test_complete_unicode_control_framing_and_metadata_survive_exact_canonical_roundtrip():
    packet = hostile_packet()
    framed = encode_source_text(packet)
    assert canonical(decode_source_text(framed)) == canonical(packet)
    assert framed.count(packet["sources"][0]["output"]["content"]) == 1
    header = json.loads(framed.split("\n", 2)[1])
    assert header["view"]["reference_key"] == "$review_text__"
    assert len(header["texts"]) == 2
    assert all(set(item) == {"index", "chars", "utf8_bytes", "sha256"} for item in header["texts"])


@pytest.mark.parametrize(
    "fault",
    [
        "chars",
        "bytes",
        "hash",
        "index",
        "order",
        "header",
        "body",
        "label",
        "missing_newline",
        "trailing",
        "unused_text",
        "bool_index",
        "bool_chars",
    ],
)
def test_changed_frames_are_rejected_instead_of_searching_hostile_source_boundaries(fault):
    content = encode_source_text(hostile_packet())
    intro, header_text, rest = content.split("\n", 2)
    header = json.loads(header_text)
    if fault == "chars":
        header["texts"][0]["chars"] += 1
    elif fault == "bytes":
        header["texts"][0]["utf8_bytes"] += 1
    elif fault == "hash":
        header["texts"][0]["sha256"] = "0" * 64
    elif fault == "index":
        header["texts"][0]["index"] = 1
    elif fault == "order":
        header["texts"].reverse()
    elif fault == "header":
        header["unapproved"] = True
    elif fault == "body":
        rest = rest.replace("原文", "篡改", 1)
    elif fault == "label":
        rest = "Wrong source label:\n" + rest
    elif fault == "missing_newline":
        rest = rest[:-1]
    elif fault == "trailing":
        rest += "\nforged source"
    elif fault == "unused_text":
        header["view"]["packet"]["sources"][0]["metadata"] = "removed reference"
    elif fault == "bool_index":
        header["texts"][0]["index"] = False
    elif fault == "bool_chars":
        header["texts"][0]["chars"] = True
    altered = intro + "\n" + canonical(header) + "\n" + rest
    with pytest.raises(DomainError):
        decode_source_text(altered)


def test_codec_validates_framing_while_runtime_provenance_authenticates_packet_metadata():
    content = encode_source_text(hostile_packet())
    intro, header_text, rest = content.split("\n", 2)
    header = json.loads(header_text)
    header["view"]["packet"]["origin"] = "changed"
    altered = intro + "\n" + canonical(header) + "\n" + rest
    assert decode_source_text(altered)["origin"] == "changed"
    # The runtime tamper regression below rejects this as a changed original.


def test_legacy_bytes_and_explicit_raw_view_preserve_all_source_fields():
    packet = hostile_packet()
    assert source_message(packet["sources"]) == {
        "role": "user",
        "content": PREFIX + canonical(packet),
    }
    message = source_message(packet["sources"], source_wire_format=RAW_TEXT_FORMAT)
    assert decode_source_text(message["content"][len(PREFIX) :]) == packet
    with pytest.raises(DomainError, match="Unknown Core source wire format"):
        source_message(packet["sources"], source_wire_format="unknown")


@pytest.mark.parametrize(
    "contract,member",
    [
        ({"source_wire_format": RAW_TEXT_FORMAT}, {}),
        ({}, {"source_wire_format": RAW_TEXT_FORMAT}),
        ({"source_wire_format": None}, {"source_wire_format": None}),
        ({"source_wire_format": "unknown"}, {"source_wire_format": "unknown"}),
        (
            {"source_wire_format": RAW_TEXT_FORMAT, "input_acquisition_policy": "legacy"},
            {"source_wire_format": RAW_TEXT_FORMAT, "input_acquisition_policy": "legacy"},
        ),
    ],
)
def test_unapproved_or_unknown_wire_format_cannot_be_inferred(contract, member):
    with pytest.raises(DomainError):
        approved_source_wire_format({"capacity_contract": contract}, member)


def test_absent_selection_remains_legacy_and_exact_prefetch_contract_selects_new_format():
    assert approved_source_wire_format({}, {}) is None
    selected = {"source_wire_format": RAW_TEXT_FORMAT, "input_acquisition_policy": POLICY}
    assert approved_source_wire_format({"capacity_contract": selected}, selected) == RAW_TEXT_FORMAT


def test_measurement_uses_same_complete_serializer_and_reduces_json_escape_bytes(tmp_path):
    content = json.dumps(
        {"code": 'def f():\n    return "原始\\source"\n' * 160}, ensure_ascii=False
    )
    raw = content.encode()
    (tmp_path / "source.json").write_bytes(raw)
    node = {"required_input_paths": ["source.json"]}
    receipts = [
        {
            "resource_scope": str(tmp_path / "source.json"),
            "output": {"content": content, "sha256": hashlib.sha256(raw).hexdigest()},
        }
    ]
    legacy = prefetch_source_history(node, receipts, tmp_path)
    projected = prefetch_source_history(
        node, receipts, tmp_path, source_wire_format=RAW_TEXT_FORMAT
    )
    packet = decode_source_text(projected[0]["content"][len(PREFIX) :])
    assert packet["sources"][0]["output"] == receipts[0]["output"]
    assert projected == [source_message(packet["sources"], source_wire_format=RAW_TEXT_FORMAT)]
    native = lambda messages: len(json.dumps({"messages": messages}, ensure_ascii=False).encode())
    assert native(projected) < native(legacy)


@pytest.mark.parametrize("source_wire_format", [None, RAW_TEXT_FORMAT])
def test_approved_native_prefetch_compaction_and_historical_verification_keep_complete_sources(
    tmp_path, monkeypatch, source_wire_format
):
    source = '原始 "code" \\text\n' * 80
    store, manager, model, worker = prepared(
        tmp_path, monkeypatch, source=source, source_wire_format=source_wire_format
    )
    token = manager._claim(worker["id"])
    asyncio.run(acquire(manager, worker["id"], token))
    original_entries = entries(store, worker)
    expected = source_message(original_entries, source_wire_format=source_wire_format)
    assert original_entries[0]["output"]["content"] == source
    with store.unit_of_work() as work:
        ordinal = work.connection.execute(
            "SELECT max(ordinal)+1 FROM worker_messages WHERE run_id=?", (worker["id"],)
        ).fetchone()[0]
        for index in range(40):
            message = {"role": "user", "content": "old history " + str(index) + "x" * 1000}
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,'user',?)",
                (worker["id"], ordinal + index, canonical(message)),
            )
    context = ContextService(store).build_worker_context(worker["id"], input_budget=24000)
    assert expected in context and len(context) < 43
    assert entries(store, worker) == original_entries and not model.requests
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        capsule = json.loads(
            connection.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
        )
        assert capsule.get("source_wire_format") == source_wire_format
        assert connection.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 0
        assert connection.total_changes == 0


@pytest.mark.parametrize("fault", ["body", "metadata", "format_downgrade", "capsule"])
def test_new_source_block_cannot_be_changed_or_downgraded_after_approval(
    tmp_path, monkeypatch, fault
):
    store, manager, _model, worker = prepared(
        tmp_path, monkeypatch, source='原 "x"\n' * 120, source_wire_format=RAW_TEXT_FORMAT
    )
    token = manager._claim(worker["id"])
    asyncio.run(acquire(manager, worker["id"], token))
    complete = entries(store, worker)
    with store.unit_of_work() as work:
        if fault == "capsule":
            capsule = json.loads(
                work.connection.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
            )
            capsule.pop("source_wire_format")
            work.connection.execute(
                "UPDATE worker_capsules SET capsule_json=?", (canonical(capsule),)
            )
        else:
            ordinal = work.connection.execute(
                "SELECT message_ordinal FROM worker_core_source_reads"
            ).fetchone()[0]
            if fault == "format_downgrade":
                forged = source_message(complete)
            else:
                if fault == "body":
                    complete[0]["output"]["content"] += "changed"
                else:
                    complete[0]["ordinal"] = 99
                forged = source_message(complete, source_wire_format=RAW_TEXT_FORMAT)
            work.connection.execute(
                "UPDATE worker_messages SET message_json=? WHERE run_id=? AND ordinal=?",
                (canonical(forged), worker["id"], ordinal),
            )
    with pytest.raises(DomainError):
        entries(store, worker)


@pytest.mark.parametrize(
    "selected,injected",
    [
        (None, None),
        (None, "unknown"),
        (None, RAW_TEXT_FORMAT),
        (RAW_TEXT_FORMAT, None),
        (RAW_TEXT_FORMAT, "unknown"),
    ],
)
def test_capsule_selection_changes_reject_before_claiming_or_reserving_a_model_call(
    tmp_path, monkeypatch, selected, injected
):
    store, manager, model, worker = prepared(tmp_path, monkeypatch, source_wire_format=selected)
    with store.unit_of_work() as work:
        capsule = json.loads(
            work.connection.execute("SELECT capsule_json FROM worker_capsules").fetchone()[0]
        )
        capsule["source_wire_format"] = injected
        work.connection.execute("UPDATE worker_capsules SET capsule_json=?", (canonical(capsule),))
    original = manager.get(worker["id"])
    with pytest.raises(DomainError):
        manager._claim(worker["id"])
    assert manager.get(worker["id"]) == original and not model.requests
    with store.connection() as connection:
        for table in ("worker_core_source_reads", "worker_model_reservations", "action_receipts"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_legacy_approved_source_cannot_be_replaced_by_an_unapproved_new_format(
    tmp_path, monkeypatch
):
    store, manager, _model, worker = prepared(tmp_path, monkeypatch, source='原 "x"\n' * 120)
    token = manager._claim(worker["id"])
    asyncio.run(acquire(manager, worker["id"], token))
    complete = entries(store, worker)
    forged = source_message(complete, source_wire_format=RAW_TEXT_FORMAT)
    with store.unit_of_work() as work:
        ordinal = work.connection.execute(
            "SELECT message_ordinal FROM worker_core_source_reads"
        ).fetchone()[0]
        work.connection.execute(
            "UPDATE worker_messages SET message_json=? WHERE run_id=? AND ordinal=?",
            (canonical(forged), worker["id"], ordinal),
        )
    with pytest.raises(DomainError):
        entries(store, worker)


def test_offline_real_http_ledger_fresh_review_pass_and_completion_with_complete_raw_sources(
    tmp_path, monkeypatch
):
    source = '原始 complete "data" \\source\n' * 80
    store, manager, scripted, worker = prepared(
        tmp_path, monkeypatch, source=source, source_wire_format=RAW_TEXT_FORMAT, mode="live"
    )

    def responder(body, ordinal):
        if ordinal == 1:
            block = next(m for m in body["messages"] if m.get("content", "").startswith(PREFIX))
            packet = decode_source_text(block["content"][len(PREFIX) :])
            assert packet["sources"][0]["output"]["content"] == source
            assert packet["sources"][0]["receipt_id"].startswith("receipt_")
            return {"content": json.dumps(delivery("Complete original source analysis."))}
        assert ordinal == 2 and not body.get("tools")
        return {
            "content": json.dumps(
                {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.9}
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
        assert len(requests) == 2 and not scripted.requests
        with store.connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            CompletionGate(store).check(
                type("ReadWork", (), {"connection": connection})(),
                connection.execute(
                    "SELECT * FROM quests WHERE id=?", (worker["quest_id"],)
                ).fetchone(),
            )
            assert connection.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 1
            attempts = connection.execute(
                "SELECT state,input_charged,output_charged FROM model_budget_attempts"
            ).fetchall()
            assert len(attempts) == 2 and all(row["state"] == "settled" for row in attempts)
            assert sum(row["input_charged"] + row["output_charged"] for row in attempts) == 80
            assert connection.total_changes == 0
