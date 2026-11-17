"""Scripted Mock topology correction and sanitized diagnostics; no real Provider calls."""

import asyncio
import copy
import json

import pytest
from pydantic import ValidationError
from test_router_intelligence_phase2 import ScriptedMock, decision

from forge_core.domain import DomainError
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService, _decision_error_digest
from forge_core.router_domain import RouterDecision
from forge_core.store import Store


def assignment(index, depends_on=None):
    return {
        "objective": f"Deliver bounded specialist analysis {index}",
        "role": "research" if index == 0 else "reviewer",
        "depends_on": [] if depends_on is None else depends_on,
    }


INVALID_TEAMS = [
    (
        {"strategy": "parallel", "assignments": [assignment(0, [0])]},
        "team_dependencies_not_earlier_distinct",
    ),
    (
        {"strategy": "single", "assignments": [assignment(0), assignment(1)]},
        "team_single_worker_count",
    ),
    (
        {"strategy": "parallel", "assignments": [assignment(0), assignment(1, [0])]},
        "team_independent_dependencies",
    ),
    (
        {
            "strategy": "sequential",
            "assignments": [assignment(0), assignment(1, [0]), assignment(2)],
        },
        "team_sequential_success_chain",
    ),
    (
        {
            "strategy": "hierarchical",
            "assignments": [assignment(0), assignment(1, [0]), assignment(2, [0, 1])],
        },
        "team_hierarchical_fan_in",
    ),
]


def autonomous_router(store, provider):
    return RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=provider,
        model_id="explicit-mock",
        context_limit=131072,
        output_reserve=4096,
        autonomous=True,
    )


def rejected_events(store):
    return [
        json.loads(row["payload"])
        for row in store.list_events()
        if row["event_type"] == "router.decision_rejected"
    ]


@pytest.mark.parametrize("team,reason", INVALID_TEAMS)
def test_team_rejection_persists_only_whitelisted_reason(tmp_path, team, reason):
    store = Store(tmp_path / "topology.sqlite3")
    team = copy.deepcopy(team)
    private = "PRIVATE_REJECTED_TOPOLOGY_OBJECTIVE"
    team["assignments"][0]["objective"] = private
    provider = ScriptedMock(decision("create_quest", execution_team=team))
    instance = autonomous_router(store, provider)
    instance.max_repairs = 0
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(instance.handle_user_message("Analyze a local project", request_id="invalid"))
    assert rejected_events(store)[0]["errors"] == [f"execution_team: {reason}"]
    with store.connection() as connection:
        assert private not in "\n".join(connection.iterdump())
    assert len(provider.requests) == 1
    assert store.list_messages() == [] and store.list_quests() == []


@pytest.mark.parametrize(
    "reason,location,expected",
    [
        (ValueError("PRIVATE_UNTRUSTED_CTX"), ("execution_team",), "execution_team: value_error"),
        (
            ValueError("Sequential experts must form a success chain"),
            ("execution_team", "assignments", 0),
            "execution_team.assignments.0: value_error",
        ),
        (
            ValueError("Sequential experts must form a success chain", "PRIVATE_EXTRA_ARG"),
            ("execution_team",),
            "execution_team: value_error",
        ),
    ],
)
def test_unrecognized_or_misplaced_context_stays_generic(reason, location, expected):
    error = ValidationError.from_exception_data(
        "RouterDecision",
        [
            {
                "type": "value_error",
                "loc": location,
                "input": "PRIVATE_INPUT",
                "ctx": {"error": reason},
            }
        ],
    )
    assert _decision_error_digest(error) == [expected]
    assert "PRIVATE" not in json.dumps(_decision_error_digest(error))


@pytest.mark.parametrize(
    "strategy,dependencies",
    [
        ("single", [[]]),
        ("parallel", [[], [], []]),
        ("sequential", [[], [0], [1]]),
        ("hierarchical", [[], [], [0, 1]]),
    ],
)
def test_documented_shapes_satisfy_unchanged_strict_validator(strategy, dependencies):
    team = {
        "strategy": strategy,
        "assignments": [assignment(index, value) for index, value in enumerate(dependencies)],
    }
    parsed = RouterDecision.model_validate(decision("create_quest", execution_team=team))
    assert parsed.execution_team.model_dump() == team


def assert_topology_contract(text):
    for literal in (
        "exactly strategy and assignments",
        "only objective, role and depends_on",
        "single has exactly one assignment with depends_on=[]",
        "parallel has depends_on=[] for every assignment",
        "shape [[],[0],[1],...]",
        "every specialist has depends_on=[]",
        "last integrator has depends_on=[0,...,n-2]",
        "shape is [[],[],[0,1]]",
    ):
        assert literal in text


def test_extra_field_repair_sees_legal_topology_within_single_reflection(tmp_path):
    store = Store(tmp_path / "repaired-topology.sqlite3")
    private_key, private_value = "PRIVATE_TEAM_EXPLANATION", "PRIVATE_TEAM_REASONING"
    invalid_team = {
        "strategy": "hierarchical",
        "assignments": [assignment(0), assignment(1, [0]), assignment(2, [0, 1])],
        private_key: private_value,
    }
    valid_team = {
        "strategy": "hierarchical",
        "assignments": [assignment(0), assignment(1), assignment(2, [0, 1])],
    }

    def corrected(request):
        assert request.budget_context.purpose == "router_reflect"
        repair = json.loads(request.messages[-1]["content"])
        # Extra fields prevent the model validator running on the initial response.
        assert repair["rejected_decision_error"] == [
            "execution_team.[unknown_field]: extra_forbidden"
        ]
        assert_topology_contract(repair["required_action"])
        return decision("create_quest", execution_team=valid_team)

    provider = ScriptedMock(decision("create_quest", execution_team=invalid_team), corrected)
    instance = autonomous_router(store, provider)
    result = asyncio.run(
        instance.handle_user_message("Analyze a local project", request_id="repair")
    )
    assert result["intent"] == "create_quest"
    assert len(provider.requests) == 2 and instance.max_repairs == 1
    assert_topology_contract(provider.requests[0].messages[0]["content"])
    assert [request.budget_context.purpose for request in provider.requests] == [
        "router",
        "router_reflect",
    ]
    assert {request.max_output_tokens for request in provider.requests} == {4096}
    assert {request.context_limit for request in provider.requests} == {131072}
    assert provider.requests[1].tools in (None, [])
    assert len(rejected_events(store)) == 1
    quest = store.get_quest(result["quest_id"])
    assert json.loads(quest["metadata"])["execution_team"] == valid_team
    with store.connection() as connection:
        persisted = "\n".join(connection.iterdump())
    assert private_key not in persisted and private_value not in persisted
    assert not any(row["event_type"] == "worker.started" for row in store.list_events())
