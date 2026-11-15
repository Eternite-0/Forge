"""A real offline native Worker help event creates one independently bounded Composer cycle."""

import asyncio
import copy
import hashlib
import json

import httpx
import pytest
from test_composer_lifecycle_v28 import stage
from test_composer_resources_v21 import semantic_team
from test_event_dispatcher import drain_dispatcher

from forge_core.composition_cycles import cycle_state
from forge_core.domain import DomainError
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


def requesting_worker(tmp_path, monkeypatch):
    main, quest, plan, _requests = stage(tmp_path, monkeypatch)
    assert asyncio.run(main.process_pending_team())
    with main.store.connection() as connection:
        team = dict(
            connection.execute(
                "SELECT * FROM team_proposals WHERE quest_id=?", (quest.id,)
            ).fetchone()
        )
    body = json.loads(team["body_json"])
    # Explicit authenticated team approval grants a help depth before the Worker starts.
    limits = {**body["limits"], "max_depth": 1}
    approved = main.team_service.propose(
        quest.id, body["roles"], limits, strategy="greedy", expected_version=2
    )
    main.team_service.approve(
        quest.id, 1, approved["proposal_digest"], expected_version=2, actor="local_user"
    )
    manager = WorkerManager(
        main.store,
        main.team_service.capacity_providers,
        ToolEngine(main.store),
        team_authority=main.team_service,
    )
    worker = manager.start(
        quest.id, plan["id"], "analysis", team_digest=approved["proposal_digest"], member_index=0
    )

    def help_reply(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "help",
                                    "type": "function",
                                    "function": {
                                        "name": "worker_request_help",
                                        "arguments": json.dumps(
                                            {
                                                "role": "reviewer",
                                                "reason": "Need an independent assessment.",
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    main.router.provider._transport = httpx.MockTransport(help_reply)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "blocked"
    drain_dispatcher(main.dispatcher)
    with main.store.connection() as connection:
        event = dict(
            connection.execute(
                "SELECT * FROM events WHERE quest_id=? AND event_type='worker.help_requested'",
                (quest.id,),
            ).fetchone()
        )
    return main, quest, plan, manager, worker, event


def composition_transport(main, quest, *, outcome):
    requests = []

    def respond(request):
        data = json.loads(request.content)
        requests.append(data)
        if outcome == "timeout":
            raise httpx.ReadTimeout("Offline bounded fixture", request=request)
        proposal = semantic_team(main.quest_graph.get(quest.id))
        if outcome == "invalid" or (outcome == "repair" and len(requests) == 1):
            proposal["roles"][0]["input_contract"] = [" "]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(proposal)}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            },
        )

    main.router.provider._transport = httpx.MockTransport(respond)
    return requests


@pytest.mark.parametrize("outcome,count", [("valid", 1), ("repair", 2)])
def test_real_help_has_a_new_native_binding_and_waits_for_reassignment_approval(
    tmp_path, monkeypatch, outcome, count
):
    main, quest, plan, manager, worker, event = requesting_worker(tmp_path, monkeypatch)
    requests = composition_transport(main, quest, outcome=outcome)
    assert asyncio.run(main.process_pending_team())
    with main.store.connection() as connection:
        measured = json.loads(
            connection.execute(
                "SELECT payload FROM events WHERE event_type='composer.reassignment_measured'"
            ).fetchone()[0]
        )
        team = dict(
            connection.execute(
                "SELECT * FROM team_proposals WHERE quest_id=? AND status='proposed'", (quest.id,)
            ).fetchone()
        )
        attempts = connection.execute(
            "SELECT * FROM model_budget_attempts WHERE parent_call_id=?", (measured["cycle_id"],)
        ).fetchall()
        original_json = connection.execute(
            "SELECT graph_json FROM quest_plans WHERE id=?", (plan["id"],)
        ).fetchone()[0]
        assert hashlib.sha256(original_json.encode()).hexdigest() == plan["digest"]
        assert cycle_state(connection, measured)["accepted_semantic_team"]
        assert (
            connection.execute(
                "SELECT state FROM worker_runs WHERE id=?", (worker["id"],)
            ).fetchone()[0]
            == "blocked"
        )
    assert len(attempts) == len(requests) == count
    assert measured["binding"]["source_event_id"] == event["id"]
    assert measured["cycle_id"] != measured["measurement"]["semantic_request_sha256"]
    assert (
        measured["measurement"]["semantic_request_sha256"]
        != plan["capacity_contract"]["composition_capacity"]["semantic_request_sha256"]
    )
    for index, attempt in enumerate(attempts):
        assert attempt["state"] == "settled"
        assert (
            attempt["input_reserved"] + attempt["output_reserved"]
            <= measured["measurement"]["initial_call_bound" if index == 0 else "repair_call_bound"]
            <= 65536
        )
    context = json.loads(
        next(item["content"] for item in requests[0]["messages"] if item["role"] == "user")
    )
    assert context["help_request"]["source_event_id"] == event["id"]
    assert context["help_request"]["request"] == json.loads(event["payload"])
    body = json.loads(team["body_json"])
    assert body["composition_cycle_id"] == measured["cycle_id"]
    assert body["reassignment"]["run_id"] == worker["id"]
    with pytest.raises(DomainError):
        manager.start(
            quest.id, plan["id"], "analysis", team_digest=team["proposal_digest"], member_index=0
        )
    main.team_service.approve(
        quest.id,
        1,
        team["proposal_digest"],
        expected_version=main.store.get_quest(quest.id)["version"],
        actor="local_user",
    )
    assert manager.get(worker["id"])["state"] == "cancelled"
    replacement = manager.start(
        quest.id, plan["id"], "analysis", team_digest=team["proposal_digest"], member_index=0
    )
    assert replacement["id"] != worker["id"] and replacement["state"] == "queued"


@pytest.mark.parametrize("outcome,count", [("invalid", 2), ("timeout", 1)])
def test_failed_or_unknown_help_cycle_is_durable_and_never_replayed(
    tmp_path, monkeypatch, outcome, count
):
    main, quest, _plan, _manager, _worker, event = requesting_worker(tmp_path, monkeypatch)
    requests = composition_transport(main, quest, outcome=outcome)
    assert not asyncio.run(main.process_pending_team())
    assert len(requests) == count
    with main.store.connection() as connection:
        record = connection.execute(
            "SELECT payload FROM events WHERE event_type='composer.reassignment_measured'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE event_inbox SET notice_next_attempt=0,notice_dead_at=NULL WHERE consumer_id='composer' AND event_id=?",
            (event["id"],),
        )
        connection.commit()
    assert not asyncio.run(main.process_pending_team())
    assert len(requests) == count
    with main.store.connection() as connection:
        assert (
            connection.execute(
                "SELECT payload FROM events WHERE event_type='composer.reassignment_measured'"
            ).fetchone()[0]
            == record
        )


def test_approval_rejects_a_changed_native_reassignment_registry(tmp_path, monkeypatch):
    main, quest, _plan, _manager, _worker, _event = requesting_worker(tmp_path, monkeypatch)
    composition_transport(main, quest, outcome="valid")
    assert asyncio.run(main.process_pending_team())
    with main.store.connection() as connection:
        proposal = connection.execute(
            "SELECT proposal_digest FROM team_proposals WHERE status='proposed'"
        ).fetchone()[0]
    model = main.store.list_models()[0]
    changed = copy.deepcopy(model)
    changed.max_output_tokens = 3072
    main.store.save_model(changed)
    with pytest.raises((DomainError, ValueError)):
        main.team_service.approve(
            quest.id,
            1,
            proposal,
            expected_version=main.store.get_quest(quest.id)["version"],
            actor="local_user",
        )


@pytest.mark.parametrize("change", ["source_payload", "completed_target"])
def test_approval_rejects_a_stale_help_binding_without_replacing_the_worker(
    tmp_path, monkeypatch, change
):
    main, quest, plan, manager, worker, event = requesting_worker(tmp_path, monkeypatch)
    composition_transport(main, quest, outcome="valid")
    assert asyncio.run(main.process_pending_team())
    with main.store.connection() as connection:
        proposal = connection.execute(
            "SELECT proposal_digest FROM team_proposals WHERE status='proposed'"
        ).fetchone()[0]
        if change == "source_payload":
            payload = json.loads(event["payload"])
            payload["reason"] = "Changed after composition measurement"
            connection.execute(
                "UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), event["id"])
            )
        else:
            connection.execute(
                "UPDATE task_nodes SET status='succeeded' WHERE plan_id=? AND node_key='analysis'",
                (plan["id"],),
            )
        connection.commit()
    version = main.store.get_quest(quest.id)["version"]
    with pytest.raises(DomainError):
        main.team_service.approve(
            quest.id, 1, proposal, expected_version=version, actor="local_user"
        )
    assert manager.get(worker["id"])["state"] == "blocked"
    assert main.store.get_quest(quest.id)["version"] == version
    with main.store.connection() as connection:
        assert (
            connection.execute(
                "SELECT status FROM team_proposals WHERE proposal_digest=?", (proposal,)
            ).fetchone()[0]
            == "proposed"
        )
        assert not connection.execute(
            "SELECT 1 FROM events WHERE event_type='team.reassigned'"
        ).fetchone()
