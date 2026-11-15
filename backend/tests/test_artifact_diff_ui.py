"""Real immutable tool history; model calls are explicitly offline Mock protocol."""

import asyncio
import hashlib
import hmac
import importlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.artifact_diff import (
    MAX_DIFF_BYTES,
    ArtifactDiffInvalid,
    ArtifactDiffTooLarge,
    ArtifactDiffUnavailable,
    read_artifact_diff,
)
from forge_core.domain import ConversationMessage
from forge_core.settings import Workspace


def history(tmp_path, contents=("one\ntwo\n", "one\nchanged\nthree\n"), path="work/result.txt"):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write", "file_revise"])
    calls = []

    async def script(request):
        ordinal = len(calls)
        calls.append(request)
        if ordinal >= len(contents):
            return response(delivery())
        if ordinal == 0:
            return tool("file_write", {"path": path, "content": contents[0]})
        with store.connection() as connection:
            prior = connection.execute(
                "SELECT id,result_json FROM action_receipts ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return tool(
            "file_revise",
            {
                "path": path,
                "content": contents[ordinal],
                "prior_receipt_id": prior["id"],
                "expected_sha256": json.loads(prior["result_json"])["sha256"],
            },
            ordinal=ordinal + 1,
        )

    model.generate = script
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        receipts = [
            row[0] for row in connection.execute("SELECT id FROM action_receipts ORDER BY rowid")
        ]
    return store, worker, receipts


def snapshot(store):
    with store.connection() as connection:
        return {
            name: [tuple(row) for row in connection.execute(f"SELECT * FROM {name} ORDER BY rowid")]
            for name in (
                "action_intents",
                "action_receipts",
                "worker_runs",
                "approvals",
                "task_grants",
            )
        }


def test_exact_creation_and_revision_diffs_survive_external_change_and_delete(tmp_path):
    store, worker, receipts = history(
        tmp_path, ("one\ntwo\n", "one\nchanged\nthree\n", "one\nchanged\nthree\nlast")
    )
    before = snapshot(store)
    initial = read_artifact_diff(store, receipts[0])
    assert initial["change"] == "created" and initial["base_receipt_id"] is None
    assert initial["added"] == 2 and initial["removed"] == 0
    assert initial["text"] == "--- /dev/null\n+++ b/work/result.txt\n@@ -0,0 +1,2 @@\n+one\n+two\n"
    revised = read_artifact_diff(store, receipts[1])
    assert revised["change"] == "revised" and revised["base_receipt_id"] == receipts[0]
    assert revised["added"] == 2 and revised["removed"] == 1
    assert "-two\n+changed\n+three\n" in revised["text"]
    assert revised["quest_id"] == worker["quest_id"] and revised["run_id"] == worker["id"]
    assert revised["execution_mode"] == "mock" and revised["origin"] is None
    assert revised["base_sha256"] == initial["sha256"]
    latest = read_artifact_diff(store, receipts[2])
    assert latest["added"] == 1 and latest["removed"] == 0
    assert "+last\n\\ No newline at end of file\n" in latest["text"]
    target = tmp_path / "work/result.txt"
    target.write_text("USER MODIFIED CURRENT FILE", encoding="utf-8")
    assert read_artifact_diff(store, receipts[1]) == revised
    target.unlink()
    assert read_artifact_diff(store, receipts[1]) == revised
    assert snapshot(store) == before
    serialized = json.dumps(revised)
    assert str(tmp_path) not in serialized
    assert all(
        f'"{key}"' not in serialized for key in ("parameters_json", "result_json", "content")
    )


def test_source_ids_require_user_conversation_and_content_integrity(tmp_path):
    store, worker, receipts = history(tmp_path)
    original = ConversationMessage(role="user", content="Original private request")
    update = ConversationMessage(role="user", content="Updated private request")
    store.add_message(original)
    store.add_message(update)
    metadata = {
        "original_request_message_id": original.id,
        "original_request_sha256": hashlib.sha256(original.content.encode()).hexdigest(),
        "goal_update_message_id": update.id,
        "goal_update_sha256": hashlib.sha256(update.content.encode()).hexdigest(),
    }
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET metadata=? WHERE id=?", (json.dumps(metadata), worker["quest_id"])
        )
    result = read_artifact_diff(store, receipts[-1])
    assert result["origin"] == {
        "original_request_message_id": original.id,
        "goal_update_message_id": update.id,
    }
    assert original.content not in json.dumps(result) and update.content not in json.dumps(result)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET role='assistant' WHERE id=?", (original.id,))
        work.connection.execute("UPDATE messages SET content='tampered' WHERE id=?", (update.id,))
    assert read_artifact_diff(store, receipts[-1])["origin"] is None
    with store.unit_of_work() as work:
        work.connection.execute("INSERT INTO conversations VALUES ('other','2026-01-01')")
        work.connection.execute(
            "UPDATE messages SET role='user',conversation_id='other' WHERE id=?", (original.id,)
        )
    assert read_artifact_diff(store, receipts[-1])["origin"] is None


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
def test_sensitive_paths_never_expose_retained_body(tmp_path, name):
    store, _worker, receipts = history(tmp_path, path=f"work/{name}")
    with pytest.raises(ArtifactDiffUnavailable):
        read_artifact_diff(store, receipts[-1])


@pytest.mark.parametrize("state", ["failed", "uncertain", "prepared", "executing"])
def test_non_successful_current_or_prior_intent_has_no_diff(tmp_path, state):
    store, _worker, receipts = history(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE action_intents SET state=?", (state,))
    with pytest.raises(ArtifactDiffUnavailable):
        read_artifact_diff(store, receipts[-1])


@pytest.mark.parametrize(
    "corruption", ["receipt_digest", "wrong_run", "different_mode", "outside_workspace"]
)
def test_storage_corruption_cannot_bypass_chain_or_workspace_identity(tmp_path, corruption):
    store, _worker, receipts = history(tmp_path)
    with store.unit_of_work() as work:
        if corruption == "receipt_digest":
            # Deliberately corrupt storage after removing its normal immutability guard.
            work.connection.execute("DROP TRIGGER action_receipts_immutable_update")
            work.connection.execute("UPDATE action_receipts SET result_digest=?", ("0" * 64,))
        elif corruption == "wrong_run":
            work.connection.execute(
                "INSERT INTO workers SELECT 'worker_other',quest_id,role,model_id,status,hidden,"
                "created_at,metadata FROM workers LIMIT 1"
            )
            work.connection.execute("UPDATE worker_runs SET worker_id='worker_other'")
        elif corruption == "different_mode":
            work.connection.execute("UPDATE worker_runs SET execution_mode='live'")
        else:
            work.connection.execute("DROP TRIGGER action_intents_immutable_binding")
            work.connection.execute(
                "UPDATE action_intents SET resource_scope=?",
                (str(tmp_path.parent / "outside.txt"),),
            )
    with pytest.raises(ArtifactDiffUnavailable):
        read_artifact_diff(store, receipts[-1])


def test_store_workspace_binding_and_invalid_id_are_closed(tmp_path):
    store, _worker, receipts = history(tmp_path)
    for identity in ("../private", "receipt_" + "x" * 101, "not-a-receipt", "receipt_x\n"):
        with pytest.raises(ArtifactDiffInvalid):
            read_artifact_diff(store, identity)
    with pytest.raises(ArtifactDiffUnavailable):
        read_artifact_diff(store, "receipt_missing")
    store.workspace = Workspace.from_path(tmp_path.parent / "another-workspace")
    with pytest.raises(ArtifactDiffUnavailable):
        read_artifact_diff(store, receipts[-1])


def test_diff_line_budget_and_bounded_output(tmp_path, monkeypatch):
    store, _worker, receipts = history(tmp_path, ("first\n", "line\n" * 2001))
    with pytest.raises(ArtifactDiffTooLarge):
        read_artifact_diff(store, receipts[-1])
    other = tmp_path / "bounded"
    other.mkdir()
    store, _worker, receipts = history(other, ("a" * 5000, "b" * 5000))
    monkeypatch.setattr("forge_core.artifact_diff.MAX_DIFF_BYTES", 1024)
    result = read_artifact_diff(store, receipts[-1])
    assert result["truncated"] and len(result["text"].encode("utf-8")) <= 1024 <= MAX_DIFF_BYTES
    assert result["added"] == result["removed"] == 1


def test_chain_parse_budget_prevents_unbounded_validation(tmp_path, monkeypatch):
    store, _worker, receipts = history(tmp_path)
    monkeypatch.setattr("forge_core.artifact_diff.MAX_CHAIN_BYTES", 2)

    def must_not_validate(*args):
        raise AssertionError("Large history must be rejected before repeated chain parsing")

    monkeypatch.setattr("forge_core.artifact_diff.verify_file_revision_chain", must_not_validate)
    with pytest.raises(ArtifactDiffTooLarge):
        read_artifact_diff(store, receipts[-1])


def test_authenticated_route_errors_are_sanitized_and_read_only(tmp_path, monkeypatch):
    store, _worker, receipts = history(tmp_path)
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path / "api-root"))
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "api-root/api.sqlite3"))
    from forge_api import main

    importlib.reload(main)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    session = client.get("/health").json()["session"]
    headers = {
        "Authorization": "Bearer "
        + hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
    }
    url = f"/api/artifacts/receipts/{receipts[-1]}/diff"
    assert client.get(url).status_code == 401
    before = snapshot(store)
    result = client.get(url, headers=headers)
    assert result.status_code == 200 and result.json() == read_artifact_diff(store, receipts[-1])
    errors = (
        ("receipt_missing", 404, "此文件的历史差异不可用。"),
        ("bad-id", 422, "无效的文件回执标识。"),
    )
    for identity, status, detail in errors:
        result = client.get(f"/api/artifacts/receipts/{identity}/diff", headers=headers)
        assert result.status_code == status and result.json()["detail"] == detail
        assert str(tmp_path) not in result.text and secret not in result.text
    monkeypatch.setattr(
        main, "read_artifact_diff", lambda *args: (_ for _ in ()).throw(ArtifactDiffTooLarge())
    )
    result = client.get(url, headers=headers)
    assert (
        result.status_code == 422 and result.json()["detail"] == "历史版本过大，无法在预览中比较。"
    )
    assert snapshot(store) == before
