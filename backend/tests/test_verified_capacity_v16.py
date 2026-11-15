"""Source-bound limits and opt-in preflight; no external Provider calls."""

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from test_router_intelligence_phase2 import ScriptedMock, decision, router, wire_graph

from forge_core.capacity_preflight import (
    discover_input_inventory,
    residual_budget_snapshot,
    verified_input_inventory,
    verify_input_inventory,
)
from forge_core.constraints import extract_execution_constraints
from forge_core.domain import ConversationMessage, DomainError, ModelProfile, ProtocolSupport, Quest
from forge_core.graph_domain import GraphProposal, graph_contract_dump
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.runtime import ModelCallContext
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import CompositionLimits, RoleSpec, TeamCompositionService


def prepared(tmp_path, *, content=None):
    store = Store(tmp_path / "capacity.sqlite3", workspace=Workspace.from_path(tmp_path))
    root = store.workspace.root
    (root / "inputs").mkdir(exist_ok=True)
    (root / "inputs" / "unicode.json").write_text(
        json.dumps({"line": '中文 "quoted" \\ source\n' * 6}, ensure_ascii=False), encoding="utf-8"
    )
    content = content or (
        "Inspect inputs/unicode.json. Task execution limits: "
        '{"max_concurrency":1,"max_total_tokens":180000,"max_nodes":2,"max_members":2}'
    )
    user = ConversationMessage(role="user", content=content)
    store.add_message(user)
    quest = Quest(
        title="Verified source",
        goal="Inspect the complete Unicode source",
        metadata={
            "original_request_message_id": user.id,
            "original_request_sha256": hashlib.sha256(content.encode()).hexdigest(),
        },
    )
    store.save_quest(quest)
    store.save_model(
        ModelProfile(
            id="big",
            provider="p",
            model_id="big",
            display_name="Big",
            context_window=65536,
            max_output_tokens=4096,
            tool_calling_support=ProtocolSupport(supported=True, source="user_override"),
        )
    )
    return store, quest


def capacity_graph():
    data = wire_graph()
    data.update(max_total_tokens=180000, coordination_reserve_tokens=18000)
    node = data["nodes"][0]
    node.update(
        budget_tokens=150000,
        required_capabilities=[],
        required_input_paths=["inputs/unicode.json"],
        capacity_bounds={
            "max_source_receipt_bytes": 2048,
            "max_artifact_bytes": 3072,
            "max_delivery_bytes": 1536,
        },
    )
    return data


def generate(store, quest, provider, **kwargs):
    service = QuestGraphService(store, **kwargs)
    return asyncio.run(
        service.generate(
            quest.id,
            expected_version=0,
            provider=provider,
            model_id="big",
            provider_id="p",
            capabilities={"json_schema": True},
            capability_sources={"json_schema": "mock"},
            context_limit=65536,
            require_capacity_contract=True,
        )
    )


def test_literal_structured_limits_bind_unicode_spans_and_intersect():
    text = '前言 Task execution limits: {"max_concurrency":2,"max_total_tokens":100}。 Task execution limits: {"max_concurrency":1}'
    result = extract_execution_constraints("message-exact", text)
    assert result["limits"] == {"max_concurrency": 1, "max_total_tokens": 100}
    assert result["source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert all(text[span["start"] : span["end"]] == span["text"] for span in result["source_spans"])
    assert extract_execution_constraints("m", "one researcher; Gateway concurrency 2") is None


@pytest.mark.parametrize(
    "block",
    [
        '{"max_concurrency":true}',
        '{"max_concurrency":0}',
        '{"max_concurrency":1.0}',
        '{"max_concurrency":1,"max_concurrency":2}',
        '{"some_role":"research"}',
        "[]",
        "{bad}",
    ],
)
def test_invalid_explicit_execution_block_cannot_become_a_weak_hint(block):
    with pytest.raises(DomainError):
        extract_execution_constraints("m", "Task execution limits: " + block)


def test_router_persists_structured_source_bound_limits(tmp_path):
    store = Store(tmp_path / "router.sqlite3")
    provider = ScriptedMock(decision("create_quest"))
    text = 'Inspect one file. Task execution limits: {"max_concurrency":1}'
    result = asyncio.run(router(store, provider).handle_user_message(text, request_id="cap"))
    metadata = json.loads(store.get_quest(result["quest_id"])["metadata"])
    binding = metadata["execution_constraints"]
    assert binding["limits"]["max_concurrency"] == 1
    assert binding["source_message_id"] == metadata["original_request_message_id"]
    assert binding["source_sha256"] == metadata["original_request_sha256"]


def test_inventory_uses_actual_wrapper_bytes_and_detects_tampered_manifest(tmp_path):
    store, _ = prepared(tmp_path)
    root = store.workspace.root
    actual = verified_input_inventory(store, ["inputs/unicode.json"])[0]
    manifest = [{**actual, "source_bytes": 999999, "contains_excerpts": True}]
    (root / "inputs" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    inventory = verified_input_inventory(store, ["inputs/unicode.json", "inputs/manifest.json"])
    wrapper = next(item for item in inventory if item["path"] == "inputs/unicode.json")
    assert wrapper["bytes"] == len((root / wrapper["path"]).read_bytes()) < 999999
    manifest[0]["bytes"] += 1
    (root / "inputs" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DomainError, match="actual file bytes/hash"):
        verify_input_inventory(store, inventory)


@pytest.mark.parametrize("level", [1, 2, 3])
def test_real_seeded_scenario_manifest_wrapper_is_verified_without_full_source_size_guess(
    tmp_path, level
):
    script = Path(__file__).resolve().parents[1] / "scripts/phase2/acceptance_scenarios.py"
    spec = importlib.util.spec_from_file_location("forge_v16_seeded_inputs", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    store = Store(tmp_path / "seed.sqlite3", workspace=Workspace.from_path(tmp_path))
    prompt = module.seed_scenario(level, tmp_path)["prompt"]
    inventory = discover_input_inventory(store, prompt)
    manifest = json.loads(
        (tmp_path / "inputs" / f"scenario-{level}" / "manifest.json").read_text(encoding="utf-8")
    )
    by_path = {item["path"]: item for item in inventory}
    assert f"inputs/scenario-{level}/manifest.json" in by_path
    assert len(inventory) == len(manifest["inputs"]) + 1
    for record in manifest["inputs"]:
        actual = by_path[record["path"]]
        assert actual["bytes"] == record["bytes"] == len((tmp_path / record["path"]).read_bytes())
        assert actual["sha256"] == record["sha256"]


def test_real_l1_sources_can_pass_the_new_planner_capacity_contract_at_original_ceiling(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/phase2/acceptance_scenarios.py"
    spec = importlib.util.spec_from_file_location("forge_v16_l1_admission", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prompt = module.seed_scenario(1, tmp_path)["prompt"]
    store, quest = prepared(tmp_path, content=prompt)
    inventory = discover_input_inventory(store, prompt)
    from forge_core.capacity_preflight import _receipt, canonical

    known = len(canonical([_receipt(store, item) for item in inventory]).encode())
    data = capacity_graph()
    data["nodes"][0].update(
        budget_tokens=162000,
        required_input_paths=[item["path"] for item in inventory],
        capacity_bounds={
            "max_source_receipt_bytes": known + 1024,
            "max_artifact_bytes": 1,
            "max_delivery_bytes": 1536,
        },
    )
    provider = ScriptedMock(data)
    plan = generate(store, quest, provider)
    assert len(provider.requests) == 1
    assert plan["max_total_tokens"] == 180000 and len(plan["nodes"]) == 1
    observation = plan["capacity_contract"]["nodes"]["n0"]
    assert observation["minimum_node_tokens"] <= 162000
    assert observation["feedback_bytes_policy"] == 2048


def test_semantic_only_node_has_worker_repair_capacity_and_no_fictional_model_reviews(tmp_path):
    store, quest = prepared(tmp_path)
    data = capacity_graph()
    data["nodes"][0].update(
        success_criteria=["semantic_numeric:scheduler-v1:work/scheduler.py"],
        expected_artifact="work/scheduler.py",
        allowed_tools=["file_read", "file_write", "file_revise", "numeric_ast"],
    )
    plan = generate(store, quest, ScriptedMock(data))
    capacity = plan["capacity_contract"]["nodes"]["n0"]
    assert capacity["criterion_mode"] == "fixed_verification"
    assert capacity["review_envelope"]["version"] == "worker-capacity-v1"
    assert "max_review_input_tokens" not in capacity["review_envelope"]
    expected = capacity["review_envelope"]["correction_calls"] * (
        capacity["review_envelope"]["correction_input_tokens"]
        + capacity["review_envelope"]["correction_output_tokens"]
    )
    assert capacity["review_cycle_reserve"] == expected


@pytest.mark.parametrize(
    "path",
    [
        "../outside.json",
        "inputs/../../outside.json",
        ".env",
        "data/operator-settings.json",
        "secrets/key.json",
        "inputs/private.pem",
        ".config/token.json",
    ],
)
def test_inventory_obeys_existing_workspace_governance(tmp_path, path):
    store, _ = prepared(tmp_path)
    with pytest.raises(DomainError):
        verified_input_inventory(store, [path])


def test_output_reference_remains_unknown_until_a_producer_contract(tmp_path):
    store, _ = prepared(tmp_path)
    result = discover_input_inventory(store, "Read inputs/unicode.json; write work/future.json.")
    assert [item["path"] for item in result] == ["inputs/unicode.json"]


def test_new_planner_repairs_underfunded_node_once_with_same_cap(tmp_path):
    store, quest = prepared(tmp_path)
    first, second = capacity_graph(), capacity_graph()
    first["nodes"][0]["budget_tokens"] = 10000
    provider = ScriptedMock(first, second)
    plan = generate(store, quest, provider)
    assert len(provider.requests) == 2
    limits = [json.loads(req.messages[1]["content"])["limits"] for req in provider.requests]
    assert limits[0]["max_total_tokens"] == limits[1]["max_total_tokens"] == 180000
    assert "No eligible model fits" in provider.requests[1].messages[-1]["content"]
    assert plan["capacity_contract"]["input_inventory"][0]["bytes"] > 100
    assert plan["capacity_contract"]["nodes"]["n0"]["review_cycle_reserve"] > 10000
    assert plan["execution_constraints"]["limits"]["max_concurrency"] == 1
    assert store.list_workers(quest.id) == []


def test_undeclared_future_input_is_not_assumed_zero(tmp_path):
    store, quest = prepared(tmp_path)
    data = capacity_graph()
    data["nodes"][0]["required_input_paths"] = ["work/unproduced.json"]
    provider = ScriptedMock(data)
    with pytest.raises(DomainError, match="Invalid graph proposal"):
        generate(store, quest, provider)
    assert len(provider.requests) == 2
    assert "declared direct predecessor" in provider.requests[1].messages[-1]["content"]
    assert QuestGraphService(store).history(quest.id) == []


def test_input_change_blocks_approval_without_rewriting_plan(tmp_path):
    store, quest = prepared(tmp_path)
    service = QuestGraphService(store)
    plan = generate(store, quest, ScriptedMock(capacity_graph()))
    before = service.get(quest.id)
    (store.workspace.root / "inputs" / "unicode.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(DomainError, match="inventory changed"):
        service.approve(
            quest.id, plan["digest"], plan["revision"], expected_version=1, actor="local_user"
        )
    assert service.get(quest.id) == before


def test_team_hard_task_concurrency_independent_of_operator_limit(tmp_path):
    store, quest = prepared(tmp_path)
    graph = QuestGraphService(store)
    raw = {
        "nodes": [
            {
                "key": "one",
                "objective": "Inspect",
                "success_criteria": ["done"],
                "budget_tokens": 500,
            }
        ],
        "edges": [],
        "max_total_tokens": 180000,
    }
    plan = graph.propose(quest.id, raw, expected_version=0)
    graph.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    service = TeamCompositionService(store, operator_max_concurrency=4)
    roles = [RoleSpec(id="research", task_node_key="one", input_tokens=100, output_tokens=100)]
    limits = CompositionLimits(max_members=2, max_depth=1, max_concurrency=2, max_total_tokens=200)
    with pytest.raises(DomainError, match="max_concurrency exceeds source-bound"):
        service.propose(quest.id, roles, limits, strategy="greedy", expected_version=2)
    assert store.get_quest(quest.id)["version"] == 2
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM team_proposals").fetchone()[0] == 0


def test_composer_actual_selected_model_context_recheck(tmp_path):
    store, quest = prepared(tmp_path)
    plan = generate(store, quest, ScriptedMock(capacity_graph()))
    graph = QuestGraphService(store)
    graph.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    store.save_model(store.list_models()[0].model_copy(update={"context_window": 4096}))
    service = TeamCompositionService(
        store, capacity_providers=ProviderPool({"p": ScriptedMock({})})
    )
    roles = [
        RoleSpec(
            id="research",
            task_node_key="n0",
            tools=["directory_list", "file_read"],
            input_tokens=147952,
            output_tokens=2048,
        )
    ]
    limits = CompositionLimits(
        max_members=1,
        max_depth=1,
        max_concurrency=1,
        max_total_tokens=150000,
        allowed_tools=["directory_list", "file_read"],
    )
    with pytest.raises(DomainError, match="selected model context"):
        service.propose(quest.id, roles, limits, strategy="greedy", expected_version=2)


def test_unknown_model_profile_cannot_pass_capacity_by_omission(tmp_path):
    store, quest = prepared(tmp_path)
    model = store.list_models()[0].model_copy(update={"context_window": None})
    store.save_model(model)
    provider = ScriptedMock(capacity_graph())
    with pytest.raises(DomainError, match="Invalid graph proposal"):
        generate(store, quest, provider)
    assert len(provider.requests) == 2


def test_scope_residual_includes_opening_and_unresolved_reservations(tmp_path):
    store, quest = prepared(tmp_path)
    ledger = ModelCallBudgetLedger(
        store.path,
        token_limit=200000,
        concurrency_limit=4,
        scope_limits={("workspace", store.workspace.id): (190000, 4)},
    )
    ledger.admit(
        ModelCallContext(
            call_id="unresolved",
            purpose="planner",
            workspace_id=store.workspace.id,
            quest_id=quest.id,
        ),
        "p",
        "big",
        input_tokens=2000,
        output_tokens=1000,
        remote_timeout=1,
    )
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO model_budget_opening VALUES (?,?,?,?,?,?)",
            ("historical-opening", store.workspace.id, quest.id, 1000, None, None),
        )
    with store.connection() as connection:
        result = residual_budget_snapshot(connection, store, quest.id, 180000, ledger)
    assert result["remaining"] == 176000
    assert len(result["scopes"]) == 4
    provider = ScriptedMock(capacity_graph())
    data = capacity_graph()
    data["nodes"][0]["budget_tokens"] = 161000
    provider = ScriptedMock(data)
    with pytest.raises(DomainError, match="Invalid graph proposal"):
        generate(store, quest, provider, budget_ledger=ledger)
    assert "residual" in provider.requests[1].messages[-1]["content"]


def composed(store, quest):
    plan = generate(store, quest, ScriptedMock(capacity_graph()))
    QuestGraphService(store).approve(
        quest.id, plan["digest"], 1, expected_version=1, actor="local_user"
    )
    service = TeamCompositionService(
        store, capacity_providers=ProviderPool({"p": ScriptedMock({})})
    )
    roles = [
        RoleSpec(
            id="research",
            task_node_key="n0",
            tools=["directory_list", "file_read"],
            input_tokens=147952,
            output_tokens=2048,
        )
    ]
    limits = CompositionLimits(
        max_members=1,
        max_depth=1,
        max_concurrency=1,
        max_total_tokens=150000,
        allowed_tools=["directory_list", "file_read"],
    )
    team = service.propose(quest.id, roles, limits, strategy="greedy", expected_version=2)
    return plan, service, team


def test_new_team_hash_binds_verified_inputs_envelope_and_approval_rechecks_source(tmp_path):
    store, quest = prepared(tmp_path)
    plan, service, team = composed(store, quest)
    member = team["members"][0]
    assert member["review_envelope"]["version"] == "review-envelope-v1"
    assert member["required_input_paths"] == ["inputs/unicode.json"]
    assert len(member["verified_input_inventory"][0]["sha256"]) == 64
    assert team["execution_constraints"]["limits"]["max_concurrency"] == 1
    before = store.get_quest(quest.id)
    (store.workspace.root / "inputs" / "unicode.json").write_text("changed", encoding="utf-8")
    with pytest.raises(DomainError, match="inventory changed"):
        service.approve(
            quest.id,
            plan["revision"],
            team["proposal_digest"],
            expected_version=2,
            actor="local_user",
        )
    assert store.get_quest(quest.id) == before


def test_actual_role_allocation_must_fund_cycle_even_when_node_ceiling_is_large(tmp_path):
    store, quest = prepared(tmp_path)
    plan = generate(store, quest, ScriptedMock(capacity_graph()))
    QuestGraphService(store).approve(
        quest.id, plan["digest"], 1, expected_version=1, actor="local_user"
    )
    service = TeamCompositionService(
        store, capacity_providers=ProviderPool({"p": ScriptedMock({})})
    )
    role = RoleSpec(
        id="research",
        task_node_key="n0",
        tools=["directory_list", "file_read"],
        input_tokens=7000,
        output_tokens=2048,
    )
    limits = CompositionLimits(
        max_members=1,
        max_depth=1,
        max_concurrency=1,
        max_total_tokens=9048,
        allowed_tools=["directory_list", "file_read"],
    )
    with pytest.raises(DomainError, match="Node lacks complete Worker"):
        service.propose(quest.id, [role], limits, strategy="greedy", expected_version=2)


def test_source_limit_tampering_before_team_approval_is_rejected(tmp_path):
    store, quest = prepared(tmp_path)
    plan, service, team = composed(store, quest)
    metadata = json.loads(store.get_quest(quest.id)["metadata"])
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE messages SET content=content || ' changed' WHERE id=?",
            (metadata["original_request_message_id"],),
        )
    with pytest.raises(DomainError, match="integrity check failed"):
        service.approve(
            quest.id,
            plan["revision"],
            team["proposal_digest"],
            expected_version=2,
            actor="local_user",
        )


def test_unmodified_new_team_can_be_approved_without_contract_rewriting(tmp_path):
    store, quest = prepared(tmp_path)
    plan, service, team = composed(store, quest)
    approved = service.approve(
        quest.id, plan["revision"], team["proposal_digest"], expected_version=2, actor="local_user"
    )
    assert approved["proposal_digest"] == team["proposal_digest"]
    assert approved["members"] == team["members"]


def test_legacy_manual_canonical_shape_omits_all_new_contract_fields():
    raw = {
        "nodes": [
            {"key": "a", "objective": "Legacy", "success_criteria": ["done"], "budget_tokens": 10}
        ],
        "edges": [],
    }
    result = graph_contract_dump(GraphProposal.model_validate(raw))
    assert "capacity_contract" not in result and "execution_constraints" not in result
    assert "coordination_reserve_tokens" not in result
    assert "required_input_paths" not in result["nodes"][0]
    assert result["nodes"][0]["expected_artifact"] is None
