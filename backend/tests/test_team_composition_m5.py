import pytest
from pydantic import ValidationError

from forge_core.domain import Measurement, ModelProfile, PerformanceMeasurement, ProtocolSupport
from forge_core.team_composition import (
    CompositionLimits,
    RoleSpec,
    TeamComposerEngine,
    TeamCompositionService,
    TeamWire,
)


def profile(identity, *, capabilities=None, price=None, currency=None):
    return ModelProfile(
        id=identity,
        provider="provider",
        model_id=identity,
        display_name=identity,
        capabilities=capabilities or {},
        context_window=4096,
        max_output_tokens=512,
        tool_calling_support=ProtocolSupport(supported=True, source="user_override"),
        input_cost_per_million=price,
        output_cost_per_million=price,
        currency=currency,
    )


def test_conflicting_capability_and_performance_sources_are_rejected():
    with pytest.raises(ValidationError):
        ModelProfile(
            id="m",
            provider="p",
            display_name="M",
            capabilities={"coding": 10},
            capability_measurements={"coding": Measurement(value=90, source="user_override")},
        )
    with pytest.raises(ValidationError):
        ModelProfile(
            id="m",
            provider="p",
            display_name="M",
            reliability=0.1,
            performance_measurements={
                "reliability": PerformanceMeasurement(value=0.9, source="user_override")
            },
        )
    with pytest.raises(ValidationError):
        ModelProfile(
            id="m",
            provider="p",
            display_name="M",
            performance_measurements={
                "timeout_rate": PerformanceMeasurement(value=2, source="user_override")
            },
        )


def test_dynamic_role_requires_complete_bounded_contract():
    with pytest.raises(ValidationError):
        RoleSpec(id="custom", input_tokens=10, output_tokens=10)
    with pytest.raises(ValidationError):
        RoleSpec(id="custom", dynamic=True, input_tokens=10, output_tokens=10)
    with pytest.raises(ValidationError):
        RoleSpec(
            id="custom",
            dynamic=True,
            input_tokens=10,
            output_tokens=10,
            responsibility="Review",
            input_contract=["input"],
            acceptance_criteria=["Done"],
            output_schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
                "additionalProperties": True,
            },
        )
    role = RoleSpec(
        id="custom",
        dynamic=True,
        input_tokens=10,
        output_tokens=10,
        responsibility="Review output",
        input_contract=["Artifact"],
        acceptance_criteria=["Done"],
        output_schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
    )
    assert role.dynamic and role.output_schema["additionalProperties"] is False


def test_composer_wire_has_closed_required_objects():
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(TeamWire.model_json_schema())


def test_candidate_audit_excludes_irrelevant_capability_and_uses_known_confidence():
    role = RoleSpec(
        id="coding", required_capabilities={"coding": 70}, input_tokens=100, output_tokens=50
    )
    first = profile("first", capabilities={"coding": 80, "vision": 100})
    second = profile("second", capabilities={"coding": 80})
    second.capability_measurements["coding"].confidence = 0.9
    rejected = profile("unknown")
    limits = CompositionLimits(max_members=1, max_depth=1, max_concurrency=1, max_total_tokens=150)
    result = TeamComposerEngine().compose(
        [role], [first, second, rejected], limits, strategy="coverage"
    )
    assert result["members"][0]["profile_id"] == "second"
    assert result["members"][0]["capability_confidence"]["coding"] == 0.9
    assert (
        result["candidate_audit"]["coding"]["rejected"][0]["reason"]
        == "unknown_or_insufficient_capability"
    )


def test_unknown_capability_and_price_fail_hard_requirements():
    role = RoleSpec(
        id="coding", required_capabilities={"coding": 70}, input_tokens=100, output_tokens=50
    )
    limits = CompositionLimits(
        max_members=1,
        max_depth=1,
        max_concurrency=1,
        max_total_tokens=150,
        max_cost_microunits=1000,
        currency="USD",
    )
    engine = TeamComposerEngine()
    with pytest.raises(ValueError):
        engine.compose([role], [profile("unknown")], limits, strategy="greedy")
    with pytest.raises(ValueError):
        engine.compose(
            [role],
            [profile("unknown-price", capabilities={"coding": 90})],
            limits,
            strategy="greedy",
        )
    with pytest.raises(ValueError):
        engine.compose(
            [role],
            [profile("mixed", capabilities={"coding": 90}, price=1, currency="EUR")],
            limits,
            strategy="greedy",
        )


def test_token_only_mode_preserves_unknown_cost_and_provenance():
    role = RoleSpec(
        id="coding", required_capabilities={"coding": 70}, input_tokens=100, output_tokens=50
    )
    limits = CompositionLimits(max_members=1, max_depth=1, max_concurrency=1, max_total_tokens=150)
    result = TeamComposerEngine().compose(
        [role], [profile("m", capabilities={"coding": 80})], limits, strategy="greedy"
    )
    assert result["members"][0]["estimated_cost_microunits"] is None
    assert result["members"][0]["capability_sources"]["coding"] == "user_override"


def test_scope_and_concurrency_limits_cannot_be_bypassed():
    with pytest.raises(ValueError):
        TeamComposerEngine().compose(
            [
                RoleSpec(
                    id="coding",
                    required_capabilities={"coding": 70},
                    tools=["shell"],
                    input_tokens=100,
                    output_tokens=50,
                )
            ],
            [profile("m", capabilities={"coding": 80})],
            CompositionLimits(
                max_members=1,
                max_depth=1,
                max_concurrency=1,
                max_total_tokens=150,
                allowed_tools=["read"],
            ),
            strategy="greedy",
        )


@pytest.mark.parametrize("strategy", ["single_model", "greedy", "coverage", "complementarity"])
def test_all_baselines_respect_hard_requirements_and_bounds(strategy):
    roles = [
        RoleSpec(
            id="research",
            required_capabilities={"research": 70},
            input_tokens=100,
            output_tokens=50,
        ),
        RoleSpec(
            id="coding", required_capabilities={"coding": 70}, input_tokens=100, output_tokens=50
        ),
    ]
    models = [
        profile("general", capabilities={"coding": 80, "research": 80}),
        profile("coder", capabilities={"coding": 95, "vision": 90}),
        profile("researcher", capabilities={"research": 95, "mathematics": 90}),
    ]
    limits = CompositionLimits(max_members=2, max_depth=1, max_concurrency=2, max_total_tokens=300)
    result = TeamComposerEngine().compose(roles, models, limits, strategy=strategy)
    assert len(result["members"]) == 2 and result["estimated_tokens"] == 300
    for member, role in zip(result["members"], roles, strict=True):
        assert member["capabilities"][role.id] >= 70
    if strategy == "single_model":
        assert {member["profile_id"] for member in result["members"]} == {"general"}


def test_team_approval_is_plan_and_profile_bound_and_revocable(tmp_path):
    from forge_core.domain import DomainError, Quest
    from forge_core.quest_graph import QuestGraphService
    from forge_core.store import Store

    store = Store(tmp_path / "team.sqlite3")
    quest = Quest(title="Team", goal="Prepare")
    store.save_quest(quest)
    graph = {
        "nodes": [
            {
                "key": "a",
                "objective": "Write",
                "success_criteria": ["Done"],
                "budget_tokens": 500,
                "required_capabilities": {"coding": 70},
            }
        ],
        "edges": [],
        "max_total_tokens": 500,
    }
    plans = QuestGraphService(store)
    plan = plans.propose(quest.id, graph, expected_version=0)
    plans.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    model = profile("m", capabilities={"coding": 80})
    store.save_model(model)
    role = RoleSpec(
        id="coding",
        task_node_key="a",
        required_capabilities={"coding": 70},
        input_tokens=100,
        output_tokens=50,
    )
    limits = CompositionLimits(max_members=1, max_depth=1, max_concurrency=1, max_total_tokens=150)
    service = TeamCompositionService(store)
    proposal = service.propose(quest.id, [role], limits, strategy="greedy", expected_version=2)
    with store.unit_of_work() as work, pytest.raises(DomainError):
        service.get_approved(
            quest.id,
            plan["plan_revision"],
            proposal["proposal_digest"],
            connection=work.connection,
        )
    with pytest.raises(DomainError):
        service.approve(
            quest.id,
            plan["plan_revision"],
            proposal["proposal_digest"],
            expected_version=2,
            actor="model",
        )
    service.approve(
        quest.id,
        plan["plan_revision"],
        proposal["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    with store.unit_of_work() as work:
        assert (
            service.get_approved(
                quest.id,
                plan["plan_revision"],
                proposal["proposal_digest"],
                connection=work.connection,
            )["members"][0]["profile_id"]
            == "m"
        )
    model.enabled = False
    store.save_model(model)
    with store.unit_of_work() as work, pytest.raises(DomainError, match="stale"):
        service.get_approved(
            quest.id,
            plan["plan_revision"],
            proposal["proposal_digest"],
            connection=work.connection,
        )
    service.revoke(quest.id, proposal["proposal_digest"], actor="local_user")
    with store.unit_of_work() as work, pytest.raises(DomainError):
        service.get_approved(
            quest.id,
            plan["plan_revision"],
            proposal["proposal_digest"],
            connection=work.connection,
        )


def test_team_cannot_expand_approved_node_scope_or_weaken_capability(tmp_path):
    from forge_core.domain import DomainError, Quest
    from forge_core.quest_graph import QuestGraphService
    from forge_core.store import Store

    store = Store(tmp_path / "scope.sqlite3")
    quest = Quest(title="Scope", goal="No escalation")
    store.save_quest(quest)
    plans = QuestGraphService(store)
    plan = plans.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "a",
                    "objective": "Review",
                    "success_criteria": ["Done"],
                    "budget_tokens": 500,
                    "required_capabilities": {"coding": 70},
                }
            ],
            "edges": [],
            "max_total_tokens": 500,
        },
        expected_version=0,
    )
    plans.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    store.save_model(profile("m", capabilities={"coding": 80}))
    service = TeamCompositionService(store)
    limits = CompositionLimits(
        max_members=1, max_depth=1, max_concurrency=1, max_total_tokens=150, allowed_tools=["shell"]
    )
    role = RoleSpec(
        id="coding",
        task_node_key="a",
        required_capabilities={"coding": 70},
        tools=["shell"],
        input_tokens=100,
        output_tokens=50,
    )
    with pytest.raises(DomainError, match="scope"):
        service.propose(quest.id, [role], limits, strategy="greedy", expected_version=2)
    limits.allowed_tools = []
    role.tools = []
    role.required_capabilities = {"coding": 50}
    with pytest.raises(DomainError, match="weakens"):
        service.propose(quest.id, [role], limits, strategy="greedy", expected_version=2)


def test_replacement_does_not_invalidate_unsettled_worker(tmp_path):
    from forge_core.domain import DomainError, Quest
    from forge_core.provider_pool import ProviderPool
    from forge_core.quest_graph import QuestGraphService
    from forge_core.store import Store
    from forge_core.tool_engine import ToolEngine
    from forge_core.worker_runtime import WorkerManager

    class MockProvider:
        execution_mode = "mock"

    store = Store(tmp_path / "replacement.sqlite3")
    quest = Quest(title="Replacement", goal="Keep old receipts valid")
    store.save_quest(quest)
    graphs = QuestGraphService(store)
    plan = graphs.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "a",
                    "objective": "Work",
                    "success_criteria": ["Done"],
                    "budget_tokens": 500,
                    "required_capabilities": {"coding": 70},
                }
            ],
            "edges": [],
            "max_total_tokens": 500,
        },
        expected_version=0,
    )
    graphs.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    store.save_model(profile("m", capabilities={"coding": 80}))
    service = TeamCompositionService(store)
    role = RoleSpec(
        id="coding",
        task_node_key="a",
        required_capabilities={"coding": 70},
        input_tokens=100,
        output_tokens=50,
    )
    limits = CompositionLimits(max_members=1, max_depth=1, max_concurrency=1, max_total_tokens=150)
    old = service.propose(quest.id, [role], limits, strategy="greedy", expected_version=2)
    service.approve(
        quest.id,
        plan["plan_revision"],
        old["proposal_digest"],
        expected_version=2,
        actor="local_user",
    )
    manager = WorkerManager(
        store, ProviderPool({"provider": MockProvider()}), ToolEngine(store), team_authority=service
    )
    manager.start(quest.id, plan["id"], "a", team_digest=old["proposal_digest"], member_index=0)
    version = store.get_quest(quest.id)["version"]
    replacement = service.propose(
        quest.id, [role], limits, strategy="coverage", expected_version=version
    )
    with pytest.raises(DomainError, match="unsettled"):
        service.approve(
            quest.id,
            plan["plan_revision"],
            replacement["proposal_digest"],
            expected_version=version,
            actor="local_user",
        )
    with pytest.raises(DomainError, match="unsettled"):
        service.revoke(quest.id, old["proposal_digest"], actor="local_user")
    with store.unit_of_work() as work:
        assert (
            service.get_approved(
                quest.id, plan["plan_revision"], old["proposal_digest"], connection=work.connection
            )["status"]
            == "approved"
        )
