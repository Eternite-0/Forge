"""Desktop control channel uses an isolated real Core subprocess."""

import hashlib
import hmac
import json
import os
import secrets
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.request

from forge_core.workspace_lock import WorkspaceLock


def _write_frame(stream, payload):
    encoded = json.dumps(payload).encode("utf-8")
    stream.write(struct.pack(">I", len(encoded)) + encoded)
    stream.flush()


def _read_frame(stream):
    header = stream.read(4)
    assert len(header) == 4
    size = struct.unpack(">I", header)[0]
    assert 0 < size <= 65536
    return json.loads(stream.read(size))


def test_desktop_host_bootstrap_authentication_and_shutdown(tmp_path):
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith("FORGE_"):
            env.pop(key)
    process = subprocess.Popen(
        [sys.executable, "-m", "forge_api", "--desktop-host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    secret = secrets.token_hex(32)
    try:
        _write_frame(
            process.stdin,
            {
                "version": 1,
                "type": "bootstrap",
                "secret": secret,
                "workspace_root": str(tmp_path),
                "credentials": {},
            },
        )
        assert _read_frame(process.stdout) == {
            "version": 1,
            "type": "credentials_required",
            "providers": [],
        }
        _write_frame(process.stdin, {"version": 1, "type": "credentials", "values": {}})
        ready = _read_frame(process.stdout)
        assert ready["type"] == "ready" and ready["version"] == 1 and ready["port"] > 0
        base = f"http://127.0.0.1:{ready['port']}"
        health = json.load(urllib.request.urlopen(base + "/health", timeout=5))
        bearer = hmac.new(
            bytes.fromhex(secret), health["session"].encode(), hashlib.sha256
        ).hexdigest()
        request = urllib.request.Request(
            base + "/health", headers={"Authorization": "Bearer " + bearer}
        )
        assert json.load(urllib.request.urlopen(request, timeout=5))["conversation_id"] == "main"
        request = urllib.request.Request(
            base + "/api/settings", headers={"Authorization": "Bearer " + bearer}
        )
        assert (
            json.load(urllib.request.urlopen(request, timeout=5))["core_generation"]
            == ready["generation"]
        )
        _write_frame(process.stdin, {"version": 1, "type": "shutdown", "request_id": "stop"})
        assert _read_frame(process.stdout) == {
            "version": 1,
            "type": "stopped",
            "request_id": "stop",
        }
        assert process.wait(timeout=15) == 0
        with sqlite3.connect(tmp_path / "data/forge.sqlite3") as connection:
            events = connection.execute(
                "SELECT event_type,payload FROM events WHERE event_type IN ('core.started','core.stopped') ORDER BY seq"
            ).fetchall()
        assert [event[0] for event in events] == ["core.started", "core.stopped"]
        assert all(json.loads(event[1])["generation"] == ready["generation"] for event in events)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_cli_blocks_desktop_for_same_workspace(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("FORGE_")}
    env["FORGE_CORE_SECRET"] = secrets.token_hex(32)
    env["FORGE_WORKSPACE_ROOT"] = str(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env["FORGE_PORT"] = str(port)
    cli = subprocess.Popen(
        [sys.executable, "-m", "forge_api"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    desktop = None
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).close()
                break
            except OSError:
                assert cli.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
        desktop = subprocess.Popen(
            [sys.executable, "-m", "forge_api", "--desktop-host"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        _write_frame(
            desktop.stdin,
            {
                "version": 1,
                "type": "bootstrap",
                "secret": secrets.token_hex(32),
                "workspace_root": str(tmp_path),
                "credentials": {},
            },
        )
        assert _read_frame(desktop.stdout) == {
            "version": 1,
            "type": "error",
            "code": "WORKSPACE_BUSY",
        }
        assert desktop.wait(timeout=15) == 2
        assert cli.poll() is None
        destination = tmp_path.parent / "blocked-backup"
        maintenance = subprocess.run(
            [
                sys.executable,
                "-m",
                "forge_api.maintenance",
                "backup",
                "--workspace-root",
                str(tmp_path),
                "--path",
                str(destination),
            ],
            capture_output=True,
            env=env,
            timeout=15,
            check=False,
        )
        assert maintenance.returncode == 2
        assert json.loads(maintenance.stdout) == {"ok": False, "code": "WORKSPACE_BUSY"}
        assert not destination.exists()
    finally:
        if desktop is not None:
            if desktop.poll() is None:
                desktop.kill()
                desktop.wait(timeout=10)
            desktop.stdin.close()
            desktop.stdout.close()
            desktop.stderr.close()
        cli.terminate()
        cli.wait(timeout=15)


def test_same_lock_instance_nested_releases_for_other_process(tmp_path):
    lock = WorkspaceLock(tmp_path)
    code = "import sys; from forge_core.workspace_lock import WorkspaceLock; lock = WorkspaceLock(sys.argv[1]); lock.__enter__(); lock.__exit__(None, None, None)"
    with lock:
        with lock:
            assert (
                subprocess.run(
                    [sys.executable, "-c", code, str(tmp_path)], capture_output=True, check=False
                ).returncode
                != 0
            )
        assert (
            subprocess.run(
                [sys.executable, "-c", code, str(tmp_path)], capture_output=True, check=False
            ).returncode
            != 0
        )
    assert (
        subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)], capture_output=True, check=False
        ).returncode
        == 0
    )


def test_desktop_host_requests_only_configured_provider_credentials(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("FORGE_")}
    env.update(
        FORGE_PROVIDER_ID="direct",
        FORGE_PROVIDER_KIND="openai_compatible",
        FORGE_PROVIDER_BASE_URL="https://example.invalid/v1",
        FORGE_PROVIDER_CREDENTIAL_REF="FORGE_PROVIDER_KEY_DIRECT",
        FORGE_PROVIDER_KEY_STALE="do-not-send",
    )
    env["FORGE_PROVIDERS_JSON"] = json.dumps(
        [
            {
                "id": "pooled",
                "kind": "openai_compatible",
                "base_url": "https://example.invalid/v1",
                "credential_ref": "FORGE_PROVIDER_KEY_POOL",
            }
        ]
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "forge_api", "--desktop-host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    secret = secrets.token_hex(32)
    try:
        _write_frame(
            process.stdin,
            {
                "version": 1,
                "type": "bootstrap",
                "secret": secret,
                "workspace_root": str(tmp_path),
                "credentials": {},
            },
        )
        required = _read_frame(process.stdout)
        assert required == {
            "version": 1,
            "type": "credentials_required",
            "providers": [
                {"provider_id": "direct", "credential_ref": "FORGE_PROVIDER_KEY_DIRECT"},
                {"provider_id": "pooled", "credential_ref": "FORGE_PROVIDER_KEY_POOL"},
            ],
        }
        _write_frame(
            process.stdin,
            {
                "version": 1,
                "type": "credentials",
                "values": {"FORGE_PROVIDER_KEY_DIRECT": "direct-fixture-secret"},
            },
        )
        ready = _read_frame(process.stdout)
        assert ready["type"] == "ready"
        _write_frame(process.stdin, {"version": 1, "type": "shutdown", "request_id": "stop"})
        assert _read_frame(process.stdout)["type"] == "stopped"
        assert process.wait(timeout=15) == 0
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        assert (
            "direct-fixture-secret" not in stderr
            and secret not in stderr
            and "do-not-send" not in stderr
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_maintenance_desktop_gate_eof_prevents_any_backup(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("FORGE_")}
    destination = tmp_path / "should-not-exist"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "forge_api.maintenance",
            "backup",
            "--workspace-root",
            str(tmp_path / "missing-workspace"),
            "--path",
            str(destination),
            "--desktop-gated",
        ],
        input=b"",
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert process.returncode == 5
    assert json.loads(process.stdout) == {"ok": False, "code": "DESKTOP_GATE_CLOSED"}
    assert not destination.exists() and not (tmp_path / "missing-workspace").exists()


def test_desktop_credentials_follow_saved_operator_config(tmp_path):
    from forge_core.operator_settings import write_operator_settings

    config = {
        "provider": {
            "id": "saved",
            "kind": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "credential_ref": "FORGE_PROVIDER_KEY_SAVED",
            "supports_tools": True,
            "supports_json_schema": True,
        },
        "router_model_id": "model",
        "router_context_limit": 32768,
        "model_token_limit": 1000,
        "model_concurrency_limit": 1,
        "network_hosts": [],
    }
    write_operator_settings(tmp_path, 0, config)
    env = {key: value for key, value in os.environ.items() if not key.startswith("FORGE_")}
    process = subprocess.Popen(
        [sys.executable, "-m", "forge_api", "--desktop-host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        _write_frame(
            process.stdin,
            {
                "version": 1,
                "type": "bootstrap",
                "secret": secrets.token_hex(32),
                "workspace_root": str(tmp_path),
                "credentials": {},
            },
        )
        assert _read_frame(process.stdout) == {
            "version": 1,
            "type": "credentials_required",
            "providers": [{"provider_id": "saved", "credential_ref": "FORGE_PROVIDER_KEY_SAVED"}],
        }
        _write_frame(process.stdin, {"version": 1, "type": "credentials", "values": {}})
        assert _read_frame(process.stdout)["type"] == "ready"
        _write_frame(process.stdin, {"version": 1, "type": "shutdown", "request_id": "stop"})
        assert _read_frame(process.stdout)["type"] == "stopped"
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
