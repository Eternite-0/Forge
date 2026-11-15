"""Complete native composition requests use the same semantics and finite bounds."""

import copy
import json

import pytest
from test_composer_resources_v21 import graph_fixture

from forge_core.composer_request import (
    REPAIR_BYTES,
    measure,
    output_bound,
    parts,
    repair_message,
    semantic_plan,
)
from forge_core.domain import DomainError, Quest
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.runtime import ModelRequest
from forge_core.store import Store


def prepared(tmp_path):
    store = Store(tmp_path / "composition.sqlite3")
    graph = graph_fixture()
    graph["capacity_contract"].update(
        {
            "input_inventory": [{"path": "inputs/a.json", "sha256": "a" * 64}],
            "input_acquisition_policy": "core-declared-source-prefetch-v1",
            "source_wire_format": "core-source-raw-text-v1",
            "action_evidence_allowances": {"a": 2048},
            "hypothesis_read_paths": {"a": ["inputs/recalled.json"]},
            "residual_budget_telemetry": {"private": "diagnostics only"},
        }
    )
    provider = ProviderGateway(
        ProviderConfig(
            id="p",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_json_schema=True,
            reasoning_effort="none",
        )
    )
    return store, graph, provider, Quest(title="Test", goal="Keep the whole original objective")


def test_projection_preserves_all_semantics_and_does_not_mutate_full_plan(tmp_path):
    _, graph, _, _ = prepared(tmp_path)
    before = copy.deepcopy(graph)
    projection = semantic_plan(graph)
    for key in set(graph) - {"capacity_contract"}:
        assert projection[key] == graph[key]
    for key in [
        "input_inventory",
        "input_acquisition_policy",
        "source_wire_format",
        "action_evidence_allowances",
        "hypothesis_read_paths",
    ]:
        assert projection["capacity_contract"][key] == graph["capacity_contract"][key]
    assert projection["approved_output_bounds"] == {"a": 2048}
    assert "residual_budget_telemetry" not in projection["capacity_contract"]
    assert graph == before


@pytest.mark.parametrize(
    "diagnostic",
    [
        {"kind": "invalid_team_proposal", "reason": "team_residual_budget_insufficient"},
        {"reason": '"\\\n\t原' * 300},
        {"reason": "x" * 100000},
    ],
)
def test_actual_initial_and_bounded_repair_native_requests_fit_measured_envelope(
    tmp_path, diagnostic
):
    store, graph, provider, quest = prepared(tmp_path)
    with store.connection() as connection:
        envelope = measure(
            store,
            connection,
            quest.model_dump(),
            graph,
            provider,
            model_id="native",
            context_limit=65536,
            capabilities={"json_schema": "fixture"},
            operator_max_concurrency=2,
        )
    messages, schema = parts(store, quest.model_dump(), graph, operator_max_concurrency=2)
    repair = repair_message(diagnostic)
    assert len(repair["content"].encode()) <= REPAIR_BYTES
    for selected, key in [
        (messages, "initial_call_bound"),
        (messages + [repair], "repair_call_bound"),
    ]:
        request = ModelRequest(
            provider_id="p",
            model_id="native",
            messages=selected,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "team_proposal", "strict": True, "schema": schema},
            },
            max_output_tokens=envelope["output_bound"],
            max_retries=0,
            model_capabilities={"json_schema": True},
            model_capability_sources={"json_schema": "fixture"},
        )
        native = (
            len(json.dumps(provider._payload(request, False), ensure_ascii=False).encode()) + 64
        )
        assert native + request.max_output_tokens <= envelope[key] <= 65536
    assert (
        envelope["total_future_bound"]
        == envelope["initial_call_bound"] + envelope["repair_call_bound"]
    )
    assert envelope["max_retries"] == 0


def test_complete_future_request_rejects_an_insufficient_context(tmp_path):
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


def test_output_bound_is_finite_and_respects_actual_model_limit():
    graph = graph_fixture()
    assert output_bound(graph) == 2048
    assert output_bound(graph, 1024) == 1024
    graph["nodes"] *= 100
    assert output_bound(graph) == 8192
