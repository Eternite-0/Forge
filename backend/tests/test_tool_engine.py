import pytest

from forge_core.domain import DomainError, Quest
from forge_core.store import Store
from forge_core.tool_engine import ToolEngine


@pytest.fixture
def mock_run(tmp_path):
    import hashlib
    import json
    import time

    from forge_core.domain import WorkerSession
    from forge_core.quest_graph import QuestGraphService
    from forge_core.settings import Workspace

    store = Store(workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Tool task", goal="Test real local tools")
    store.save_quest(quest)
    plan = QuestGraphService(store).propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": "check",
                    "success_criteria": ["done"],
                    "budget_tokens": 100,
                    "allowed_tools": [
                        "file_read",
                        "file_write",
                        "numeric_ast",
                        "browser_get",
                        "git_status",
                    ],
                }
            ],
            "edges": [],
        },
        expected_version=0,
    )
    QuestGraphService(store).approve(
        quest.id, plan["digest"], plan["revision"], expected_version=1, actor="local_user"
    )
    worker = WorkerSession(quest_id=quest.id, role="test")
    store.save_worker(worker)
    body = {
        "quest_id": quest.id,
        "plan_revision": plan["revision"],
        "plan_digest": plan["digest"],
        "profile_hashes": {},
        "members": [
            {
                "role_id": "test",
                "task_node_key": "n",
                "provider_id": "mock",
                "model_id": "mock",
                "tool_scope": [
                    "file_read",
                    "file_write",
                    "numeric_ast",
                    "browser_get",
                    "git_status",
                ],
            }
        ],
    }
    serialized = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    team_digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    with store.unit_of_work() as work:
        c = work.connection
        c.execute("UPDATE quests SET status='running' WHERE id=?", (quest.id,))
        c.execute("UPDATE workers SET status='running' WHERE id=?", (worker.id,))
        c.execute(
            "UPDATE task_nodes SET status='running',worker_id=? WHERE plan_id=? AND node_key='n'",
            (worker.id, plan["id"]),
        )
        c.execute(
            "INSERT INTO team_proposals VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "team-id",
                quest.id,
                plan["revision"],
                plan["digest"],
                team_digest,
                "approved",
                serialized,
                "now",
                "local_user",
                "now",
            ),
        )
        c.execute(
            """INSERT INTO worker_runs(id,worker_id,quest_id,plan_id,node_key,plan_revision,plan_digest,team_digest,member_index,role_id,provider_id,model_id,execution_mode,state,generation,lease_token,lease_until,allowed_tools_json,capability_sources_json,token_limit,tool_limit,step_limit,deadline,depth,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "run",
                worker.id,
                quest.id,
                plan["id"],
                "n",
                plan["revision"],
                plan["digest"],
                team_digest,
                0,
                "test",
                "mock",
                "mock",
                "mock",
                "running",
                1,
                "lease",
                time.time() + 120,
                json.dumps(["file_read", "file_write", "numeric_ast", "browser_get", "git_status"]),
                "{}",
                1000,
                10,
                10,
                time.time() + 120,
                0,
                "now",
            ),
        )
    return store, quest, worker, plan


def reserve(store, call_id, name, parameters):
    import json

    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) VALUES ('run',?,?,?,?, 'running')",
            (call_id, call_id, name, json.dumps(parameters)),
        )


def test_actual_mock_run_file_write_needs_grant_and_receipt_is_immutable(mock_run):
    import asyncio
    import sqlite3

    from forge_core.governance import GovernanceService

    store, quest, worker, _ = mock_run
    (store.workspace.root / "work").mkdir()
    params = {"path": "work/result.txt", "content": "hello"}
    reserve(store, "write", "file_write", params)
    engine = ToolEngine(store)
    arguments = {
        "call_id": "write",
        "quest_id": quest.id,
        "worker_id": worker.id,
        "run_id": "run",
        "lease_token": "lease",
        "tool_name": "file_write",
        "parameters": params,
    }
    with pytest.raises(DomainError):
        asyncio.run(engine.execute(**arguments))
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    result = asyncio.run(engine.execute(**arguments))
    assert result["state"] == "succeeded" and result["call_id"] == "write"
    assert (store.workspace.root / "work/result.txt").read_text() == "hello"
    assert asyncio.run(engine.execute(**arguments))["receipt_id"] == result["receipt_id"]
    with store.unit_of_work() as work:
        with pytest.raises(sqlite3.IntegrityError):
            work.connection.execute(
                "DELETE FROM action_receipts WHERE id=?", (result["receipt_id"],)
            )
        evidence = work.connection.execute(
            "SELECT trusted,payload FROM evidence WHERE kind='tool'"
        ).fetchone()
        assert evidence["trusted"] == 0


def test_actual_numeric_subprocess_and_escape_rejection(mock_run):
    import asyncio

    store, quest, worker, _ = mock_run
    engine = ToolEngine(store)
    for number, source in enumerate(("result = sum(data)", "import os\nresult=1")):
        call_id = f"numeric-{number}"
        params = {"source": source, "inputs": {"data": [1, 2, 3]}}
        reserve(store, call_id, "numeric_ast", params)
        result = asyncio.run(
            engine.execute(
                call_id=call_id,
                quest_id=quest.id,
                worker_id=worker.id,
                run_id="run",
                lease_token="lease",
                tool_name="numeric_ast",
                parameters=params,
            )
        )
        assert result["state"] == ("succeeded" if number == 0 else "failed")
        assert result["output"]["exit_code"] == number


def test_r2_approval_consumption_and_intent_are_atomic_on_real_sql_failure(mock_run):
    import sqlite3

    from forge_core.governance import GovernanceService

    store, quest, worker, _ = mock_run
    params = {"url": "https://example.test/item"}
    reserve(store, "network", "browser_get", params)
    governance = GovernanceService(store)
    approval = governance.request_approval(
        quest.id, "browser_get", params, params["url"], "R2", "request"
    )
    governance.decide_approval(approval["id"], "APPROVED", "local_user")
    with store.connection() as connection:
        connection.execute(
            "CREATE TRIGGER abort_intent BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'failure'); END"
        )
    engine = ToolEngine(store, network_hosts=["example.test"])
    with pytest.raises(sqlite3.IntegrityError):
        engine.prepare(
            call_id="network",
            quest_id=quest.id,
            worker_id=worker.id,
            run_id="run",
            lease_token="lease",
            tool_name="browser_get",
            parameters=params,
            approval_id=approval["id"],
        )
    assert governance.get_approval(approval["id"])["consumed_at"] is None
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0


def test_r2_approval_cannot_be_consumed_twice(mock_run):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from forge_core.governance import GovernanceService

    store, quest, worker, _ = mock_run
    params = {"url": "https://example.test/item"}
    for call_id in ("one", "two"):
        reserve(store, call_id, "browser_get", params)
    governance = GovernanceService(store)
    approval = governance.request_approval(
        quest.id, "browser_get", params, params["url"], "R2", "request"
    )
    governance.decide_approval(approval["id"], "APPROVED", "local_user")
    engine = ToolEngine(store, network_hosts=["example.test"])
    barrier = Barrier(2)

    def attempt(call_id):
        barrier.wait(timeout=5)
        try:
            engine.prepare(
                call_id=call_id,
                quest_id=quest.id,
                worker_id=worker.id,
                run_id="run",
                lease_token="lease",
                tool_name="browser_get",
                parameters=params,
                approval_id=approval["id"],
            )
            return "accepted"
        except DomainError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ("one", "two"))) == ["accepted", "denied"]
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 1


def test_replaced_parameters_and_sensitive_paths_are_rejected(mock_run, tmp_path):
    store, quest, worker, _ = mock_run
    engine = ToolEngine(store)
    reserve(store, "read", "file_read", {"path": "work/a"})
    with pytest.raises(DomainError):
        engine.prepare(
            call_id="read",
            quest_id=quest.id,
            worker_id=worker.id,
            run_id="run",
            lease_token="lease",
            tool_name="file_read",
            parameters={"path": "work/b"},
        )
    for path in ("../outside", ".env", "work/private.key"):
        with pytest.raises(DomainError):
            engine._scope("file_read", {"path": path})


def test_abandoned_effect_is_uncertain_and_never_replayed(mock_run):
    import asyncio
    import time

    from forge_core.governance import GovernanceService

    store, quest, worker, _ = mock_run
    (store.workspace.root / "work").mkdir()
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    params = {"path": "work/once", "content": "data"}
    reserve(store, "crash", "file_write", params)
    engine = ToolEngine(store)
    args = {
        "call_id": "crash",
        "quest_id": quest.id,
        "worker_id": worker.id,
        "run_id": "run",
        "lease_token": "lease",
        "tool_name": "file_write",
        "parameters": params,
    }
    intent = engine.prepare(**args)
    _, started = engine._begin(intent["id"], "lease")
    assert started
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET lease_until=? WHERE id='run'", (time.time() - 1,)
        )
    assert engine.recover_abandoned() == 1
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT state FROM action_intents WHERE id=?", (intent["id"],)
            ).fetchone()[0]
            == "uncertain"
        )
    with pytest.raises(DomainError):
        asyncio.run(engine.execute(**args))
    assert not (store.workspace.root / "work/once").exists()


def test_file_write_create_only_never_overwrites(mock_run):
    import asyncio

    from forge_core.governance import GovernanceService

    store, quest, worker, _ = mock_run
    folder = store.workspace.root / "work"
    folder.mkdir()
    (folder / "existing").write_text("original")
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    params = {"path": "work/existing", "content": "replacement"}
    reserve(store, "existing", "file_write", params)
    result = asyncio.run(
        ToolEngine(store).execute(
            call_id="existing",
            quest_id=quest.id,
            worker_id=worker.id,
            run_id="run",
            lease_token="lease",
            tool_name="file_write",
            parameters=params,
        )
    )
    assert result["state"] == "uncertain"
    assert (folder / "existing").read_text() == "original"


def test_real_git_status_rejects_external_gitdir(mock_run):
    import asyncio
    import shutil
    import subprocess

    if not shutil.which("git"):
        pytest.skip("Git is not installed")
    store, quest, worker, _ = mock_run
    root = store.workspace.root
    repository = root / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True, capture_output=True)
    params = {"path": "repository"}
    reserve(store, "git-safe", "git_status", params)
    engine = ToolEngine(store)
    result = asyncio.run(
        engine.execute(
            call_id="git-safe",
            quest_id=quest.id,
            worker_id=worker.id,
            run_id="run",
            lease_token="lease",
            tool_name="git_status",
            parameters=params,
        )
    )
    assert result["state"] == "succeeded"
    assert result["output"]["exit_code"] == 0
    external = root / "external-reference"
    external.mkdir()
    (external / ".git").write_text("gitdir: ../repository/.git", encoding="utf-8")
    with pytest.raises(DomainError):
        engine._git_repository(external)


def test_http_error_is_failed_receipt(mock_run, monkeypatch):
    import asyncio

    from forge_core.governance import GovernanceService

    store, quest, worker, _ = mock_run
    params = {"url": "https://example.test/unavailable"}
    reserve(store, "bad-http", "browser_get", params)
    governance = GovernanceService(store)
    approval = governance.request_approval(
        quest.id, "browser_get", params, params["url"], "R2", "request"
    )
    governance.decide_approval(approval["id"], "APPROVED", "local_user")
    engine = ToolEngine(store, network_hosts=["example.test"])
    monkeypatch.setattr(
        engine,
        "_network_request",
        lambda *args: {"status_code": 503, "body": "unavailable", "sha256": "a" * 64},
    )
    result = asyncio.run(
        engine.execute(
            call_id="bad-http",
            quest_id=quest.id,
            worker_id=worker.id,
            run_id="run",
            lease_token="lease",
            tool_name="browser_get",
            parameters=params,
        )
    )
    assert result["state"] == "failed"
    assert result["output"]["status_code"] == 503


def test_v7_failure_rolls_back_all_components(tmp_path):
    import sqlite3
    from contextlib import closing

    from forge_core.migrations import MIGRATIONS, migrate

    with closing(sqlite3.connect(tmp_path / "legacy.sqlite3", isolation_level=None)) as connection:
        migrate(connection, target_version=6)
        before = connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()

        def fail_after_step(conn):
            MIGRATIONS[7](conn)
            conn.execute("INSERT INTO nonexistent_table VALUES (1)")

        steps = dict(MIGRATIONS)
        steps[7] = fail_after_step
        with pytest.raises(sqlite3.OperationalError):
            migrate(connection, migrations=steps)
        assert (
            connection.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
            == before
        )
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 6
        migrate(connection, target_version=7)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 7


def test_mock_worker_and_forged_receipt_never_complete(mock_run):
    from forge_core.evidence import NodeVerifier

    store, quest, _worker, plan = mock_run
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='review_pending' WHERE id='run'")
        work.connection.execute(
            "UPDATE task_nodes SET status='review_pending',criteria_json=? WHERE plan_id=? AND node_key='n'",
            ('["json_valid:work/result.json"]', plan["id"]),
        )
        work.connection.execute(
            "INSERT INTO worker_messages VALUES ('run',0,'assistant',?)",
            ('{"role":"assistant","content":"{}","tool_calls":[]}',),
        )
    with pytest.raises(DomainError, match="live Worker"):
        NodeVerifier(store).verify(quest.id, "n")
    assert store.get_quest(quest.id)["status"] != "completed"


def test_unknown_or_unapproved_action_is_rejected(tmp_path):
    store = Store(tmp_path / "tool.sqlite3")
    quest = Quest(title="Tools", goal="Govern tool calls")
    store.save_quest(quest)
    engine = ToolEngine(store)
    with pytest.raises(DomainError):
        engine.prepare(
            call_id="call",
            quest_id=quest.id,
            worker_id="forged",
            run_id="forged",
            tool_name="shell",
            parameters={"command": "rm -rf /"},
        )
    assert store.list_events() == []


def test_model_cannot_register_tool_or_trusted_evidence(tmp_path):
    store = Store(tmp_path / "tools.sqlite3")
    engine = ToolEngine(store)
    assert not hasattr(engine, "register")
    assert all(tool.name not in {"grant", "approve", "shell"} for tool in engine.contracts())


@pytest.mark.parametrize(
    "configuration",
    [
        "[include]\npath = /outside/config\n",
        "[core]\nworktree = /outside\n",
        "[extensions]\nworktreeConfig = true\n",
        "[core]\nfsmonitor = evil\n",
    ],
)
def test_git_config_cannot_redirect_reads(mock_run, configuration):
    store, _, _, _ = mock_run
    root = store.workspace.root / "repository"
    (root / ".git").mkdir(parents=True)
    (root / ".git/config").write_text(configuration, encoding="utf-8")
    with pytest.raises(DomainError):
        ToolEngine(store)._git_repository(root)


def test_dns_uses_remaining_wall_deadline(mock_run, monkeypatch):
    import socket
    import threading
    import time

    store, _, _, _ = mock_run
    release = threading.Event()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: (release.wait(1), [])[1])
    started = time.monotonic()
    try:
        with pytest.raises(DomainError, match="timed out"):
            ToolEngine(store, network_hosts=["example.test"])._public_url(
                "https://example.test", threading.Event(), started + 0.04
            )
        assert time.monotonic() - started < 0.5
    finally:
        release.set()


@pytest.mark.parametrize(
    "body",
    [
        '{"jsonrpc":"2.0","id":"bad-mcp","error":{"code":-1}}',
        '{"jsonrpc":"2.0","id":"other","result":{}}',
        "not JSON",
    ],
)
def test_mcp_protocol_error_has_failed_receipt(mock_run, monkeypatch, body):
    import asyncio
    import hashlib
    import json

    from forge_core.governance import GovernanceService

    store, quest, worker, plan = mock_run
    with store.unit_of_work() as work:
        run = work.connection.execute("SELECT * FROM worker_runs WHERE id='run'").fetchone()
        tools = json.loads(run["allowed_tools_json"]) + ["mcp_call"]
        proposal = work.connection.execute(
            "SELECT body_json FROM team_proposals WHERE id='team-id'"
        ).fetchone()
        team = json.loads(proposal[0])
        team["members"][0]["tool_scope"] = tools
        encoded = json.dumps(team, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        work.connection.execute(
            "UPDATE team_proposals SET body_json=?,proposal_digest=? WHERE id='team-id'",
            (encoded, digest),
        )
        work.connection.execute(
            "UPDATE worker_runs SET allowed_tools_json=?,team_digest=? WHERE id='run'",
            (json.dumps(tools), digest),
        )
        work.connection.execute(
            "UPDATE task_nodes SET allowed_tools=? WHERE plan_id=?", (json.dumps(tools), plan["id"])
        )
    params = {"server": "fixture", "tool": "read", "arguments": {}}
    reserve(store, "bad-mcp", "mcp_call", params)
    governance = GovernanceService(store)
    approval = governance.request_approval(
        quest.id, "mcp_call", params, "mcp:fixture:read", "R2", "request"
    )
    governance.decide_approval(approval["id"], "APPROVED", "local_user")
    engine = ToolEngine(
        store,
        network_hosts=["example.test"],
        mcp_servers={"fixture": {"url": "https://example.test/rpc", "tools": ["read"]}},
    )
    monkeypatch.setattr(
        engine,
        "_network_request",
        lambda *args: {
            "status_code": 200,
            "body": body,
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
        },
    )
    result = asyncio.run(
        engine.execute(
            call_id="bad-mcp",
            quest_id=quest.id,
            worker_id=worker.id,
            run_id="run",
            lease_token="lease",
            tool_name="mcp_call",
            parameters=params,
        )
    )
    assert result["state"] == "failed" and result["receipt_id"]


def test_old_plan_pending_does_not_suppress_current_tool_approval(mock_run):
    import asyncio

    from forge_core.governance import GovernanceService
    from forge_core.tool_engine import ToolAuthorizationRequired

    store, quest, worker, _ = mock_run
    params = {"url": "https://example.test/item"}
    old = GovernanceService(store).request_approval(
        quest.id, "browser_get", params, params["url"], "R2", "old plan"
    )
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE approvals SET plan_revision=0 WHERE id=?", (old["id"],))
    reserve(store, "current", "browser_get", params)
    with pytest.raises(ToolAuthorizationRequired):
        asyncio.run(
            ToolEngine(store, network_hosts=["example.test"]).execute(
                call_id="current",
                quest_id=quest.id,
                worker_id=worker.id,
                run_id="run",
                lease_token="lease",
                tool_name="browser_get",
                parameters=params,
            )
        )
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM approvals WHERE plan_revision=1 AND status='PENDING'"
            ).fetchone()[0]
            == 1
        )


def test_concurrent_same_call_requests_one_approval_and_separate_calls_bind_individually(mock_run):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from forge_core.governance import GovernanceService
    from forge_core.tool_engine import ToolAuthorizationRequired

    store, quest, worker, _ = mock_run
    params = {"url": "https://example.test/item"}
    reserve(store, "first", "browser_get", params)
    reserve(store, "second", "browser_get", params)
    engine = ToolEngine(store, network_hosts=["example.test"])
    barrier = Barrier(2)

    def request(call_id):
        barrier.wait(timeout=5)
        with pytest.raises(ToolAuthorizationRequired):
            asyncio.run(
                engine.execute(
                    call_id=call_id,
                    quest_id=quest.id,
                    worker_id=worker.id,
                    run_id="run",
                    lease_token="lease",
                    tool_name="browser_get",
                    parameters=params,
                )
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(request, ["first", "first"]))
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
    barrier = Barrier(1)
    request("second")
    with store.connection() as connection:
        approvals = connection.execute("SELECT id FROM approvals").fetchall()
        assert len(approvals) == 2
        bindings = connection.execute(
            "SELECT call_id,approval_id FROM tool_approval_requests ORDER BY call_id"
        ).fetchall()
    for item in approvals:
        GovernanceService(store).decide_approval(item[0], "APPROVED", "local_user")
    for call_id, approval_id in bindings:
        intent = engine.prepare(
            call_id=call_id,
            quest_id=quest.id,
            worker_id=worker.id,
            run_id="run",
            lease_token="lease",
            tool_name="browser_get",
            parameters=params,
        )
        assert intent["approval_id"] == approval_id
