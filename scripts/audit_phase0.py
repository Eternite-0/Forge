"""Run isolated Phase 0 diagnostics without modifying application source or user data.

Exit 0 means the diagnostics ran; findings are in the JSON, not a passing product gate.
Run with backend/.venv/Scripts/python.exe scripts/audit_phase0.py from the repo root.
"""

import asyncio
import importlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".local" / "phase0" / f"probes-{uuid4().hex[:8]}"
RUN.mkdir(parents=True)
os.environ["FORGE_DB_PATH"] = str(RUN / "api.sqlite3")

from fastapi.testclient import TestClient
from forge_core.domain import ConversationMessage, ForgeEvent, ModelProfile, Quest, QuestStatus
from forge_core.quest_service import QuestService
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.runtime import AgentLoop, ModelResponse, ToolDefinition, ToolRegistry
from forge_core.store import Store

api = importlib.import_module("forge_api.main")
results = {"started_at": datetime.now(timezone.utc).isoformat(), "diagnostics": {}}


def observe(name, value):
    results["diagnostics"][name] = value


def new_store(name):
    return Store(str(RUN / f"{name}.sqlite3"))


with TestClient(api.app, raise_server_exceptions=False) as client:
    response = client.post("/api/conversation/message", json={"content": "帮我开发审计示例"})
    assert response.status_code == 200, response.text
    quest_id = client.get("/api/quests").json()[0]["id"]
    matrix = {}
    for name, method, url, kwargs in [
        ("health", "get", "/health", {}),
        ("missing_quest", "get", "/api/quests/not-found", {}),
        ("empty_message", "post", "/api/conversation/message", {"json": {"content": ""}}),
        ("missing_content", "post", "/api/conversation/message", {"json": {}}),
        ("oversize_message", "post", "/api/conversation/message", {"json": {"content": "x" * 20001}}),
        ("whitespace_message", "post", "/api/conversation/message", {"json": {"content": "   "}}),
        ("invalid_status", "post", f"/api/quests/{quest_id}/transition", {"json": {"status": "bogus"}}),
        ("illegal_transition", "post", f"/api/quests/{quest_id}/transition", {"json": {"status": "completed"}}),
        ("negative_event_cursor", "get", "/api/events?after=-1", {}),
        ("unknown_quest_worker", "post", "/api/quests/missing/workers?role=research", {}),
    ]:
        res = getattr(client, method)(url, **kwargs)
        matrix[name] = {"status": res.status_code, "content_type": res.headers.get("content-type")}
    observe("api_validation_matrix", matrix)

    transitions = []
    for status in ("awaiting_approval", "approved", "queued", "running", "completed"):
        res = client.post(f"/api/quests/{quest_id}/transition", json={"status": status})
        transitions.append({"target": status, "http_status": res.status_code})
    observe("completion_without_execution_or_approval", {
        "transitions": transitions,
        "final_status": client.get(f"/api/quests/{quest_id}").json()["quest"]["status"],
        "workers": client.get(f"/api/quests/{quest_id}").json()["workers"],
        "authorization_header_sent": False,
    })

    res = client.post(f"/api/quests/{quest_id}/workers", params={"role": "", "model_id": "missing-model"})
    observe("worker_on_completed_quest_with_invalid_assignment", {
        "http_status": res.status_code, "worker": res.json(),
    })
    res = client.post(f"/api/quests/{quest_id}/workers?role=untrusted-site", headers={"Origin": "https://untrusted.invalid"})
    observe("untrusted_origin_simple_post", {
        "http_status": res.status_code,
        "worker_created": res.status_code == 200,
        "access_control_allow_origin": res.headers.get("access-control-allow-origin"),
    })

    origins = {}
    for origin in ("http://localhost:5173", "tauri://localhost", "http://tauri.localhost", "https://untrusted.invalid"):
        res = client.options("/api/conversation/message", headers={
            "Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        origins[origin] = res.status_code
    observe("cors_preflight", origins)

    res = client.post("/api/models", json={
        "id": "invalid-profile", "provider": "audit", "display_name": "Audit",
        "capabilities": {"coding": 10000, "research": -50},
        "context_window": -1, "input_cost_per_million": -9,
    })
    observe("invalid_model_metadata", {"http_status": res.status_code, "accepted_profile": res.json()})

    fault_quest = Quest(title="Fault injection", goal="Isolated audit")
    api.store.save_quest(fault_quest)
    original_append = api.store.append_event

    def fail_append(_event):
        raise sqlite3.OperationalError("Phase 0 injected event-write failure")

    api.store.append_event = fail_append
    try:
        res = client.post(f"/api/quests/{fault_quest.id}/transition", json={"status": "awaiting_approval"})
    finally:
        api.store.append_event = original_append
    observe("transition_event_failure", {
        "http_status": res.status_code, "body": res.text,
        "content_type": res.headers.get("content-type"),
        "persisted_status": api.store.get_quest(fault_quest.id)["status"],
        "events_for_quest": [e for e in api.store.list_events() if e["quest_id"] == fault_quest.id],
    })
    request_headers = {"Idempotency-Key": "phase0-identical-request"}
    before = len(api.store.list_quests())
    for _ in range(2):
        client.post("/api/conversation/message", json={"content": "帮我研究同一个目标"}, headers=request_headers)
    observe("request_retry", {"same_idempotency_key": True, "quests_created": len(api.store.list_quests()) - before})
    observe("openapi", {
        "paths": sorted(client.get("/openapi.json").json()["paths"]),
        "security_schemes": client.get("/openapi.json").json().get("components", {}).get("securitySchemes"),
    })

store = new_store("schema")
for _ in range(2):
    Store(store.path)
with sqlite3.connect(store.path) as connection:
    versions = connection.execute("SELECT version FROM schema_meta").fetchall()
    connection.execute("UPDATE schema_meta SET version=999")
connection.close()
Store(store.path)
with sqlite3.connect(store.path) as connection:
    future_versions = connection.execute("SELECT version FROM schema_meta").fetchall()
    tables = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall()
    integrity = connection.execute("PRAGMA integrity_check").fetchall()
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
connection.close()
observe("schema_version_handling", {"versions_after_three_opens": versions, "future_version_reopen": future_versions})
observe("sqlite_schema", {"tables": tables, "integrity_check": integrity, "foreign_key_check": foreign_key_errors})

held_connections = []
original_connect = store._connect


def capture_connect():
    connection = original_connect()
    held_connections.append(connection)
    return connection


store._connect = capture_connect
store.list_messages()
observe("connection_lifecycle", {"usable_after_repository_method": held_connections[0].execute("SELECT 1").fetchone()[0] == 1})
for connection in held_connections:
    connection.close()
store._connect = original_connect

orphan_event = ForgeEvent(conversation_id="nonexistent", quest_id="missing", worker_id="missing", event_type="audit.orphan")
sequence = store.append_event(orphan_event)
observe("event_foreign_keys", {"orphan_event_accepted": bool(sequence)})


class BrokenComposer:
    def compose(self, _roles):
        raise RuntimeError("Phase 0 injected composer failure")


store = new_store("router-fault")
try:
    RouterService(store, BrokenComposer()).handle_user_message("帮我开发故障注入示例")
except RuntimeError as exc:
    observe("router_partial_write", {"error": str(exc), "messages": len(store.list_messages()), "quests": len(store.list_quests()), "events": len(store.list_events())})

store = new_store("transition-race")
quest = Quest(title="Interleaved transitions", goal="Isolated audit")
store.save_quest(quest)
other_actor = QuestService(Store(store.path))
original_transition = store.transition_quest


def interleaved_transition(quest_id, new_status, updated_at):
    other_actor.transition(quest_id, QuestStatus.REJECTED)
    return original_transition(quest_id, new_status, updated_at)


store.transition_quest = interleaved_transition
QuestService(store).transition(quest.id, QuestStatus.AWAITING_APPROVAL)
observe("stale_transition_interleaving", {
    "method": "Second Store rejects after first service reads, before first service updates",
    "final_status": store.get_quest(quest.id)["status"],
    "events": [json.loads(e["payload"]) for e in store.list_events()],
})

store = new_store("history")
with sqlite3.connect(store.path) as connection:
    rows = []
    for i in range(205):
        message = ConversationMessage(role="user", content=str(i))
        rows.append((message.id, "main", message.role, message.content, message.created_at, "{}"))
    connection.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?)", rows)
connection.close()
history = store.list_messages()
observe("history_limit", {"stored": 205, "returned_by_default": len(history), "first_returned_content": history[0]["content"]})

registry = ModelRegistry(new_store("unknown-capability"))
registry.upsert(ModelProfile(id="unknown", provider="audit", display_name="Unknown capabilities"))
observe("unknown_capability_ranking", {"candidates": [{"id": m.id, "score": score} for m, score in registry.candidates({"coding": 80})]})
observe("router_classifier", {text: RouterService.classify(text).value for text in (
    "Build a desktop application", "帮我开发状态监控工具", "批准", "修改需求：使用离线模式", "继续之前那个任务",
)})


class ToolCallingProvider:
    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return ModelResponse(text="", tool_calls=[{"name": "audit_read", "arguments": {}}], usage={})


provider = ToolCallingProvider()
tools = ToolRegistry()
tools.register(ToolDefinition("audit_read", "Synthetic audit tool", {"type": "object"}))
loop_result = asyncio.run(AgentLoop(provider, tools, max_steps=8).run("audit", []))
observe("agent_loop_tool_boundary", {"result": loop_result, "provider_calls": len(provider.requests), "advertised_tools": provider.requests[0].tools})

for folder in ("cwd-a", "cwd-b"):
    directory = RUN / folder
    directory.mkdir()
    (directory / ".env").write_text("FORGE_DB_PATH=env-selected.sqlite3\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("FORGE_DB_PATH", None)
    command = [sys.executable, "-c", "import json,pathlib; import forge_api.main as m; print(json.dumps({'db':str(pathlib.Path(m.db_path).resolve()),'router':m.store.ensure_router()['router_session_id']}))"]
    child = subprocess.run(command, cwd=directory, env=environment, capture_output=True, text=True, check=True)
    observe(folder, {"dotenv_value": "env-selected.sqlite3", "actual": json.loads(child.stdout)})

results["ended_at"] = datetime.now(timezone.utc).isoformat()
results["meaning_of_exit_zero"] = "Diagnostics executed, not product acceptance. Evaluate observations against audit report."
output = ROOT / ".local" / "phase0" / "probes.json"
output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(results, ensure_ascii=False, indent=2))
