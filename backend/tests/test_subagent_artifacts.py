"""Real delegated contracts/files/immutable receipts, with zero model/provider calls."""

import asyncio
import hashlib
import hmac
import importlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient
from test_artifact_diff_ui import snapshot
from test_subagent_activity import activity as activity  # noqa: PLC0414
from test_subagent_activity import delegated as delegated  # noqa: PLC0414

from forge_core.artifact_diff import VersionReadBudget, read_artifact_diff
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.subagent_activity import SubagentNotFound, read_subagents
from forge_core.subagent_artifacts import read_subagent_artifacts
from forge_core.worker_envelope import canonical


def record_file(store, manager, run, path, content, *, prior=None):
    """Reserve an explicitly scripted test action, then execute governed local file IO."""
    current = manager.get(run["id"])
    token = current["lease_token"] if current["state"] == "running" else manager._claim(run["id"])
    model_id = "fixture-" + secrets.token_hex(5)
    call_id = "call_" + hashlib.sha256(f"{run['id']}:{model_id}".encode()).hexdigest()
    name = "file_revise" if prior else "file_write"
    parameters = {"path": path, "content": content}
    if prior:
        parameters.update(
            prior_receipt_id=prior["receipt_id"], expected_sha256=prior["output"]["sha256"]
        )
    with store.unit_of_work() as work:
        manager._fenced(work.connection, run["id"], token)
        work.connection.execute(
            "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state)"
            " VALUES (?,?,?,?,?,'running')",
            (run["id"], call_id, model_id, name, canonical(parameters)),
        )
        work.connection.execute(
            "UPDATE worker_runs SET tool_spent=tool_spent+1 WHERE id=?", (run["id"],)
        )
    result = asyncio.run(
        manager.tools.execute(
            call_id=call_id,
            quest_id=run["quest_id"],
            worker_id=run["worker_id"],
            run_id=run["id"],
            lease_token=token,
            tool_name=name,
            parameters=parameters,
        )
    )
    assert result["state"] == "succeeded"
    with store.unit_of_work() as work:
        manager._fenced(work.connection, run["id"], token)
        work.connection.execute(
            "UPDATE worker_tool_calls SET state=?,receipt_id=?,result_json=? WHERE run_id=? AND call_id=?",
            (result["state"], result["receipt_id"], canonical(result), run["id"], call_id),
        )
    return result


@pytest.fixture
def artifacts(activity):
    store, manager, runs = activity
    run = runs[0]
    path = f"work/{run['quest_id']}/report.md"
    first = record_file(store, manager, run, path, "first\n")
    last = record_file(store, manager, run, path, "verified history 😊\n", prior=first)
    return store, manager, runs, path, first, last


def test_exact_run_historical_metadata_and_diff_correspondence(artifacts):
    store, _manager, runs, path, _first, last = artifacts
    before = snapshot(store)
    page = read_subagent_artifacts(store, "main", runs[0]["id"])
    assert page["conversation_id"] == "main" and page["workspace_id"] == store.workspace.id
    assert page["run_id"] == runs[0]["id"] and page["execution_mode"] == "mock"
    assert page["provenance"] == "historical_receipts" and page["current_files_verified"] is False
    assert not page["unavailable"] and not page["has_more"] and page["next_before"] is None
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["receipt_id"] == last["receipt_id"] and item["path"] == path
    assert item["bytes"] == len("verified history 😊\n".encode())
    assert item["sha256"] == hashlib.sha256("verified history 😊\n".encode()).hexdigest()
    assert item["change"] == "revised" and item["execution_mode"] == "mock"
    preview = read_artifact_diff(store, item["receipt_id"])
    assert preview["run_id"] == runs[0]["id"] and preview["path"] == item["path"]
    assert preview["sha256"] == item["sha256"] and preview["receipt_id"] == item["receipt_id"]
    serialized = json.dumps(page)
    assert "verified history" not in serialized and str(store.workspace.root) not in serialized
    assert not any(key in serialized for key in ("parameters_json", "result_json", "lease_token"))
    assert snapshot(store) == before


def test_current_file_changes_and_deletion_never_change_historical_claim(artifacts):
    store, _manager, runs, path, _first, _last = artifacts
    before = read_subagent_artifacts(store, "main", runs[0]["id"])
    actual = store.workspace.root / path
    actual.write_text("CURRENT BYTES DIFFER", encoding="utf-8")
    assert read_subagent_artifacts(store, "main", runs[0]["id"]) == before
    actual.unlink()
    assert read_subagent_artifacts(store, "main", runs[0]["id"]) == before


def test_absence_is_not_failure_and_other_run_does_not_inherit_artifacts(artifacts):
    store, _manager, runs, _path, _first, _last = artifacts
    absent = read_subagent_artifacts(store, "main", runs[1]["id"])
    assert absent["items"] == [] and absent["unavailable"] is False and not absent["has_more"]
    assert absent["state"] == "queued"


def test_wrong_scope_and_changed_delegation_fail_before_receipt_read(artifacts, monkeypatch):
    store, _manager, runs, _path, _first, _last = artifacts
    from forge_core import subagent_artifacts

    def forbidden(*args, **kwargs):
        raise AssertionError("Scope rejection must precede receipt reads")

    monkeypatch.setattr(subagent_artifacts, "_bounded_versions", forbidden)
    with store.unit_of_work() as work:
        work.connection.execute("INSERT INTO conversations VALUES ('other','fixture')")
    with pytest.raises(SubagentNotFound):
        read_subagent_artifacts(store, "other", runs[0]["id"])
    with pytest.raises(SubagentNotFound):
        read_subagent_artifacts(store, "main", "run_missing")
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET content='changed original source'")
    with pytest.raises(SubagentNotFound):
        read_subagent_artifacts(store, "main", runs[0]["id"])


def test_project_scope_reuses_authoritative_agent_relation(artifacts):
    store, _manager, runs, _path, _first, _last = artifacts
    with store.unit_of_work() as work:
        for project in ("a", "b"):
            work.connection.execute(
                "INSERT INTO workspace_projects VALUES (?,?,?,?,?)",
                (project, f"/fixture/{project}", project, "[]", "fixture"),
            )
        work.connection.execute(
            "INSERT INTO task_project_links VALUES (?,?,?)", (runs[0]["quest_id"], "a", "fixture")
        )
    assert read_subagent_artifacts(store, "main", runs[0]["id"], project_id="a")["items"]
    with pytest.raises(SubagentNotFound):
        read_subagent_artifacts(store, "main", runs[0]["id"], project_id="b")


def test_invalid_latest_receipt_is_unavailable_not_an_older_version_fallback(artifacts):
    store, _manager, runs, _path, _first, last = artifacts
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_tool_calls SET result_json='{}' WHERE receipt_id=?",
            (last["receipt_id"],),
        )
    page = read_subagent_artifacts(store, "main", runs[0]["id"])
    assert page["items"] == [] and page["unavailable"] is True
    assert not page["has_more"]


@pytest.mark.parametrize("corruption", ["mode", "arguments", "ancestor_checkpoint", "deep_json"])
def test_receipt_chain_must_remain_bound_to_exact_execution(artifacts, corruption):
    store, _manager, runs, _path, first, last = artifacts
    with store.unit_of_work() as work:
        if corruption == "mode":
            work.connection.execute(
                "UPDATE worker_runs SET execution_mode='live' WHERE id=?", (runs[0]["id"],)
            )
        elif corruption == "arguments":
            work.connection.execute(
                "UPDATE worker_tool_calls SET arguments_json='{}' WHERE receipt_id=?",
                (last["receipt_id"],),
            )
        elif corruption == "ancestor_checkpoint":
            work.connection.execute(
                "UPDATE worker_tool_calls SET result_json='{}' WHERE receipt_id=?",
                (first["receipt_id"],),
            )
        else:
            work.connection.execute(
                "UPDATE worker_tool_calls SET result_json=? WHERE receipt_id=?",
                ("[" * 1500 + "0" + "]" * 1500, last["receipt_id"]),
            )
    page = read_subagent_artifacts(store, "main", runs[0]["id"])
    assert not page["items"] and page["unavailable"]


def test_sensitive_path_never_appears_in_list_or_unavailability_reason(activity):
    store, manager, runs = activity
    path = f"work/{runs[0]['quest_id']}/secret-notes.txt"
    record_file(store, manager, runs[0], path, "PRIVATE CONTENT")
    page = read_subagent_artifacts(store, "main", runs[0]["id"])
    assert page["items"] == [] and page["unavailable"] is True
    assert "secret-notes" not in json.dumps(page) and "PRIVATE CONTENT" not in json.dumps(page)


def test_artifact_pages_are_bounded_and_cursors_cannot_cross_run_or_activity(artifacts, tmp_path):
    store, manager, runs, _path, _first, last = artifacts
    other = record_file(store, manager, runs[0], f"work/{runs[0]['quest_id']}/second.txt", "two")
    first = read_subagent_artifacts(store, "main", runs[0]["id"], limit=1)
    assert first["items"][0]["receipt_id"] == other["receipt_id"] and first["has_more"]
    next_page = read_subagent_artifacts(
        store, "main", runs[0]["id"], limit=1, before=first["next_before"]
    )
    assert [item["receipt_id"] for item in next_page["items"]] == [last["receipt_id"]]
    assert not next_page["has_more"]
    for target_run in (runs[1]["id"], "run_unknown"):
        with pytest.raises(ValueError, match="cursor"):
            read_subagent_artifacts(store, "main", target_run, before=first["next_before"])
    activity_cursor = read_subagents(store, "main", limit=1)["next_before"]
    with pytest.raises(ValueError, match="cursor"):
        read_subagent_artifacts(store, "main", runs[0]["id"], before=activity_cursor)
    with pytest.raises(ValueError, match="cursor"):
        read_subagents(store, "main", before=first["next_before"])
    other_store = Store(workspace=Workspace.from_path(tmp_path / "other-workspace"))
    with pytest.raises(ValueError, match="cursor"):
        read_subagent_artifacts(other_store, "main", runs[0]["id"], before=first["next_before"])


def test_aggregate_chain_budget_is_shared_across_candidates(artifacts, monkeypatch):
    store, manager, runs, _path, _first, _last = artifacts
    other = record_file(store, manager, runs[0], f"work/{runs[0]['quest_id']}/second.txt", "two")
    from forge_core import subagent_artifacts

    # The newest one-hop file fits, but the preceding two-hop chain must not get
    # a fresh allowance for each candidate.
    monkeypatch.setattr(
        subagent_artifacts, "VersionReadBudget", lambda: VersionReadBudget(remaining_versions=1)
    )
    page = read_subagent_artifacts(store, "main", runs[0]["id"])
    assert [item["receipt_id"] for item in page["items"]] == [other["receipt_id"]]
    assert page["unavailable"] is True
    monkeypatch.setattr(
        subagent_artifacts, "VersionReadBudget", lambda: VersionReadBudget(remaining_bytes=1)
    )
    page = read_subagent_artifacts(store, "main", runs[0]["id"])
    assert page["items"] == [] and page["unavailable"] is True


def test_rejected_chain_still_consumes_shared_budget_and_page_cursor_advances(
    artifacts, monkeypatch
):
    store, manager, runs, _path, _first, last = artifacts
    newest = record_file(
        store,
        manager,
        runs[0],
        f"work/{runs[0]['quest_id']}/second.txt",
        "two",
    )
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_tool_calls SET result_json='{}' WHERE receipt_id=?",
            (newest["receipt_id"],),
        )
    from forge_core import subagent_artifacts

    monkeypatch.setattr(
        subagent_artifacts,
        "VersionReadBudget",
        lambda: VersionReadBudget(remaining_versions=1),
    )
    page = read_subagent_artifacts(store, "main", runs[0]["id"])
    assert not page["items"] and page["unavailable"]
    monkeypatch.setattr(subagent_artifacts, "VersionReadBudget", VersionReadBudget)
    first = read_subagent_artifacts(store, "main", runs[0]["id"], limit=1)
    assert not first["items"] and first["unavailable"] and first["has_more"]
    second = read_subagent_artifacts(
        store,
        "main",
        runs[0]["id"],
        limit=1,
        before=first["next_before"],
    )
    assert [item["receipt_id"] for item in second["items"]] == [last["receipt_id"]]
    assert not second["has_more"] and not second["unavailable"]


def test_workspace_binding_cannot_be_reinterpreted_for_artifacts_or_detail(artifacts, monkeypatch):
    store, _manager, runs, _path, _first, _last = artifacts
    monkeypatch.setattr(
        store,
        "workspace",
        Workspace.from_path(store.workspace.root, workspace_id="wrong"),
    )
    with pytest.raises(SubagentNotFound):
        read_subagent_artifacts(store, "main", runs[0]["id"])
    with pytest.raises(SubagentNotFound):
        read_subagents(store, "main", run_id=runs[0]["id"])


def test_terminal_status_is_not_inferred_from_file_receipts(artifacts):
    store, _manager, runs, _path, _first, _last = artifacts
    for state in ("review_pending", "failed", "cancelled", "succeeded"):
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE worker_runs SET state=? WHERE id=?", (state, runs[0]["id"])
            )
        page = read_subagent_artifacts(store, "main", runs[0]["id"])
        assert page["state"] == state and page["items"] and not page["current_files_verified"]


def test_artifact_http_auth_scope_bounds_and_no_metadata_disclosure(
    artifacts, tmp_path, monkeypatch
):
    store, _manager, runs, _path, _first, _last = artifacts
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
    path = f"/api/conversations/main/subagents/{runs[0]['id']}/artifacts"
    assert client.get(path).status_code == 401
    response = client.get(path, headers=headers)
    assert response.status_code == 200 and len(response.json()["items"]) == 1
    assert secret not in response.text and "verified history" not in response.text
    assert client.get(path + "?limit=21", headers=headers).status_code == 422
    assert client.get(path + "?before=bad", headers=headers).status_code == 422
    assert client.get(path.replace("/main/", "/other/"), headers=headers).status_code == 404
    assert (
        client.get(path, headers={**headers, "Origin": "https://untrusted.example"}).status_code
        == 403
    )
