"""Timeline previews use real local tool receipts with bounded public summaries.

The model protocol is explicitly Mock; SQLite and file operations are real.
"""

import asyncio
import json

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.timeline import read_timeline

_OLD_CONTENT = "PRIVATE_ORIGINAL_CONTENT_DO_NOT_EXPOSE"
_NEW_CONTENT = "PRIVATE_REVISED_CONTENT_DO_NOT_EXPOSE"


def _receipts(store):
    return [item for item in read_timeline(store)["items"] if item["kind"] == "tool_receipt"]


def _revise(tmp_path, path, *, monkeypatch=None, expire_after_effect=False):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write", "file_revise"])
    observed = []

    async def script(request):
        observed.append(request)
        if len(observed) == 1:
            return tool("file_write", {"path": path, "content": _OLD_CONTENT})
        if len(observed) == 2:
            with store.connection() as connection:
                source = connection.execute(
                    "SELECT r.id,r.result_json FROM action_receipts r "
                    "JOIN action_intents i ON i.id=r.intent_id WHERE i.tool_name='file_write'"
                ).fetchone()
            return tool(
                "file_revise",
                {
                    "path": path,
                    "content": _NEW_CONTENT,
                    "prior_receipt_id": source["id"],
                    "expected_sha256": json.loads(source["result_json"])["sha256"],
                },
                ordinal=2,
            )
        return response(delivery())

    if expire_after_effect:
        original_perform = manager.tools._perform

        def expire_lease_after_revision(intent, lease_token, cancelled):
            result = original_perform(intent, lease_token, cancelled)
            if intent["tool_name"] == "file_revise":
                # The local replacement happened, but its ownership expired before
                # ToolEngine._finish: a real receipt must retain uncertain status.
                with store.unit_of_work() as work:
                    work.connection.execute(
                        "UPDATE worker_runs SET lease_until=0 WHERE id=?", (worker["id"],)
                    )
            return result

        monkeypatch.setattr(manager.tools, "_perform", expire_lease_after_revision)
    model.generate = script
    outcome = asyncio.run(manager.run(worker["id"]))
    assert outcome["state"] == ("blocked" if expire_after_effect else "review_pending")
    assert (tmp_path / path).read_text(encoding="utf-8") == _NEW_CONTENT
    receipt = next(item for item in _receipts(store) if item["detail"]["tool"] == "file_revise")
    return store, receipt


@pytest.mark.parametrize("path", ["work/result.py", "work/报告.md"])
def test_file_revise_projects_only_safe_path_identity_and_verified_digest(tmp_path, path):
    store, receipt = _revise(tmp_path, path)
    assert receipt["status"] == "succeeded"
    assert receipt["execution_mode"] == "mock"
    with store.connection() as connection:
        recorded = connection.execute(
            "SELECT result_digest FROM action_receipts WHERE id=?", (receipt["event_id"],)
        ).fetchone()
    assert receipt["detail"] == {
        "tool": "file_revise",
        "path": path,
        "result_sha256": recorded["result_digest"],
    }
    serialized = json.dumps(read_timeline(store), ensure_ascii=False)
    assert _OLD_CONTENT not in serialized and _NEW_CONTENT not in serialized
    assert str(tmp_path) not in serialized
    for private_field in (
        "content",
        "parameters_json",
        "result_json",
        "prior_receipt_id",
        "prior_sha256",
        "stdout",
        "stderr",
        "body",
    ):
        assert f'"{private_field}":' not in serialized


@pytest.mark.parametrize(
    "name",
    [
        "secret-notes.txt",
        "TOKEN-notes.txt",
        "credential-notes.txt",
        "password-notes.txt",
        "public.env.backup",
        "public.key.txt",
        "public.pem.txt",
    ],
)
def test_file_revise_keeps_existing_sensitive_path_filter(tmp_path, name):
    store, receipt = _revise(tmp_path, f"work/{name}")
    assert receipt["status"] == "succeeded"
    assert set(receipt["detail"]) == {"tool", "result_sha256"}
    assert receipt["detail"]["tool"] == "file_revise"
    serialized = json.dumps(read_timeline(store), ensure_ascii=False)
    assert name not in serialized
    assert _OLD_CONTENT not in serialized and _NEW_CONTENT not in serialized


def test_actual_read_failure_remains_failed_without_leaking_error_payload(tmp_path):
    store, manager, _model, worker = prepared(
        tmp_path,
        [
            tool("file_read", {"path": "work/missing.txt"}),
            response(delivery()),
        ],
    )
    asyncio.run(manager.run(worker["id"]))
    receipt = next(item for item in _receipts(store) if item["detail"]["tool"] == "file_read")
    assert receipt["status"] == "failed"
    assert receipt["detail"]["path"] == "work/missing.txt"
    assert set(receipt["detail"]) == {"tool", "path", "result_sha256"}
    assert "resource_unavailable" not in json.dumps(read_timeline(store))


def test_revision_with_expired_ownership_retains_uncertain_status(tmp_path, monkeypatch):
    store, receipt = _revise(
        tmp_path, "work/result.txt", monkeypatch=monkeypatch, expire_after_effect=True
    )
    assert receipt["status"] == "uncertain"
    assert receipt["execution_mode"] == "mock"
    assert receipt["detail"]["path"] == "work/result.txt"
    assert set(receipt["detail"]) == {"tool", "path", "result_sha256"}
    serialized = json.dumps(read_timeline(store))
    assert _OLD_CONTENT not in serialized and _NEW_CONTENT not in serialized
