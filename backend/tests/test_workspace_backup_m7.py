"""Real SQLite backup/restore and tamper rejection for a stopped workspace."""

import hashlib
import hmac
import importlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forge_core.domain import ConversationMessage
from forge_core.migrations import SCHEMA_VERSION
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.workspace_backup import BackupError, backup_workspace, restore_workspace


def test_backup_restore_preserves_database_and_sets_replay_fence(tmp_path):
    root = tmp_path / "workspace"
    workspace = Workspace.from_path(root)
    store = Store(root / "data" / "forge.sqlite3", workspace=workspace)
    store.add_message(ConversationMessage(role="user", content="before snapshot"))
    archive = tmp_path / "backup"
    backup_workspace(root, archive)
    store.add_message(ConversationMessage(role="user", content="after snapshot"))
    restored = restore_workspace(root, archive)
    assert restored["fenced"] is True
    with sqlite3.connect(root / "data" / "forge.sqlite3") as connection:
        assert connection.execute("SELECT content FROM messages ORDER BY rowid").fetchall() == [
            ("before snapshot",)
        ]
    assert (root / ".forge-restore-fence.json").is_file()
    assert (tmp_path / restored["rollback_directory"]).is_dir()


def test_restore_review_preserves_later_budget_and_revokes_old_approval(tmp_path):
    from forge_core.domain import Quest
    from forge_core.governance import GovernanceService
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.runtime import ModelCallContext
    from forge_core.workspace_backup import review_restore

    root = tmp_path / "workspace"
    store = Store(root / "data" / "forge.sqlite3", workspace=Workspace.from_path(root))
    quest = Quest(title="task", goal="review")
    store.save_quest(quest)
    approval = GovernanceService(store).request_approval(
        quest.id, "publish", {"file": "one"}, "remote", "R2", "reason"
    )
    archive = tmp_path / "backup"
    backup_workspace(root, archive)
    ledger = ModelCallBudgetLedger(store.path, token_limit=1000, concurrency_limit=1)
    call = ledger.admit(
        ModelCallContext("after", "router", store.workspace.id),
        "p",
        "m",
        input_tokens=20,
        output_tokens=10,
        remote_timeout=30,
    )
    ledger.settle(call, {"input_tokens": 10, "output_tokens": 5})
    restored = restore_workspace(root, archive)
    marker = json.loads((root / ".forge-restore-fence.json").read_text(encoding="utf-8"))
    result = review_restore(
        root,
        expected_manifest_sha256=marker["manifest_sha256"],
        expected_rollback_directory=restored["rollback_directory"],
        reason="reviewed old state and retained usage",
    )
    assert result["fenced"] is False
    with sqlite3.connect(root / "data" / "forge.sqlite3") as connection:
        assert (
            connection.execute(
                "SELECT status FROM approvals WHERE id=?", (approval["id"],)
            ).fetchone()[0]
            == "REVOKED"
        )
        assert connection.execute(
            "SELECT input_charged,output_charged FROM model_budget_attempts WHERE attempt_id=?",
            (call,),
        ).fetchone() == (10, 5)
        assert connection.execute("SELECT COUNT(*) FROM restore_reviews").fetchone()[0] == 1
    repeated = review_restore(
        root,
        expected_manifest_sha256=marker["manifest_sha256"],
        expected_rollback_directory=restored["rollback_directory"],
        reason="reviewed old state and retained usage",
    )
    assert repeated == {"fenced": False, "already_reviewed": True}


def test_restore_review_rejects_unknown_model_attempt_and_preserves_fence(tmp_path):
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.runtime import ModelCallContext
    from forge_core.workspace_backup import review_restore

    root = tmp_path / "workspace"
    store = Store(root / "data" / "forge.sqlite3", workspace=Workspace.from_path(root))
    archive = tmp_path / "backup"
    backup_workspace(root, archive)
    ledger = ModelCallBudgetLedger(store.path, token_limit=1000, concurrency_limit=1)
    ledger.admit(
        ModelCallContext("after", "router", store.workspace.id),
        "p",
        "m",
        input_tokens=20,
        output_tokens=10,
        remote_timeout=30,
    )
    restored = restore_workspace(root, archive)
    marker = json.loads((root / ".forge-restore-fence.json").read_text(encoding="utf-8"))
    with pytest.raises(BackupError, match="Unresolved"):
        review_restore(
            root,
            expected_manifest_sha256=marker["manifest_sha256"],
            expected_rollback_directory=restored["rollback_directory"],
            reason="reviewed but remote state is unknown",
        )
    assert (root / ".forge-restore-fence.json").is_file()
    with sqlite3.connect(root / "data" / "forge.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM restore_reviews").fetchone()[0] == 0


def test_restore_review_is_idempotent_after_fence_removal_failure(tmp_path, monkeypatch):
    from forge_core.workspace_backup import review_restore

    root = tmp_path / "workspace"
    Store(root / "data" / "forge.sqlite3", workspace=Workspace.from_path(root))
    archive = tmp_path / "backup"
    backup_workspace(root, archive)
    restored = restore_workspace(root, archive)
    fence = root / ".forge-restore-fence.json"
    marker = json.loads(fence.read_text(encoding="utf-8"))
    original_unlink = Path.unlink

    def fail_fence(path, *args, **kwargs):
        if path == fence:
            raise OSError("injected fence removal failure")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", fail_fence)
        with pytest.raises(OSError, match="injected"):
            review_restore(
                root,
                expected_manifest_sha256=marker["manifest_sha256"],
                expected_rollback_directory=restored["rollback_directory"],
                reason="explicitly reviewed preserved state",
            )
    assert fence.exists()
    result = review_restore(
        root,
        expected_manifest_sha256=marker["manifest_sha256"],
        expected_rollback_directory=restored["rollback_directory"],
        reason="explicitly reviewed preserved state",
    )
    assert result == {"fenced": False, "already_reviewed": True}
    with sqlite3.connect(root / "data" / "forge.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM restore_reviews").fetchone()[0] == 1


def test_restored_core_blocks_all_mutations_and_background_replay(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    workspace = Workspace.from_path(root)
    Store(root / "data" / "forge.sqlite3", workspace=workspace)
    archive = tmp_path / "backup"
    backup_workspace(root, archive)
    restore_workspace(root, archive)
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(root))
    monkeypatch.delenv("FORGE_DB_PATH", raising=False)
    from forge_api import main

    importlib.reload(main)
    with TestClient(
        main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345)
    ) as client:
        session = client.get("/health").json()["session"]
        bearer = hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
        headers = {"Authorization": "Bearer " + bearer}
        assert client.get("/api/settings", headers=headers).json()["restore_fenced"] is True
        assert (
            client.post(
                "/api/conversation/message", headers=headers, json={"content": "continue"}
            ).status_code
            == 423
        )
        assert (
            client.post(
                "/api/approvals/anything/decision", headers=headers, json={"decision": "APPROVED"}
            ).status_code
            == 423
        )
        assert client.get("/health", headers=headers).json()["background"] == {
            "restore_fenced": "review_required"
        }


def test_failed_publish_restores_original_workspace(tmp_path, monkeypatch):
    from forge_core import workspace_backup

    root = tmp_path / "workspace"
    store = Store(root / "data" / "forge.sqlite3", workspace=Workspace.from_path(root))
    store.add_message(ConversationMessage(role="user", content="snapshot"))
    archive = tmp_path / "backup"
    backup_workspace(root, archive)
    store.add_message(ConversationMessage(role="user", content="must survive failure"))
    original_replace = workspace_backup.os.replace

    def fail_candidate(source, destination):
        if Path(source).name.startswith(".forge-restore-") and Path(destination) == root:
            raise OSError("injected publish failure")
        return original_replace(source, destination)

    monkeypatch.setattr(workspace_backup.os, "replace", fail_candidate)
    with pytest.raises(OSError, match="injected"):
        restore_workspace(root, archive)
    assert [item["content"] for item in store.list_messages()] == [
        "snapshot",
        "must survive failure",
    ]
    assert not (root / ".forge-restore-fence.json").exists()


def test_v7_snapshot_is_migrated_in_staging_before_publish(tmp_path):
    root = tmp_path / "workspace"
    store = Store(root / "data" / "forge.sqlite3", workspace=Workspace.from_path(root))
    store.add_message(ConversationMessage(role="user", content="legacy"))
    archive = tmp_path / "backup"
    backup_workspace(root, archive)
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    db = archive / "files" / manifest["database"]
    with closing(sqlite3.connect(db)) as connection:
        for table in (
            "superagent_trigger_deliveries",
            "superagent_subscriptions",
            "superagent_timers",
            "superagent_file_watches",
            "superagent_triggers",
            "superagent_threads",
            "superagent_sources",
            "delegation_contracts",
            "delegation_mandates",
            "task_project_links",
            "workspace_projects",
            "runtime_heartbeats",
            "worker_core_source_reads",
            "worker_model_reservations",
            "model_budget_attempts",
            "model_budget_metadata",
            "model_budget_opening",
            "model_provider_cooldowns",
            "model_budget_legacy_quests",
            "model_budget_scope_reconciliations",
            "context_archive_heads",
            "context_archive_sources",
            "context_archives",
            "memories",
            "revoked_memory_sources",
            "memory_source_index",
            "memory_source_cursor",
            "memory_terms",
            "constraint_records",
            "constraint_message_index",
            "constraint_index_cursor",
        ):
            connection.execute(f"DROP TABLE {table}")
        for column in (
            "output_limit",
            "model_max_output_tokens",
            "model_context_window",
            "next_attempt_at",
            "repairs",
        ):
            connection.execute(f"ALTER TABLE worker_runs DROP COLUMN {column}")
        connection.execute("DROP TABLE restore_reviews")
        connection.execute("DROP TABLE model_budget_worker_teams")
        connection.execute("DROP TABLE semantic_test_runs")
        connection.execute("UPDATE schema_meta SET version=7")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    entry = next(item for item in manifest["files"] if item["path"] == manifest["database"])
    entry.update(size=db.stat().st_size, sha256=hashlib.sha256(db.read_bytes()).hexdigest())
    manifest["schema_version"] = 7
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    restore_workspace(root, archive)
    with sqlite3.connect(root / manifest["database"]) as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT content FROM messages").fetchone()[0] == "legacy"


@pytest.mark.parametrize("setting", ["MAX_ENTRIES", "MAX_MANIFEST_BYTES"])
def test_backup_rejects_limits_before_publishing(tmp_path, monkeypatch, setting):
    from forge_core import workspace_backup

    root = tmp_path / "workspace"
    Store(root / "data" / "forge.sqlite3", workspace=Workspace.from_path(root))
    monkeypatch.setattr(workspace_backup, setting, 1)
    destination = tmp_path / "backup"
    with pytest.raises(BackupError):
        backup_workspace(root, destination)
    assert not destination.exists()


@pytest.mark.parametrize(
    "after_publish,placeholder", [(False, False), (False, True), (True, False)]
)
def test_crash_during_directory_swap_recovers_without_new_router(
    tmp_path, after_publish, placeholder
):
    root = tmp_path / "workspace"
    workspace = Workspace.from_path(root)
    store = Store(root / "data" / "forge.sqlite3", workspace=workspace)
    router_id = store.ensure_router()["router_session_id"]
    archive = tmp_path / "backup"
    backup_workspace(root, archive)
    code = """
import os, sys
from pathlib import Path
from forge_core import workspace_backup
original = workspace_backup.os.replace
def interrupted(source, destination):
    if Path(source).name.startswith('.forge-restore-') and Path(destination) == Path(sys.argv[1]):
        if sys.argv[3] == '1': original(source, destination)
        os._exit(91)
    return original(source, destination)
workspace_backup.os.replace = interrupted
workspace_backup.restore_workspace(sys.argv[1], sys.argv[2])
"""
    env = {key: value for key, value in os.environ.items() if not key.startswith("FORGE_")}
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), str(archive), "1" if after_publish else "0"],
        env=env,
        timeout=30,
        check=False,
    )
    assert result.returncode == 91
    if placeholder:
        root.mkdir()
    from test_desktop_host_m7 import _read_frame, _write_frame

    host = subprocess.Popen(
        [sys.executable, "-m", "forge_api", "--desktop-host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    secret = secrets.token_hex(32)
    try:
        _write_frame(
            host.stdin,
            {
                "version": 1,
                "type": "bootstrap",
                "secret": secret,
                "workspace_root": str(root),
                "credentials": {},
            },
        )
        assert _read_frame(host.stdout)["type"] == "credentials_required"
        _write_frame(host.stdin, {"version": 1, "type": "credentials", "values": {}})
        ready = _read_frame(host.stdout)
        base = f"http://127.0.0.1:{ready['port']}"
        health = json.load(urllib.request.urlopen(base + "/health", timeout=5))
        bearer = hmac.new(
            bytes.fromhex(secret), health["session"].encode(), hashlib.sha256
        ).hexdigest()
        request = urllib.request.Request(
            base + "/health", headers={"Authorization": "Bearer " + bearer}
        )
        assert (
            json.load(urllib.request.urlopen(request, timeout=5))["router_session_id"] == router_id
        )
        _write_frame(host.stdin, {"version": 1, "type": "shutdown", "request_id": "stop"})
        assert _read_frame(host.stdout)["type"] == "stopped"
        assert host.wait(timeout=15) == 0
    finally:
        if host.poll() is None:
            host.kill()
            host.wait(timeout=10)
        host.stdin.close()
        host.stdout.close()
        host.stderr.close()
    assert (root / ".forge-restore-fence.json").exists() is after_publish
    if placeholder:
        assert len(list(root.parent.glob(".forge-empty-placeholder-*"))) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path normalization")
def test_workspace_lock_key_matches_windows_extended_path(tmp_path):
    from forge_core.workspace_lock import WorkspaceLock, workspace_key

    extended = "\\\\?\\" + str(tmp_path)
    assert workspace_key(WorkspaceLock(tmp_path).path) == workspace_key(
        WorkspaceLock(extended).path
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "hash",
        "traversal",
        "duplicate",
        "ads",
        "future",
        "mixed",
        "missing_table",
        "missing_index",
        "missing_check",
    ],
)
def test_invalid_restore_never_changes_workspace(tmp_path, tamper):
    root = tmp_path / "workspace"
    workspace = Workspace.from_path(root)
    store = Store(root / "data" / "forge.sqlite3", workspace=workspace)
    store.add_message(ConversationMessage(role="user", content="preserve me"))
    archive = tmp_path / "backup"
    backup_workspace(root, archive)
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if tamper == "hash":
        manifest["files"][0]["sha256"] = "0" * 64
    elif tamper == "traversal":
        manifest["files"][0]["path"] = "../escape"
    elif tamper == "duplicate":
        manifest["files"].append(dict(manifest["files"][0]))
    elif tamper == "ads":
        manifest["files"][0]["path"] = "file:stream"
    elif tamper == "future":
        manifest["schema_version"] = 999999
    else:
        db_path = archive / "files" / manifest["database"]
        with closing(sqlite3.connect(db_path)) as connection:
            if tamper == "mixed":
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute("INSERT INTO schema_meta(singleton,version) VALUES (2,999)")
            elif tamper == "missing_table":
                connection.execute("DROP TABLE models")
            elif tamper == "missing_index":
                connection.execute("DROP INDEX idx_model_budget_quest")
            else:
                connection.execute("PRAGMA writable_schema=ON")
                connection.execute(
                    "UPDATE sqlite_master SET sql=replace(sql, 'CHECK(output_limit > 0)', '') WHERE type='table' AND name='worker_runs'"
                )
                connection.execute("PRAGMA writable_schema=OFF")
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        entry = next(item for item in manifest["files"] if item["path"] == manifest["database"])
        entry["size"] = db_path.stat().st_size
        entry["sha256"] = hashlib.sha256(db_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError):
        restore_workspace(root, archive)
    assert [row["content"] for row in store.list_messages()] == ["preserve me"]
