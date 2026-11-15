import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from test_tool_engine import mock_run as mock_run  # noqa: PLC0414 - shared pytest fixture
from test_tool_engine import reserve

from forge_core.domain import DomainError
from forge_core.store import Store
from forge_core.tool_engine import ToolEngine
from forge_core.worker_environment import process_environment, worker_environment


def _copy_run(store):
    with store.unit_of_work() as work:
        names = [row[1] for row in work.connection.execute("PRAGMA table_info(worker_runs)")]
        columns = ",".join(names)
        selection = ",".join("'other-run'" if name == "id" else name for name in names)
        work.connection.execute(
            f"INSERT INTO worker_runs({columns}) SELECT {selection} FROM worker_runs WHERE id='run'"
        )


def test_private_working_directory_really_isolates_relative_outputs_and_temp(mock_run):
    store, _, _, _ = mock_run
    _copy_run(store)
    first, second = worker_environment(store, "run"), worker_environment(store, "other-run")
    assert first["cwd"] != second["cwd"] and first["tmp"] != second["tmp"]
    script = (
        "import os,tempfile,pathlib,json; "
        "pathlib.Path('result.txt').write_text('owned'); "
        "print(json.dumps({'cwd':os.getcwd(),'tmp':tempfile.gettempdir()}))"
    )
    result = ToolEngine._run_process(
        [sys.executable, "-I", "-c", script],
        first["cwd"],
        process_environment(ToolEngine._environment(), first),
    )
    actual = json.loads(result["stdout"])
    assert Path(actual["cwd"]) == Path(first["cwd"])
    assert Path(actual["tmp"]) == Path(first["tmp"])
    assert (Path(first["cwd"]) / "result.txt").exists()
    assert not (Path(second["cwd"]) / "result.txt").exists()
    assert first["os_sandbox"] is False
    assert worker_environment(Store(store.path, workspace=store.workspace), "run") == first


def test_governed_tool_process_uses_run_directory_and_unchanged_receipt(mock_run):
    store, quest, worker, _ = mock_run
    parameters = {"source": "result = data * 2", "inputs": {"data": 7}}
    reserve(store, "isolated-numeric", "numeric_ast", parameters)
    engine = ToolEngine(store)
    kwargs = {
        "call_id": "isolated-numeric",
        "quest_id": quest.id,
        "worker_id": worker.id,
        "run_id": "run",
        "lease_token": "lease",
        "tool_name": "numeric_ast",
        "parameters": parameters,
    }
    result = asyncio.run(engine.execute(**kwargs))
    assert result["state"] == "succeeded"
    assert result["output"]["cwd"] == worker_environment(store, "run")["cwd"]
    assert result["output"]["result_json"] == "14"
    assert asyncio.run(engine.execute(**kwargs))["receipt_id"] == result["receipt_id"]


def test_directory_resolution_does_not_create_unknown_or_linked_environments(mock_run, tmp_path):
    store, _, _, _ = mock_run
    with pytest.raises(DomainError):
        worker_environment(store, "missing")
    with pytest.raises(DomainError):
        worker_environment(store, "../escape")
    expected = worker_environment(store, "run", create=False)
    assert not Path(expected["root"]).exists()
    external = tmp_path / "outside"
    external.mkdir()
    parent = Path(expected["root"]).parent
    parent.mkdir(parents=True)
    try:
        os.symlink(external, expected["root"], target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable on this host")
    with pytest.raises(DomainError, match="linked"):
        worker_environment(store, "run")
    assert not (external / "work").exists()


def test_environment_can_bind_uncommitted_run_without_committing_it(mock_run):
    store, _, _, _ = mock_run
    with pytest.raises(RuntimeError), store.unit_of_work() as work:
        names = [row[1] for row in work.connection.execute("PRAGMA table_info(worker_runs)")]
        columns = ",".join(names)
        selection = ",".join("'new-run'" if name == "id" else name for name in names)
        work.connection.execute(
            f"INSERT INTO worker_runs({columns}) SELECT {selection} FROM worker_runs WHERE id='run'"
        )
        environment = worker_environment(store, "new-run", create=False, connection=work.connection)
        assert not Path(environment["root"]).exists()
        assert work.connection.in_transaction
        raise RuntimeError("rollback new run")
    with pytest.raises(DomainError, match="persisted"):
        worker_environment(store, "new-run", create=False)
