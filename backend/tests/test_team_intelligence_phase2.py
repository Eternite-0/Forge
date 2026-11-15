"""Offline fixtures for performance provenance and bounded team adaptation."""

import json

import pytest
from test_team_reassignment_m5 import prepared_team

from forge_core.domain import CAPABILITY_NAMES
from forge_core.model_performance import performance_snapshot
from forge_core.registry import DEFAULT_ROLES
from forge_core.team_composition import TeamComposerEngine


def observations(store, model, outcomes, *, mode="live", provider="p"):
    # Synthetic database rows exercise the live/mock filter; no Provider runs.
    with store.unit_of_work() as work:
        work.connection.executemany(
            "INSERT INTO model_observations VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"fixture-{model}-{mode}-{i}",
                    model,
                    provider,
                    mode,
                    outcome,
                    i + 1,
                    None,
                    10,
                    f"2026-11-15T00:00:{i:02d}+00:00",
                )
                for i, outcome in enumerate(outcomes)
            ],
        )


def snapshot(store, model, *, limit=256, mode="live"):
    with store.connection() as connection:
        return performance_snapshot(connection, "p", model, mode=mode, limit=limit)


def test_observations_are_bounded_sourced_and_transport_does_not_become_task_success(tmp_path):
    store, *_ = prepared_team(tmp_path)
    observations(store, "m", ["success"] * 8)
    observations(store, "m", ["error"] * 9, mode="mock")
    item = snapshot(store, "m", limit=5)
    measurement = item["measurements"]["transport_success_rate"]
    assert measurement["value"] == 1 and measurement["n_samples"] == 5
    assert measurement["confidence"]["lower"] < 0.6
    assert item["sample_truncated"]["gateway"]
    assert "task_success_rate" not in item["measurements"]
    assert "cost_microunits" not in item["measurements"]
    assert item["source_refs"] and len(item["snapshot_sha256"]) == 64
    assert snapshot(store, "m", mode="mock")["measurements"]["transport_success_rate"]["value"] == 0
    reopened = type(store)(store.path, workspace=store.workspace)
    assert snapshot(reopened, "m", limit=5) == item
    with pytest.raises(ValueError):
        snapshot(store, "m", limit=0)


def test_one_observation_cannot_change_capability_or_selection_confidence(tmp_path):
    store, _, _, roles, limits, *_ = prepared_team(tmp_path)
    original = store.list_models()[0]
    other = original.model_copy(update={"id": "z", "model_id": "z"})
    observations(store, "z", ["success"])
    result = TeamComposerEngine().compose(
        roles,
        [original, other],
        limits,
        strategy="greedy",
        online_snapshots={"z": snapshot(store, "z")},
    )
    assert {member["profile_id"] for member in result["members"]} == {"m"}
    assert all(member["online_utility_adjustments"] == {} for member in result["members"])
    assert original.capability_measurements == {}


def test_composition_rejects_mock_or_wrong_model_performance_provenance(tmp_path):
    store, _, _, roles, limits, *_ = prepared_team(tmp_path)
    original = store.list_models()[0]
    observations(store, "m", ["success"] * 10, mode="mock")
    with pytest.raises(ValueError, match="provenance"):
        TeamComposerEngine().compose(
            roles,
            [original],
            limits,
            strategy="greedy",
            online_snapshots={"m": snapshot(store, "m", mode="mock")},
        )
    item = snapshot(store, "m")
    item["provider_id"] = "unrelated-provider"
    with pytest.raises(ValueError, match="provenance"):
        TeamComposerEngine().compose(
            roles, [original], limits, strategy="greedy", online_snapshots={"m": item}
        )


def test_live_performance_changes_new_proposals_without_changing_manual_profile_hashes(tmp_path):
    store, quest, _, roles, limits, service, _, _, runs = prepared_team(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='failed'")
    profile = store.list_models()[0]
    store.save_model(profile.model_copy(update={"id": "z", "model_id": "z"}))
    before = [model.model_dump_json() for model in store.list_models()]
    observations(store, "m", ["error"] * 6)
    observations(store, "z", ["success"] * 20)
    replacement = service.propose_reassignment(
        quest.id,
        runs[0]["id"],
        "Replace unavailable model",
        roles,
        limits,
        strategy="greedy",
        expected_version=store.get_quest(quest.id)["version"],
    )
    assert {member["profile_id"] for member in replacement["members"]} == {"z"}
    assert replacement["members"][0]["online_performance"]["source"] == "core_execution_observation"
    assert [model.model_dump_json() for model in store.list_models()] == before


def test_worker_metrics_use_terminal_outcomes_and_ignore_mock_workers(tmp_path):
    store, _, _, _, _, _, _, _, runs = prepared_team(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='succeeded' WHERE id=?", (runs[0]["id"],)
        )
        work.connection.execute(
            "UPDATE worker_runs SET state='failed' WHERE id=?", (runs[1]["id"],)
        )
    assert "task_success_rate" not in snapshot(store, "m")["measurements"]
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET execution_mode='live'")
        work.connection.execute(
            "INSERT INTO worker_tool_calls(run_id,call_id,model_call_id,tool_name,arguments_json,state) VALUES (?,?,?,?,?,'failed')",
            (runs[0]["id"], "bad", "bad", "file_read", "{}"),
        )
        from forge_core.domain import ForgeEvent

        work.append_event(
            ForgeEvent(
                event_type="worker.progress",
                worker_id=runs[0]["worker_id"],
                payload={
                    "run_id": runs[0]["id"],
                    "phase": "self_repair",
                    "reason_code": "invalid_output_contract",
                },
            )
        )
        work.append_event(
            ForgeEvent(
                event_type="worker.result",
                worker_id=runs[0]["worker_id"],
                payload={"run_id": runs[0]["id"]},
            )
        )
    item = snapshot(store, "m")["measurements"]
    assert item["task_success_rate"]["value"] == 0.5
    assert item["schema_compliance"]["value"] == 0.5
    assert item["retry_count"]["value"] == 1
    assert item["retry_rate"]["value"] == 0.5
    assert item["tool_call_success_rate"]["value"] == 0
    assert "token_consumption" not in item


def test_incremental_recomposition_can_omit_completed_roles_without_losing_history(tmp_path):
    store, quest, plan, roles, _, service, old, _, runs = prepared_team(tmp_path)
    from forge_core.team_composition import CompositionLimits

    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='succeeded' WHERE id=?", (runs[0]["id"],)
        )
        work.connection.execute(
            "UPDATE worker_runs SET state='blocked' WHERE id=?", (runs[1]["id"],)
        )
        work.connection.execute(
            "UPDATE task_nodes SET status='succeeded' WHERE plan_id=? AND node_key='a'",
            (plan["id"],),
        )
    version = store.get_quest(quest.id)["version"]
    replacement = service.propose_reassignment(
        quest.id,
        runs[1]["id"],
        "Only replace unfinished reviewer",
        [roles[1]],
        CompositionLimits(max_members=1, max_concurrency=1, max_depth=1, max_total_tokens=200),
        strategy="greedy",
        expected_version=version,
    )
    service.approve(
        quest.id,
        plan["revision"],
        replacement["proposal_digest"],
        expected_version=version,
        actor="local_user",
    )
    with store.unit_of_work() as work:
        node = work.connection.execute(
            "SELECT * FROM task_nodes WHERE plan_id=? AND node_key='a'", (plan["id"],)
        ).fetchone()
        assert node["status"] == "succeeded" and node["worker_id"] == runs[0]["worker_id"]
        assert (
            service.get_historical_approved(
                quest.id,
                plan["revision"],
                old["proposal_digest"],
                connection=work.connection,
                run_id=runs[0]["id"],
            )["status"]
            == "superseded"
        )
        assert json.loads(
            work.connection.execute(
                "SELECT body_json FROM team_proposals WHERE proposal_digest=?",
                (old["proposal_digest"],),
            ).fetchone()[0]
        )["members"]


def test_preview_role_capabilities_use_registry_canonical_names():
    assert all(set(role.required_capabilities).issubset(CAPABILITY_NAMES) for role in DEFAULT_ROLES)
