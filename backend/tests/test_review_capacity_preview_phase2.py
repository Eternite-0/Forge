"""Read-only advisory sizes over real local receipts; all Providers are offline."""

import asyncio
import hashlib
import json

import httpx
import pytest
import test_worker_intelligence_phase2 as worker_fixture
from test_reviewer_capacity_phase2 import PASS, pending
from test_reviewer_context_phase2 import reconstruct
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.domain import DomainError, ProtocolSupport
from forge_core.independent_review import (
    IndependentReviewService,
    _hash,
    _json,
    _legacy_review_messages,
    _packet,
    _review_messages,
    review_capacity_preview,
)
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway


def preview(store, worker, provider):
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        before = tuple(connection.iterdump())
        result = review_capacity_preview(store, connection, row, provider)
        assert tuple(connection.iterdump()) == before
        assert connection.total_changes == 0
        assert not any(isinstance(value, str) for value in result.values())
        assert not {"verdict", "packet", "packet_sha256", "content", "call_id"} & result.keys()
        return result


def report(tmp_path):
    content = _json({"finding": 'Required 中文 "quoted" \\ evidence', "detail": "complete" * 100})
    store, manager, model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/input.txt"}),
            tool("file_write", {"path": "work/findings.json", "content": content}, 2),
            response(delivery()),
        ],
        tools=["file_read", "file_write"],
        criteria=["json_review:work/findings.json"],
    )
    return store, manager, model, worker, content


def test_running_stages_are_pure_estimates_and_never_become_review_packets(tmp_path):
    store, manager, model, worker, content = report(tmp_path)
    generate = model.generate
    observed = []

    async def inspect(request):
        result = preview(store, worker, model)
        assert result["available"] and not result["complete"]
        for key in (
            "raw_packet_bytes",
            "native_input_reservation",
            "raw_headroom",
            "context_headroom",
            "budget_headroom",
        ):
            assert result[key] is None
        assert result["estimated_partial_raw_packet_bytes"] > 0
        assert result["estimated_partial_native_input_reservation"] > 0
        assert not any("floor" in key for key in result)
        with store.connection() as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT * FROM worker_runs WHERE id=?", (worker["id"],)
            ).fetchone()
            with pytest.raises(DomainError, match="assignment changed"):
                _packet(store, connection, row)
            assert (
                connection.execute(
                    "SELECT count(*) FROM events WHERE event_type IN ('worker.result','review.result')"
                ).fetchone()[0]
                == 0
            )
        observed.append(result)
        return await generate(request)

    model.generate = inspect
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert [item["settled_receipt_count"] for item in observed] == [0, 1, 2]
    assert [item["missing_artifact_count"] for item in observed] == [1, 1, 0]
    assert [item["artifact_bytes"] for item in observed] == [[], [], [len(content.encode())]]
    assert manager.get(worker["id"])["repairs"] == 0
    complete = preview(store, worker, model)
    assert complete["available"] and complete["complete"]
    assert complete["artifact_bytes"] == [len(content.encode())]
    assert complete["estimated_partial_raw_packet_bytes"] is None
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        packet = _packet(store, connection, row)
        before = _json(packet), _hash(packet)
        assert complete["raw_packet_bytes"] == len(before[0].encode())
        assert packet["artifacts"][0]["content"] == json.loads(content)
        assert packet["receipts"][0]["output"]["content"] == "Original source"
        assert (
            _json(_packet(store, connection, row)),
            _hash(_packet(store, connection, row)),
        ) == before


@pytest.mark.parametrize("structured", [False, True])
def test_complete_measurement_matches_native_payload_and_settled_model_review(
    tmp_path, monkeypatch, structured
):
    if structured:
        profile = worker_fixture.ModelProfile
        monkeypatch.setattr(
            worker_fixture,
            "ModelProfile",
            lambda **fields: profile(
                **fields,
                structured_output_support=ProtocolSupport(supported=True, source="test_fixture"),
            ),
        )
    store, manager, _model, worker, _content = report(tmp_path)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    posted = []

    def handle(request):
        posted.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": _json(PASS)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 15},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            supports_json_schema=structured,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(handle),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1),
    )
    measured = preview(store, worker, gateway)
    assert measured["available"] and measured["complete"] and not posted
    manager.providers = ProviderPool({"protocol": gateway})
    verdict = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert verdict["verdict"] == "PASS" and verdict["review_context"] == "independent_same_model"
    assert len(posted) == 1
    native = len(json.dumps(posted[0], ensure_ascii=False).encode()) + 64
    assert measured["native_input_reservation"] == native
    assert measured["budget_headroom"] == (
        measured["remaining_cumulative_tokens"] - native - measured["minimum_verdict_tokens"]
    )
    assert bool(posted[0].get("response_format")) is structured
    with store.connection() as connection:
        attempt = connection.execute("SELECT * FROM model_budget_attempts").fetchone()
        assert attempt["state"] == "settled" and attempt["input_reserved"] == native


@pytest.mark.parametrize("tamper", ["receipt", "arguments", "artifact", "team", "pending"])
def test_unverifiable_or_pending_evidence_never_has_usable_capacity(tmp_path, tamper):
    store, manager, model, worker, _content = report(tmp_path)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert preview(store, worker, model)["complete"]
    if tamper == "artifact":
        (tmp_path / "work/findings.json").write_text('{"changed":true}', encoding="utf-8")
    else:
        statement = {
            "receipt": "UPDATE worker_tool_calls SET receipt_id=(SELECT id FROM action_receipts "
            "ORDER BY rowid DESC LIMIT 1) WHERE tool_name='file_read'",
            "arguments": "UPDATE worker_tool_calls SET arguments_json='{}'",
            "team": "UPDATE team_proposals SET body_json='{}'",
            "pending": "UPDATE worker_tool_calls SET state='pending'",
        }[tamper]
        with store.unit_of_work() as work:
            work.connection.execute(statement)
    result = preview(store, worker, model)
    assert not result["available"] and not result["complete"]
    assert result["raw_packet_bytes"] is result["native_input_reservation"] is None
    assert result["estimated_partial_native_input_reservation"] is None
    assert result["budget_headroom"] is None


@pytest.mark.parametrize("state", ["reserved", "unknown", "settled"])
def test_unknown_or_unsettled_model_usage_never_becomes_available_capacity(tmp_path, state):
    store, manager, model, worker, _content = report(tmp_path)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO model_budget_attempts(attempt_id,call_id,purpose,workspace_id,quest_id,"
            "worker_run_id,provider_id,model_id,input_reserved,output_reserved,state,created_at) "
            "VALUES ('unknown-attempt','unknown-call','worker',?,?,?,?,?,100,100,?,0)",
            (
                store.workspace.id,
                worker["quest_id"],
                worker["id"],
                "protocol",
                "fixture",
                state,
            ),
        )
    result = preview(store, worker, model)
    assert not result["available"] and not result["complete"]
    assert result["raw_packet_bytes"] is result["native_input_reservation"] is None


def test_stale_run_and_caller_without_snapshot_are_unavailable_without_changes(tmp_path):
    store, manager, model, worker, _content = report(tmp_path)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        row = dict(connection.execute("SELECT * FROM worker_runs").fetchone())
        assert not review_capacity_preview(store, connection, row, model)["available"]
        connection.execute("BEGIN")
        row["token_spent"] += 1
        before = tuple(connection.iterdump())
        assert not review_capacity_preview(store, connection, row, model)["available"]
        assert tuple(connection.iterdump()) == before


def test_actual_revision_updates_sizes_without_reusing_previous_delivery(tmp_path, monkeypatch):
    store, manager, model, worker, packet, content = pending(
        tmp_path,
        monkeypatch,
        source_bytes=1000,
        explanation_repeats=100,
        raw_oversize=False,
    )
    model.responses.append(
        response(
            {
                "verdict": "NEEDS_REVISION",
                "findings": ["Reduce duplicate explanations while retaining the finding."],
                "revision_goals": ["Retain the finding and citation; remove repeated explanation."],
                "confidence": 0.8,
            }
        )
    )
    verdict = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert verdict["revision_applied"]
    prior = packet["artifacts"][0]
    revised = _json({**content, "explanation": "One complete explanation."})
    model.responses.extend(
        [
            tool(
                "file_revise",
                {
                    "path": "work/findings.json",
                    "prior_receipt_id": prior["receipt_id"],
                    "expected_sha256": prior["sha256"],
                    "content": revised,
                },
                50,
            ),
            response(delivery()),
        ]
    )
    generate = model.generate
    observations = []

    async def inspect(request):
        current = preview(store, worker, model)
        assert current["available"] and not current["complete"]
        assert current["native_input_reservation"] is None
        observations.append(current)
        return await generate(request)

    model.generate = inspect
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert len(observations) == 2
    assert observations[0]["artifact_bytes"] == [len(_json(content).encode())]
    assert observations[1]["artifact_bytes"] == [len(revised.encode())]
    assert observations[1]["settled_receipt_count"] == observations[0]["settled_receipt_count"] + 1
    assert preview(store, worker, model)["complete"]


def test_legacy_complete_packet_has_no_raw_cap_and_hash_remains_unchanged(tmp_path, monkeypatch):
    store, manager, model, worker, original, _ = pending(tmp_path, monkeypatch)
    before = _json(original), _hash(original)
    result = preview(store, worker, model)
    assert result["available"] and result["complete"]
    assert result["raw_packet_bytes"] == len(before[0].encode()) > 65536
    assert result["raw_packet_cap"] is None and result["raw_headroom"] is None
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        complete = _packet(store, connection, row)
        assert (_json(complete), _hash(complete)) == before
    assert manager.get(worker["id"])["state"] == "review_pending"


@pytest.mark.parametrize("raw_size", [32768, 32769, 65536, 65537])
@pytest.mark.parametrize("source_size", [4096, 4097])
def test_shared_representation_thresholds_preserve_every_byte(raw_size, source_size):
    source = ('quoted " \\ 中文\n' * 500).encode()[:source_size].decode(errors="ignore")
    source += "a" * (source_size - len(source.encode()))
    packet = {
        "receipts": [
            {
                "tool_name": "file_read",
                "output": {
                    "content": source,
                    "sha256": hashlib.sha256(source.encode()).hexdigest(),
                },
            }
        ],
        "user_constraints": {"items": []},
        "original_request": None,
        "updated_request": None,
        "padding": "",
    }
    packet["padding"] = "x" * (raw_size - len(_json(packet).encode()))
    assert len(_json(packet).encode()) == raw_size
    before = _hash(packet)
    system = {"role": "system", "content": "offline representation"}
    legacy = _legacy_review_messages(packet, system)
    assert len(legacy) == (3 if raw_size > 32768 and source_size > 4096 else 2)
    if len(legacy) == 3:
        assert legacy[1]["content"].endswith(source)
    else:
        assert json.loads(legacy[-1]["content"])["receipts"][0]["output"]["content"] == source
    messages = _review_messages(packet, system)
    assert reconstruct(messages) == packet
    assert _hash(reconstruct(messages)) == before
    assert len(json.dumps(messages, ensure_ascii=False).encode()) <= len(
        json.dumps(legacy, ensure_ascii=False).encode()
    )
    assert _hash(packet) == before
