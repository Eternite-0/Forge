"""Actual SQLite and governed file operations with explicitly Mock model protocols."""

import asyncio
import hashlib
import json
import sqlite3

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.domain import DomainError
from forge_core.independent_review import IndependentReviewService
from forge_core.tool_engine import verify_file_revision_chain
from forge_core.worker_runtime import worker_lease_seconds_from_env


def references(request):
    message = next(
        message["content"]
        for message in reversed(request.messages)
        if message.get("content", "").startswith("Core artifact receipt references")
    )
    return json.loads(message.split(": ", 1)[1])


def test_file_revise_keeps_immutable_version_chain_and_review_uses_latest_artifact(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path,
        [],
        tools=["file_write", "file_revise"],
        criteria=["json_review:work/result.json"],
    )
    observed = []

    async def script(request):
        if not observed:
            observed.append("create")
            return tool("file_write", {"path": "work/result.json", "content": '{"value":1}'})
        if len(observed) < 3:
            source = references(request)[0]
            observed.append(source["receipt_id"])
            return tool(
                "file_revise",
                {
                    "path": "work/result.json",
                    "prior_receipt_id": source["receipt_id"],
                    "expected_sha256": source["sha256"],
                    "content": json.dumps({"value": len(observed)}),
                },
                ordinal=len(observed),
            )
        if len(observed) == 3:
            observed.append("delivery")
            return response(delivery())
        packet = json.loads(request.messages[-1]["content"])
        assert packet["artifacts"][0]["content"] == {"value": 3}
        return response(
            {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
        )

    model.generate = script
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert json.loads((tmp_path / "work/result.json").read_text()) == {"value": 3}
    with store.connection() as connection:
        receipts = connection.execute("SELECT id FROM action_receipts ORDER BY rowid").fetchall()
        verified = verify_file_revision_chain(connection, worker["id"], receipts[-1][0])
        assert verified["revision_receipt_ids"] == [row[0] for row in reversed(receipts)]
        contents = [
            json.loads(row[0])["content"]
            for row in connection.execute(
                "SELECT parameters_json FROM action_intents ORDER BY rowid"
            )
        ]
        assert [json.loads(item) for item in contents] == [{"value": 1}, {"value": 2}, {"value": 3}]
    with store.unit_of_work() as work, pytest.raises(sqlite3.IntegrityError):
        work.connection.execute(
            "UPDATE action_intents SET parameters_json='{}' WHERE tool_name='file_write'"
        )
    reviewed = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert reviewed["verdict"] == "PASS" and reviewed["execution_mode"] == "mock"


def test_invalid_json_gets_bounded_parser_feedback_then_owned_revision_and_model_review(tmp_path):
    store, manager, model, worker = prepared(
        tmp_path, [], tools=["file_write", "file_revise"], criteria=["json_review:work/result.json"]
    )
    stages = []

    async def script(request):
        stage = len(stages)
        stages.append(stage)
        if stage == 0:
            return tool("file_write", {"path": "work/result.json", "content": '{"value":'})
        if stage in {1, 3}:
            return response(delivery())
        if stage == 2:
            assert any(
                "revision_request" in message.get("content", "") for message in request.messages
            )
            source = references(request)[0]
            return tool(
                "file_revise",
                {
                    "path": "work/result.json",
                    "prior_receipt_id": source["receipt_id"],
                    "expected_sha256": source["sha256"],
                    "content": '{"value":2}',
                },
                ordinal=2,
            )
        packet = json.loads(request.messages[-1]["content"])
        assert packet["artifacts"][0]["content"] == {"value": 2}
        return response(
            {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
        )

    model.generate = script
    service = IndependentReviewService(manager)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    parser_feedback = asyncio.run(service.review(worker["id"]))
    assert parser_feedback["verdict"] == "NEEDS_REVISION"
    assert parser_feedback["review_context"] == "deterministic_artifact_validation"
    assert len(stages) == 2 and manager.get(worker["id"])["state"] == "queued"
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    reviewed = asyncio.run(service.review(worker["id"]))
    assert reviewed["verdict"] == "PASS" and reviewed["review_context"] == "independent_same_model"
    assert len(stages) == 5 and manager.get(worker["id"])["repairs"] == 1
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='review.started'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "invalid", ["user_file", "unknown_receipt", "wrong_hash", "changed_content", "unknown_outcome"]
)
def test_file_revise_rejects_unowned_stale_or_unknown_source_without_overwrite(tmp_path, invalid):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write", "file_revise"])
    target = tmp_path / "work/result.json"
    original = tmp_path / "work/user.json"
    original.write_text("USER ORIGINAL", encoding="utf-8")
    calls = []

    async def script(request):
        calls.append(request)
        if len(calls) == 1:
            return tool("file_write", {"path": "work/result.json", "content": '{"value":1}'})
        source = references(request)[0]
        params = {
            "path": "work/result.json",
            "prior_receipt_id": source["receipt_id"],
            "expected_sha256": source["sha256"],
            "content": '{"value":2}',
        }
        if invalid == "user_file":
            params["path"] = "work/user.json"
        elif invalid == "unknown_receipt":
            params["prior_receipt_id"] = "receipt-not-owned"
        elif invalid == "wrong_hash":
            params["expected_sha256"] = "0" * 64
        elif invalid == "changed_content":
            target.write_text("USER CHANGED", encoding="utf-8")
        else:
            with store.unit_of_work() as work:
                work.connection.execute(
                    "UPDATE action_intents SET state='uncertain' WHERE id=(SELECT intent_id FROM action_receipts WHERE id=?)",
                    (source["receipt_id"],),
                )
        return tool("file_revise", params, ordinal=2)

    model.generate = script
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and len(calls) == 2
    assert original.read_text() == "USER ORIGINAL"
    assert target.read_text() == ("USER CHANGED" if invalid == "changed_content" else '{"value":1}')
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM action_intents WHERE tool_name='file_revise'"
            ).fetchone()[0]
            == 0
        )


def test_file_revise_cannot_expand_an_existing_create_only_contract(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write"])
    calls = []

    async def script(request):
        calls.append(request)
        if len(calls) == 1:
            return tool("file_write", {"path": "work/result.json", "content": '{"value":1}'})
        assert not any(item["name"] == "file_revise" for item in request.tools)
        with store.connection() as connection:
            receipt = connection.execute("SELECT id FROM action_receipts").fetchone()[0]
        return tool(
            "file_revise",
            {
                "path": "work/result.json",
                "prior_receipt_id": receipt,
                "expected_sha256": hashlib.sha256(b'{"value":1}').hexdigest(),
                "content": '{"value":2}',
            },
            ordinal=2,
        )

    model.generate = script
    assert asyncio.run(manager.run(worker["id"]))["state"] == "blocked"
    assert (tmp_path / "work/result.json").read_text() == '{"value":1}'


def test_file_revise_unknown_replace_result_blocks_replay_and_retains_previous_bytes(
    tmp_path, monkeypatch
):
    import forge_core.tool_engine as engine

    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write", "file_revise"])
    real_replace = engine.os.replace
    calls = []

    def uncertain_replace(source, target):
        real_replace(source, target)
        raise OSError("Simulated lost replacement acknowledgement")

    async def script(request):
        calls.append(request)
        if len(calls) == 1:
            return tool("file_write", {"path": "work/result.json", "content": '{"value":1}'})
        source = references(request)[0]
        monkeypatch.setattr(engine.os, "replace", uncertain_replace)
        return tool(
            "file_revise",
            {
                "path": "work/result.json",
                "prior_receipt_id": source["receipt_id"],
                "expected_sha256": source["sha256"],
                "content": '{"value":2}',
            },
            ordinal=2,
        )

    model.generate = script
    assert asyncio.run(manager.run(worker["id"]))["state"] == "blocked"
    assert (tmp_path / "work/result.json").read_text() == '{"value":2}'
    with pytest.raises(DomainError, match="Unresolved"):
        manager.retry(worker["id"])
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT state FROM action_intents WHERE tool_name='file_revise'"
            ).fetchone()[0]
            == "uncertain"
        )
        first = connection.execute(
            "SELECT parameters_json FROM action_intents WHERE tool_name='file_write'"
        ).fetchone()[0]
        assert json.loads(first)["content"] == '{"value":1}'
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
    assert not list((tmp_path / "work").glob(".forge-revise-*"))


@pytest.mark.parametrize("change", ["grant_revoked", "owner_paused", "content_changed"])
def test_file_revise_rechecks_authority_and_target_immediately_before_replace(
    tmp_path, monkeypatch, change
):
    import forge_core.tool_engine as engine

    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write", "file_revise"])
    target = tmp_path / "work/result.json"
    mkstemp = engine.tempfile.mkstemp
    calls = []

    def race(*args, **kwargs):
        temporary = mkstemp(*args, **kwargs)
        if change == "grant_revoked":
            with store.unit_of_work() as work:
                work.connection.execute("UPDATE task_grants SET revoked_at='revoked'")
        elif change == "owner_paused":
            manager.control(worker["id"], "pause")
        else:
            target.write_text("USER CHANGED", encoding="utf-8")
        return temporary

    async def script(request):
        calls.append(request)
        if len(calls) == 1:
            return tool("file_write", {"path": "work/result.json", "content": '{"value":1}'})
        source = references(request)[0]
        monkeypatch.setattr(engine.tempfile, "mkstemp", race)
        return tool(
            "file_revise",
            {
                "path": "work/result.json",
                "prior_receipt_id": source["receipt_id"],
                "expected_sha256": source["sha256"],
                "content": '{"value":2}',
            },
            ordinal=2,
        )

    model.generate = script
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == ("paused" if change == "owner_paused" else "blocked")
    assert target.read_text() == ("USER CHANGED" if change == "content_changed" else '{"value":1}')
    assert not list((tmp_path / "work").glob(".forge-revise-*"))


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "601", "invalid", ""])
def test_worker_lease_configuration_rejects_invalid_bound(value):
    with pytest.raises(ValueError):
        worker_lease_seconds_from_env({"FORGE_WORKER_LEASE_SECONDS": value})


def test_worker_lease_configuration_explicit_default_and_operator_bound():
    assert worker_lease_seconds_from_env({}) == 60
    assert worker_lease_seconds_from_env({"FORGE_WORKER_LEASE_SECONDS": "150"}) == 150
    assert worker_lease_seconds_from_env({"FORGE_WORKER_LEASE_SECONDS": "600"}) == 600


def test_configured_provider_timeout_stays_below_longer_worker_lease(tmp_path):
    from types import SimpleNamespace

    _store, manager, model, worker = prepared(tmp_path, [response(delivery())])
    manager.lease_seconds = 150
    model.config = SimpleNamespace(timeout_seconds=20)
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert model.requests[0].timeout_seconds == 20
