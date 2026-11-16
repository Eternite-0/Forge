"""Offline model fixtures and real governed filesystem effects; no Provider calls."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool
from test_worker_write_scope_repair import narrow_grant

from forge_core.domain import DomainError


def setup(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["directory_create"])
    root = narrow_grant(store, worker)
    return store, manager, model, worker, root


@pytest.mark.parametrize("existing", [True, False])
def test_directory_create_ensures_only_authorized_ordinary_directory(tmp_path, existing):
    store, manager, model, worker, root = setup(tmp_path)
    target = root if existing else root + "/report"
    model.responses[:] = [tool("directory_create", {"path": target}), response(delivery())]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 0
    assert (tmp_path / target).is_dir()
    assert len(model.requests) == 2
    with store.connection() as connection:
        call = connection.execute("SELECT * FROM worker_tool_calls").fetchone()
        receipt = connection.execute("SELECT * FROM action_receipts").fetchone()
        intent = connection.execute("SELECT * FROM action_intents").fetchone()
        assert call["state"] == intent["state"] == "succeeded"
        assert call["receipt_id"] == receipt["id"]
        assert json.loads(receipt["result_json"]) == {"path": str(tmp_path / target)}
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1


def test_existing_file_is_not_reported_as_created_directory(tmp_path):
    store, manager, model, worker, root = setup(tmp_path)
    target = tmp_path / root / "report"
    target.write_text("unchanged", encoding="utf-8")
    model.responses[:] = [tool("directory_create", {"path": str(target)})]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and target.read_text(encoding="utf-8") == "unchanged"
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("linked_parent", [False, True])
def test_directory_create_does_not_accept_symlink_alias_even_within_grant(
    tmp_path, linked_parent
):
    store, manager, model, worker, root = setup(tmp_path)
    real = tmp_path / root / "real"
    real.mkdir()
    alias = tmp_path / root / "alias"
    try:
        os.symlink(real, alias, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Creating directory symlinks is unavailable on this host")
        import _winapi

        _winapi.CreateJunction(str(real), str(alias))
    target = alias / "new" if linked_parent else alias
    model.responses[:] = [tool("directory_create", {"path": str(target)})]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked"
    assert not (real / "new").exists()
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("linked_parent", [False, True])
def test_reparse_point_is_not_accepted_as_ordinary_directory(tmp_path, monkeypatch, linked_parent):
    store, manager, model, worker, root = setup(tmp_path)
    target = tmp_path / root
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    requested = root + "/new" if linked_parent else root
    model.responses[:] = [tool("directory_create", {"path": requested})]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked"
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


def test_arbitrary_fileexists_exception_without_directory_stays_uncertain(tmp_path, monkeypatch):
    store, manager, model, worker, root = setup(tmp_path)
    target = tmp_path / root / "missing"
    original = Path.mkdir

    def fail(path, *args, **kwargs):
        if path == target:
            raise FileExistsError("injected mkdir failure without directory")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail)
    model.responses[:] = [tool("directory_create", {"path": str(target)})]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and not target.exists()
    with store.connection() as connection:
        assert connection.execute("SELECT state FROM action_intents").fetchone()[0] == "uncertain"
        assert connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


def test_unknown_directory_creation_is_never_automatically_replayed(tmp_path, monkeypatch):
    store, manager, model, worker, root = setup(tmp_path)
    target = tmp_path / root / "created-before-crash"
    original = manager.tools._perform
    effects = []

    def interrupted(*args):
        original(*args)
        effects.append(args[0]["id"])
        raise RuntimeError("process result lost after side effect")

    monkeypatch.setattr(manager.tools, "_perform", interrupted)
    model.responses[:] = [tool("directory_create", {"path": str(target)})]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and target.is_dir()
    assert len(effects) == len(model.requests) == 1
    with store.connection() as connection:
        intent = dict(connection.execute("SELECT * FROM action_intents").fetchone())
    assert intent["state"] == "uncertain"
    # Retry and normal scheduling cannot replay an operation with unknown effects.
    with pytest.raises(DomainError, match="Unresolved tool result"):
        manager.retry(worker["id"])
    assert asyncio.run(manager.run(worker["id"]))["state"] == "blocked"
    assert len(effects) == 1

