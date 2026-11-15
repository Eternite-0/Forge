"""Trusted Composer admission policy; no Provider calls or Worker execution."""

import pytest
from test_team_reassignment_m5 import prepared_team

from forge_core.domain import DomainError
from forge_core.team_composition import TeamCompositionService


def snapshot(store, quest_id):
    with store.connection() as connection:
        return {
            "quest": dict(
                connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
            ),
            "teams": [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM team_proposals WHERE quest_id=? ORDER BY id", (quest_id,)
                )
            ],
            "events": [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM events WHERE quest_id=? ORDER BY seq", (quest_id,)
                )
            ],
            "runs": [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM worker_runs WHERE quest_id=? ORDER BY id", (quest_id,)
                )
            ],
        }


@pytest.mark.parametrize("cap", [True, False, 0, -1, 1.0, "2", [], {}])
def test_operator_cap_requires_trusted_positive_integer(cap):
    with pytest.raises(ValueError, match="positive integer"):
        TeamCompositionService(None, operator_max_concurrency=cap)


@pytest.mark.parametrize("cap", [None, 1, 2, 100])
def test_operator_cap_retains_explicit_configuration(cap):
    assert (
        TeamCompositionService(None, operator_max_concurrency=cap).operator_max_concurrency == cap
    )


def test_over_limit_proposal_has_no_persistence_effect(tmp_path):
    store, quest, _, roles, limits, _, _, _, _ = prepared_team(tmp_path)
    service = TeamCompositionService(store, operator_max_concurrency=1)
    before = snapshot(store, quest.id)
    with pytest.raises(DomainError, match="^Team concurrency exceeds operator limit$"):
        service.propose(
            quest.id,
            roles,
            limits,
            strategy="coverage",
            expected_version=store.get_quest(quest.id)["version"],
        )
    assert snapshot(store, quest.id) == before


@pytest.mark.parametrize("cap,requested", [(None, 2), (1, 1), (2, 1), (2, 2)])
def test_in_limit_or_legacy_proposal_preserves_model_concurrency(tmp_path, cap, requested):
    store, quest, _, roles, limits, _, _, _, _ = prepared_team(tmp_path)
    service = TeamCompositionService(store, operator_max_concurrency=cap)
    limits = limits.model_copy(update={"max_concurrency": requested})
    proposal = service.propose(
        quest.id,
        roles,
        limits,
        strategy="coverage",
        expected_version=store.get_quest(quest.id)["version"],
    )
    assert proposal["limits"]["max_concurrency"] == requested
    assert limits.max_concurrency == requested


def test_reassignment_proposal_enforces_same_operator_cap(tmp_path):
    store, quest, _, roles, limits, _, _, _, runs = prepared_team(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='blocked' WHERE id=?", (runs[1]["id"],)
        )
    before = snapshot(store, quest.id)
    service = TeamCompositionService(store, operator_max_concurrency=1)
    with pytest.raises(DomainError, match="Team concurrency exceeds operator limit"):
        service.propose_reassignment(
            quest.id,
            runs[1]["id"],
            "Replace blocked member",
            roles,
            limits,
            strategy="coverage",
            expected_version=store.get_quest(quest.id)["version"],
        )
    assert snapshot(store, quest.id) == before


@pytest.mark.parametrize("replacement", [False, True])
def test_pending_approval_rechecks_lower_operator_cap_atomically(tmp_path, replacement):
    store, quest, plan, roles, limits, original, _, _, runs = prepared_team(tmp_path)
    version = store.get_quest(quest.id)["version"]
    if replacement:
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE worker_runs SET state='blocked' WHERE id=?", (runs[1]["id"],)
            )
        proposal = original.propose_reassignment(
            quest.id,
            runs[1]["id"],
            "Replace blocked member",
            roles,
            limits,
            strategy="coverage",
            expected_version=version,
        )
    else:
        proposal = original.propose(
            quest.id, roles, limits, strategy="coverage", expected_version=version
        )
    before = snapshot(store, quest.id)
    capped = TeamCompositionService(store, operator_max_concurrency=1)
    with pytest.raises(DomainError, match="Team concurrency exceeds operator limit"):
        capped.approve(
            quest.id,
            plan["revision"],
            proposal["proposal_digest"],
            expected_version=version,
            actor="local_user",
        )
    assert snapshot(store, quest.id) == before


def test_lower_cap_preserves_approved_contract_and_historical_evidence(tmp_path):
    store, quest, plan, _, _, _, team, _, runs = prepared_team(tmp_path)
    before = snapshot(store, quest.id)
    capped = TeamCompositionService(store, operator_max_concurrency=1)
    with store.unit_of_work() as work:
        approved = capped.get_approved(
            quest.id, plan["revision"], team["proposal_digest"], connection=work.connection
        )
        historical = capped.get_historical_approved(
            quest.id,
            plan["revision"],
            team["proposal_digest"],
            connection=work.connection,
            run_id=runs[0]["id"],
        )
    assert approved["limits"]["max_concurrency"] == 2
    assert historical == approved
    assert snapshot(store, quest.id) == before
