"""Private framed stdio lifecycle for the native desktop parent."""

import asyncio
import json
import os
import re
import socket
import struct
import sys

import uvicorn

from forge_core.operator_settings import apply_operator_settings
from forge_core.providers.config import ProviderConfig
from forge_core.settings import Settings
from forge_core.workspace_backup import recover_interrupted_restore
from forge_core.workspace_lock import WorkspaceBusy, WorkspaceLock

MAX_FRAME = 65536


def _read_exact(stream, count):
    result = bytearray()
    while len(result) < count:
        chunk = stream.read(count - len(result))
        if not chunk:
            if not result:
                return None
            raise ValueError("Truncated control frame")
        result.extend(chunk)
    return bytes(result)


def read_frame(stream):
    header = _read_exact(stream, 4)
    if header is None:
        return None
    size = struct.unpack(">I", header)[0]
    if not 1 <= size <= MAX_FRAME:
        raise ValueError("Invalid control frame length")
    data = _read_exact(stream, size)
    if data is None:
        raise ValueError("Truncated control frame")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Invalid control message")
    return value


def write_frame(stream, value):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not 1 <= len(data) <= MAX_FRAME:
        raise ValueError("Invalid control frame length")
    stream.write(struct.pack(">I", len(data)) + data)
    stream.flush()


async def serve(stdin, stdout):
    import secrets

    message = await asyncio.to_thread(read_frame, stdin)
    if (
        not isinstance(message, dict)
        or set(message) != {"version", "type", "secret", "workspace_root", "credentials"}
        or message["version"] != 1
        or message["type"] != "bootstrap"
    ):
        raise ValueError("Invalid bootstrap")
    secret, root, credentials = message["secret"], message["workspace_root"], message["credentials"]
    if (
        not isinstance(secret, str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", secret)
        or not isinstance(root, str)
        or not os.path.isabs(root)
        or not isinstance(credentials, dict)
        or len(credentials) > 32
    ):
        raise ValueError("Invalid bootstrap")
    if credentials:
        raise ValueError("Credentials require the explicit request handshake")
    os.environ["FORGE_CORE_SECRET"] = secret
    os.environ["FORGE_WORKSPACE_ROOT"] = root
    from forge_core.settings import Workspace

    with WorkspaceLock(Workspace.from_path(root).root):
        recover_interrupted_restore(Workspace.from_path(root).root)
        apply_operator_settings(root)
        settings = Settings.load(workspace_root=root)
        await _credential_handshake_and_serve(stdin, stdout, settings, secrets)


async def _credential_handshake_and_serve(stdin, stdout, settings, secrets):
    required = _provider_requirements()
    for key in tuple(os.environ):
        if key.startswith("FORGE_PROVIDER_KEY_"):
            del os.environ[key]
    write_frame(stdout, {"version": 1, "type": "credentials_required", "providers": required})
    supplied = await asyncio.to_thread(read_frame, stdin)
    if (
        not isinstance(supplied, dict)
        or set(supplied) != {"version", "type", "values"}
        or supplied["version"] != 1
        or supplied["type"] != "credentials"
        or not isinstance(supplied["values"], dict)
    ):
        raise ValueError("Invalid credentials response")
    allowed = {item["credential_ref"] for item in required}
    for name, value in supplied["values"].items():
        if (
            name not in allowed
            or not isinstance(value, str)
            or not 1 <= len(value) <= 2560
            or any(not 0x20 <= ord(character) <= 0x7E for character in value)
        ):
            raise ValueError("Invalid credential mapping")
        os.environ[name] = value
    await _serve_locked(stdin, stdout, settings, secrets)


def _provider_requirements():
    names = (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
    )
    configs = []
    if any(os.environ.get(name) for name in names):
        configs.append(ProviderConfig.from_env())
    raw = json.loads(os.environ.get("FORGE_PROVIDERS_JSON", "[]"))
    if not isinstance(raw, list) or len(raw) > 16:
        raise ValueError("Invalid provider configuration")
    configs.extend(ProviderConfig(**item) for item in raw)
    ids, refs, result = set(), set(), []
    for config in configs:
        if config.id in ids or config.credential_ref in refs:
            raise ValueError("Conflicting provider credential configuration")
        ids.add(config.id)
        refs.add(config.credential_ref)
        result.append({"provider_id": config.id, "credential_ref": config.credential_ref})
    return result


async def _serve_locked(stdin, stdout, settings, secrets):
    from forge_api.main import app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_config=None, access_log=False, lifespan="on"
        )
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(20):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Core startup failed")
                await asyncio.sleep(0.01)
        write_frame(
            stdout,
            {
                "version": 1,
                "type": "ready",
                "port": port,
                "workspace_id": settings.workspace.id,
                "pid": os.getpid(),
                "generation": app.state.lifecycle_generation or secrets.token_hex(16),
            },
        )
        command = await asyncio.to_thread(read_frame, stdin)
        if command is not None and (
            set(command) != {"version", "type", "request_id"}
            or command["version"] != 1
            or command["type"] != "shutdown"
            or not isinstance(command["request_id"], str)
            or not 1 <= len(command["request_id"]) <= 200
        ):
            raise ValueError("Invalid shutdown")
        server.should_exit = True
        await task
        if command is not None:
            write_frame(
                stdout, {"version": 1, "type": "stopped", "request_id": command["request_id"]}
            )
    finally:
        server.should_exit = True
        if not task.done():
            await task
        sock.close()


def main():
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        asyncio.run(serve(sys.stdin.buffer, sys.stdout.buffer))
    except WorkspaceBusy:
        write_frame(sys.stdout.buffer, {"version": 1, "type": "error", "code": "WORKSPACE_BUSY"})
        raise SystemExit(2) from None
    except Exception:  # noqa: BLE001 - control channel never emits secrets or exception data
        print("Desktop Core lifecycle failed", file=sys.stderr)
        raise SystemExit(1) from None
