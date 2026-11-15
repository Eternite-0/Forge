"""Offline state mechanics for approved remaining DAGs and bounded child expansion."""

import copy
import json

import pytest
from test_team_reassignment_m5 import prepared_team

from forge_core.domain import DomainError
from forge_core.plan_lineage import validated_lineage_source
from forge_core.quest_graph import QuestGraphService
from forge_core.team_composition import CompositionLimits, RoleSpec


def prepared(tmp_path):
    store, quest, plan, roles, limits, teams, _, manager, runs = prepared_team(tmp_path)
    # This explicit Mock fixture isolates transition/counter mechanics. It is
    # never an execution-quality or live PASS acceptance claim.
    with store.unit_of_work() as work:
        for run, state in zip(runs, ("succeeded", "blocked"), strict=True):
            work.connection.execute(
                "UPDATE worker_runs SET state=?,token_spent=7,steps=1 WHERE id=?",
                (state, run["id"]),
            )
            work.connection.execute(
                "UPDATE task_nodes SET status=? WHERE plan_id=? AND node_key=?",
                (state, plan["id"], run["node_key"]),
            )
    with store.connection() as connection:
        raw = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE id=?", (plan["id"],)
            ).fetchone()[0]
        )
    return store, quest, plan, raw, roles, limits, teams, manager, runs


def test_stage_then_user_approval_preserves_completed_runs_counters_and_ledger(tmp_path):
    store, quest, parent, raw, *_rest, runs = prepared(tmp_path)
    service = QuestGraphService(store)
    with store.connection() as connection:
        old_runs = [
            dict(row) for row in connection.execute("SELECT * FROM worker_runs ORDER BY id")
        ]
        old_ledger = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM worker_budget_ledger ORDER BY scope,scope_id"
            )
        ]
    raw["nodes"][1]["objective"] = "Correct unfinished work using earlier receipts"
    before = store.get_quest(quest.id)
    staged = service.propose_remaining(quest.id, raw, expected_version=before["version"])
    assert store.get_quest(quest.id)["plan_revision"] == parent["revision"]
    assert staged["status"] == "proposed" and staged["revision"] > parent["revision"]
    with store.connection() as connection:
        assert [
            dict(row) for row in connection.execute("SELECT * FROM worker_runs ORDER BY id")
        ] == old_runs
    approved = service.approve(
        quest.id,
        staged["digest"],
        staged["revision"],
        expected_version=before["version"] + 1,
        actor="local_user",
    )
    with store.connection() as connection:
        current = connection.execute(
            "SELECT * FROM quest_plans WHERE id=?", (approved["id"],)
        ).fetchone()
        inherited = validated_lineage_source(connection, store.get_quest(quest.id), current, "a")
        assert inherited["source_run"]["id"] == runs[0]["id"]
        assert dict(
            connection.execute("SELECT * FROM worker_runs WHERE id=?", (runs[0]["id"],)).fetchone()
        ) == next(row for row in old_runs if row["id"] == runs[0]["id"])
        cancelled = connection.execute(
            "SELECT * FROM worker_runs WHERE id=?", (runs[1]["id"],)
        ).fetchone()
        assert (
            cancelled["state"] == "cancelled"
            and cancelled["token_spent"] == 7
            and cancelled["steps"] == 1
        )
        assert [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM worker_budget_ledger ORDER BY scope,scope_id"
            )
        ] == old_ledger


@pytest.mark.parametrize(
    "mutation", ["completed_contract", "global_budget", "unsettled_model", "unsettled_tool"]
)
def test_remaining_rejects_unsafe_revision_without_switching_plan(tmp_path, mutation):
    store, quest, _parent, raw, *_rest, runs = prepared(tmp_path)
    if mutation == "completed_contract":
        raw["nodes"][0]["objective"] = "Changed completed obligations"
    elif mutation == "global_budget":
        raw["max_total_tokens"] += 1
    else:
        with store.unit_of_work() as work:
            if mutation == "unsettled_model":
                work.connection.execute(
                    "UPDATE worker_runs SET state='running' WHERE id=?", (runs[1]["id"],)
                )
            else:
                work.connection.execute(
                    "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) VALUES (?,?,?,?,?,'uncertain')",
                    (runs[1]["id"], "open", "open", "file_read", "{}"),
                )
    before = store.get_quest(quest.id)
    with pytest.raises(DomainError):
        QuestGraphService(store).propose_remaining(
            quest.id, raw, expected_version=before["version"]
        )
    assert store.get_quest(quest.id) == before
    assert len(QuestGraphService(store).history(quest.id)) == 1


def expansion(raw):
    data = copy.deepcopy(raw)
    parent = data["nodes"].pop(1)
    children = [
        {
            **copy.deepcopy(parent),
            "key": key,
            "objective": f"Child {key}: {parent['objective']}",
            "budget_tokens": 400,
        }
        for key in ("b_design", "b_review")
    ]
    data["nodes"].extend(children)
    data["edges"] = [{"source": "b_design", "target": "b_review", "condition": "success"}]
    return data


def test_bounded_expansion_requires_new_approval_and_role_depth_matches_real_children(tmp_path):
    store, quest, parent, raw, _, _, teams, *_ = prepared(tmp_path)
    service = QuestGraphService(store)
    before = store.get_quest(quest.id)
    staged = service.propose_remaining(
        quest.id, expansion(raw), expected_version=before["version"], expansion_parent="b"
    )
    record = staged["hierarchical_expansions"][0]
    assert record["parent_node_key"] == "b" and record["depth"] == 1
    assert set(record["child_node_keys"]) == {"b_design", "b_review"}
    assert store.get_quest(quest.id)["plan_revision"] == parent["revision"]
    service.approve(
        quest.id,
        staged["digest"],
        staged["revision"],
        expected_version=before["version"] + 1,
        actor="local_user",
    )
    roles = [
        RoleSpec(id=role, task_node_key=key, input_tokens=100, output_tokens=100, depth=0)
        for role, key in (("architect", "b_design"), ("reviewer", "b_review"))
    ]
    limits = CompositionLimits(max_members=2, max_depth=1, max_concurrency=1, max_total_tokens=400)
    version = store.get_quest(quest.id)["version"]
    with pytest.raises(DomainError, match="Role depth"):
        teams.propose(quest.id, roles, limits, strategy="greedy", expected_version=version)
    for role in roles:
        role.depth = 1
    team = teams.propose(quest.id, roles, limits, strategy="greedy", expected_version=version)
    assert {member["depth"] for member in team["members"]} == {1}
    assert "a" not in {member["task_node_key"] for member in team["members"]}


@pytest.mark.parametrize(
    "mutation", ["tool", "parent_budget", "delivery_criteria", "other_contract", "depth"]
)
def test_hierarchical_expansion_cannot_increase_authority_or_parent_budget(tmp_path, mutation):
    store, quest, _, raw, *_ = prepared(tmp_path)
    data = expansion(raw)
    if mutation == "tool":
        data["nodes"][1]["allowed_tools"] = ["file_write"]
    elif mutation == "parent_budget":
        for node in data["nodes"][1:]:
            node["budget_tokens"] = 501
        data["nodes"][0]["budget_tokens"] = 900  # cannot pay by weakening completed contract
    elif mutation == "delivery_criteria":
        for node in data["nodes"][1:]:
            node["success_criteria"] = ["weaker"]
    elif mutation == "other_contract":
        data["nodes"][0]["objective"] = "Change completed research"
    else:
        # A second real expansion would exceed this explicit source limit.
        source_limits = {"limits": {"max_depth": 0}}
        from forge_core.hierarchical_plans import expansion_contract

        original = {**raw, "execution_constraints": source_limits}
        with pytest.raises(DomainError, match="depth/node/member"):
            expansion_contract(original, data, {"id": "p", "digest": "d"}, "b", {"a"})
        return
    with pytest.raises((DomainError, ValueError)):
        QuestGraphService(store).propose_remaining(
            quest.id,
            data,
            expected_version=store.get_quest(quest.id)["version"],
            expansion_parent="b",
        )
