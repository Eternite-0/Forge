"""Run the shared Core launcher as real processes in CLI and desktop directories."""

import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from forge_core.settings import Workspace
from forge_core.store import Store


def test_core_launcher_restart_preserves_workspace_and_rejects_stale_state(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    (workspace / ".env").write_text(
        f"FORGE_DB_PATH=state/core.sqlite3\nFORGE_PORT={port}\n", encoding="utf-8"
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("FORGE_")}
    # The explicit CLI option must win over a conflicting process setting.
    environment["FORGE_WORKSPACE_ROOT"] = str(tmp_path / "wrong-workspace")
    # Inject a labeled mock only in the child test process while exercising the real launcher.
    hook = tmp_path / "test-injection"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(
        """
import json
from forge_core.router import RouterService
_init = RouterService.__init__
class MockProvider:
    execution_mode = "mock"
    async def generate(self, request):
        decision = {"intent":"create_quest","reply":"mock","title":"测试","goal":"测试","quest_id":None,"candidates":[]}
        return type("MockResponse", (), {"text":json.dumps(decision),"tool_calls":[],"usage":{}})()
def init(self, *args, **kwargs):
    kwargs.update(provider=MockProvider(), model_id="mock")
    _init(self, *args, **kwargs)
RouterService.__init__ = init
""",
        encoding="utf-8",
    )
    environment["PYTHONPATH"] = str(hook) + os.pathsep + environment.get("PYTHONPATH", "")
    base_url = f"http://127.0.0.1:{port}"
    token = None

    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = Request(base_url + path, data=data, headers=headers)
        try:
            response = urlopen(req, timeout=1)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    observations = []
    quest_id = None
    for name in ("cli-cwd", "desktop-cwd"):
        secret = secrets.token_hex(32)
        environment["FORGE_CORE_SECRET"] = secret
        token = None
        cwd = tmp_path / name
        cwd.mkdir()
        (cwd / ".env").write_text("FORGE_DB_PATH=wrong.sqlite3\n", encoding="utf-8")
        with (tmp_path / f"{name}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "forge_api", "--workspace-root", str(workspace)],
                cwd=cwd,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, (tmp_path / f"{name}.log").read_text()
                    try:
                        status, health = request("/health")
                        assert status == 200
                        token = hmac.new(
                            bytes.fromhex(secret), health["session"].encode(), hashlib.sha256
                        ).hexdigest()
                        break
                    except (URLError, TimeoutError):
                        assert time.monotonic() < deadline, "Core did not start"
                        time.sleep(0.05)
                if quest_id is None:
                    status, _result = request(
                        "/api/conversation/message", {"content": "帮我开发数据层"}
                    )
                    assert status == 200
                    status, quests = request("/api/quests")
                    assert status == 200
                    quest_id = quests[0]["id"]
                    status, rejected = request(
                        f"/api/quests/{quest_id}/transition",
                        {
                            "status": "rejected",
                            "expected_version": 0,
                        },
                    )
                    assert status == 200
                    assert rejected["version"] == 1
                status, conflict = request(
                    f"/api/quests/{quest_id}/transition",
                    {
                        "status": "awaiting_approval",
                        "expected_version": 0,
                    },
                )
                assert status == 409
                assert "version" in conflict["detail"].lower()
                status, illegal = request(
                    f"/api/quests/{quest_id}/transition",
                    {
                        "status": "running",
                        "expected_version": 1,
                    },
                )
                assert status == 409
                assert "Illegal Quest transition" in illegal["detail"]
                status, conversation = request("/api/conversation")
                assert status == 200
                assert len(conversation["messages"]) == 2
                observations.append(
                    {
                        "cwd": str(cwd),
                        "router": conversation["router"]["router_session_id"],
                        "messages": 2,
                        "stale_http": 409,
                        "illegal_http": 409,
                    }
                )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        assert not (cwd / "wrong.sqlite3").exists()
        log_text = (tmp_path / f"{name}.log").read_text(encoding="utf-8")
        assert secret not in log_text and token not in log_text
    assert observations[0]["router"] == observations[1]["router"]
    store = Store(workspace=Workspace.from_path(workspace), path="state/core.sqlite3")
    assert store.get_quest(quest_id)["status"] == "rejected"
    assert store.get_quest(quest_id)["version"] == 1
    events = store.list_events()
    outbox = store.list_outbox(pending_only=False)
    business_events = [event for event in events if not event["event_type"].startswith("core.")]
    assert [event["event_type"] for event in business_events] == [
        "quest.proposed",
        "quest.status_changed",
    ]
    assert sum(event["event_type"] == "core.started" for event in events) == 2
    assert len(events) == len(outbox)
    assert {event["id"] for event in events} == {entry["event_id"] for entry in outbox}
    assert not (tmp_path / "wrong-workspace").exists()
    print(
        json.dumps(
            {
                "observations": observations,
                "db": store.path,
                "quest_id": quest_id,
                "final_status": "rejected",
                "version": 1,
                "events": len(events),
                "outbox": len(outbox),
            }
        )
    )
