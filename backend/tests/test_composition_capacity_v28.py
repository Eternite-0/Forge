"""Offline native measurement and actual ledger settlement for future Composer stages."""

import copy
import hashlib
import uuid

import pytest
from test_composer_request_v28 import prepared

from forge_core.capacity_preflight import coordination_budget_snapshot
from forge_core.composer_request import VERSION, canonical, measure
from forge_core.composition_capacity import (
    bind_measurement,
    provider_for_graph,
    remaining_composition_capacity,
    validate_binding,
)
from forge_core.domain import DomainError, ModelProfile
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.runtime import ModelCallContext


def native_measure(store, connection, quest, graph, provider):
    return measure(
        store,
        connection,
        quest,
        graph,
        provider,
        model_id="native",
        context_limit=65536,
        capabilities={"json_schema": "fixture"},
        operator_max_concurrency=2,
    )


def bound(tmp_path):
    store, graph, provider, quest = prepared(tmp_path)
    store.save_quest(quest)
    graph["capacity_contract"]["composition_capacity_policy"] = VERSION
    with store.connection() as connection:
        record = bind_measurement(
            store, connection, quest.model_dump(), graph, provider, native_measure
        )
    ledger = ModelCallBudgetLedger(store.path, token_limit=1000000, concurrency_limit=10)
    return store, graph, provider, quest, ledger, record


def attempt(ledger, store, quest, record, *, call_id=None, parent=None, settled=True):
    context = ModelCallContext(
        call_id=call_id or uuid.uuid4().hex,
        purpose="team_composer",
        workspace_id=store.workspace.id,
        quest_id=quest.id,
        parent_call_id=parent or record["semantic_request_sha256"],
    )
    identifier = ledger.admit(
        context,
        record["provider_id"],
        record["model_id"],
        input_tokens=100,
        output_tokens=50,
        remote_timeout=30,
    )
    if settled:
        ledger.settle(identifier, {"input_tokens": 80, "output_tokens": 20})
    return identifier, context.call_id


def remaining(store, quest, graph):
    with store.connection() as connection:
        return remaining_composition_capacity(connection, quest.id, graph)


def test_exact_native_binding_has_a_stable_observation_floor_and_no_model_call(tmp_path):
    store, graph, provider, quest, ledger, record = bound(tmp_path)
    original = copy.deepcopy(record)
    assert record["attempt_rowid_floor"] == 0
    attempt(ledger, store, quest, record)
    with store.connection() as connection:
        assert (
            bind_measurement(store, connection, quest.model_dump(), graph, provider, native_measure)
            == original
        )
        assert (
            validate_binding(store, connection, quest.model_dump(), graph, provider, native_measure)
            == original
        )
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM worker_runs").fetchone()[0] == 0
    assert provider.health()["requests"] == 0


@pytest.mark.parametrize("change", ["objective", "inventory", "context", "concurrency", "source"])
def test_binding_rejects_changed_full_request_or_native_configuration(tmp_path, change):
    store, graph, provider, quest, _ledger, _record = bound(tmp_path)
    selected_measure = native_measure
    if change == "objective":
        graph["nodes"][0]["objective"] = "A different objective"
    elif change == "inventory":
        store.save_model(
            ModelProfile(id="new", provider="p", model_id="native", display_name="New model")
        )
    elif change == "source":
        graph["capacity_contract"]["source_wire_format"] = "changed"
    else:

        def selected_measure(*args):
            result = native_measure(*args)
            result["context_limit" if change == "context" else "operator_max_concurrency"] += 1
            return result

    with store.connection() as connection, pytest.raises(DomainError, match="binding"):
        validate_binding(store, connection, quest.model_dump(), graph, provider, selected_measure)


@pytest.mark.parametrize("missing", ["callback", "provider", "measurement", "policy"])
def test_explicit_contract_fails_closed_when_a_binding_requirement_is_missing(tmp_path, missing):
    store, graph, provider, quest, _ledger, _record = bound(tmp_path)
    callback = native_measure
    if missing == "callback":
        callback = None
    elif missing == "provider":
        provider = None
    elif missing == "measurement":
        graph["capacity_contract"].pop("composition_capacity")
    else:
        graph["capacity_contract"].pop("composition_capacity_policy")
    with store.connection() as connection, pytest.raises(DomainError):
        validate_binding(store, connection, quest.model_dump(), graph, provider, callback)


def test_legacy_absence_remains_compatible_but_a_record_cannot_select_an_unavailable_provider(
    tmp_path,
):
    store, graph, provider, quest, _ledger, _record = bound(tmp_path)
    assert provider_for_graph(ProviderPool({"p": provider}), graph) is provider
    with pytest.raises(DomainError, match="unavailable"):
        provider_for_graph(ProviderPool({}), graph)
    graph["capacity_contract"].pop("composition_capacity")
    graph["capacity_contract"].pop("composition_capacity_policy")
    with store.connection() as connection:
        assert validate_binding(store, connection, quest.model_dump(), graph, None, None) is None
        assert remaining_composition_capacity(connection, quest.id, graph) is None


def test_disabled_cumulative_budget_does_not_relax_a_native_model_context_limit(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store, graph, provider, quest = prepared(tmp_path)
    with store.connection() as connection, pytest.raises(DomainError, match="context"):
        measure(
            store,
            connection,
            quest.model_dump(),
            graph,
            provider,
            model_id="native",
            context_limit=4096,
            capabilities={"json_schema": "fixture"},
            operator_max_concurrency=2,
        )


def test_future_reserve_uses_the_maximum_of_original_and_measured_remaining_not_their_sum(
    tmp_path,
):
    store, graph, _provider, quest, ledger, record = bound(tmp_path)
    graph["coordination_reserve_tokens"] = 1000
    with store.connection() as connection:
        snapshot = coordination_budget_snapshot(connection, store, quest.id, graph, ledger)
    assert snapshot["future_coordination_reserve_tokens"] == record["total_future_bound"]
    assert snapshot["original_unspent_coordination_tokens"] == 1000
    assert snapshot["max_worker_allocations_now"] == 400000 - record["total_future_bound"]
    graph["coordination_reserve_tokens"] = record["total_future_bound"] + 2000
    with store.connection() as connection:
        snapshot = coordination_budget_snapshot(connection, store, quest.id, graph, ledger)
    assert snapshot["future_coordination_reserve_tokens"] == graph["coordination_reserve_tokens"]


def test_only_exact_cycle_settled_calls_release_their_future_stage(tmp_path):
    store, graph, _provider, quest, ledger, record = bound(tmp_path)
    attempt(ledger, store, quest, record, parent="another-composition-cycle")
    assert remaining(store, quest, graph)["future_composer_tokens"] == record["total_future_bound"]
    pending, _call_id = attempt(ledger, store, quest, record, settled=False)
    state = remaining(store, quest, graph)
    assert state["future_composer_tokens"] == record["total_future_bound"]
    assert state["unresolved_composer_calls"] == 1
    ledger.settle(pending, {"input_tokens": 80, "output_tokens": 20})
    state = remaining(store, quest, graph)
    assert state["future_composer_tokens"] == record["repair_call_bound"]
    assert state["settled_composer_calls"] == 1
    attempt(ledger, store, quest, record)
    state = remaining(store, quest, graph)
    assert state["future_composer_tokens"] == 0
    assert state["settled_composer_calls"] == 2


@pytest.mark.parametrize("invalid_usage", [{}, {"input_tokens": None, "output_tokens": 20}])
def test_unknown_usage_retains_full_stage_and_charged_or_reserved_scope(tmp_path, invalid_usage):
    store, graph, _provider, quest, ledger, record = bound(tmp_path)
    identifier, _ = attempt(ledger, store, quest, record, settled=False)
    ledger.settle(identifier, invalid_usage)
    with store.connection() as connection:
        snapshot = coordination_budget_snapshot(connection, store, quest.id, graph, ledger)
    assert snapshot["composition"]["future_composer_tokens"] == record["total_future_bound"]
    assert snapshot["remaining"] == graph["max_total_tokens"] - 150
    assert snapshot["coordination_already_settled"] == 0


@pytest.mark.parametrize("violation", ["retry", "third_call"])
def test_transport_retries_or_an_extra_cycle_call_are_never_silently_accepted(tmp_path, violation):
    store, graph, _provider, quest, ledger, record = bound(tmp_path)
    _, call_id = attempt(ledger, store, quest, record)
    if violation == "retry":
        attempt(ledger, store, quest, record, call_id=call_id)
    else:
        attempt(ledger, store, quest, record)
        attempt(ledger, store, quest, record)
    with pytest.raises(DomainError, match="retries|initial call and one repair"):
        remaining(store, quest, graph)


@pytest.mark.parametrize("matching", [False, True])
def test_only_an_accepted_team_for_the_exact_full_plan_releases_optional_repair(tmp_path, matching):
    store, graph, _provider, quest, ledger, record = bound(tmp_path)
    attempt(ledger, store, quest, record)
    graph_digest = hashlib.sha256(canonical(graph).encode()).hexdigest()
    with store.connection() as connection:
        # Isolated lifecycle fixture; the production proposal gate is tested separately.
        connection.execute(
            "INSERT INTO team_proposals(id,quest_id,plan_revision,plan_digest,proposal_digest,"
            "status,body_json,created_at) VALUES (?,?,1,?,?,'proposed','{}','fixture')",
            ("team", quest.id, graph_digest if matching else "different-plan", "team-digest"),
        )
        connection.commit()
    state = remaining(store, quest, graph)
    assert state["accepted_semantic_team"] is matching
    assert state["future_composer_tokens"] == (0 if matching else record["repair_call_bound"])
