"""Mock reasoning; actual SQLite receipts and governed file writes in temporary source."""

import asyncio
import hashlib
import json

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.domain import DomainError
from forge_core.governance import GovernanceService
from forge_core.independent_review import _packet, _review_receipts, _specialist_artifact_sources


def specialist(tmp_path, *, content='{"finding":"source-bound specialist body"}'):
    value = delivery()
    # This free-text path must never be used to acquire another file.
    value["artifacts"] = ["work/unreceipted.json (untrusted prose)"]
    store, manager, _model, worker = prepared(
        tmp_path,
        [tool("file_write", {"path": "work/research.json", "content": content}), response(value)],
        tools=["file_write"],
    )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    return store, manager, worker


def packet(store, worker, *, parse_diagnostics=False):
    with store.connection() as c:
        c.execute("BEGIN")
        run = c.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        return _packet(store, c, run, parse_diagnostics=parse_diagnostics)


def test_delivery_only_specialist_exposes_owned_body_and_keeps_original_criteria(tmp_path):
    store, _manager, worker = specialist(tmp_path)
    (tmp_path / "work/unreceipted.json").write_text('{"private":"never read"}', encoding="utf-8")
    observed = packet(store, worker)
    assert observed["success_criteria"] == ["delivery_review"]
    assert len(observed["artifacts"]) == 1
    assert observed["artifacts"][0]["content"] == {"finding": "source-bound specialist body"}
    assert "never read" not in json.dumps(observed)


@pytest.mark.parametrize("tamper", ["stale", "revoked", "other_worker"])
def test_stale_ungranted_or_other_worker_source_is_not_read(tmp_path, tamper):
    store, _manager, worker = specialist(tmp_path)
    if tamper == "stale":
        (tmp_path / "work/research.json").write_text('{"changed":true}', encoding="utf-8")
    elif tamper == "revoked":
        GovernanceService(store).revoke_workspace(worker["quest_id"], "work", "local_user")
    else:
        with store.connection() as c:
            run = c.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
            _receipts, artifacts = _review_receipts(store, c, run)
            changed = dict(run, id="another-worker-run")
            with pytest.raises(DomainError):
                _specialist_artifact_sources(store, c, changed, ["delivery_review"], artifacts)
        return
    with pytest.raises(DomainError):
        packet(store, worker)


def test_invalid_specialist_json_remains_real_bytes_and_parse_diagnostic(tmp_path):
    store, _manager, worker = specialist(tmp_path, content='{"broken":')
    with pytest.raises(json.JSONDecodeError):
        packet(store, worker)
    observed = packet(store, worker, parse_diagnostics=True)
    assert observed["artifacts"][0]["format_error"]["kind"] == "invalid_json"
    assert observed["artifacts"][0]["sha256"] == hashlib.sha256(b'{"broken":').hexdigest()
    assert "content" not in observed["artifacts"][0]


def test_latest_owned_revision_body_only_and_original_receipt_chain_retained(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write", "file_revise"])
    original = '{"revision":1}'
    model.responses[:] = [
        tool("file_write", {"path": "work/research.json", "content": original}),
        response("placeholder"),
        response(delivery()),
    ]
    generate = model.generate

    async def revised(request):
        if len(model.requests) == 1:
            with store.connection() as c:
                receipt = c.execute("SELECT id FROM action_receipts").fetchone()[0]
            model.responses[0] = tool(
                "file_revise",
                {
                    "path": "work/research.json",
                    "content": '{"revision":2}',
                    "prior_receipt_id": receipt,
                    "expected_sha256": hashlib.sha256(original.encode()).hexdigest(),
                },
                2,
            )
        return await generate(request)

    model.generate = revised
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    observed = packet(store, worker)
    assert len(observed["artifacts"]) == 1 and observed["artifacts"][0]["content"] == {
        "revision": 2
    }
    assert len(observed["receipts"]) == 2


def test_extra_specialist_artifact_keeps_complete_evidence_and_native_admission_limits(
    tmp_path, monkeypatch
):
    from forge_core.independent_review import (
        IndependentReviewService,
        _json,
        review_capacity_preview,
    )

    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
    content = json.dumps({"large": "x" * 65000})
    store, manager, worker = specialist(tmp_path, content=content)
    before = manager.get(worker["id"])
    model = manager.providers.get(before["provider_id"])
    requests = len(model.requests)
    observed = packet(store, worker)
    assert "review_envelope" not in observed["contract"]
    assert len(_json(observed).encode("utf-8")) > 65536
    assert observed["artifacts"][0]["content"] == json.loads(content)
    assert observed["artifacts"][0]["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert observed["artifacts"][0]["receipt_id"] == observed["receipts"][0]["id"]
    with store.connection() as connection:
        connection.execute("BEGIN")
        run = connection.execute(
            "SELECT * FROM worker_runs WHERE id=?", (worker["id"],)
        ).fetchone()
        preview = review_capacity_preview(store, connection, run, model)
    assert preview["complete"] and preview["raw_packet_cap"] is None
    assert preview["raw_packet_bytes"] == len(_json(observed).encode("utf-8"))
    assert preview["native_input_reservation"] > before["model_context_window"] == 64000
    assert preview["context_headroom"] < 0 and preview["budget_headroom"] < 0
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["verdict"] == "BLOCKED"
    assert result["review_context"] == "deterministic_review_capacity"
    assert result["capacity_diagnostics"]["native_input_reservation"] > 64000
    assert len(model.requests) == requests
    assert (tmp_path / "work/research.json").read_bytes() == content.encode()
    after = manager.get(worker["id"])
    for key in ("model_context_window", "token_limit", "token_spent", "tool_limit", "step_limit"):
        assert after[key] == before[key]
