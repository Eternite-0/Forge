"""Programmatic candidate regressions; scripted Mock models, no Provider calls."""

import asyncio
import json

import pytest
import test_worker_report_guidance_phase2 as reports
from test_reviewer_capacity_phase2 import PASS, pending
from test_superagent_loop import MockProvider, conversational_decision, response
from test_superagent_loop import rig as base_rig
from test_worker_intelligence_phase2 import response as worker_response

from forge_core.domain import DomainError
from forge_core.independent_review import (
    IndependentReviewService,
    _json,
    _packet,
    _review_input_reservation,
    review_capacity_preview,
)
from forge_core.workspace_discovery import WorkspaceDiscovery


@pytest.fixture(autouse=True)
def enforced_original_budget_policy(monkeypatch):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")


def setup_projects(tmp_path):
    store, router, _ = base_rig.__wrapped__(tmp_path)
    roots = [tmp_path / name for name in ("project_beta", "project_alpha")]
    for root in roots:
        root.mkdir()
        (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return store, router, roots


def table_rows(store, table):
    with store.connection() as c:
        return [dict(row) for row in c.execute("SELECT * FROM " + table)]


def test_first_and_second_explicit_roots_present_before_model_and_bind_separate_quests(tmp_path):
    store, router, roots = setup_projects(tmp_path)
    requests = []

    def decide(request, ordinal):
        context = json.loads(request.messages[1]["content"])
        projects = context["project_context"]
        focused = projects["focused_project_id"]
        root = next(item["root"] for item in projects["projects"] if item["id"] == focused)
        assert root == str(roots[ordinal - 1])
        assert projects["grants_tool_access"] is False
        assert context["quests"] == []
        requests.append(context)
        return response(conversational_decision())

    router.provider = MockProvider(decide)
    created = [asyncio.run(router.handle_user_message(f'Analyze "{root}".', request_id=str(i)))
               for i, root in enumerate(roots)]
    assert len({item["quest_id"] for item in created}) == 2
    for item, root in zip(created, roots, strict=True):
        assert WorkspaceDiscovery(store).for_quest(item["quest_id"])["root"] == str(root)
    assert len(requests) == 2


def test_registration_changes_only_existing_project_metadata_and_no_authority(tmp_path):
    store, _, roots = setup_projects(tmp_path)
    tables = ["quests", "task_project_links", "workers", "worker_runs", "task_grants", "approvals",
              "delegation_mandates", "delegation_contracts", "action_intents", "model_budget_attempts"]
    before = {name: table_rows(store, name) for name in tables}
    with store.connection() as c:
        c.execute("BEGIN")
        records = WorkspaceDiscovery(store).register_user_request(f'Inspect "{roots[0]}"', connection=c)
        assert len(records) == 1 and records[0]["grants_tool_access"] is False
        c.commit()
    assert {name: table_rows(store, name) for name in tables} == before


def test_replay_and_conflicting_request_id_do_not_scan_new_paths(tmp_path, monkeypatch):
    store, router, roots = setup_projects(tmp_path)
    router.provider = MockProvider(lambda *_: response(conversational_decision()))
    text = f'Inspect "{roots[0]}"'
    original = asyncio.run(router.handle_user_message(text, request_id="stable"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Replay/conflict must precede discovery")

    monkeypatch.setattr(WorkspaceDiscovery, "register_user_request", forbidden)
    assert asyncio.run(router.handle_user_message(text, request_id="stable")) == original
    with pytest.raises(DomainError, match="different input"):
        asyncio.run(router.handle_user_message(f'Inspect "{roots[1]}"', request_id="stable"))
    assert {item["root"] for item in WorkspaceDiscovery(store).list_projects()} == {str(roots[0])}
    assert len(router.provider.requests) == 1


@pytest.mark.parametrize("case", ["missing", "relative", "multiple", "duplicate"])
def test_named_project_registration_keeps_parser_identity_and_ambiguity(tmp_path, case):
    store, _, roots = setup_projects(tmp_path)
    text = {"missing": f'Inspect "{tmp_path / "absent"}"', "relative": 'Inspect "project_alpha"',
            "multiple": f'Compare "{roots[0]}" and "{roots[1]}"',
            "duplicate": f'Inspect "{roots[0]}" and "{roots[0]}"'}[case]
    with store.connection() as c:
        c.execute("BEGIN")
        records = WorkspaceDiscovery(store).register_user_request(text, connection=c)
        c.commit()
    assert len(records) == {"missing": 0, "relative": 0, "multiple": 2, "duplicate": 1}[case]
    assert table_rows(store, "task_project_links") == []


def test_current_user_text_only_and_model_selected_path_does_not_register(tmp_path):
    store, router, roots = setup_projects(tmp_path)
    router.provider = MockProvider(lambda *_: response({**conversational_decision("chat"),
                                                       "reply": f'I found "{roots[1]}"'}))
    asyncio.run(router.handle_user_message(f'Inspect "{roots[0]}"', request_id="scope"))
    assert {p["root"] for p in WorkspaceDiscovery(store).list_projects()} == {str(roots[0])}


def test_registration_rolls_back_with_snapshot_transaction(tmp_path, monkeypatch):
    store, router, roots = setup_projects(tmp_path)

    def failed_snapshot(*_args):
        raise DomainError("Injected snapshot failure")

    monkeypatch.setattr(router, "_snapshot", failed_snapshot)
    with pytest.raises(DomainError, match="snapshot failure"):
        asyncio.run(router.handle_user_message(f'Inspect "{roots[0]}"', request_id="rollback"))
    assert WorkspaceDiscovery(store).list_projects() == []


def generous_approved_pending(tmp_path, monkeypatch):
    original_plan = reports.QuestGraphService.propose
    original_team = reports.TeamCompositionService.propose

    def plan(self, quest_id, proposal, **kwargs):
        for node in proposal["nodes"]:
            node["budget_tokens"] = 500000
        proposal["max_total_tokens"] = 500000 * len(proposal["nodes"])
        return original_plan(self, quest_id, proposal, **kwargs)

    def team(self, quest_id, roles, limits, **kwargs):
        for role in roles:
            role["input_tokens"] = 200000
        limits["max_total_tokens"] = 203000 * len(roles)
        return original_team(self, quest_id, roles, limits, **kwargs)

    monkeypatch.setattr(reports.QuestGraphService, "propose", plan)
    monkeypatch.setattr(reports.TeamCompositionService, "propose", team)
    return pending(tmp_path, monkeypatch, successor=False, raw_oversize=False,
                   context_window=100000000)


def test_legacy_complete_packet_over_65k_uses_native_model_and_original_cumulative_admission(
    tmp_path, monkeypatch
):
    store, manager, model, worker, packet, _ = generous_approved_pending(tmp_path, monkeypatch)
    original = _json(packet)
    assert len(original.encode()) > 65536
    assert "review_envelope" not in packet["contract"]
    before = manager.get(worker["id"])
    model.responses.append(worker_response(PASS))
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    request = model.requests[-1]
    measured = _review_input_reservation(model, request, 0)
    assert result["verdict"] == "PASS" and result["review_context"] == "independent_same_model"
    assert request.max_output_tokens <= before["output_limit"]
    assert measured + request.max_output_tokens <= before["model_context_window"]
    assert measured + request.max_output_tokens <= before["token_limit"] - before["token_spent"]
    with store.connection() as c:
        c.execute("BEGIN")
        row = c.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        assert _json(_packet(store, c, row)) == original
    after = manager.get(worker["id"])
    for key in ("token_limit", "step_limit", "tool_limit", "output_limit", "model_context_window"):
        assert after[key] == before[key]


def test_legacy_preview_reports_native_headroom_without_invented_raw_cap(tmp_path, monkeypatch):
    store, _manager, model, worker, _packet, _ = generous_approved_pending(tmp_path, monkeypatch)
    with store.connection() as c:
        c.execute("BEGIN")
        row = c.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        before = c.total_changes
        observation = review_capacity_preview(store, c, row, model)
        assert c.total_changes == before
    assert observation["complete"] and observation["raw_packet_bytes"] > 65536
    assert observation["raw_packet_cap"] is None and observation["raw_headroom"] is None
    assert observation["context_headroom"] > 0 and observation["budget_headroom"] > 0


def test_legacy_actual_context_shortage_still_never_admits_model_review(tmp_path, monkeypatch):
    _store, manager, model, worker, _, _ = pending(
        tmp_path, monkeypatch, explanation_repeats=1200,
        context_window=50000, raw_oversize=False, successor=False,
    )
    before = len(model.requests)
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["verdict"] == "BLOCKED"
    assert result["review_context"] == "deterministic_review_capacity"
    assert result["capacity_diagnostics"]["native_input_reservation"] > 50000
    assert len(model.requests) == before


def test_explicit_envelope_retains_raw_and_component_contract_limits():
    from forge_core.independent_review import _review_raw_packet_cap
    from forge_core.review_envelope import ReviewEnvelopeError, validate_review_packet

    envelope = {"version": "review-envelope-v1", "max_source_bytes": 1024,
                "max_artifact_bytes": 1024, "max_delivery_bytes": 1024,
                "max_review_input_tokens": 4096, "verdict_tokens": 1024,
                "correction_input_tokens": 4096, "correction_output_tokens": 1024,
                "correction_calls": 1}
    packet = {"contract": {"review_envelope": envelope}, "receipts": [], "artifacts": [],
              "delivery": "x" * 2000}
    assert _review_raw_packet_cap(packet) == 65536
    with pytest.raises(ReviewEnvelopeError, match="review_delivery_bytes_envelope_exceeded"):
        validate_review_packet(packet, envelope)
    assert _review_raw_packet_cap({"contract": {}}) is None
