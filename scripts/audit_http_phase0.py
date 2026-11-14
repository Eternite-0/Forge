"""Start and restart a real isolated API process, then record HTTP evidence."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".local" / "phase0" / f"http-{uuid4().hex[:8]}"
RUN.mkdir(parents=True)
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]

environment = os.environ.copy()
environment["FORGE_DB_PATH"] = str(RUN / "http.sqlite3")
command = [sys.executable, "-m", "uvicorn", "forge_api.main:app", "--host", "127.0.0.1", "--port", str(port)]
observations = []
for attempt in range(2):
    with (RUN / f"server-{attempt}.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(command, cwd=ROOT / "backend", env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as client:
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        raise RuntimeError(f"API process exited with {server.returncode}")
                    try:
                        health = client.get("/health")
                        health.raise_for_status()
                        break
                    except httpx.TransportError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("API startup timed out")
                        time.sleep(0.1)
                if attempt == 0:
                    sent = client.post("/api/conversation/message", json={"content": "帮我研究实际 HTTP 重启恢复"})
                    sent.raise_for_status()
                conversation = client.get("/api/conversation")
                quests = client.get("/api/quests")
                conversation.raise_for_status()
                quests.raise_for_status()
                observations.append({
                    "attempt": attempt,
                    "health_status": health.status_code,
                    "router_session_id": health.json()["router_session_id"],
                    "conversation_id": conversation.json()["conversation_id"],
                    "message_count": len(conversation.json()["messages"]),
                    "quest_count": len(quests.json()),
                    "quest_ids": [q["id"] for q in quests.json()],
                    "router_event_cursor": conversation.json()["router"]["event_cursor"],
                })
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)

assert observations[0]["router_session_id"] == observations[1]["router_session_id"]
assert observations[0]["quest_ids"] == observations[1]["quest_ids"]
assert observations[1]["message_count"] == 2
frontend_assets = []
vite_path = ROOT / "desktop" / "node_modules" / "vite" / "bin" / "vite.js"
if (ROOT / "desktop" / "dist" / "index.html").exists() and vite_path.exists():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        preview_port = listener.getsockname()[1]
    preview_command = [shutil.which("node"), str(vite_path), "preview", "--host", "127.0.0.1", "--port", str(preview_port), "--strictPort"]
    with (RUN / "frontend-preview.log").open("w", encoding="utf-8") as log:
        preview = subprocess.Popen(preview_command, cwd=ROOT / "desktop", stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{preview_port}", trust_env=False, timeout=2) as client:
                deadline = time.monotonic() + 20
                while True:
                    if preview.poll() is not None:
                        raise RuntimeError(f"Frontend preview exited with {preview.returncode}")
                    try:
                        index = client.get("/")
                        index.raise_for_status()
                        break
                    except httpx.TransportError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Frontend preview startup timed out")
                        time.sleep(0.1)
                frontend_assets.append({"path": "/", "http_status": index.status_code})
                asset_paths = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', index.text)
                assert len(asset_paths) >= 2
                for asset in asset_paths:
                    response = client.get(asset)
                    response.raise_for_status()
                    frontend_assets.append({"path": asset, "http_status": response.status_code, "bytes": len(response.content)})
        finally:
            if preview.poll() is None:
                preview.terminate()
                try:
                    preview.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    preview.kill()
                    preview.wait(timeout=5)
result = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "command": command,
    "observations": observations,
    "frontend_assets": frontend_assets,
    "verified": "Real HTTP API startup and durable records after process restart; no Worker recovery claimed.",
}
(ROOT / ".local" / "phase0" / "http-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
