"""Two immutable task sources reach native measurement and the actual Worker."""

import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from test_delivery_diagnostics_v22 import prompt_contract
from test_worker_request_framing_v17 import ObservedGateway
from test_worker_runtime import MockApprovedTeam, MockToolEngine, ScriptedMock

from forge_core.capacity_preflight import node_review_envelope
from forge_core.domain import ConversationMessage, DomainError, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.worker_prompts import (
    canonical,
    initial_worker_messages,
    measure_initial_worker_request,
)
from forge_core.worker_runtime import WorkerManager
from forge_core.worker_task_sources import (
    INPUT_ACQUISITION_POLICY,
    MAX_TASK_REQUEST_CHARS,
    verified_task_sources,
)


@pytest.fixture
def graph(tmp_path):
    return make_graph(tmp_path)


def make_graph(tmp_path, *, policy=True):
    """New fixture explicitly approves the new protocol before any Worker starts."""
    store = Store(tmp_path / "sources.sqlite3", workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Task sources", goal="Execute the exact original task")
    store.save_quest(quest)
    data = {
        "nodes": [
            {
                "key": "n",
                "objective": "Implement",
                "success_criteria": ["verified"],
                "budget_tokens": 4000,
                "required_input_paths": [],
                "capacity_bounds": {
                    "max_source_receipt_bytes": 1024,
                    "max_artifact_bytes": 1024,
                    "max_delivery_bytes": 1024,
                },
            }
        ],
        "edges": [],
        "max_total_tokens": 4400,
        "coordination_reserve_tokens": 400,
    }
    if policy:
        data["capacity_contract"] = {
            "version": "verified-capacity-v1",
            "input_inventory": [],
            "nodes": {},
            "input_acquisition_policy": INPUT_ACQUISITION_POLICY,
        }
    service = QuestGraphService(store)
    plan = service.propose(quest.id, data, expected_version=0)
    service.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    return store, quest, plan


class SourceTeam(MockApprovedTeam):
    def get_approved(self, *args, **kwargs):
        team = super().get_approved(*args, **kwargs)
        if self.plan.get("input_acquisition_policy"):
            team["members"][0]["input_acquisition_policy"] = self.plan["input_acquisition_policy"]
        return team


def manager_for(store, plan, *, policy=True):
    contract = {**plan}
    if policy:
        contract["input_acquisition_policy"] = INPUT_ACQUISITION_POLICY
    return WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=SourceTeam(contract),
    )


def bind_sources(store, quest):
    sources = {
        "original_request": ConversationMessage(
            role="user", content='原始任务：保留 "引用"、路径 C:\\work 和换行\n及全部验收要求。'
        ),
        "updated_request": ConversationMessage(
            role="user", content="更新：优先分析边界，输出完整引用。"
        ),
    }
    metadata = {}
    for field, message in sources.items():
        store.add_message(message)
        prefix = "original_request" if field == "original_request" else "goal_update"
        metadata[prefix + "_message_id"] = message.id
        metadata[prefix + "_sha256"] = hashlib.sha256(message.content.encode()).hexdigest()
    store.add_message(ConversationMessage(role="user", content="UNRELATED_HISTORY_MARKER"))
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET metadata=? WHERE id=?", (json.dumps(metadata), quest.id)
        )
    return sources, metadata


def measure(store, quest_id, plan, manager, *, provider=None):
    current = store.get_quest(quest_id)
    approved = QuestGraphService(store).get(quest_id)
    with store.connection() as connection:
        connection.execute("BEGIN")
        member = manager.team_authority.get_approved(
            quest_id, plan["plan_revision"], "team", connection=connection
        )["members"][0]
        before = connection.total_changes
        result = measure_initial_worker_request(
            store, connection, current, approved, approved["nodes"][0], member, provider=provider
        )
        assert connection.total_changes == before
    return result


def test_complete_two_sources_have_same_hash_in_native_preflight_capsule_and_worker(graph):
    store, quest, plan = graph
    sources, _metadata = bind_sources(store, quest)
    posts = []
    provider = ObservedGateway(transport=httpx.MockTransport(lambda request: posts.append(request)))
    provider.config = replace(provider.config, id="mock-provider")
    manager = manager_for(store, plan)
    result = measure(store, quest.id, plan, manager, provider=provider)
    request, native_payload = provider.serialized[-1]
    preflight_task = json.loads(request.messages[-1]["content"])
    worker = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.connection() as connection:
        capsule = json.loads(
            connection.execute(
                "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (worker["id"],)
            ).fetchone()[0]
        )
        message = json.loads(
            connection.execute(
                "SELECT message_json FROM worker_messages WHERE run_id=? AND ordinal=1",
                (worker["id"],),
            ).fetchone()[0]
        )
    actual_task = json.loads(message["content"])
    for field, source in sources.items():
        expected = {
            "id": source.id,
            "content": source.content,
            "sha256": hashlib.sha256(source.content.encode()).hexdigest(),
        }
        assert preflight_task[field] == actual_task[field] == capsule[field] == expected
    assert "UNRELATED_HISTORY_MARKER" not in json.dumps(request.messages)
    assert "UNRELATED_HISTORY_MARKER" not in message["content"]
    assert result["native_input_reservation"] == (
        len(json.dumps(native_payload, ensure_ascii=False).encode()) + 64
    )
    assert not posts
    assert capsule["input_acquisition_policy"] == INPUT_ACQUISITION_POLICY


@pytest.mark.parametrize("field", ["original_request", "updated_request"])
@pytest.mark.parametrize(
    "fault",
    [
        "changed",
        "missing",
        "assistant",
        "conversation",
        "hash",
        "missing_hash",
        "missing_id",
        "long",
    ],
)
def test_invalid_source_rejects_runtime_and_preflight_before_any_worker_side_effect(
    graph, field, fault
):
    store, quest, plan = graph
    sources, metadata = bind_sources(store, quest)
    source = sources[field]
    prefix = "original_request" if field == "original_request" else "goal_update"
    with store.unit_of_work() as work:
        if fault == "changed":
            work.connection.execute(
                "UPDATE messages SET content=content||' changed' WHERE id=?", (source.id,)
            )
        elif fault == "missing":
            work.connection.execute("DELETE FROM messages WHERE id=?", (source.id,))
        elif fault == "assistant":
            work.connection.execute("UPDATE messages SET role='assistant' WHERE id=?", (source.id,))
        elif fault == "conversation":
            work.connection.execute("INSERT INTO conversations VALUES ('other','fixture')")
            work.connection.execute(
                "UPDATE messages SET conversation_id='other' WHERE id=?", (source.id,)
            )
        elif fault == "long":
            content = "x" * (MAX_TASK_REQUEST_CHARS + 1)
            work.connection.execute(
                "UPDATE messages SET content=? WHERE id=?", (content, source.id)
            )
            metadata[prefix + "_sha256"] = hashlib.sha256(content.encode()).hexdigest()
        elif fault == "hash":
            metadata[prefix + "_sha256"] = "0" * 64
        else:
            metadata.pop(prefix + ("_sha256" if fault == "missing_hash" else "_message_id"))
        work.connection.execute(
            "UPDATE quests SET metadata=? WHERE id=?", (json.dumps(metadata), quest.id)
        )
    before_events = store.list_events(limit=100)
    manager = manager_for(store, plan)
    with pytest.raises(DomainError, match="Worker task source"):
        measure(store, quest.id, plan, manager)
    with pytest.raises(DomainError, match="Worker task source"):
        manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.connection() as connection:
        for table in ("worker_runs", "workers", "worker_messages", "worker_budget_ledger"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert store.list_events(limit=100) == before_events


@pytest.mark.parametrize("has_sources", [False, True])
def test_legacy_task_omits_both_source_fields_from_native_and_actual_initial_messages(
    tmp_path, has_sources
):
    store, quest, plan = make_graph(tmp_path, policy=False)
    if has_sources:
        bind_sources(store, quest)
    manager = manager_for(store, plan, policy=False)
    provider = ObservedGateway(transport=httpx.MockTransport(lambda _: pytest.fail("No network")))
    provider.config = replace(provider.config, id="mock-provider")
    measure(store, quest.id, plan, manager, provider=provider)
    request, _payload = provider.serialized[-1]
    preflight_task = json.loads(request.messages[-1]["content"])
    worker = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.connection() as connection:
        assert verified_task_sources(connection, store.get_quest(quest.id)) == {}
        actual = json.loads(
            connection.execute(
                "SELECT message_json FROM worker_messages WHERE run_id=? AND ordinal=1",
                (worker["id"],),
            ).fetchone()[0]
        )
    assert (
        set(json.loads(actual["content"]))
        == set(preflight_task)
        == {"objective", "success_criteria", "quest_id", "plan_id", "node_key"}
    )


@pytest.mark.parametrize("fault", ["member_missing", "member_unknown", "plan_unknown", "plan_null"])
def test_unknown_or_different_protocol_marker_rejects_measurement_and_start(graph, fault):
    store, quest, plan = graph
    bind_sources(store, quest)
    manager = manager_for(store, plan)
    if fault.startswith("member"):
        if fault == "member_missing":
            manager.team_authority.plan.pop("input_acquisition_policy")
        else:
            manager.team_authority.plan["input_acquisition_policy"] = "unknown"
    else:
        with store.unit_of_work() as work:
            data = json.loads(
                work.connection.execute(
                    "SELECT graph_json FROM quest_plans WHERE id=?", (plan["id"],)
                ).fetchone()[0]
            )
            data["capacity_contract"]["input_acquisition_policy"] = (
                None if fault == "plan_null" else "unknown"
            )
            work.connection.execute(
                "UPDATE quest_plans SET graph_json=? WHERE id=?", (json.dumps(data), plan["id"])
            )
    with pytest.raises(DomainError, match="input acquisition policy"):
        measure(store, quest.id, plan, manager)
    with pytest.raises(DomainError, match="input acquisition policy"):
        manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)


@pytest.mark.parametrize("field", ["original_request", "updated_request"])
@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize(
    "fault", ["changed", "binding_removed", "capsule_source", "capsule_policy"]
)
def test_queued_worker_rechecks_sources_and_capsule_before_claiming_or_model_calls(
    graph, field, fault, running
):
    store, quest, plan = graph
    sources, _metadata = bind_sources(store, quest)
    manager = manager_for(store, plan)
    worker = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    token = manager._claim(worker["id"]) if running else None
    with store.unit_of_work() as work:
        if fault == "changed":
            work.connection.execute(
                "UPDATE messages SET content=content||' changed' WHERE id=?", (sources[field].id,)
            )
        elif fault == "binding_removed":
            work.connection.execute("UPDATE quests SET metadata='{}' WHERE id=?", (quest.id,))
        else:
            capsule = json.loads(
                work.connection.execute(
                    "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (worker["id"],)
                ).fetchone()[0]
            )
            if fault == "capsule_source":
                capsule[field]["content"] += " changed"
            else:
                capsule.pop("input_acquisition_policy")
            work.connection.execute(
                "UPDATE worker_capsules SET capsule_json=? WHERE run_id=?",
                (json.dumps(capsule), worker["id"]),
            )
    before = manager.get(worker["id"])
    with pytest.raises(DomainError, match="Worker task source|input acquisition policy"):
        if running:
            with store.connection() as connection:
                manager._fenced(connection, worker["id"], token)
        else:
            manager._claim(worker["id"])
    assert manager.get(worker["id"]) == before
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM worker_tool_calls").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM worker_model_reservations").fetchone()[0] == 0
        )


def test_real_capacity_entry_rejects_tampered_bound_original_source_before_native_sizing(graph):
    store, quest, plan = graph
    sources, _metadata = bind_sources(store, quest)
    manager = manager_for(store, plan)
    approved = QuestGraphService(store).get(quest.id)
    approved["nodes"][0]["budget_tokens"] = approved["max_total_tokens"] = 180000
    approved["nodes"][0]["success_criteria"] = ["python_ast:work/future.py"]
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE messages SET content=content||' changed' WHERE id=?",
            (sources["original_request"].id,),
        )
    with store.connection() as connection:
        member = manager.team_authority.get_approved(quest.id, 1, "team", connection=connection)[
            "members"
        ][0]
        member["estimated_tokens"] = 180000
        before = connection.total_changes
        with pytest.raises(DomainError, match="Worker task source"):
            node_review_envelope(
                store,
                connection,
                store.get_quest(quest.id),
                approved,
                approved["nodes"][0],
                member,
                None,
                resource_sizing=True,
            )
        assert connection.total_changes == before


def test_constructor_requires_explicit_known_policy_to_project_sources(graph):
    store, quest, plan = graph
    bind_sources(store, quest)
    manager = manager_for(store, plan)
    worker = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.connection() as connection:
        capsule = json.loads(
            connection.execute(
                "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (worker["id"],)
            ).fetchone()[0]
        )
    capsule.pop("input_acquisition_policy")
    legacy_task = json.loads(initial_worker_messages(capsule, has_successors=False)[1]["content"])
    assert "original_request" not in legacy_task and "updated_request" not in legacy_task
    capsule["input_acquisition_policy"] = "unknown"
    with pytest.raises(DomainError, match="Unknown Worker input acquisition policy"):
        initial_worker_messages(capsule, has_successors=False)


@pytest.mark.parametrize("marker", [INPUT_ACQUISITION_POLICY, "unknown", None])
def test_legacy_no_capacity_capsule_cannot_acquire_an_unapproved_protocol(tmp_path, marker):
    store, quest, plan = make_graph(tmp_path, policy=False)
    manager = manager_for(store, plan, policy=False)
    worker = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.unit_of_work() as work:
        capsule = json.loads(
            work.connection.execute(
                "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (worker["id"],)
            ).fetchone()[0]
        )
        capsule["input_acquisition_policy"] = marker
        work.connection.execute(
            "UPDATE worker_capsules SET capsule_json=? WHERE run_id=?",
            (json.dumps(capsule), worker["id"]),
        )
    with pytest.raises(DomainError, match="input acquisition policy"):
        manager._claim(worker["id"])


def test_compact_protocol_keeps_complete_contracts_sources_and_legacy_policy_bytes():
    contract = prompt_contract(bounded=True)
    legacy_messages = initial_worker_messages(contract, has_successors=False)
    assert hashlib.sha256(legacy_messages[0]["content"].encode()).hexdigest() == (
        "93c8abcec6fc28a7e1a9556671f6c501575aec25f1b7ad80467f5dc77f911859"
    )
    contract["input_acquisition_policy"] = INPUT_ACQUISITION_POLICY
    for key in ("original_request", "updated_request"):
        text = f'完整 {key}: "引用" \\路径\n不可丢失要求。'
        contract[key] = {
            "id": key,
            "content": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
    current = initial_worker_messages(contract, has_successors=False)
    policy, task = current[0]["content"], json.loads(current[1]["content"])
    for key in ("original_request", "updated_request"):
        assert task.pop(key) == contract[key]
    assert task == json.loads(legacy_messages[1]["content"])
    assert (
        canonical(
            {
                key: contract[key]
                for key in (
                    "responsibility",
                    "input_contract",
                    "output_schema",
                    "acceptance_criteria",
                )
            }
        )
        in policy
    )
    assert (
        canonical(
            {
                "capacity_contract": contract["review_envelope"],
                "required_input_paths": contract["required_input_paths"],
            }
        )
        in policy
    )
    assert "satisfy this node's original requirements" in policy
    assert "ALL eight delivery fields" in policy and "one shared evidence list" in policy
    assert "sorted keys, compact separators, literal Unicode" in policy
    assert len(legacy_messages[0]["content"].encode()) - len(policy.encode()) >= 1000
