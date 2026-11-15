"""Offline workspace snapshots with verified SQLite and a conservative restore fence."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from contextlib import closing
from pathlib import Path, PurePosixPath

from dotenv import dotenv_values

from .migrations import SCHEMA_VERSION, _version, migrate
from .settings import Settings, Workspace
from .workspace_lock import WorkspaceLock, workspace_key

FENCE_NAME = ".forge-restore-fence.json"
MAX_ENTRIES = 100000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


class BackupError(ValueError):
    pass


def _journal_path(root):
    root = Path(root).absolute()
    digest = hashlib.sha256(workspace_key(root).encode("utf-8")).hexdigest()[:24]
    return root.parent / f".forge-workspace-{digest}.restore.json"


def _write_journal(root, value):
    path = _journal_path(root)
    if path.exists():
        raise BackupError("Interrupted restore requires recovery")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sync_directory(path):
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def recover_interrupted_restore(root):
    """Called while holding WorkspaceLock, before any Core database open."""
    root = Path(root).absolute()
    journal = _journal_path(root)
    if not journal.exists():
        return False
    try:
        _not_link(journal)
        if journal.stat().st_size > 8192:
            raise BackupError("Invalid restore journal")
        data = json.loads(journal.read_text(encoding="utf-8"))
        if (
            set(data) != {"version", "root", "stage", "rollback", "manifest_sha256"}
            or data["version"] != 1
            or not isinstance(data["root"], str)
            or workspace_key(data["root"]) != workspace_key(root)
            or not re.fullmatch(r"\.forge-restore-[A-Za-z0-9_-]+", data["stage"])
            or not re.fullmatch(r"\.forge-previous-[0-9a-f]{32}", data["rollback"])
            or not re.fullmatch(r"[0-9a-f]{64}", data["manifest_sha256"])
        ):
            raise BackupError("Invalid restore journal")
        stage, rollback = root.parent / data["stage"], root.parent / data["rollback"]
        if not root.exists() and rollback.is_dir():
            _not_link(rollback)
            os.replace(rollback, root)
            _sync_directory(root.parent)
        elif root.is_dir() and rollback.is_dir() and stage.is_dir() and not any(root.iterdir()):
            _not_link(root)
            _not_link(rollback)
            _not_link(stage)
            placeholder = root.parent / f".forge-empty-placeholder-{uuid.uuid4().hex}"
            os.replace(root, placeholder)
            try:
                os.replace(rollback, root)
            except BaseException:
                os.replace(placeholder, root)
                raise
            _sync_directory(root.parent)
        elif root.is_dir() and rollback.is_dir() and not stage.exists():
            _not_link(root)
            _not_link(rollback)
            fence = root / FENCE_NAME
            _not_link(fence)
            marker = json.loads(fence.read_text(encoding="utf-8"))
            if (
                marker.get("manifest_sha256") != data["manifest_sha256"]
                or marker.get("rollback_directory") != data["rollback"]
            ):
                raise BackupError("Restore journal does not match fence")
        elif root.is_dir() and not rollback.exists() and stage.is_dir():
            _not_link(root)
            _not_link(stage)
        else:
            raise BackupError("Ambiguous interrupted restore")
        journal.unlink()
        _sync_directory(root.parent)
        return True
    except (OSError, ValueError, TypeError, KeyError):
        raise BackupError("Interrupted restore requires manual recovery") from None


def _safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise BackupError("Invalid backup path")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or name != path.as_posix()
        or any(
            part in ("", ".", "..")
            or part.endswith((".", " "))
            or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
            for part in path.parts
        )
    ):
        raise BackupError("Invalid backup path")
    return path


def _not_link(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise BackupError("Links and reparse points are not permitted")
    return info


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace(root):
    root = Path(root)
    if not root.is_absolute() or not root.is_dir():
        raise BackupError("Workspace root must be an existing absolute directory")
    _not_link(root)
    settings = Settings.load(workspace_root=root)
    env_file = settings.workspace.resolve(os.environ.get("FORGE_ENV_FILE", ".env"))
    if not env_file.is_relative_to(settings.workspace.root):
        raise BackupError("Settings file must be inside the workspace")
    try:
        relative = settings.db_path.relative_to(settings.workspace.root)
    except ValueError:
        raise BackupError("Database must be inside the workspace") from None
    _safe_name(relative.as_posix())
    if not settings.db_path.is_file():
        raise BackupError("Workspace database missing")
    return settings, relative


def _check_database(path, workspace_id, expected_version=None):
    if not path.is_file():
        raise BackupError("Snapshot database missing")
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA trusted_schema=OFF")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("Snapshot database integrity failure")
            version = _version(conn)
            binding = conn.execute("SELECT id FROM workspace WHERE singleton=1").fetchone()
            if (
                not 7 <= version <= SCHEMA_VERSION
                or (expected_version is not None and version != expected_version)
                or binding is None
                or binding[0] != workspace_id
                or conn.execute("PRAGMA foreign_key_check").fetchone() is not None
            ):
                raise BackupError("Snapshot database identity or version mismatch")
            return version
    except (sqlite3.Error, ValueError):
        raise BackupError("Invalid snapshot database") from None


def _check_current_structure(path):
    with (
        closing(sqlite3.connect(":memory:", isolation_level=None)) as reference,
        closing(sqlite3.connect(path)) as candidate,
    ):
        migrate(reference)
        expected = {
            row[0]
            for row in reference.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        actual = {
            row[0]
            for row in candidate.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not expected <= actual:
            raise BackupError("Snapshot database lacks required tables")
        for name in expected:
            expected_columns = reference.execute(f'PRAGMA table_info("{name}")').fetchall()
            actual_columns = candidate.execute(f'PRAGMA table_info("{name}")').fetchall()
            expected_fk = reference.execute(f'PRAGMA foreign_key_list("{name}")').fetchall()
            actual_fk = candidate.execute(f'PRAGMA foreign_key_list("{name}")').fetchall()
            expected_sql = reference.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()[0]
            actual_sql = candidate.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()[0]
            if (
                expected_columns != actual_columns
                or expected_fk != actual_fk
                or re.sub(r"\s+", " ", expected_sql).strip()
                != re.sub(r"\s+", " ", actual_sql).strip()
            ):
                raise BackupError("Snapshot database schema mismatch")
            expected_indexes = reference.execute(f'PRAGMA index_list("{name}")').fetchall()
            actual_indexes = candidate.execute(f'PRAGMA index_list("{name}")').fetchall()
            actual_by_name = {row[1]: row[2:] for row in actual_indexes}
            for index in expected_indexes:
                if actual_by_name.get(index[1]) != index[2:]:
                    raise BackupError("Snapshot database index mismatch")
                if (
                    reference.execute(f'PRAGMA index_xinfo("{index[1]}")').fetchall()
                    != candidate.execute(f'PRAGMA index_xinfo("{index[1]}")').fetchall()
                ):
                    raise BackupError("Snapshot database index mismatch")
        expected_triggers = reference.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
        ).fetchall()
        actual_triggers = candidate.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
        ).fetchall()
        if expected_triggers != actual_triggers:
            raise BackupError("Snapshot database contains unexpected triggers")


def _check_staged_configuration(stage, root, settings, db_relative):
    env_path = settings.workspace.resolve(os.environ.get("FORGE_ENV_FILE", ".env"))
    relative = env_path.relative_to(root)
    staged_env = stage / relative
    values = dotenv_values(staged_env, interpolate=False) if staged_env.is_file() else {}
    if "FORGE_CORE_SECRET" in values or "FORGE_WORKSPACE_ROOT" in values:
        raise BackupError("Snapshot contains forbidden workspace settings")
    selected_id = os.environ.get(
        "FORGE_WORKSPACE_ID", values.get("FORGE_WORKSPACE_ID", settings.workspace.id)
    )
    if selected_id != settings.workspace.id:
        raise BackupError("Snapshot workspace setting mismatch")
    configured_db = os.environ.get(
        "FORGE_DB_PATH", values.get("FORGE_DB_PATH", "data/forge.sqlite3")
    )
    if not configured_db:
        raise BackupError("Snapshot database setting invalid")
    configured_path = Path(configured_db).expanduser()
    expected = (
        configured_path if configured_path.is_absolute() else root / configured_path
    ).resolve()
    if expected != root / db_relative:
        raise BackupError("Snapshot database setting mismatch")
    from .operator_settings import read_operator_settings

    read_operator_settings(stage)


def backup_workspace(root, destination):
    root = Path(root)
    if not root.is_absolute():
        raise BackupError("Workspace root must be absolute")
    with WorkspaceLock(root):
        recover_interrupted_restore(root)
        return _backup_workspace_locked(root, destination)


def _backup_workspace_locked(root, destination):
    settings, db_relative = _workspace(root)
    root, destination = settings.workspace.root, Path(destination).absolute()
    if (
        destination.exists()
        or destination == root
        or root in destination.parents
        or destination in root.parents
    ):
        raise BackupError("Invalid backup destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with WorkspaceLock(root):
        stage = Path(tempfile.mkdtemp(prefix=".forge-backup-", dir=destination.parent))
        try:
            files, directories = [], []
            content_root = stage / "files"
            content_root.mkdir()
            for current, dirs, names in os.walk(root, followlinks=False):
                current = Path(current)
                for name in dirs:
                    item = current / name
                    _not_link(item)
                    relative = item.relative_to(root).as_posix()
                    _safe_name(relative)
                    directories.append(relative)
                    if len(directories) + len(files) > MAX_ENTRIES:
                        raise BackupError("Backup entry limit exceeded")
                for name in names:
                    item = current / name
                    info = _not_link(item)
                    relative = item.relative_to(root).as_posix()
                    _safe_name(relative)
                    if relative in {
                        db_relative.as_posix(),
                        db_relative.as_posix() + "-wal",
                        db_relative.as_posix() + "-shm",
                    }:
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        raise BackupError("Unsupported workspace file")
                    output = content_root.joinpath(*PurePosixPath(relative).parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, output)
                    after = _not_link(item)
                    if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise BackupError("Workspace file changed during backup")
                    files.append(
                        {"path": relative, "size": output.stat().st_size, "sha256": _digest(output)}
                    )
                    if len(directories) + len(files) > MAX_ENTRIES:
                        raise BackupError("Backup entry limit exceeded")
            snapshot = content_root / db_relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            with (
                closing(sqlite3.connect(settings.db_path)) as source,
                closing(sqlite3.connect(snapshot)) as target,
            ):
                source.backup(target)
            version = _check_database(snapshot, settings.workspace.id)
            files.append(
                {
                    "path": db_relative.as_posix(),
                    "size": snapshot.stat().st_size,
                    "sha256": _digest(snapshot),
                }
            )
            if len(directories) + len(files) > MAX_ENTRIES:
                raise BackupError("Backup entry limit exceeded")
            manifest = {
                "format": 1,
                "workspace_id": settings.workspace.id,
                "database": db_relative.as_posix(),
                "schema_version": version,
                "directories": sorted(directories),
                "files": sorted(files, key=lambda item: item["path"]),
            }
            encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
            if len(encoded) > MAX_MANIFEST_BYTES:
                raise BackupError("Backup manifest too large")
            (stage / "manifest.json").write_bytes(encoded)
            os.replace(stage, destination)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return {"path": str(destination), "files": len(files), "schema_version": version}


def restore_workspace(root, archive):
    root = Path(root)
    if not root.is_absolute():
        raise BackupError("Workspace root must be absolute")
    with WorkspaceLock(root):
        recover_interrupted_restore(root)
        return _restore_workspace_locked(root, archive)


def _restore_workspace_locked(root, archive):
    settings, db_relative = _workspace(root)
    root, archive = settings.workspace.root, Path(archive).absolute()
    if (
        not archive.is_dir()
        or archive == root
        or root in archive.parents
        or archive in root.parents
    ):
        raise BackupError("Invalid backup source")
    _not_link(archive)
    with WorkspaceLock(root):
        manifest_path = archive / "manifest.json"
        _not_link(manifest_path)
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise BackupError("Backup manifest too large")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                set(manifest)
                != {"format", "workspace_id", "database", "schema_version", "directories", "files"}
                or type(manifest["format"]) is not int
                or manifest["format"] != 1
                or manifest["workspace_id"] != settings.workspace.id
                or manifest["database"] != db_relative.as_posix()
                or type(manifest["schema_version"]) is not int
                or not 1 <= manifest["schema_version"] <= SCHEMA_VERSION
                or not isinstance(manifest["directories"], list)
                or not isinstance(manifest["files"], list)
                or len(manifest["files"]) + len(manifest["directories"]) > MAX_ENTRIES
            ):
                raise BackupError("Invalid backup manifest")
            names = set()
            for directory in manifest["directories"]:
                name = _safe_name(directory).as_posix()
                key = name.casefold()
                if key in names:
                    raise BackupError("Duplicate backup path")
                names.add(key)
            for item in manifest["files"]:
                if (
                    not isinstance(item, dict)
                    or set(item) != {"path", "size", "sha256"}
                    or type(item["size"]) is not int
                    or item["size"] < 0
                    or not isinstance(item["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                ):
                    raise BackupError("Invalid backup entry")
                path = _safe_name(item["path"])
                if path.as_posix().casefold() in names:
                    raise BackupError("Duplicate backup path")
                names.add(path.as_posix().casefold())
                source = archive / "files"
                _not_link(source)
                for component in path.parts:
                    source = source / component
                    _not_link(source)
                if (
                    not source.is_file()
                    or source.stat().st_size != item["size"]
                    or _digest(source) != item["sha256"]
                ):
                    raise BackupError("Backup file integrity failure")
            if db_relative.as_posix().casefold() not in names:
                raise BackupError("Backup database missing")
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError):
            raise BackupError("Invalid backup") from None
        stage = Path(tempfile.mkdtemp(prefix=".forge-restore-", dir=root.parent))
        rollback = root.parent / f".forge-previous-{uuid.uuid4().hex}"
        try:
            for directory in manifest["directories"]:
                stage.joinpath(*_safe_name(directory).parts).mkdir(parents=True, exist_ok=True)
            for item in manifest["files"]:
                relative = _safe_name(item["path"])
                output = stage.joinpath(*relative.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(archive.joinpath("files", *relative.parts), output)
                if output.stat().st_size != item["size"] or _digest(output) != item["sha256"]:
                    raise BackupError("Backup changed during restore")
            _check_database(stage / db_relative, settings.workspace.id, manifest["schema_version"])
            _check_staged_configuration(stage, root, settings, db_relative)
            from .store import Store

            Store(stage / db_relative, workspace=Workspace(settings.workspace.id, stage))
            _check_database(stage / db_relative, settings.workspace.id, SCHEMA_VERSION)
            _check_current_structure(stage / db_relative)
            manifest_hash = _digest(manifest_path)
            (stage / FENCE_NAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "workspace_id": settings.workspace.id,
                        "reason": "restore_requires_review_and_budget_reconciliation",
                        "manifest_sha256": manifest_hash,
                        "rollback_directory": rollback.name,
                    }
                ),
                encoding="utf-8",
            )
            _write_journal(
                root,
                {
                    "version": 1,
                    "root": str(root),
                    "stage": stage.name,
                    "rollback": rollback.name,
                    "manifest_sha256": manifest_hash,
                },
            )
            try:
                os.replace(root, rollback)
            except BaseException:
                _journal_path(root).unlink()
                raise
            _sync_directory(root.parent)
            try:
                os.replace(stage, root)
                _sync_directory(root.parent)
            except BaseException:
                os.replace(rollback, root)
                _sync_directory(root.parent)
                _journal_path(root).unlink()
                raise
            _journal_path(root).unlink()
            _sync_directory(root.parent)
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
    return {
        "workspace_id": settings.workspace.id,
        "fenced": True,
        "rollback_directory": rollback.name,
    }


def review_restore(root, *, expected_manifest_sha256, expected_rollback_directory, reason):
    """Reconcile a fenced restore against its retained pre-restore database, offline."""
    from .domain import now_iso

    root = Path(root)
    if (
        not root.is_absolute()
        or not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256 or "")
        or not re.fullmatch(r"\.forge-previous-[0-9a-f]{32}", expected_rollback_directory or "")
        or not isinstance(reason, str)
        or not 10 <= len(reason.strip()) <= 1000
    ):
        raise BackupError("Invalid restore review")
    with WorkspaceLock(root):
        recover_interrupted_restore(root)
        settings, db_relative = _workspace(root)
        root = settings.workspace.root
        fence = root / FENCE_NAME
        if not fence.exists():
            with closing(sqlite3.connect(settings.db_path)) as connection:
                done = connection.execute(
                    "SELECT 1 FROM restore_reviews WHERE manifest_sha256=? AND rollback_directory=?",
                    (expected_manifest_sha256, expected_rollback_directory),
                ).fetchone()
            if done:
                return {"fenced": False, "already_reviewed": True}
            raise BackupError("Restore fence missing")
        _not_link(fence)
        if fence.stat().st_size > 8192:
            raise BackupError("Invalid restore fence")
        try:
            marker = json.loads(fence.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise BackupError("Invalid restore fence") from None
        if (
            marker.get("version") != 1
            or marker.get("workspace_id") != settings.workspace.id
            or marker.get("manifest_sha256") != expected_manifest_sha256
            or marker.get("rollback_directory") != expected_rollback_directory
        ):
            raise BackupError("Restore review binding mismatch")
        fence_hash = _digest(fence)
        rollback = root.parent / expected_rollback_directory
        _not_link(rollback)
        previous_db = rollback / db_relative
        _check_database(previous_db, settings.workspace.id, SCHEMA_VERSION)
        _check_current_structure(previous_db)
        with closing(
            sqlite3.connect(settings.db_path, isolation_level=None, uri=True)
        ) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "ATTACH DATABASE ? AS previous", (previous_db.as_uri() + "?mode=ro",)
            )
            connection.execute("BEGIN IMMEDIATE")
            reviewed = connection.execute(
                "SELECT 1 FROM restore_reviews WHERE fence_sha256=?", (fence_hash,)
            ).fetchone()
            if not reviewed:
                for schema in ("main", "previous"):
                    if (
                        connection.execute(
                            f"SELECT 1 FROM {schema}.model_budget_attempts WHERE state='reserved' LIMIT 1"
                        ).fetchone()
                        or connection.execute(
                            f"SELECT 1 FROM {schema}.action_intents WHERE state IN ('prepared','executing','uncertain') LIMIT 1"
                        ).fetchone()
                        or connection.execute(
                            f"SELECT 1 FROM {schema}.worker_tool_calls WHERE state IN ('pending','running','uncertain') LIMIT 1"
                        ).fetchone()
                        or connection.execute(
                            f"SELECT 1 FROM {schema}.worker_core_source_reads WHERE state IN ('pending','running','failed','uncertain') LIMIT 1"
                        ).fetchone()
                        or connection.execute(
                            f"SELECT breached FROM {schema}.model_budget_metadata WHERE singleton=1"
                        ).fetchone()[0]
                    ):
                        raise BackupError(
                            "Unresolved execution or model reservation requires separate reconciliation"
                        )
                for table in ("action_intents", "action_receipts", "worker_core_source_reads"):
                    if (
                        connection.execute(
                            f"SELECT 1 FROM (SELECT * FROM main.{table} EXCEPT SELECT * FROM previous.{table}) LIMIT 1"
                        ).fetchone()
                        or connection.execute(
                            f"SELECT 1 FROM (SELECT * FROM previous.{table} EXCEPT SELECT * FROM main.{table}) LIMIT 1"
                        ).fetchone()
                    ):
                        raise BackupError(
                            "Post-snapshot external effects require separate reconciliation"
                        )
                immutable = "attempt_id,call_id,purpose,workspace_id,quest_id,worker_run_id,parent_call_id,provider_id,model_id,input_reserved,output_reserved,cost_reserved,currency,input_price,output_price,price_version"
                if connection.execute(
                    f"SELECT 1 FROM (SELECT {immutable} FROM main.model_budget_attempts EXCEPT SELECT {immutable} FROM previous.model_budget_attempts) LIMIT 1"
                ).fetchone():
                    raise BackupError("Snapshot model ledger is not contained in retained ledger")
                if connection.execute(
                    "SELECT 1 FROM main.model_budget_attempts a JOIN previous.model_budget_attempts b USING(attempt_id) WHERE a.state IN ('settled','overrun') AND (COALESCE(a.input_charged,a.input_reserved)>COALESCE(b.input_charged,b.input_reserved) OR COALESCE(a.output_charged,a.output_reserved)>COALESCE(b.output_charged,b.output_reserved) OR COALESCE(a.cost_charged,a.cost_reserved,0)>COALESCE(b.cost_charged,b.cost_reserved,0)) LIMIT 1"
                ).fetchone():
                    raise BackupError("Retained model usage is below snapshot usage")
                if connection.execute(
                    "SELECT 1 FROM main.model_budget_metadata a,previous.model_budget_metadata b WHERE a.singleton=1 AND b.singleton=1 AND a.reconciled_cost IS NOT NULL AND (b.reconciled_cost IS NULL OR a.reconciled_currency!=b.reconciled_currency OR a.reconciled_cost>b.reconciled_cost)"
                ).fetchone():
                    raise BackupError("Retained historical cost is below snapshot cost")
                for table in ("model_budget_opening", "model_budget_scope_reconciliations"):
                    if connection.execute(
                        f"SELECT 1 FROM (SELECT * FROM main.{table} EXCEPT SELECT * FROM previous.{table}) LIMIT 1"
                    ).fetchone():
                        raise BackupError(
                            "Snapshot opening balance is not contained in retained ledger"
                        )
                if connection.execute(
                    "SELECT 1 FROM (SELECT * FROM main.model_budget_worker_teams EXCEPT SELECT * FROM previous.model_budget_worker_teams) LIMIT 1"
                ).fetchone():
                    raise BackupError("Worker team budget binding changed")
                connection.execute(
                    "INSERT OR IGNORE INTO model_budget_worker_teams SELECT * FROM previous.model_budget_worker_teams"
                )
                if connection.execute(
                    "SELECT 1 FROM main.worker_budget_ledger a LEFT JOIN previous.worker_budget_ledger b ON a.scope=b.scope AND a.scope_id=b.scope_id WHERE b.scope IS NULL OR a.token_limit!=b.token_limit OR a.tool_limit!=b.tool_limit OR a.tokens_allocated>b.tokens_allocated OR a.tools_allocated>b.tools_allocated LIMIT 1"
                ).fetchone():
                    raise BackupError("Retained Worker allocation is incompatible")
                if connection.execute(
                    "SELECT 1 FROM main.worker_cost_ledger a LEFT JOIN previous.worker_cost_ledger b ON a.scope=b.scope AND a.scope_id=b.scope_id AND a.currency=b.currency WHERE b.scope IS NULL OR a.limit_microunits!=b.limit_microunits OR a.reserved_microunits>b.reserved_microunits LIMIT 1"
                ).fetchone():
                    raise BackupError("Retained Worker cost allocation is incompatible")
                for table in (
                    "model_budget_attempts",
                    "model_budget_opening",
                    "model_budget_scope_reconciliations",
                    "worker_budget_ledger",
                    "worker_cost_ledger",
                ):
                    connection.execute(f"DELETE FROM main.{table}")
                    connection.execute(f"INSERT INTO main.{table} SELECT * FROM previous.{table}")
                connection.execute(
                    "UPDATE model_budget_metadata SET historical_usage=(SELECT historical_usage FROM previous.model_budget_metadata WHERE singleton=1),reconciled_cost=(SELECT reconciled_cost FROM previous.model_budget_metadata WHERE singleton=1),reconciled_currency=(SELECT reconciled_currency FROM previous.model_budget_metadata WHERE singleton=1),breached=(SELECT breached FROM previous.model_budget_metadata WHERE singleton=1) WHERE singleton=1"
                )
                for provider_id, retry_after in connection.execute(
                    "SELECT provider_id,retry_after FROM previous.model_provider_cooldowns"
                ).fetchall():
                    connection.execute(
                        "INSERT INTO model_provider_cooldowns VALUES (?,?) ON CONFLICT(provider_id) DO UPDATE SET retry_after=MAX(retry_after,excluded.retry_after)",
                        (provider_id, retry_after),
                    )
                now = now_iso()
                connection.execute(
                    "UPDATE approvals SET status='REVOKED' WHERE status IN ('PENDING','APPROVED') AND consumed_at IS NULL"
                )
                connection.execute(
                    "UPDATE task_grants SET revoked_at=? WHERE revoked_at IS NULL", (now,)
                )
                connection.execute(
                    "UPDATE team_proposals SET status='revoked' WHERE status IN ('proposed','approved')"
                )
                connection.execute(
                    "UPDATE quest_plans SET status='superseded' WHERE status IN ('proposed','approved','paused')"
                )
                connection.execute(
                    """UPDATE quests SET status='needs_input',
                    plan_revision=MAX(plan_revision,COALESCE((SELECT plan_revision FROM previous.quests p WHERE p.id=quests.id),0),COALESCE((SELECT MAX(revision) FROM previous.quest_plans p WHERE p.quest_id=quests.id),0),COALESCE((SELECT MAX(revision) FROM main.quest_plans p WHERE p.quest_id=quests.id),0))+1,
                    version=MAX(version,COALESCE((SELECT version FROM previous.quests p WHERE p.id=quests.id),0))+1,updated_at=?
                    WHERE status NOT IN ('completed','failed','cancelled','rejected')""",
                    (now,),
                )
                connection.execute(
                    "UPDATE worker_runs SET state='cancelled',generation=generation+1,lease_token=NULL,lease_until=NULL WHERE state IN ('queued','running','paused','blocked')"
                )
                connection.execute(
                    "UPDATE workers SET status='cancelled' WHERE status IN ('queued','running','paused','blocked')"
                )
                connection.execute(
                    "UPDATE task_nodes SET status='blocked' WHERE status IN ('queued','running','paused')"
                )
                connection.execute("UPDATE worker_inbox SET consumed=1 WHERE consumed=0")
                connection.execute(
                    "UPDATE event_inbox SET notice_dead_at=?,notice_error='restore_review',notice_token=NULL WHERE processed_at IS NULL AND notice_dead_at IS NULL",
                    (now,),
                )
                connection.execute(
                    "UPDATE event_deliveries SET state='dead',lease_token=NULL,lease_until=NULL,last_error='restore_review' WHERE state IN ('pending','leased')"
                )
                connection.execute(
                    "UPDATE outbox SET delivered_at=? WHERE delivered_at IS NULL", (now,)
                )
                connection.execute(
                    "UPDATE event_consumers SET cursor_seq=(SELECT COALESCE(MAX(seq),0) FROM events),scan_seq=(SELECT COALESCE(MAX(seq),0) FROM events),priority_scan_seq=(SELECT COALESCE(MAX(seq),0) FROM events)"
                )
                connection.execute(
                    "INSERT INTO restore_reviews VALUES (?,?,?,?,?)",
                    (
                        fence_hash,
                        expected_manifest_sha256,
                        expected_rollback_directory,
                        reason.strip(),
                        now,
                    ),
                )
            connection.commit()
        fence.unlink()
        _sync_directory(root)
        return {"fenced": False, "already_reviewed": bool(reviewed)}
