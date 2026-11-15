"""Actual Composer pump regressions with scripted mock responses, no network."""

import asyncio
import copy
import importlib
import json
import secrets

import pytest

from forge_core.domain import ModelProfile, Quest


class ComposerMock:
    execution_mode = "mock"

    def __init__(self, *proposals):
        self.proposals = proposals
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        proposal = self.proposals[min(len(self.requests) - 1, len(self.proposals) - 1)]
        return type(
            "ScriptedResponse",
            (),
            {"text": json.dumps(proposal), "usage": {}, "finish_reason": "stop"},
        )()


def team_wire():
    return {
        "roles": [
            {
                "id": "coding",
                "task_node_key": "n",
                "required_capabilities": [{"name": "coding", "minimum": 70.0}],
                "tools": [],
                "input_tokens": 100,
                "output_tokens": 50,
                "max_steps": 4,
                "max_tool_calls": 0,
                "max_duration_seconds": 20,
                "depth": 0,
                "dynamic": False,
                "responsibility": "Inspect the bounded task",
                "input_contract": ["Approved objective"],
                "output_schema_json": None,
                "acceptance_criteria": ["delivery_review"],
            }
        ],
        "limits": {
            "max_members": 1,
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 150,
            "max_total_tool_calls": 0,
            "max_cost_microunits": None,
            "currency": None,
            "allowed_tools": [],
        },
        "strategy": "greedy",
    }


@pytest.fixture
def composer_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for name in (
        "FORGE_DB_PATH",
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
        "FORGE_ROUTER_MODEL_ID",
        "FORGE_ROUTER_CONTEXT_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="Composer repair", goal="Inspect source under the approved contract")
    main.store.save_quest(quest)
    plan = main.quest_graph.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": quest.goal,
                    "success_criteria": ["delivery_review"],
                    "budget_tokens": 1000,
                    "required_capabilities": {"coding": 70.0},
                    "allowed_tools": [],
                }
            ],
            "edges": [],
            "max_total_tokens": 1000,
            "max_duration_seconds": 30,
        },
        expected_version=0,
    )
    main.quest_graph.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    main.store.save_model(
        ModelProfile(
            id="candidate",
            provider="fixture-provider",
            model_id="fixture-model",
            display_name="Offline candidate",
            capabilities={"coding": 80},
            context_window=64000,
            max_output_tokens=4000,
        )
    )
    main.router.model_id = "composer-mock"
    main.router.provider_id = "composer-mock"
    main.router.context_limit = 64000
    main.dispatcher.register("composer")
    for _ in range(30):
        main.dispatcher.tick()
        if any(
            row["event_type"] == "quest.plan_approved" for row in main.dispatcher.inbox("composer")
        ):
            break
    notice = next(
        row
        for row in main.dispatcher.inbox("composer")
        if row["event_type"] == "quest.plan_approved"
    )
    return main, quest, notice


def notice_row(main, event_id):
    with main.store.connection() as connection:
        return dict(
            connection.execute(
                "SELECT * FROM event_inbox WHERE consumer_id='composer' AND event_id=?", (event_id,)
            ).fetchone()
        )


@pytest.mark.parametrize(
    "violation,message",
    [
        ("capability", "Role weakens approved task capability requirements"),
        ("duration", "Team duration budget exceeds plan"),
        ("concurrency", "Team concurrency exceeds operator limit"),
    ],
)
def test_semantic_rejection_is_repaired_once_before_persisting_a_team(
    composer_stage, violation, message
):
    main, quest, notice = composer_stage
    valid = team_wire()
    invalid = copy.deepcopy(valid)
    if violation == "capability":
        invalid["roles"][0]["required_capabilities"][0]["minimum"] = 40.0
    elif violation == "duration":
        invalid["roles"][0]["max_duration_seconds"] = 31
    else:
        invalid["limits"].update(max_members=3, max_concurrency=3)
    mock = ComposerMock(invalid, valid)
    main.router.provider = mock
    assert asyncio.run(main.process_pending_team()) is True
    assert len(mock.requests) == 2
    assert json.loads(mock.requests[0].messages[1]["content"])["execution_limits"] == {
        "max_concurrency": main.native_worker_concurrency
    }
    repair = json.loads(mock.requests[1].messages[-1]["content"])
    assert message in repair["rejected_proposal_error"]
    with main.store.connection() as connection:
        teams = connection.execute(
            "SELECT * FROM team_proposals WHERE quest_id=?", (quest.id,)
        ).fetchall()
    assert len(teams) == 1 and teams[0]["status"] == "proposed"
    member = json.loads(teams[0]["body_json"])["members"][0]
    assert member["capabilities"]["coding"] >= 70.0
    assert member["max_duration_seconds"] == 20
    saved = notice_row(main, notice["event_id"])
    assert saved["processed_at"] is not None and saved["notice_attempts"] == 1
    assert saved["notice_dead_at"] is None
    assert main.store.list_workers(quest.id) == []


def test_repeated_semantic_failure_keeps_persisted_attempt_count_and_stops_at_five(composer_stage):
    main, quest, notice = composer_stage
    invalid = team_wire()
    invalid["roles"][0]["required_capabilities"][0]["minimum"] = 40.0
    mock = ComposerMock(invalid)
    main.router.provider = mock
    with main.store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE event_inbox SET notice_attempts=2,notice_next_attempt=0 WHERE consumer_id='composer' AND event_id=?",
            (notice["event_id"],),
        )
    for expected in (3, 4, 5):
        assert asyncio.run(main.process_pending_team()) is False
        saved = notice_row(main, notice["event_id"])
        assert saved["notice_attempts"] == expected
        assert saved["processed_at"] is None
        assert "composition_failed" in saved["notice_error"]
        assert (saved["notice_dead_at"] is not None) == (expected == 5)
        with main.store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE event_inbox SET notice_next_attempt=0 WHERE consumer_id='composer' AND event_id=?",
                (notice["event_id"],),
            )
    assert len(mock.requests) == 6
    assert asyncio.run(main.process_pending_team()) is False
    assert len(mock.requests) == 6
    assert notice_row(main, notice["event_id"])["notice_attempts"] == 5
    with main.store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM team_proposals WHERE quest_id=?", (quest.id,)
            ).fetchone()[0]
            == 0
        )
