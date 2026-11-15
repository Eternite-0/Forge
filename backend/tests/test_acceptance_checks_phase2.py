"""Offline evaluator regressions using synthetic ledger fixtures, never live calls."""

import asyncio
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.domain import ConversationMessage, ForgeEvent, Quest
from forge_core.independent_review import IndependentReviewService
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.settings import Workspace
from forge_core.store import Store


def module(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / "phase2" / (name + ".py")
    spec = importlib.util.spec_from_file_location("phase2_test_" + name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def evaluator():
    return module("acceptance_checks")


def publish(store, root):
    target = root / "data" / "forge.sqlite3"
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(store.path, target)


def ledger(store, call_id, purpose, quest_id=None, run_id=None, *, unknown=False):
    with store.unit_of_work() as work:
        work.connection.execute(
            """INSERT INTO model_budget_attempts(
            attempt_id,call_id,purpose,workspace_id,quest_id,worker_run_id,provider_id,model_id,
            input_reserved,output_reserved,state,created_at,input_charged,output_charged)
            VALUES(?,?,?,?,?,?,'protocol','fixture',1000,100,'settled',0,?,?)""",
            (
                "synthetic-" + call_id,
                call_id,
                purpose,
                store.workspace.id,
                quest_id,
                run_id,
                None if unknown else 25,
                None if unknown else 15,
            ),
        )


def level1_fixture(root):
    sources = module("acceptance_scenarios")
    store, manager, model, run = prepared(root, [], tools=["file_read", "directory_list"])
    scenario = sources.seed_scenario(1, root)
    result = delivery(
        "Priority ranked synthetic observations, independently assessed by a scripted fixture."
    )
    result["findings"] = [
        "Priority 1: fixture finding",
        "Priority 2: fixture finding",
        "Priority 3: fixture finding",
    ]
    result["evidence_references"] = [
        item["source_path"] + ":1 " + item["source_sha256"] for item in scenario["input_manifest"]
    ]
    model.responses = [
        tool("directory_list", {"path": "inputs/scenario-1"}),
        tool("file_read", {"path": "inputs/scenario-1/manifest.json"}, 2),
        *[
            tool("file_read", {"path": item["path"]}, index + 3)
            for index, item in enumerate(scenario["input_manifest"])
        ],
        response(result),
        response({"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}),
    ]
    # Explicit synthetic adapter/ledger data exercises live-evidence validation;
    # this does not execute HTTP or claim a real Provider demonstration.
    model.execution_mode = "live"
    model._budget_ledger = ModelCallBudgetLedger(store.path, token_limit=60000, concurrency_limit=1)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET execution_mode='live' WHERE id=?", (run["id"],)
        )
    generate = model.generate

    async def generated(request):
        context = request.budget_context
        admitted = model._budget_ledger.admit(
            context,
            request.provider_id,
            request.model_id,
            input_tokens=1000,
            output_tokens=request.max_output_tokens,
            remote_timeout=request.timeout_seconds,
        )
        output = await generate(request)
        model._budget_ledger.settle(admitted, output.usage)
        return output

    model.generate = generated
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    assert asyncio.run(IndependentReviewService(manager).review(run["id"]))["verdict"] == "PASS"
    for purpose in ("router", "planner", "team_composer", "router_notice"):
        ledger(store, purpose, purpose, None if purpose == "router" else run["quest_id"])
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='succeeded' WHERE id=?", (run["id"],))
        work.connection.execute(
            "UPDATE task_nodes SET status='succeeded' WHERE plan_id=?", (run["plan_id"],)
        )
        work.connection.execute(
            "UPDATE quests SET status='completed' WHERE id=?", (run["quest_id"],)
        )
    event = ForgeEvent(
        event_type="quest.status_changed",
        quest_id=run["quest_id"],
        payload={"from": "running", "to": "completed"},
    )
    store.append_event(event)
    store.add_message(
        ConversationMessage(
            role="assistant",
            content="Synthetic completion notice",
            metadata={"source": "router_notice", "event_id": event.id, "execution_mode": "live"},
        )
    )
    publish(store, root)
    return store, scenario, run


def test_missing_database_remains_unknown_and_does_not_create_files(tmp_path, evaluator):
    scenario = module("acceptance_scenarios").describe_scenario(3)
    report = evaluator.evaluate_scenario(scenario, {"final_status": "completed"}, tmp_path)
    assert report["status"] == "unknown" and not report["passed"]
    assert set(scenario["expected_checks"]) <= set(report["unknown_checks"])
    assert list(tmp_path.iterdir()) == []


def test_completed_quest_claim_alone_never_passes_acceptance(tmp_path, evaluator):
    store = Store(workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="Synthetic", goal="Synthetic completion claim")
    store.save_quest(quest)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status='completed' WHERE id=?", (quest.id,))
    report = evaluator.evaluate_scenario(
        module("acceptance_scenarios").describe_scenario(1), {"final_status": "completed"}, tmp_path
    )
    assert report["checks"]["completed_quest"]["status"] == "passed"
    assert report["checks"]["approved_current_dag"]["status"] == "failed"
    assert not report["passed"]


def test_full_l1_evaluator_pass_uses_durable_records_not_inspection_booleans(tmp_path, evaluator):
    store, scenario, _run = level1_fixture(tmp_path)
    report = evaluator.evaluate_scenario(
        scenario,
        {"final_status": "failed", "model_calls": 0},
        tmp_path,
        run_record={"duration_seconds": 5},
    )
    assert report["passed"], report
    assert report["checks"]["input_sources_read"]["status"] == "passed"
    assert report["checks"]["router_completion_summary"]["status"] == "passed"
    assert store.list_messages()[-1]["content"] == "Synthetic completion notice"


def test_tampered_source_copy_fails_even_with_old_live_review_pass(tmp_path, evaluator):
    _store, scenario, _run = level1_fixture(tmp_path)
    path = tmp_path / scenario["input_manifest"][0]["path"]
    path.write_text("{}", encoding="utf-8")
    report = evaluator.evaluate_scenario(scenario, {}, tmp_path)
    assert report["checks"]["input_integrity"]["status"] == "failed"
    assert not report["passed"]


def test_unknown_usage_is_not_zero_and_prevents_acceptance(tmp_path, evaluator):
    store, scenario, run = level1_fixture(tmp_path)
    ledger(store, "unknown-usage", "worker", run["quest_id"], run["id"], unknown=True)
    publish(store, tmp_path)
    report = evaluator.evaluate_scenario(scenario, {}, tmp_path)
    assert report["checks"]["accounted_model_usage"]["status"] == "unknown"
    assert report["status"] == "unknown" and not report["passed"]


def test_mock_reviews_and_receipts_cannot_be_promoted_by_driver_snapshot(tmp_path, evaluator):
    store, manager, _model, run = prepared(
        tmp_path, [tool("file_read", {"path": "work/input.txt"}), response(delivery())]
    )
    asyncio.run(manager.run(run["id"]))
    publish(store, tmp_path)
    report = evaluator.evaluate_scenario(
        module("acceptance_scenarios").describe_scenario(1),
        {
            "final_status": "completed",
            "review_results": [{"verdict": "PASS", "execution_mode": "live"}],
        },
        tmp_path,
    )
    assert report["checks"]["successful_worker_nodes"]["status"] == "failed"
    assert report["checks"]["readonly_tool_receipts"]["status"] == "failed"
    assert report["checks"]["independent_delivery_review"]["status"] == "failed"


def test_unrelated_quest_completion_notice_is_not_accepted(tmp_path, evaluator):
    store, scenario, _run = level1_fixture(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET metadata='{}' WHERE role='assistant'")
    other = Quest(title="Other", goal="Other task")
    store.save_quest(other)
    event = ForgeEvent(
        event_type="quest.status_changed", quest_id=other.id, payload={"to": "completed"}
    )
    store.append_event(event)
    store.add_message(
        ConversationMessage(
            role="assistant",
            content="Other completed",
            metadata={"source": "router_notice", "event_id": event.id, "execution_mode": "live"},
        )
    )
    publish(store, tmp_path)
    with store.connection() as connection:
        quest_id = connection.execute("SELECT id FROM quests WHERE id!=?", (other.id,)).fetchone()[
            0
        ]
    report = evaluator.evaluate_scenario(scenario, {}, tmp_path, run_record={"quest_id": quest_id})
    assert report["checks"]["router_completion_summary"]["status"] == "failed"


@pytest.mark.parametrize("level", [2, 3])
def test_l2_l3_require_actual_roles_artifacts_experiments_and_semantic_evidence(
    tmp_path, evaluator, level
):
    _store, _scenario, _run = level1_fixture(tmp_path)
    scenario = module("acceptance_scenarios").describe_scenario(level)
    report = evaluator.evaluate_scenario(
        scenario,
        {"semantic_test_runs": 1, "artifact_hashes": [{"receipt_hash_matches": True}]},
        tmp_path,
    )
    assert report["checks"]["artifact_hashes"]["status"] == "failed"
    assert report["checks"]["independent_content_review"]["status"] == "failed"
    if level == 2:
        assert report["checks"]["three_or_four_specialist_workers"]["status"] == "failed"
        assert report["checks"]["artifact_receipt_hashes"]["status"] == "failed"
    else:
        assert report["checks"]["real_numeric_ast_receipts"]["status"] == "failed"
        assert report["checks"]["independent_fixed_semantic_suite"]["status"] == "failed"
        assert report["checks"]["core_restart_continuity"]["status"] == "unknown"
        assert report["checks"]["context_compaction_continuity"]["status"] == "unknown"


def test_l3_restart_scope_stays_explicit_and_unbacked_compaction_fails(tmp_path, evaluator):
    store, _scenario, _run = level1_fixture(tmp_path)
    with store.connection() as connection:
        router_identity = connection.execute(
            "SELECT router_session_id FROM router_state"
        ).fetchone()[0]
    scenario = module("acceptance_scenarios").describe_scenario(3)
    report = evaluator.evaluate_scenario(
        scenario,
        {},
        tmp_path,
        run_record={
            "restart": {
                "router_identity_retained": True,
                "same_workspace": True,
                "initial_router_identity": router_identity,
                "restored_router_identity": router_identity,
                "after": "approved_plan_before_team_execution",
            },
            "compaction": {
                "archive_created": True,
                "source_readback_available": True,
                "source_count": 1,
                "verified_sources": [{"source_id": "invented", "content_sha256": "a" * 64}],
            },
        },
    )
    restart = report["checks"]["core_restart_continuity"]
    assert restart["status"] == "failed"
    assert "approved_plan_before_team_execution" in restart["detail"]
    assert report["checks"]["context_compaction_continuity"]["status"] == "failed"


def test_restart_boolean_without_direct_identities_is_unknown(tmp_path, evaluator):
    _store, _scenario, _run = level1_fixture(tmp_path)
    scenario = module("acceptance_scenarios").describe_scenario(3)
    report = evaluator.evaluate_scenario(
        scenario,
        {},
        tmp_path,
        run_record={
            "restart": {
                "router_identity_retained": True,
                "same_workspace": True,
                "after": "approved_plan_before_team_execution",
            },
        },
    )
    assert report["checks"]["core_restart_continuity"]["status"] == "unknown"


def test_known_changed_router_identity_fails_even_when_restart_anchors_are_missing(
    tmp_path, evaluator
):
    _store, _scenario, _run = level1_fixture(tmp_path)
    scenario = module("acceptance_scenarios").describe_scenario(3)
    report = evaluator.evaluate_scenario(
        scenario,
        {},
        tmp_path,
        run_record={
            "restart": {
                "router_identity_retained": True,
                "same_workspace": True,
                "initial_router_identity": "old-router",
                "restored_router_identity": "changed-router",
                "after": "completed_nodes_with_remaining_execution",
            },
        },
    )
    assert report["checks"]["core_restart_continuity"]["status"] == "failed"


@pytest.mark.parametrize(
    "mutation", [None, "receipt", "counter", "completed_node", "unsettled", "resume"]
)
def test_l3_continuity_rechecks_actual_completed_node_receipt_and_budget_anchors(
    tmp_path, evaluator, mutation
):
    import copy

    store, _scenario, run = level1_fixture(tmp_path)
    # Synthetic second DAG node isolates restart-anchor evaluation; no claim is
    # made that this hand-built graph passed full scenario/plan acceptance.
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO task_nodes(plan_id,node_key,objective,criteria_json,budget_tokens,required_capabilities,max_attempts) VALUES (?, 'remaining', 'Remaining synthetic work','[\"delivery_review\"]',1000,'{}',1)",
            (run["plan_id"],),
        )
    publish(store, tmp_path)
    before = module("live_e2e").continuity_snapshot(tmp_path, run["quest_id"])
    after = copy.deepcopy(before)
    with store.connection() as connection:
        identity = connection.execute("SELECT router_session_id FROM router_state").fetchone()[0]
    restart = {
        "after": "completed_nodes_with_remaining_execution",
        "before": before,
        "after_start": after,
        "initial_router_identity": identity,
        "restored_router_identity": identity,
        "same_workspace": True,
        "router_identity_retained": True,
        "resume_status": 200,
    }
    if mutation == "receipt":
        after["receipts"][0]["result_digest"] = "0" * 64
    elif mutation == "counter":
        after["worker_counters"][0]["token_spent"] = 0
    elif mutation == "completed_node":
        next(node for node in after["nodes"] if node["status"] == "succeeded")["status"] = "paused"
    elif mutation == "unsettled":
        before["unsettled_model_attempts"] = 1
    elif mutation == "resume":
        restart["resume_status"] = 409
    checks = {}
    evaluator._continuity(
        evaluator._ReadOnlyStore(tmp_path),
        tmp_path,
        {"quest_id": run["quest_id"], "restart": restart},
        checks,
    )
    assert checks["core_restart_continuity"]["status"] == (
        "passed" if mutation is None else "failed"
    )


def test_l3_continuity_checks_final_counters_against_captured_before_snapshot(tmp_path, evaluator):
    import copy

    store, _scenario, run = level1_fixture(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO task_nodes(plan_id,node_key,objective,criteria_json,budget_tokens,required_capabilities,max_attempts) VALUES (?, 'remaining', 'Remaining work','[\"delivery_review\"]',1000,'{}',1)",
            (run["plan_id"],),
        )
    publish(store, tmp_path)
    before = module("live_e2e").continuity_snapshot(tmp_path, run["quest_id"])
    with store.connection() as connection:
        identity = connection.execute("SELECT router_session_id FROM router_state").fetchone()[0]
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET token_spent=0 WHERE id=?", (run["id"],))
    publish(store, tmp_path)
    restart = {
        "after": "completed_nodes_with_remaining_execution",
        "before": before,
        "after_start": copy.deepcopy(before),
        "initial_router_identity": identity,
        "restored_router_identity": identity,
        "same_workspace": True,
        "router_identity_retained": True,
        "resume_status": 200,
    }
    checks = {}
    evaluator._continuity(
        evaluator._ReadOnlyStore(tmp_path),
        tmp_path,
        {"quest_id": run["quest_id"], "restart": restart},
        checks,
    )
    assert checks["core_restart_continuity"]["status"] == "failed"


@pytest.mark.parametrize("same_source", [True, False])
def test_numeric_experiments_must_use_exact_delivered_scheduler_source(
    tmp_path, evaluator, same_source
):
    inputs = [
        {"eligible_a": 0, "eligible_b": 0, "cost_a": 0, "cost_b": 0},
        {"eligible_a": 1, "eligible_b": 1, "cost_a": 1, "cost_b": 2},
    ]
    source = "result = 0\n"
    written = source if same_source else "result = 1\n"
    store, manager, model, run = prepared(
        tmp_path,
        [
            tool("numeric_ast", {"source": source, "inputs": inputs[0]}),
            tool("numeric_ast", {"source": source, "inputs": inputs[1]}, 2),
            tool("file_write", {"path": "work/l3-scheduler.py", "content": written}, 3),
            response(delivery()),
        ],
        tools=["numeric_ast", "file_write"],
    )
    model.execution_mode = "live"
    generate = model.generate

    async def generated(request):
        if len(model.requests) == 3:
            with store.connection() as connection:
                receipts = [
                    row[0]
                    for row in connection.execute(
                        "SELECT r.id FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE i.tool_name='numeric_ast' ORDER BY r.created_at"
                    )
                ]
            model.responses.insert(
                0,
                tool(
                    "file_write",
                    {
                        "path": "work/l3-report.json",
                        "content": json.dumps({"experiments": receipts}),
                    },
                    4,
                ),
            )
        return await generate(request)

    model.generate = generated
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET execution_mode='live' WHERE id=?", (run["id"],)
        )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    publish(store, tmp_path)
    report = evaluator.evaluate_scenario(
        module("acceptance_scenarios").describe_scenario(3), {}, tmp_path
    )
    assert report["checks"]["real_numeric_ast_receipts"]["status"] == (
        "passed" if same_source else "failed"
    )
    # Toy constant source may exercise receipts but cannot satisfy the real
    # independent fixed scheduler suite merely by having been executed.
    assert report["checks"]["independent_fixed_semantic_suite"]["status"] == "failed"
    assert report["checks"]["experiment_specialist_execution"]["status"] == "failed"


@pytest.mark.parametrize("tamper", [False, True])
def test_latest_valid_file_revision_replaces_historical_artifact_hash(tmp_path, evaluator, tamper):
    store, manager, model, run = prepared(
        tmp_path,
        [
            tool("file_write", {"path": "work/l2-report.json", "content": '{"version": 1}'}),
            response(delivery()),
        ],
        tools=["file_write", "file_revise"],
    )
    model.execution_mode = "live"
    generate = model.generate

    async def generated(request):
        if len(model.requests) == 1:
            with store.connection() as connection:
                receipt = connection.execute(
                    "SELECT id,result_json FROM action_receipts ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
            model.responses.insert(
                0,
                tool(
                    "file_revise",
                    {
                        "path": "work/l2-report.json",
                        "prior_receipt_id": receipt["id"],
                        "expected_sha256": json.loads(receipt["result_json"])["sha256"],
                        "content": '{"version": 2}',
                    },
                    2,
                ),
            )
        return await generate(request)

    model.generate = generated
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET execution_mode='live' WHERE id=?", (run["id"],)
        )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    if tamper:
        (tmp_path / "work/l2-report.json").write_text('{"version": 3}', encoding="utf-8")
    publish(store, tmp_path)
    report = evaluator.evaluate_scenario(
        module("acceptance_scenarios").describe_scenario(2), {}, tmp_path
    )
    artifacts = report["checks"]["artifact_hashes"]
    assert artifacts["status"] == ("failed" if tamper else "passed")
    if not tamper:
        proof = artifacts["evidence"][0]
        assert len(proof["revision_receipt_ids"]) == 2
        assert proof["sha256"] == hashlib.sha256(b'{"version": 2}').hexdigest()


def test_line_citations_require_source_hash_and_a_supplied_line(evaluator):
    source = {
        "source_path": "src/module.py",
        "source_sha256": "a" * 64,
        "fragments": [{"first_line": 10, "last_line": 20}],
    }
    assert evaluator._citations([source], ["src/module.py:12 " + "a" * 64]) == ["src/module.py"]
    assert evaluator._citations([source], ["src/module.py:2 " + "a" * 64]) == []
    assert evaluator._citations([source], ["src/module.py:12"]) == []
    assert evaluator._citations([source], ["src/module.py:12-999 " + "a" * 64]) == []
    assert evaluator._citations(
        [source],
        [
            {
                "source_path": "src/module.py",
                "source_sha256": "a" * 64,
                "first_line": 12,
                "last_line": 14,
            }
        ],
    ) == ["src/module.py"]


def test_citations_cannot_borrow_a_line_range_from_another_source(evaluator):
    documents = [
        {
            "source_path": f"src/{name}.py",
            "source_sha256": digest * 64,
            "fragments": [{"first_line": 10, "last_line": 20}],
        }
        for name, digest in (("a", "a"), ("b", "b"))
    ]
    values = ["src/a.py " + "a" * 64, "src/b.py:12 " + "b" * 64]
    assert evaluator._citations(documents, values) == ["src/b.py"]


def test_six_specializations_require_six_distinct_worker_nodes(evaluator):
    required = ["research", "mathematics", "architecture", "coding", "experiment", "review"]
    all_keywords = "research mathematics architecture coding experiment review"
    assert (
        evaluator._specialists(
            [{"task_node_key": "only", "role_id": "dynamic", "responsibility": all_keywords}],
            [{"node_key": "only", "objective": all_keywords}],
            required,
        )
        is None
    )
    members = [
        {"task_node_key": f"n{index}", "role_id": role}
        for index, role in enumerate(
            ("research", "math", "architect", "coding", "experiment", "reviewer")
        )
    ]
    nodes = [
        {"node_key": member["task_node_key"], "objective": "Approved task"} for member in members
    ]
    assignment = evaluator._specialists(members, nodes, required)
    assert assignment is not None and len({value["node_key"] for value in assignment.values()}) == 6
    for member in members:
        member["role_id"] = "coding"
        member["responsibility"] = all_keywords
    assert evaluator._specialists(members, nodes, required) is None


def test_scenario_budget_includes_nonquest_coordination_attempts(tmp_path, evaluator):
    store, scenario, _run = level1_fixture(tmp_path)
    with store.connection() as connection:
        known = connection.execute(
            "SELECT sum(input_charged+output_charged) FROM model_budget_attempts"
        ).fetchone()[0]
    scenario["limits"]["max_total_tokens"] = known + 20
    ledger(store, "extra-reflection", "router_reflect")
    publish(store, tmp_path)
    report = evaluator.evaluate_scenario(scenario, {}, tmp_path, run_record={"duration_seconds": 1})
    tokens = report["checks"]["scenario_total_token_budget"]
    assert tokens["status"] == "failed"
    assert tokens["evidence"][0]["known_charged_tokens"] == known + 40


def test_unknown_reservations_cannot_fit_by_being_counted_as_zero(tmp_path, evaluator):
    store, scenario, _run = level1_fixture(tmp_path)
    scenario["limits"]["max_total_tokens"] = 1000
    ledger(store, "unknown-reflection", "router_reflect", unknown=True)
    publish(store, tmp_path)
    report = evaluator.evaluate_scenario(scenario, {}, tmp_path, run_record={"duration_seconds": 1})
    tokens = report["checks"]["scenario_total_token_budget"]
    assert tokens["status"] == "failed"
    assert tokens["evidence"][0]["unknown_reserved_tokens"] == 1100
    assert "synthetic-unknown-reflection" in tokens["evidence"][0]["unknown_attempts"]


def test_scenario_duration_steps_tools_and_review_rounds_are_actual_limits(tmp_path, evaluator):
    store, scenario, run = level1_fixture(tmp_path)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET steps=17,tool_spent=17 WHERE id=?", (run["id"],)
        )
    for index in range(3):
        store.append_event(
            ForgeEvent(
                event_type="review.started",
                quest_id=run["quest_id"],
                payload={"run_id": run["id"], "call_id": f"synthetic-extra-{index}"},
            )
        )
    publish(store, tmp_path)
    report = evaluator.evaluate_scenario(
        scenario,
        {},
        tmp_path,
        run_record={"duration_seconds": scenario["limits"]["max_duration_seconds"] + 1},
    )
    for check in (
        "scenario_duration_budget",
        "scenario_worker_step_budget",
        "scenario_worker_tool_budget",
        "scenario_review_round_budget",
    ):
        assert report["checks"][check]["status"] == "failed"


@pytest.mark.parametrize("include_original", [True, False])
def test_compaction_must_archive_and_restore_this_quests_original_request(
    tmp_path, evaluator, include_original
):
    from types import SimpleNamespace

    store, _scenario, run = level1_fixture(tmp_path)
    live_store = Store(tmp_path / "data/forge.sqlite3", workspace=store.workspace)
    original = ConversationMessage(
        role="user", content="Original contract whose source must survive compaction"
    )

    def add_original():
        live_store.add_message(original)
        with live_store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE quests SET metadata=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "original_request_message_id": original.id,
                            "original_request_sha256": hashlib.sha256(
                                original.content.encode()
                            ).hexdigest(),
                        }
                    ),
                    run["quest_id"],
                ),
            )

    if include_original:
        add_original()
    live_store.add_message(
        ConversationMessage(role="assistant", content="Recent unarchived update")
    )
    compaction = module("live_e2e").compact_stopped_core(SimpleNamespace(workspace=tmp_path))
    if not include_original:
        add_original()
    checks = {}
    evaluator._continuity(
        evaluator._ReadOnlyStore(tmp_path),
        tmp_path,
        {"quest_id": run["quest_id"], "compaction": compaction},
        checks,
    )
    assert checks["context_compaction_continuity"]["status"] == (
        "passed" if include_original else "failed"
    )


@pytest.mark.parametrize("matching_call", [True, False])
def test_exact_native_planner_call_and_graph_provenance_is_required(
    tmp_path, evaluator, matching_call
):
    store, _scenario, run = level1_fixture(tmp_path)
    source = ForgeEvent(event_type="quest.proposed", quest_id=run["quest_id"])
    store.append_event(source)
    with store.connection() as connection:
        plan_digest = connection.execute(
            "SELECT digest FROM quest_plans WHERE id=?", (run["plan_id"],)
        ).fetchone()[0]
    store.append_event(
        ForgeEvent(
            event_type="quest.plan_proposed",
            quest_id=run["quest_id"],
            payload={
                "plan_id": run["plan_id"],
                "planner_call_id": "planner" if matching_call else "unrelated",
                "response_sha256": "a" * 64,
                "graph_digest": plan_digest,
                "source_event_id": source.id,
            },
        )
    )
    publish(store, tmp_path)
    report = evaluator.evaluate_scenario(
        module("acceptance_scenarios").describe_scenario(2), {}, tmp_path
    )
    assert report["checks"]["model_generated_dag"]["status"] == (
        "passed" if matching_call else "failed"
    )
