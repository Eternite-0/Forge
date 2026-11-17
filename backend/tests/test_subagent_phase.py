"""Scale/scoping projections over offline persisted fixtures, not live executions."""

import base64
import hashlib
import hmac
import importlib
import secrets
import time

import pytest
from fastapi.testclient import TestClient
from test_subagent_activity import activity as activity  # noqa: PLC0414
from test_subagent_activity import delegated as delegated  # noqa: PLC0414

from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.subagent_activity import read_subagents

ACTIVE = ("queued", "running", "paused", "blocked", "review_pending")
TERMINAL = ("succeeded", "failed", "cancelled")


def seed_persisted_history(store, template_id, states):
    """Expand valid fixture bindings into historical records without executing them."""
    ids = []
    with store.unit_of_work() as work:
        c = work.connection
        template = dict(
            c.execute("SELECT * FROM worker_runs WHERE id=?", (template_id,)).fetchone()
        )
        worker = dict(
            c.execute("SELECT * FROM workers WHERE id=?", (template["worker_id"],)).fetchone()
        )
        for index, state in enumerate(states):
            run_id = f"run_scale_{index}_{secrets.token_hex(4)}"
            worker_id = f"worker_scale_{index}_{secrets.token_hex(4)}"
            new_worker = {**worker, "id": worker_id, "status": state}
            c.execute(
                "INSERT INTO workers ("
                + ",".join(new_worker)
                + ") VALUES ("
                + ",".join("?" for _ in new_worker)
                + ")",
                tuple(new_worker.values()),
            )
            new_run = {**template, "id": run_id, "worker_id": worker_id, "state": state}
            c.execute(
                "INSERT INTO worker_runs ("
                + ",".join(new_run)
                + ") VALUES ("
                + ",".join("?" for _ in new_run)
                + ")",
                tuple(new_run.values()),
            )
            ids.append(run_id)
    return ids


@pytest.mark.parametrize("state", ACTIVE)
def test_old_active_survives_more_than_one_hundred_newer_terminal_runs(activity, state):
    store, _manager, runs = activity
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state=?,lease_until=?,deadline=? WHERE id=?",
            (state, time.time() + 60, time.time() + 120, runs[0]["id"]),
        )
    newer = seed_persisted_history(store, runs[0]["id"], [TERMINAL[i % 3] for i in range(105)])
    all_page = read_subagents(store, "main", limit=100)
    assert all_page["phase"] == "all" and all_page["has_more"]
    assert runs[0]["id"] not in {item["run_id"] for item in all_page["items"]}
    active = read_subagents(store, "main", phase="active")
    assert active["phase"] == "active" and not active["has_more"]
    assert {item["run_id"] for item in active["items"]} == {run["id"] for run in runs}
    terminal = read_subagents(store, "main", phase="terminal", limit=100)
    assert terminal["has_more"] and {item["state"] for item in terminal["items"]} == set(TERMINAL)
    older = read_subagents(
        store, "main", phase="terminal", before=terminal["next_before"], limit=100
    )
    combined = terminal["items"] + older["items"]
    assert [item["run_id"] for item in combined] == list(reversed(newer))
    assert not older["has_more"] and older["next_before"] is None


def test_expired_running_lease_and_pending_review_remain_active(activity):
    store, _manager, runs = activity
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='running',lease_until=?,deadline=? WHERE id=?",
            (time.time() - 1, time.time() + 60, runs[0]["id"]),
        )
        work.connection.execute(
            "UPDATE worker_runs SET state='review_pending' WHERE id=?", (runs[1]["id"],)
        )
        work.connection.execute(
            "UPDATE worker_runs SET state='succeeded' WHERE id=?", (runs[2]["id"],)
        )
    active = {
        item["run_id"]: item for item in read_subagents(store, "main", phase="active")["items"]
    }
    assert set(active) == {runs[0]["id"], runs[1]["id"]}
    assert active[runs[0]["id"]]["lease_status"] == "expired"
    assert active[runs[0]["id"]]["outcome"]["kind"] == "lease_expired"
    assert active[runs[1]["id"]]["outcome"]["kind"] == "awaiting_review"
    assert [
        item["run_id"] for item in read_subagents(store, "main", phase="terminal")["items"]
    ] == [runs[2]["id"]]


def test_more_active_pages_are_bounded_and_do_not_drop_paused_or_blocked(activity):
    store, _manager, runs = activity
    added = seed_persisted_history(store, runs[0]["id"], [ACTIVE[i % 5] for i in range(105)])
    first = read_subagents(store, "main", phase="active", limit=100)
    assert len(first["items"]) == 100 and first["has_more"]
    second = read_subagents(store, "main", phase="active", limit=100, before=first["next_before"])
    ids = [item["run_id"] for item in first["items"] + second["items"]]
    assert len(ids) == 108 and len(set(ids)) == 108
    assert ids == [*reversed(added), *(run["id"] for run in reversed(runs))]
    assert not second["has_more"]


@pytest.mark.parametrize("phase", ["all", "terminal"])
def test_cursor_cannot_cross_phase(activity, phase):
    store, _manager, _runs = activity
    cursor = read_subagents(store, "main", phase="active", limit=1)["next_before"]
    with pytest.raises(ValueError, match="cursor"):
        read_subagents(store, "main", phase=phase, before=cursor)


def test_cursor_cannot_cross_workspace_and_v1_cannot_omit_workspace_scope(activity, tmp_path):
    store, _manager, _runs = activity
    cursor = read_subagents(store, "main", limit=1)["next_before"]
    other = Store(workspace=Workspace.from_path(tmp_path / "other-workspace"))
    with pytest.raises(ValueError, match="cursor"):
        read_subagents(other, "main", before=cursor)
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    assert raw.startswith("2:")
    legacy = base64.urlsafe_b64encode(("1:" + raw[2:]).encode()).decode().rstrip("=")
    with pytest.raises(ValueError, match="cursor"):
        read_subagents(store, "main", before=legacy)


def test_invalid_source_active_page_keeps_advancing_without_cross_phase_fallback(activity):
    store, _manager, _runs = activity
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET content='changed source'")
    first = read_subagents(store, "main", phase="active", limit=1)
    assert not first["items"] and first["has_more"] and first["next_before"]
    second = read_subagents(store, "main", phase="active", limit=1, before=first["next_before"])
    assert not second["items"] and second["has_more"]
    assert second["next_before"] != first["next_before"]
    last = read_subagents(store, "main", phase="active", limit=1, before=second["next_before"])
    assert not last["items"] and not last["has_more"] and last["next_before"] is None
    assert read_subagents(store, "main", phase="terminal")["items"] == []


@pytest.mark.parametrize("phase", ["", "finished", "ACTIVE", None, 1, []])
def test_invalid_phase_fails_closed(activity, phase):
    store, _manager, _runs = activity
    with pytest.raises(ValueError, match="phase"):
        read_subagents(store, "main", phase=phase)


def test_terminal_transition_does_not_stale_detail_or_duplicate_pagination(activity):
    store, _manager, runs = activity
    old = read_subagents(store, "main", phase="active", limit=1)
    assert old["items"][0]["run_id"] == runs[2]["id"]
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='failed' WHERE id=?", (runs[2]["id"],)
        )
    current = read_subagents(store, "main", phase="active", limit=1)
    assert current["items"][0]["run_id"] == runs[1]["id"]
    detail = read_subagents(store, "main", run_id=runs[2]["id"])
    assert detail["phase"] == "all" and detail["item"]["state"] == "failed"
    older = read_subagents(store, "main", phase="active", before=old["next_before"])
    assert [item["run_id"] for item in older["items"]] == [runs[1]["id"], runs[0]["id"]]
    assert [
        item["run_id"] for item in read_subagents(store, "main", phase="terminal")["items"]
    ] == [runs[2]["id"]]
    with pytest.raises(ValueError, match="phase"):
        read_subagents(store, "main", run_id=runs[2]["id"], phase="active")


def test_http_phase_filter_remains_authenticated_and_cursor_scoped(activity, tmp_path, monkeypatch):
    store, _manager, runs = activity
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path / "http"))
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
    assert client.get(url + "?phase=active").status_code == 401
    first = client.get(url + "?phase=active&limit=1", headers=headers)
    assert first.status_code == 200 and first.json()["phase"] == "active"
    cursor = first.json()["next_before"]
    assert (
        client.get(url, params={"phase": "terminal", "before": cursor}, headers=headers).status_code
        == 422
    )
    assert client.get(url + "?phase=invalid", headers=headers).status_code == 422
    assert client.get(url + "?phase=terminal", headers=headers).json()["items"] == []
    assert client.get(url + "/" + runs[0]["id"], headers=headers).json()["phase"] == "all"
    assert (
        client.get(
            url + "?phase=active", headers={**headers, "Origin": "https://foreign.example"}
        ).status_code
        == 403
    )
