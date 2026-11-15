import importlib.util
import json
from pathlib import Path

import httpx
import pytest


def test_six_mock_workers_cannot_be_claimed_as_live_alpha(tmp_path):
    import asyncio

    from forge_core.domain import ModelProfile, ProtocolSupport, Quest
    from forge_core.governance import GovernanceService
    from forge_core.provider_pool import ProviderPool
    from forge_core.quest_graph import QuestGraphService
    from forge_core.runtime import ModelResponse
    from forge_core.settings import Workspace
    from forge_core.store import Store
    from forge_core.team_composition import TeamCompositionService
    from forge_core.tool_engine import ToolEngine
    from forge_core.worker_runtime import WorkerManager

    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    (tmp_path / "work").mkdir()
    quest = Quest(title="mock six role", goal="test only")
    store.save_quest(quest)
    graph = QuestGraphService(store)
    roles = sorted(alpha.ROLES)
    nodes = [
        {
            "key": role,
            "objective": "write bounded result",
            "success_criteria": [f"json_valid:work/{role}.json"],
            "budget_tokens": 16000,
            "allowed_tools": ["file_write"],
        }
        for role in roles
    ]
    plan = graph.propose(
        quest.id, {"nodes": nodes, "edges": [], "max_total_tokens": 96000}, expected_version=0
    )
    graph.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    store.save_model(
        ModelProfile(
            id="fixture",
            provider="fixture",
            model_id="scripted",
            display_name="Mock",
            context_window=32000,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    teams = TeamCompositionService(store)
    team = teams.propose(
        quest.id,
        [
            {
                "id": role,
                "task_node_key": role,
                "tools": ["file_write"],
                "input_tokens": 9000,
                "output_tokens": 3000,
                "max_steps": 3,
                "max_tool_calls": 1,
                "max_duration_seconds": 60,
            }
            for role in roles
        ],
        {
            "max_members": 6,
            "max_depth": 0,
            "max_concurrency": 6,
            "max_total_tokens": 72000,
            "max_total_tool_calls": 6,
            "allowed_tools": ["file_write"],
        },
        strategy="greedy",
        expected_version=2,
    )
    teams.approve(
        quest.id,
        plan["plan_revision"],
        team["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")

    class Model:
        execution_mode = "mock"
        count = 0

        async def generate(self, request):
            self.count += 1
            if self.count % 2:
                return ModelResponse(
                    text="",
                    tool_calls=[
                        {
                            "id": f"write{self.count}",
                            "name": "file_write",
                            "arguments": {
                                "path": f"work/{roles[self.count // 2]}.json",
                                "content": '{"ok":true}',
                            },
                        }
                    ],
                    usage={"input_tokens": 30, "output_tokens": 20},
                    finish_reason="tool_calls",
                )
            return ModelResponse(
                text='{"summary":"mock"}',
                tool_calls=[],
                usage={"input_tokens": 30, "output_tokens": 20},
                finish_reason="stop",
            )

    manager = WorkerManager(
        store, ProviderPool({"fixture": Model()}), ToolEngine(store), team_authority=teams
    )
    for index, role in enumerate(roles):
        run = manager.start(
            quest.id, plan["id"], role, team_digest=team["proposal_digest"], member_index=index
        )
        assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        assert {
            row["role_id"] for row in connection.execute("SELECT role_id FROM worker_runs")
        } == set(roles)
        assert {
            row["execution_mode"]
            for row in connection.execute("SELECT execution_mode FROM worker_runs")
        } == {"mock"}
        assert (
            connection.execute(
                "SELECT count(*) FROM action_receipts WHERE execution_mode='mock'"
            ).fetchone()[0]
            == 6
        )

    class Client:
        def call(self, method, path):
            return {
                "workspace_root": str(tmp_path),
                "workspace_id": store.workspace.id,
                "restore_fenced": False,
            }

    assert alpha.audit(Client(), store.path, quest.id, {}, {})["status"] == "incomplete"


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_alpha_demo.py"
spec = importlib.util.spec_from_file_location("forge_alpha_demo", MODULE_PATH)
alpha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alpha)


def test_only_numeric_loopback_can_receive_bearer():
    for url in (
        "https://example.org",
        "http://localhost:8765",
        "http://127.0.0.1@evil.test",
        "http://127.0.0.1:8765/path",
    ):
        with pytest.raises(ValueError):
            alpha.CoreClient(url, "a" * 64)


def test_human_plan_approval_is_explicit_and_bound():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = alpha.CoreClient(
        "http://127.0.0.1:8765", "a" * 64, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ValueError):
        alpha.approve_plan(client, "quest_x", "bad", 1, 3)
    assert not requests
    alpha.approve_plan(client, "quest_x", "a" * 64, 2, 3)
    assert json.loads(requests[0].content) == {
        "digest": "a" * 64,
        "plan_revision": 2,
        "expected_version": 3,
    }


def test_missing_trusted_evidence_never_reports_success(tmp_path):
    report = alpha.assess_evidence(
        {
            "roles": ["research", "math", "architect", "coding", "experiment"],
            "source_receipts": [],
            "semantic_evidence": [],
            "chat_relay": [],
            "restart_verified": False,
            "compaction_verified": False,
        }
    )
    assert report["status"] == "incomplete"
    assert "reviewer" in report["missing_roles"]
    assert not report["semantic_verified"]
    forged = alpha.assess_evidence(
        {
            "roles": list(alpha.ROLES),
            "source_receipts": ["fake"],
            "semantic_evidence": ["fake"],
            "chat_relay": ["fake"],
            "restart_verified": True,
            "compaction_verified": True,
        }
    )
    assert forged["status"] == "incomplete" and not forged["semantic_verified"]


def test_prepare_uses_real_router_and_sqlite_without_approval(tmp_path, monkeypatch):
    import hashlib
    import hmac
    import importlib
    import secrets

    from fastapi.testclient import TestClient

    from forge_core.runtime import ModelResponse

    for key in list(__import__("os").environ):
        if key.startswith("FORGE_"):
            monkeypatch.delenv(key, raising=False)
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    from forge_api import main

    importlib.reload(main)

    class Provider:
        execution_mode = "mock"

        async def generate(self, request):
            decision = {
                "intent": "create_quest",
                "reply": "review",
                "title": "bounded experiment",
                "goal": "bounded selection",
                "quest_id": None,
                "candidates": [],
            }
            return ModelResponse(
                text=json.dumps(decision),
                tool_calls=[],
                usage={"input_tokens": 1, "output_tokens": 1},
            )

    main.router.provider = Provider()
    main.router.model_id = "mock"
    bearer = hmac.new(
        bytes.fromhex(secret), main.guard.session.encode(), hashlib.sha256
    ).hexdigest()
    client = alpha.CoreClient("http://127.0.0.1:8765", bearer)
    client._client = TestClient(
        main.app,
        base_url="http://127.0.0.1:8765",
        client=("127.0.0.1", 12345),
        headers={"Authorization": "Bearer " + bearer},
    )
    request_id = "alpha_" + "1" * 32
    result = alpha.prepare(client, request_id, tmp_path / "request.json")
    assert result["status"] == "awaiting_plan"
    assert main.store.get_quest(result["quest_id"])["status"] == "proposed"
    assert len(main.store.list_messages()) == 2
    with main.store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert (
        alpha.prepare(client, request_id, tmp_path / "request.json")["quest_id"]
        == result["quest_id"]
    )
    assert len(main.store.list_messages()) == 2


def test_prepare_retry_after_unknown_outcome_reuses_durable_identity(tmp_path):
    class Client:
        def __init__(self):
            self.created = False
            self.ids = []

        def call(self, method, path, *, payload=None):
            if path == "/api/settings":
                return {"workspace_id": "w", "restore_fenced": False}
            if path == "/api/quests/quest_new":
                return {"quest": {"id": "quest_new"}}
            if path == "/api/conversation/message":
                self.ids.append(payload["request_id"])
                if not self.created:
                    self.created = True
                    raise TimeoutError("unknown")
                return {
                    "intent": "create_quest",
                    "quest_id": "quest_new",
                    "user_message": {"id": "m"},
                }
            raise AssertionError(path)

    client = Client()
    path = tmp_path / "checkpoint.json"
    request_id = "alpha_" + "b" * 32
    with pytest.raises(TimeoutError):
        alpha.prepare(client, request_id, path)
    assert path.is_file()
    assert alpha.prepare(client, request_id, path)["quest_id"] == "quest_new"
    assert client.ids == [request_id, request_id]


@pytest.mark.parametrize(
    "response",
    [
        {"intent": "create_quest", "user_message": {"id": "m"}},
        {"intent": "create_quest", "quest_id": "quest_expected", "user_message": {"id": "m"}},
    ],
)
def test_prepare_fails_closed_without_exact_returned_quest(tmp_path, response):
    class Client:
        def call(self, method, path, *, payload=None):
            if path == "/api/settings":
                return {"workspace_id": "w", "restore_fenced": False}
            if path == "/api/conversation/message":
                return response
            if path == "/api/quests/quest_expected":
                return {"quest": {"id": "quest_other"}}
            raise AssertionError(path)

    result = alpha.prepare(Client(), "alpha_" + "c" * 32, tmp_path / "request.json")
    assert result["status"] == "incomplete"


@pytest.mark.parametrize("corrupt", [False, True])
def test_open_database_closes_connection_when_workspace_table_missing(
    tmp_path, monkeypatch, corrupt
):
    import sqlite3

    path = tmp_path / "broken.sqlite3"
    sqlite3.connect(path).close()
    if corrupt:
        path.write_bytes(b"not sqlite")
    original = sqlite3.connect
    connections = []

    class Tracked(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs, factory=Tracked)
        connections.append(connection)
        return connection

    monkeypatch.setattr(alpha.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        alpha._open_database({"workspace_root": str(tmp_path), "workspace_id": "x"}, path)
    assert connections[0].closed


def test_checkpoint_archive_checks_real_sqlite_sources(tmp_path):
    from contextlib import closing

    from forge_core.context_service import ContextService
    from forge_core.domain import ConversationMessage
    from forge_core.settings import Workspace
    from forge_core.store import Store

    workspace = Workspace.from_path(tmp_path)
    store = Store(tmp_path / "core.sqlite3", workspace=workspace)
    msg = ConversationMessage(role="user", content="constraint")
    store.add_message(msg)
    ContextService(store).compact_router(keep_recent=0)
    settings = {"workspace_root": str(tmp_path), "workspace_id": workspace.id}
    with closing(alpha._open_database(settings, store.path)) as connection:
        assert alpha._archive_proof(connection)["source_count"] == 1
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET content=? WHERE id=?", ("tampered", msg.id))
    with (
        closing(alpha._open_database(settings, store.path)) as connection,
        pytest.raises(ValueError, match="integrity"),
    ):
        alpha._archive_proof(connection)


def test_audit_is_read_only_and_incomplete_without_persisted_execution(tmp_path):
    from forge_core.domain import Quest
    from forge_core.settings import Workspace
    from forge_core.store import Store

    workspace = Workspace.from_path(tmp_path)
    store = Store(tmp_path / "core.sqlite3", workspace=workspace)
    quest = Quest(title="incomplete", goal="test")
    store.save_quest(quest)

    class Client:
        def call(self, method, path):
            assert (method, path) == ("GET", "/api/settings")
            return {"workspace_root": str(tmp_path), "workspace_id": workspace.id}

    with store.connection() as connection:
        before = connection.total_changes
        counts = tuple(
            connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("quests", "events", "evidence")
        )
    forged = {
        "workspace_id": workspace.id,
        "router_session_id": "fake",
        "session_challenge_sha256": "a" * 64,
        "archive_proof": {"source_hash": "fake"},
    }
    result = alpha.audit(
        Client(), store.path, quest.id, forged, {**forged, "session_challenge_sha256": "b" * 64}
    )
    assert result["status"] == "incomplete"
    assert not result["semantic_verified"] and not result["source_verified"]
    with store.connection() as connection:
        assert (
            tuple(
                connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                for table in ("quests", "events", "evidence")
            )
            == counts
        )
    assert before == 0


def test_restart_proof_requires_archive_between_generations_and_later_turn(tmp_path):
    import hashlib

    from test_semantic_evidence_m7 import _fixture

    from forge_core.context_service import ContextService
    from forge_core.domain import ConversationMessage, ForgeEvent, now_iso
    from forge_core.lifecycle import start, stop

    store, quest, plan, run, _ = _fixture(tmp_path)
    first = start(store, session_challenge="a" * 64)
    store.add_message(ConversationMessage(role="user", content="constraint"))
    ContextService(store).compact_router(keep_recent=0)
    stop(store, first)
    second = start(store, session_challenge="b" * 64)
    user = ConversationMessage(role="user", content="continue")
    assistant = ConversationMessage(
        role="assistant", content="continued", metadata={"execution_mode": "live"}
    )
    store.add_message(user)
    store.add_message(assistant)
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO router_turns VALUES (?,?,?,?,?)",
            (
                "continuation",
                "main",
                hashlib.sha256(user.content.encode()).hexdigest(),
                json.dumps(
                    {
                        "intent": "continue_quest",
                        "quest_id": quest.id,
                        "user_message": user.model_dump(),
                        "assistant_message": assistant.model_dump(),
                    }
                ),
                now_iso(),
            ),
        )
    with store.connection() as connection:
        connection.execute("BEGIN")
        router = connection.execute(
            "SELECT router_session_id FROM router_state WHERE conversation_id='main'"
        ).fetchone()[0]
        archive = alpha._archive_proof(connection)
        before = {
            "generation": first,
            "workspace_id": store.workspace.id,
            "router_session_id": router,
            "archive_proof": archive,
            "session_challenge_sha256": hashlib.sha256(("a" * 64).encode()).hexdigest(),
        }
        after = {
            "generation": second,
            "workspace_id": store.workspace.id,
            "router_session_id": router,
            "session_challenge_sha256": hashlib.sha256(("b" * 64).encode()).hexdigest(),
        }
        args = (
            connection,
            store.workspace.id,
            router,
            quest.id,
            plan["id"],
            before,
            after,
            archive,
        )
        assert alpha._restart_proof(*args) is None
    with store.unit_of_work() as work:
        work.append_event(
            ForgeEvent(
                event_type="worker.result",
                quest_id=quest.id,
                worker_id=run["worker_id"],
                payload={
                    "plan_id": plan["id"],
                    "task_node_key": run["node_key"],
                    "run_id": run["id"],
                },
            )
        )
    with store.connection() as connection:
        proof = alpha._restart_proof(
            connection, store.workspace.id, router, quest.id, plan["id"], before, after, archive
        )
        assert (
            proof
            and proof["continuation_request_id"] == "continuation"
            and proof["worker_run_id"] == run["id"]
        )
        assert (
            alpha._restart_proof(
                connection,
                store.workspace.id,
                router,
                "quest_other",
                plan["id"],
                before,
                after,
                archive,
            )
            is None
        )
        assert (
            alpha._restart_proof(
                connection,
                store.workspace.id,
                router,
                quest.id,
                plan["id"],
                before,
                {**after, "generation": first},
                archive,
            )
            is None
        )
        assert (
            alpha._restart_proof(
                connection,
                store.workspace.id,
                router,
                quest.id,
                plan["id"],
                before,
                {**after, "workspace_id": "forged"},
                archive,
            )
            is None
        )
        assert (
            alpha._restart_proof(
                connection,
                store.workspace.id,
                router,
                quest.id,
                plan["id"],
                before,
                {**after, "session_challenge_sha256": "c" * 64},
                archive,
            )
            is None
        )
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT result_json FROM router_turns WHERE request_id='continuation'"
        ).fetchone()
        turn = json.loads(row["result_json"])
        turn["intent"] = "chat"
        work.connection.execute(
            "UPDATE router_turns SET result_json=? WHERE request_id='continuation'",
            (json.dumps(turn),),
        )
    with store.connection() as connection:
        assert (
            alpha._restart_proof(
                connection, store.workspace.id, router, quest.id, plan["id"], before, after, archive
            )
            is None
        )


def test_origin_requires_turn_quest_identity_and_live_persisted_messages(tmp_path):
    import hashlib

    from forge_core.domain import ConversationMessage, now_iso
    from forge_core.settings import Workspace
    from forge_core.store import Store

    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    user = ConversationMessage(role="user", content=alpha.GOAL)
    assistant = ConversationMessage(
        role="assistant", content="accepted", metadata={"execution_mode": "live"}
    )
    store.add_message(user)
    store.add_message(assistant)
    result = {
        "intent": "create_quest",
        "quest_id": "quest_exact",
        "user_message": user.model_dump(),
        "assistant_message": assistant.model_dump(),
    }
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO router_turns VALUES (?,?,?,?,?)",
            (
                "alpha_request",
                "main",
                hashlib.sha256(alpha.GOAL.encode()).hexdigest(),
                json.dumps(result),
                now_iso(),
            ),
        )
    with store.connection() as connection:
        assert alpha._quest_origin(connection, "quest_exact") == "alpha_request"
        assert alpha._quest_origin(connection, "quest_other") is None
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE messages SET metadata=? WHERE id=?",
            (json.dumps({"execution_mode": "mock"}), assistant.id),
        )
    with store.connection() as connection:
        assert alpha._quest_origin(connection, "quest_exact") is None
