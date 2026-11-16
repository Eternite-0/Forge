"""Private harness lifecycle regressions; no Provider requests."""

import importlib.util
import io
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from forge_api.desktop_host import read_frame


@pytest.fixture
def harness():
    source = Path(__file__).resolve().parents[1] / "scripts/phase2/live_core.py"
    spec = importlib.util.spec_from_file_location("private_live_core_tests", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Process:
    def __init__(self, *, exit_code=0, timeout=False):
        self.stdin = io.BytesIO()
        self.returncode = None
        self.exit_code = exit_code
        self.timeout = timeout
        self.killed = False
        self.terminated = False
        self.frames = None

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.frames = self.stdin.getvalue() if not self.stdin.closed else None
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("offline-core", timeout)
        self.returncode = -9 if self.killed else self.exit_code
        return self.returncode

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True


def test_private_bootstrap_uses_pipe_and_not_secret_environment(tmp_path, harness, monkeypatch):
    process = Process()
    observed = {}

    def spawn(command, **options):
        observed.update(command=command, **options)
        return process

    monkeypatch.setattr(harness.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        harness.httpx,
        "get",
        lambda *_args, **_kwargs: type(
            "Health", (), {"status_code": 200, "json": lambda self: {"session": "session"}}
        )(),
    )
    core = harness.LiveCore(
        tmp_path, provider_env={}, token_limit=10000, concurrency=1, private_control=True
    )
    assert core.start(timeout=1) is core
    assert "--core-child" in observed["command"]
    assert observed["stdin"] is subprocess.PIPE
    assert "FORGE_CORE_SECRET" not in observed["env"]
    process.stdin.seek(0)
    assert read_frame(process.stdin) == {"version": 1, "type": "bootstrap", "secret": core.secret}
    result = core.stop()
    assert result["graceful_confirmed"] is True
    assert result["returncode"] == 0 and result["forced_cleanup"] is False
    frames = io.BytesIO(process.frames)
    read_frame(frames)
    assert read_frame(frames) == {"version": 1, "type": "shutdown"}
    assert process.stdin.closed and core.stdout is None


@pytest.mark.parametrize(
    "case", ["timeout", "nonzero", "closed_pipe", "hard_cleanup", "already_stopped", "not_started"]
)
def test_unconfirmed_stop_never_claims_graceful_restart(tmp_path, harness, case):
    core = harness.LiveCore(
        tmp_path, provider_env={}, token_limit=10000, concurrency=1, private_control=True
    )
    process = Process(exit_code=3 if case == "nonzero" else 0, timeout=case == "timeout")
    core.process = None if case == "not_started" else process
    if case == "already_stopped":
        process.returncode = 0
    if case == "closed_pipe":
        process.stdin.close()
    result = core.stop(graceful=case != "hard_cleanup")
    assert result["graceful_confirmed"] is False
    assert result["protocol"] == "private_core_control_v1"
    if case in {"timeout", "closed_pipe", "hard_cleanup"}:
        assert result["forced_cleanup"] is True and process.killed
    if case == "not_started":
        assert result["outcome"] == "not_started" and result["returncode"] is None
    elif case == "already_stopped":
        assert result["outcome"] == "already_stopped_unconfirmed"
    assert core.process is None or process.stdin.closed


def test_default_legacy_stop_keeps_terminate_without_claiming_graceful(tmp_path, harness):
    core = harness.LiveCore(tmp_path, provider_env={}, token_limit=10000, concurrency=1)
    core.process = Process()
    result = core.stop()
    assert core.process.terminated and not core.process.killed
    assert result["protocol"] == "legacy_terminate_v1"
    assert result["graceful_confirmed"] is False


def test_private_real_core_restarts_and_stops_without_provider_calls(tmp_path, harness):
    router_ids, generations = [], []
    database = tmp_path / "data/forge.sqlite3"
    for _ in range(2):
        core = harness.LiveCore(
            tmp_path,
            provider_env={},
            token_limit=10000,
            concurrency=1,
            extra_env={"FORGE_ROUTER_MODEL_ID": ""},
            private_control=True,
        )
        try:
            core.start(timeout=25)
            with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as c:
                router_ids.append(
                    c.execute(
                        "SELECT router_session_id FROM router_state WHERE conversation_id='main'"
                    ).fetchone()[0]
                )
                generations.append(
                    json.loads(
                        c.execute(
                            "SELECT payload FROM events WHERE event_type='core.started' ORDER BY seq DESC LIMIT 1"
                        ).fetchone()[0]
                    )["generation"]
                )
        finally:
            result = core.stop()
        assert result["graceful_confirmed"] is True and result["returncode"] == 0
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as c:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert c.execute("SELECT COUNT(*) FROM model_budget_attempts").fetchone()[0] == 0
            stopped = json.loads(
                c.execute(
                    "SELECT payload FROM events WHERE event_type='core.stopped' ORDER BY seq DESC LIMIT 1"
                ).fetchone()[0]
            )
            assert stopped["generation"] == generations[-1]
    assert router_ids[0] == router_ids[1] and generations[0] != generations[1]
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as c:
        assert (
            c.execute("SELECT COUNT(*) FROM events WHERE event_type='core.started'").fetchone()[0]
            == 2
        )
        assert (
            c.execute("SELECT COUNT(*) FROM events WHERE event_type='core.stopped'").fetchone()[0]
            == 2
        )
