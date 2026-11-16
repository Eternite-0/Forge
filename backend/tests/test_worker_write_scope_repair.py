"""Mock reasoning with real governed file tools; no Provider is called."""

import asyncio
import hashlib
import json

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.governance import GovernanceService
from forge_core.independent_review import _review_receipts
from forge_core.tool_engine import ToolAuthorizationRequired
from forge_core.worker_recovery import settled_preexecution_rejection, unresolved_calls


def narrow_grant(store, worker):
    governance = GovernanceService(store)
    governance.revoke_workspace(worker["quest_id"], "work", "local_user")
    path = "work/" + worker["quest_id"]
    (store.workspace.root / path).mkdir()
    governance.grant_workspace(worker["quest_id"], path, "local_user")
    return path


@pytest.mark.parametrize("name", ["file_write", "directory_create"])
def test_local_write_denial_feeds_real_grant_and_model_chooses_new_path(
    tmp_path, monkeypatch, name
):
    store, manager, model, worker = prepared(tmp_path, [], tools=[name])
    root = narrow_grant(store, worker)
    filename = "research.json" if name == "file_write" else "reports"
    values = {"content": '{"observed":true}'} if name == "file_write" else {}
    model.responses[:] = [
        tool(name, {"path": filename, **values}),
        tool(name, {"path": root + "/" + filename, **values}, 2),
        response(delivery()),
    ]
    effects = []
    perform = manager.tools._perform

    def counted(*args):
        effects.append(args[0]["call_id"])
        return perform(*args)

    monkeypatch.setattr(manager.tools, "_perform", counted)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert len(model.requests) == 3 and len(effects) == 1
    assert not (tmp_path / filename).exists() and (tmp_path / root / filename).exists()
    with store.connection() as c:
        calls = c.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        assert [call["state"] for call in calls] == ["failed", "succeeded"]
        assert calls[0]["receipt_id"] is None and settled_preexecution_rejection(
            c, worker["id"], calls[0]
        )
        assert not unresolved_calls(c, worker["id"])
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 1
        assert (
            c.execute("SELECT COUNT(*) FROM task_grants WHERE revoked_at IS NULL").fetchone()[0]
            == 1
        )
        feedback = json.loads(calls[0]["result_json"])["output"]
        assert feedback["allowed_write_roots"] == [root]
        assert feedback["error_code"] == "local_write_outside_existing_grant"
        receipts, _ = _review_receipts(store, c, c.execute("SELECT * FROM worker_runs").fetchone())
        assert (
            receipts[0]["state"] == "rejected_before_execution"
            and receipts[0]["receipt_id"] is None
        )
    observed = [
        json.loads(item["content"]) for item in model.requests[1].messages if item["role"] == "tool"
    ]
    assert observed[0]["output"]["allowed_write_roots"] == [root]


def test_file_revise_path_rejection_precedes_intent_and_keeps_owned_revision_chain(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write", "file_revise"])
    root = narrow_grant(store, worker)
    original = "original"
    model.responses[:] = [
        tool("file_write", {"path": root + "/candidate.py", "content": original}),
        response("placeholder"),
        response("placeholder"),
        response(delivery()),
    ]
    generate = model.generate

    async def choose_revision(request):
        if len(model.requests) in {1, 2}:
            with store.connection() as c:
                receipt = c.execute(
                    "SELECT id FROM action_receipts ORDER BY rowid LIMIT 1"
                ).fetchone()[0]
            number = len(model.requests)
            model.responses[0] = tool(
                "file_revise",
                {
                    "path": "candidate.py" if number == 1 else root + "/candidate.py",
                    "content": "revised",
                    "prior_receipt_id": receipt,
                    "expected_sha256": hashlib.sha256(original.encode()).hexdigest(),
                },
                number + 1,
            )
        return await generate(request)

    model.generate = choose_revision
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 1
    assert (tmp_path / root / "candidate.py").read_text() == "revised"
    assert not (tmp_path / "candidate.py").exists()
    with store.connection() as c:
        calls = c.execute("SELECT * FROM worker_tool_calls ORDER BY rowid").fetchall()
        assert [call["state"] for call in calls] == ["succeeded", "failed", "succeeded"]
        assert settled_preexecution_rejection(c, worker["id"], calls[1])
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 2


def test_repeated_outside_scope_write_uses_only_existing_two_repairs_and_no_effect(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write"])
    narrow_grant(store, worker)
    model.responses[:] = [
        tool("file_write", {"path": "research.json", "content": "denied"}, index + 1)
        for index in range(3)
    ]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == manager.max_repairs == 2
    assert len(model.requests) == 3 and not (tmp_path / "research.json").exists()
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_unknown_intent_prevents_write_scope_repair_and_any_additional_effect(
    tmp_path, monkeypatch
):
    store, manager, model, worker = prepared(
        tmp_path,
        [tool("file_write", {"path": "research.json", "content": "denied"})],
        tools=["file_write"],
    )
    narrow_grant(store, worker)

    async def unknown(**kwargs):
        with store.unit_of_work() as work:
            work.connection.execute(
                """INSERT INTO action_intents
                SELECT 'uncertain-source',?,quest_id,worker_id,id,plan_id,plan_revision,
                'file_write','{}','fixture','fixture','R1',NULL,execution_mode,
                'uncertain','fixture','fixture' FROM worker_runs WHERE id=?""",
                (kwargs["call_id"], worker["id"]),
            )
        raise ToolAuthorizationRequired(
            "file_write", kwargs["parameters"], str(tmp_path / "research.json"), "R1"
        )

    monkeypatch.setattr(manager.tools, "execute", unknown)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["repairs"] == 0 and len(model.requests) == 1
    assert not (tmp_path / "research.json").exists()
    with store.connection() as c:
        assert c.execute("SELECT state FROM action_intents").fetchone()[0] == "uncertain"
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0


def test_r2_exact_python_approval_still_blocks_without_repair_or_execution(tmp_path):
    store, manager, model, worker = prepared(tmp_path, [], tools=["python_unittest"])
    root = narrow_grant(store, worker)
    source = b"# deliberately never executed\n"
    (tmp_path / root / "test_candidate.py").write_bytes(source)
    model.responses[:] = [
        tool(
            "python_unittest",
            {
                "path": root,
                "manifest": {"test_candidate.py": hashlib.sha256(source).hexdigest()},
                "test_module": "test_candidate",
            },
        )
    ]
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked" and result["awaiting_authorization"] == 1
    assert result["repairs"] == 0 and len(model.requests) == 1
    with store.connection() as c:
        assert c.execute("SELECT status FROM approvals").fetchone()[0] == "PENDING"
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0


@pytest.mark.parametrize("tamper", ["parameters", "grant_hint", "event_category"])
def test_write_denial_recovery_proof_is_bound_to_original_call_grants_and_event(tmp_path, tamper):
    store, manager, model, worker = prepared(tmp_path, [], tools=["file_write"])
    narrow_grant(store, worker)
    model.responses[:] = [
        tool("file_write", {"path": "research.json", "content": "denied"}),
        response(delivery()),
    ]
    assert asyncio.run(manager.run(worker["id"]))["repairs"] == 1
    with store.unit_of_work() as work:
        c = work.connection
        call = c.execute("SELECT * FROM worker_tool_calls").fetchone()
        assert settled_preexecution_rejection(c, worker["id"], call)
        if tamper == "parameters":
            parameters = json.loads(call["arguments_json"])
            parameters["path"] = "different.json"
            c.execute("UPDATE worker_tool_calls SET arguments_json=?", (json.dumps(parameters),))
        elif tamper == "grant_hint":
            result = json.loads(call["result_json"])
            result["output"]["allowed_write_roots"] = ["ungranted"]
            c.execute("UPDATE worker_tool_calls SET result_json=?", (json.dumps(result),))
        else:
            event = c.execute(
                "SELECT id,payload FROM events WHERE event_type='worker.progress'"
            ).fetchone()
            payload = json.loads(event["payload"])
            payload["failure_category"] = "tool"
            c.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), event["id"]))
        changed = c.execute("SELECT * FROM worker_tool_calls").fetchone()
        assert not settled_preexecution_rejection(c, worker["id"], changed)
        assert unresolved_calls(c, worker["id"])
        assert c.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0] == 0
