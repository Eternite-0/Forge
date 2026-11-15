"""Real Core/process supervision. No model Provider is used by these tests."""

import hashlib
import hmac
import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from forge_api.supervisor import supervise
from forge_core.domain import ConversationMessage
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.workspace_backup import FENCE_NAME


def fixture_command(root, body):
    script = root / "supervisor-child-fixture.py"
    script.write_text(
        "import json, os, sys, time\nfrom pathlib import Path\n"
        "from forge_api.desktop_host import read_frame\n"
        "bootstrap = read_frame(sys.stdin.buffer)\n"
        "assert bootstrap['type'] == 'bootstrap'\n"
        "assert 'FORGE_CORE_SECRET' not in os.environ\n" + body,
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def wait_until(check, *, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("Expected process observation did not arrive")


def clean_environment(root, secret):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("FORGE_")}
    environment["FORGE_CORE_SECRET"] = secret
    environment["FORGE_WORKSPACE_ROOT"] = str(root)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment["FORGE_PORT"] = str(port)
    return environment, f"http://127.0.0.1:{port}"


def health(base, token=None, *, path="/health"):
    try:
        with urlopen(
            Request(base + path, headers=({"Authorization": f"Bearer {token}"} if token else {})),
            timeout=0.5,
        ) as reply:
            return reply.status, json.load(reply)
    except HTTPError as error:
        with error:
            return error.code, json.load(error)
    except (URLError, TimeoutError, OSError):
        return None


def latest_start(root):
    path = root / "data" / "forge.sqlite3"
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0.5) as connection:
            row = connection.execute(
                "SELECT payload FROM events WHERE event_type='core.started' ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            return json.loads(row[0]) if row else None
    except sqlite3.Error:
        return None


def bearer(secret, challenge):
    return hmac.new(bytes.fromhex(secret), challenge.encode(), hashlib.sha256).hexdigest()


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_default_launcher_restarts_crashed_core_and_preserves_router_and_conversation(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    store = Store(root / "data" / "forge.sqlite3", workspace=Workspace.from_path(root))
    store.add_message(ConversationMessage(role="user", content="Persist this conversation"))
    identity = store.ensure_router()["router_session_id"]
    secret = secrets.token_hex(32)
    environment, base = clean_environment(root, secret)
    log_path = tmp_path / "supervised-core.log"
    with log_path.open("w", encoding="utf-8") as output:
        parent = subprocess.Popen(
            [sys.executable, "-m", "forge_api", "--workspace-root", str(root)],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            first = wait_until(lambda: latest_start(root))
            first_health = wait_until(lambda: health(base))[1]
            old_token = bearer(secret, first_health["session"])
            assert health(base, old_token)[1]["router_session_id"] == identity
            os.kill(first["pid"], signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
            second = wait_until(
                lambda: (
                    event
                    if (event := latest_start(root)) and event["pid"] != first["pid"]
                    else None
                )
            )
            current = wait_until(
                lambda: (
                    result
                    if (result := health(base)) and result[1]["session"] != first_health["session"]
                    else None
                )
            )
            assert parent.poll() is None and second["router_session_id"] == identity
            assert "router_session_id" not in health(base, old_token)[1]
            assert health(base, old_token, path="/api/conversation")[0] == 401
            assert health(base, bearer(secret, current[1]["session"]))[0] == 200
            reopened = Store(store.path, workspace=store.workspace)
            assert reopened.list_messages()[0]["content"] == "Persist this conversation"
        finally:
            stop_process(parent)
    wait_until(lambda: health(base) is None)
    log = log_path.read_text(encoding="utf-8")
    assert secret not in log and old_token not in log
    assert "restarting (1/5)" in log


def test_normal_child_exit_is_not_restarted_and_secret_uses_private_pipe(tmp_path):
    observation = tmp_path / "observation.json"
    command = fixture_command(
        tmp_path,
        f"Path({str(observation)!r}).write_text(json.dumps({{'pid':os.getpid(),'argv':sys.argv,"
        "'secret_length':len(bootstrap['secret']),'secret_in_env':'FORGE_CORE_SECRET' in os.environ}))\n"
        "raise SystemExit(0)\n",
    )
    secret = secrets.token_hex(32)
    assert supervise(tmp_path, command=command, secret=secret, initial_backoff=0.01) == 0
    value = json.loads(observation.read_text())
    assert value["secret_length"] == 64 and not value["secret_in_env"]
    assert secret not in observation.read_text()


def test_repeated_real_child_crashes_use_bounded_backoff(tmp_path, capsys):
    counter = tmp_path / "counter.txt"
    command = fixture_command(
        tmp_path,
        f"counter=Path({str(counter)!r})\n"
        "counter.write_text(str(int(counter.read_text())+1 if counter.exists() else 1))\n"
        "raise SystemExit(1)\n",
    )
    started = time.monotonic()
    assert (
        supervise(
            tmp_path,
            command=command,
            secret=secrets.token_hex(32),
            max_restarts=2,
            initial_backoff=0.025,
            max_backoff=0.05,
            stable_seconds=100,
        )
        == 1
    )
    assert counter.read_text() == "3"
    assert time.monotonic() - started >= 0.075
    assert "repeated process failures" in capsys.readouterr().err


def test_restore_fence_stops_process_restarts(tmp_path, capsys):
    counter = tmp_path / "counter.txt"
    command = fixture_command(
        tmp_path,
        f"Path({str(tmp_path / FENCE_NAME)!r}).write_text('review required')\n"
        f"Path({str(counter)!r}).write_text('1')\nraise SystemExit(1)\n",
    )
    assert (
        supervise(tmp_path, command=command, secret=secrets.token_hex(32), initial_backoff=0.01)
        == 3
    )
    assert counter.read_text() == "1"
    assert "restore review" in capsys.readouterr().err


def test_stop_signal_handler_requests_graceful_core_shutdown(tmp_path, monkeypatch):
    secret = secrets.token_hex(32)
    environment, base = clean_environment(tmp_path, secret)
    for key in tuple(os.environ):
        if key.startswith("FORGE_"):
            monkeypatch.delenv(key)
    for key, value in environment.items():
        if key.startswith("FORGE_"):
            monkeypatch.setenv(key, value)
    handlers = {}
    original_signal = signal.signal

    def observe_signal(signum, handler):
        previous = original_signal(signum, handler)
        if callable(handler):
            handlers[signum] = handler
        return previous

    monkeypatch.setattr(signal, "signal", observe_signal)
    failures = []

    def stop_when_ready():
        try:
            wait_until(lambda: latest_start(tmp_path) and health(base))
            # Exercise the installed OS-signal handler without depending on an
            # interactive Windows console (CI and Codex normally use pipes).
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        except (AssertionError, KeyError, OSError) as error:
            failures.append(error)

    thread = threading.Thread(target=stop_when_ready, daemon=True)
    thread.start()
    assert supervise(tmp_path, secret=secret, shutdown_timeout=5) == 0
    thread.join(timeout=15)
    assert not thread.is_alive() and not failures
    assert health(base) is None
    with sqlite3.connect(tmp_path / "data" / "forge.sqlite3") as c:
        events = [
            row[0]
            for row in c.execute(
                "SELECT event_type FROM events WHERE event_type IN ('core.started','core.stopped') ORDER BY seq"
            )
        ]
    assert events == ["core.started", "core.stopped"]


def test_duplicate_default_supervisors_do_not_share_a_workspace(tmp_path):
    secret = secrets.token_hex(32)
    environment, base = clean_environment(tmp_path, secret)
    command = [sys.executable, "-m", "forge_api", "--workspace-root", str(tmp_path)]
    first = subprocess.Popen(
        command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        original = wait_until(lambda: latest_start(tmp_path))
        wait_until(lambda: health(base))
        second = subprocess.run(
            command, env=environment, capture_output=True, timeout=10, check=False
        )
        assert second.returncode == 2
        assert "already running" in second.stderr.decode()
        assert latest_start(tmp_path)["pid"] == original["pid"]
        assert secret.encode() not in second.stdout + second.stderr
    finally:
        stop_process(first)
    wait_until(lambda: health(base) is None)


def test_invalid_secret_stops_before_starting_child_without_echoing_it(tmp_path, capsys):
    secret = "intentionally-invalid-secret-value"
    assert supervise(tmp_path, secret=secret) == 2
    assert secret not in capsys.readouterr().err
    assert not (tmp_path / "data" / "forge.sqlite3").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object descendant-drain contract")
def test_restart_waits_for_previous_core_descendants_to_exit(tmp_path):
    state = tmp_path / "previous-descendant.json"
    observation = tmp_path / "replacement.json"
    command = fixture_command(
        tmp_path,
        "import ctypes, subprocess\nfrom ctypes import wintypes\n"
        f"state=Path({str(state)!r})\n"
        "if not state.exists():\n"
        "    descendant=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)'])\n"
        "    state.write_text(json.dumps(descendant.pid))\n"
        "    raise SystemExit(1)\n"
        "kernel=ctypes.WinDLL('kernel32',use_last_error=True)\n"
        "kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]\n"
        "kernel.OpenProcess.restype=wintypes.HANDLE\n"
        "kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]\n"
        "kernel.WaitForSingleObject.restype=wintypes.DWORD\n"
        "kernel.CloseHandle.argtypes=[wintypes.HANDLE]\n"
        "handle=kernel.OpenProcess(0x00100000,False,json.loads(state.read_text()))\n"
        "drained=not handle or kernel.WaitForSingleObject(handle,0)==0\n"
        "if handle: kernel.CloseHandle(handle)\n"
        f"Path({str(observation)!r}).write_text(json.dumps({{'drained':drained}}))\n"
        "raise SystemExit(0 if drained else 9)\n",
    )
    assert (
        supervise(
            tmp_path,
            command=command,
            secret=secrets.token_hex(32),
            max_restarts=1,
            initial_backoff=0.01,
        )
        == 0
    )
    assert json.loads(observation.read_text()) == {"drained": True}


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object parent-death contract")
def test_hard_parent_termination_kills_core_and_descendants(tmp_path):
    state = tmp_path / "processes.json"
    command = fixture_command(
        tmp_path,
        "import subprocess\n"
        "descendant=subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)'])\n"
        f"Path({str(state)!r}).write_text(json.dumps([os.getpid(),descendant.pid]))\n"
        "read_frame(sys.stdin.buffer)\n",
    )
    parent_code = (
        "import sys\nfrom forge_api.supervisor import supervise\n"
        f"raise SystemExit(supervise(sys.argv[1],command={command!r}))"
    )
    environment, _ = clean_environment(tmp_path, secrets.token_hex(32))
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code, str(tmp_path)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pids = wait_until(lambda: json.loads(state.read_text()) if state.exists() else None)
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handles = [kernel.OpenProcess(0x00100000, False, pid) for pid in pids]  # SYNCHRONIZE
        assert all(handles)
        try:
            parent.kill()
            parent.wait(timeout=5)
            assert all(kernel.WaitForSingleObject(handle, 5000) == 0 for handle in handles)
        finally:
            for handle in handles:
                if handle:
                    kernel.CloseHandle(handle)
    finally:
        stop_process(parent)
