"""Real delegated contracts and SQLite projections; no live model execution."""

import hashlib
import hmac
import importlib
import json
import secrets
import time

import pytest
from fastapi.testclient import TestClient
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_superagent_delegation import set_assignments
from test_worker_intelligence_phase2 import ProtocolModel

from forge_core.domain import ConversationMessage, ForgeEvent, Quest, now_iso
from forge_core.provider_pool import ProviderPool
from forge_core.subagent_activity import SubagentNotFound, read_subagents
from forge_core.superagent_delegation import issue_mandate
from forge_core.superagent_events import TriggerService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def start_runs(delegated, count=3):
    store, quest_id, _mandate, graph, teams, service = delegated
    set_assignments(
        store,
        quest_id,
        [{"objective": f"Inspect source {index}", "role": "research"} for index in range(count)],
    )
    assert service.prepare(quest_id)
    model = ProtocolModel([])
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": model}),
        ToolEngine(store),
        team_authority=teams,
    )
    plan = graph.get(quest_id)
    with store.connection() as connection:
        team = connection.execute(
            "SELECT * FROM team_proposals WHERE quest_id=?", (quest_id,)
        ).fetchone()
    runs = [
        manager.start(
            quest_id,
            plan["id"],
            node["key"],
            team_digest=team["proposal_digest"],
            member_index=index,
        )
        for index, node in enumerate(plan["nodes"])
    ]
    assert not model.requests
    return store, manager, runs


@pytest.fixture
def activity(delegated):
    return start_runs(delegated)


def test_verified_list_is_scoped_paginated_and_read_only(activity):
    store, _manager, runs = activity
    with store.connection() as connection:
        source = connection.execute("SELECT source_message_id FROM delegation_mandates").fetchone()[
            0
        ]
        before_events = connection.execute("SELECT count(*) FROM events").fetchone()[0]
    page = read_subagents(store, "main", limit=2)
    assert page["conversation_id"] == "main" and page["workspace_id"] == store.workspace.id
    assert page["has_more"] and page["next_before"]
    assert [item["run_id"] for item in page["items"]] == [runs[2]["id"], runs[1]["id"]]
    last = read_subagents(store, "main", limit=2, before=page["next_before"])
    assert [item["run_id"] for item in last["items"]] == [runs[0]["id"]]
    assert not last["has_more"] and last["next_before"] is None
    first = page["items"][0]
    assert first["parent_message_id"] == source
    assert first["source"] == {"kind": "message", "id": source}
    assert first["task_objective"] == "Inspect source 2"
    assert first["state"] == "queued" and first["execution_mode"] == "mock"
    assert first["lease_status"] == "none" and first["outcome"]["kind"] == "queued"
    assert first["task_title"] == "Research"
    assert first["plan_id"] == runs[2]["plan_id"] and first["node_key"] == runs[2]["node_key"]
    assert not {"lease_token", "allowed_tools_json", "capsule_json", "messages"} & first.keys()
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == before_events


def test_wrong_conversation_cannot_read_same_workspace_run(activity):
    store, _manager, runs = activity
    with store.unit_of_work() as work:
        work.connection.execute("INSERT INTO conversations VALUES ('other',?)", (now_iso(),))
    assert read_subagents(store, "other")["items"] == []
    with pytest.raises(SubagentNotFound):
        read_subagents(store, "other", run_id=runs[0]["id"])
    with pytest.raises(SubagentNotFound):
        read_subagents(store, "missing")
    first = read_subagents(store, "main", limit=1)
    with pytest.raises(ValueError):
        read_subagents(store, "other", before=first["next_before"])


@pytest.mark.parametrize("corruption", ["hash", "role", "conversation", "plan_actor", "team_actor"])
def test_unproved_or_manually_authorized_worker_is_not_a_subagent(activity, corruption):
    store, _manager, runs = activity
    with store.unit_of_work() as work:
        c = work.connection
        if corruption == "hash":
            c.execute("UPDATE messages SET content='changed source'")
        elif corruption == "role":
            c.execute("UPDATE messages SET role='assistant'")
        elif corruption == "conversation":
            c.execute("INSERT INTO conversations VALUES ('other',?)", (now_iso(),))
            c.execute("UPDATE messages SET conversation_id='other'")
        elif corruption == "plan_actor":
            c.execute("UPDATE quest_plans SET approved_by='local_user'")
        else:
            c.execute("UPDATE team_proposals SET approved_by='local_user'")
    assert read_subagents(store, "main")["items"] == []
    with pytest.raises(SubagentNotFound):
        read_subagents(store, "main", run_id=runs[0]["id"])


def test_trigger_delegation_has_no_guessed_parent_turn(delegated):
    store, quest_id, _mandate, _graph, _teams, _service = delegated
    triggers = TriggerService(store)
    triggers.register_source("test-events", kind="message", mode="explicit")
    trigger = triggers.receive(
        "test-events",
        "one",
        "thread-key",
        {"text": "Continue task"},
        addressed=True,
    )
    with store.unit_of_work() as work:
        work.connection.execute("DELETE FROM delegation_mandates")
        issue_mandate(work, quest_id, trigger_id=trigger["id"])
    store, _manager, runs = start_runs(delegated, count=1)
    item = read_subagents(store, "main", run_id=runs[0]["id"])["item"]
    assert item["source"] == {"kind": "trigger", "id": trigger["id"]}
    assert item["parent_message_id"] is None
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE superagent_threads SET workspace_id='wrong-workspace'")
    assert read_subagents(store, "main")["items"] == []


def test_expired_lease_never_looks_active_and_terminal_history_survives_revocation(activity):
    store, _manager, runs = activity
    now = time.time()
    with store.unit_of_work() as work:
        c = work.connection
        c.execute(
            "UPDATE worker_runs SET state='running',lease_until=?,deadline=? WHERE id=?",
            (now - 5, now + 20, runs[0]["id"]),
        )
        c.execute("UPDATE worker_runs SET state='review_pending' WHERE id=?", (runs[1]["id"],))
        c.execute("UPDATE worker_runs SET state='succeeded' WHERE id=?", (runs[2]["id"],))
        c.execute("UPDATE delegation_mandates SET state='revoked'")
    items = {item["run_id"]: item for item in read_subagents(store, "main")["items"]}
    assert items[runs[0]["id"]]["state"] == "running"
    assert items[runs[0]["id"]]["lease_status"] == "expired"
    assert items[runs[0]["id"]]["outcome"]["kind"] == "lease_expired"
    assert items[runs[1]["id"]]["outcome"]["kind"] == "awaiting_review"
    assert items[runs[2]["id"]]["outcome"]["kind"] == "verified"
    assert all(item["delegation_state"] == "revoked" for item in items.values())
    assert store.get_quest(runs[0]["quest_id"])["status"] != "completed"


def test_failed_and_cancelled_entries_are_retained(activity):
    store, _manager, runs = activity
    with store.unit_of_work() as work:
        for run, state in zip(runs, ("failed", "cancelled", "blocked"), strict=True):
            work.connection.execute("UPDATE worker_runs SET state=? WHERE id=?", (state, run["id"]))
    assert {item["state"] for item in read_subagents(store, "main")["items"]} == {
        "failed",
        "cancelled",
        "blocked",
    }


def test_empty_invalid_source_page_advances_to_older_valid_delegation(delegated):
    store, _manager, original = start_runs(delegated, count=2)
    _store, _quest_id, _mandate, graph, teams, service = delegated
    newer = Quest(title="Newer task", goal="New task")
    source = ConversationMessage(role="user", content="New source")
    with store.unit_of_work() as work:
        work.add_message(source)
        work.save_quest(newer)
        mandate = issue_mandate(work, newer.id, source.id)
    start_runs((store, newer.id, mandate, graph, teams, service), count=1)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET content='changed' WHERE id=?", (source.id,))
    page = read_subagents(store, "main", limit=1)
    assert not page["items"] and page["has_more"] and page["next_before"]
    older = read_subagents(store, "main", before=page["next_before"], limit=2)
    assert {item["run_id"] for item in older["items"]} == {run["id"] for run in original}
    assert not older["has_more"]


def test_non_delegated_run_is_excluded_and_text_projection_is_bounded(activity):
    store, _manager, runs = activity
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE task_nodes SET objective=?", ("中文😊" * 1000 + "\u202e\x00",)
        )
        work.connection.execute("UPDATE quests SET title=?", ("任务\u202e\x00" * 100,))
    item = read_subagents(store, "main", run_id=runs[0]["id"])["item"]
    assert len(item["task_title"]) <= 320 and len(item["task_objective"]) <= 1000
    assert item["task_objective"].startswith("中文😊")
    assert "\u202e" not in item["task_title"] and "\x00" not in item["task_title"]
    with store.unit_of_work() as work:
        work.connection.execute("DELETE FROM delegation_contracts")
        work.connection.execute("DELETE FROM delegation_mandates")
    assert read_subagents(store, "main")["items"] == []


def test_verification_milestone_requires_exact_run_not_historical_guess(activity):
    store, _manager, runs = activity
    run = runs[0]
    with store.unit_of_work() as work:
        for payload in (
            {"plan_id": run["plan_id"], "task_node_key": run["node_key"]},
            {"run_id": runs[1]["id"], "execution_mode": "mock"},
            {"run_id": run["id"], "execution_mode": "mock"},
        ):
            work.append_event(
                ForgeEvent(
                    event_type="task.verified",
                    quest_id=run["quest_id"],
                    worker_id=run["worker_id"],
                    payload=payload,
                )
            )
    events = read_subagents(store, "main", run_id=run["id"])["item"]["milestones"]
    assert len([event for event in events if event["event_type"] == "task.verified"]) == 1


def test_milestones_are_bounded_exact_run_facts_without_payloads(activity):
    store, _manager, runs = activity
    run = runs[0]
    secret = "PRIVATE_TOOL_ARGUMENT_AND_RAW_MODEL_REASONING"
    with store.unit_of_work() as work:
        c = work.connection
        c.execute("INSERT INTO conversations VALUES ('other',?)", (now_iso(),))
        for index in range(25):
            work.append_event(
                ForgeEvent(
                    event_type="worker.progress",
                    quest_id=run["quest_id"],
                    worker_id=run["worker_id"],
                    payload={"run_id": run["id"], "reasoning": secret, "index": index},
                )
            )
        work.append_event(
            ForgeEvent(
                event_type="worker.failed",
                quest_id=run["quest_id"],
                worker_id=run["worker_id"],
                conversation_id="other",
                payload={"run_id": run["id"], "secret": secret},
            )
        )
        work.append_event(
            ForgeEvent(
                event_type="worker.failed",
                quest_id=run["quest_id"],
                worker_id=run["worker_id"],
                payload={"run_id": runs[1]["id"], "secret": secret},
            )
        )
    item = next(
        item for item in read_subagents(store, "main")["items"] if item["run_id"] == run["id"]
    )
    detail = read_subagents(store, "main", run_id=run["id"])["item"]
    assert len(item["milestones"]) == 5 and len(detail["milestones"]) == 20
    assert all(event["event_type"] == "worker.progress" for event in detail["milestones"])
    seq = [event["seq"] for event in detail["milestones"]]
    assert seq == sorted(seq) and len(set(seq)) == len(seq)
    assert secret not in json.dumps(detail)


def test_project_filter_is_authoritative_and_cursor_cannot_cross_scope(activity):
    store, _manager, runs = activity
    with store.unit_of_work() as work:
        for key in ("project-a", "project-b"):
            work.connection.execute(
                "INSERT INTO workspace_projects VALUES (?,?,?,?,?)",
                (key, f"/fixture/{key}", key, "[]", now_iso()),
            )
        work.connection.execute(
            "INSERT INTO task_project_links VALUES (?,?,?)",
            (runs[0]["quest_id"], "project-a", now_iso()),
        )
    page = read_subagents(store, "main", project_id="project-a", limit=1)
    assert len(page["items"]) == 1 and page["items"][0]["project_id"] == "project-a"
    assert read_subagents(store, "main", project_id="project-b")["items"] == []
    with pytest.raises(SubagentNotFound):
        read_subagents(store, "main", project_id="project-b", run_id=runs[0]["id"])
    with pytest.raises(ValueError):
        read_subagents(store, "main", before=page["next_before"])
    with pytest.raises(SubagentNotFound):
        read_subagents(store, "main", project_id="missing")


@pytest.mark.parametrize("before", ["", "not/a/cursor", "x" * 97, "☃", "MA=="])
def test_bad_cursor_fails_closed(activity, before):
    store, _manager, _runs = activity
    with pytest.raises(ValueError):
        read_subagents(store, "main", before=before)


def test_authenticated_http_list_and_detail_keep_same_scope(activity, tmp_path, monkeypatch):
    store, _manager, runs = activity
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path / "http"))
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "http" / "api.sqlite3"))
    from forge_api import main

    importlib.reload(main)
    monkeypatch.setattr(main, "store", store)
    client = TestClient(main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345))
    challenge = client.get("/health").json()["session"]
    headers = {
        "Authorization": "Bearer "
        + hmac.new(
            bytes.fromhex(secret),
            challenge.encode(),
            hashlib.sha256,
        ).hexdigest()
    }
    url = "/api/conversations/main/subagents"
    assert client.get(url).status_code == 401
    assert client.get(url + "/" + runs[0]["id"]).status_code == 401
    response = client.get(url + "?limit=2", headers=headers)
    assert response.status_code == 200 and len(response.json()["items"]) == 2
    assert secret not in response.text
    assert client.get(url + "?limit=101", headers=headers).status_code == 422
    assert client.get(url + "?before=garbage", headers=headers).status_code == 422
    detail = client.get(url + "/" + runs[0]["id"], headers=headers)
    assert detail.status_code == 200 and detail.json()["item"]["run_id"] == runs[0]["id"]
    assert (
        client.get(
            "/api/conversations/other/subagents/" + runs[0]["id"], headers=headers
        ).status_code
        == 404
    )
    assert client.get(url + "/unknown", headers=headers).status_code == 404
    assert (
        client.get(url, headers={**headers, "Origin": "https://untrusted.example"}).status_code
        == 403
    )
