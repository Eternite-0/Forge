"""Offline explicit review-cycle contracts over native local approvals/HTTP."""

import asyncio
import copy
import hashlib
import json

import httpx
import pytest
from test_worker_intelligence_phase2 import delivery, prepared, response, tool

from forge_core.domain import DomainError, ForgeEvent
from forge_core.independent_review import (
    IndependentReviewService,
    _hash,
    _json,
    _legacy_review_messages,
    _packet,
    _review_assignment,
    _review_messages,
    _review_reservation,
    _ReviewBoundaryError,
    measure_review_envelope,
    review_pass_matches,
)
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.quest_graph import QuestGraphService
from forge_core.review_envelope import (
    ReviewEnvelopeError,
    project_review_text,
    restore_review_text,
    review_cycle_reserve,
    review_cycle_state,
    validate_review_envelope,
    validate_review_packet,
)
from forge_core.runtime import ModelRequest
from forge_core.team_composition import TeamComposerEngine, TeamCompositionService


def envelope(**updates):
    return {
        "version": "review-envelope-v1",
        "max_source_bytes": 10000,
        "max_artifact_bytes": 3072,
        "max_delivery_bytes": 1536,
        "max_review_input_tokens": 16000,
        "verdict_tokens": 1024,
        "correction_input_tokens": 12000,
        "correction_output_tokens": 3000,
        "correction_calls": 1,
        **updates,
    }


def new_pending(
    tmp_path, monkeypatch, *, env=None, responses=None, tools=None, criteria=None, budget=59000
):
    env = envelope() if env is None else env
    original_candidate = TeamComposerEngine._candidate
    original_propose = TeamCompositionService.propose
    from forge_core.quest_graph import QuestGraphService

    original_plan = QuestGraphService.propose

    def plan(self, quest_id, graph, **kwargs):
        graph = copy.deepcopy(graph)
        graph["max_total_tokens"] = max(60000, budget)
        graph["nodes"][0]["budget_tokens"] = max(60000, budget)
        return original_plan(self, quest_id, graph, **kwargs)

    def candidate(self, *args, **kwargs):
        result = original_candidate(self, *args, **kwargs)
        result["review_envelope"] = copy.deepcopy(env)
        source = (tmp_path / "work/input.txt").read_bytes()
        result["required_input_paths"] = ["work/input.txt"]
        result["verified_input_inventory"] = [
            {
                "path": "work/input.txt",
                "bytes": len(source),
                "sha256": hashlib.sha256(source).hexdigest(),
            }
        ]
        return result

    def propose(self, quest_id, roles, limits, **kwargs):
        # A new normal proposal binds the envelope before digest/approval.
        # New approval chooses its original allowance before any execution.
        # No existing run, historical evidence, or live scenario cap is changed.
        roles = copy.deepcopy(roles)
        for role in roles:
            role["input_tokens"] = budget - role["output_tokens"]
        limits = {**limits, "max_total_tokens": budget}
        return original_propose(self, quest_id, roles, limits, **kwargs)

    monkeypatch.setattr(TeamComposerEngine, "_candidate", candidate)
    monkeypatch.setattr(TeamCompositionService, "propose", propose)
    monkeypatch.setattr(QuestGraphService, "propose", plan)
    store, manager, model, worker = prepared(
        tmp_path,
        responses or [tool("file_read", {"path": "work/input.txt"}), response(delivery())],
        tools=tools,
        criteria=criteria,
    )
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    return store, manager, model, worker


@pytest.mark.parametrize(
    "mutation", ["missing", "unknown", "bool", "negative", "version", "context", "small_verdict"]
)
def test_envelope_requires_explicit_valid_bounded_policy(mutation):
    value = envelope()
    if mutation == "missing":
        value.pop("max_delivery_bytes")
    elif mutation == "unknown":
        value["invented"] = 1
    elif mutation == "bool":
        value["correction_calls"] = True
    elif mutation == "negative":
        value["max_artifact_bytes"] = -1
    elif mutation == "version":
        value["version"] = "invented"
    elif mutation == "context":
        value["max_review_input_tokens"] = 65537
    else:
        value["verdict_tokens"] = 1
    with pytest.raises(ReviewEnvelopeError):
        validate_review_envelope(value)


def test_cycle_policy_counts_remaining_stages_without_charging():
    env = envelope(correction_calls=2)
    assert review_cycle_reserve(env) == 2 * (16000 + 1024) + 2 * (12000 + 3000)
    assert review_cycle_reserve(env, reviews_remaining=1, correction_calls_remaining=1) == 32024
    assert review_cycle_reserve(env, reviews_remaining=0, correction_calls_remaining=0) == 0
    with pytest.raises(ReviewEnvelopeError):
        review_cycle_reserve(env, reviews_remaining=True)


def test_lossless_projection_handles_unicode_escaping_and_hostile_reference_objects():
    text = '完整来源 "quoted" \\ original\n' * 200
    packet = {
        "contract": {"review_envelope": envelope()},
        "delivery": {"evidence": text},
        "receipts": [{"output": {"content": text}}],
        "artifacts": [{"content": {"$review_text": 0, "$review_text_": 9, "source": text}}],
    }
    original = _json(packet)
    view, texts = project_review_text(packet)
    assert view["reference_key"] == "$review_text__" and texts == [text]
    assert _json(restore_review_text(view, texts)) == original
    system = {"role": "system", "content": "Complete independent review"}
    messages = _review_messages(packet, system)
    assert len(json.dumps(messages, ensure_ascii=False).encode()) < len(
        json.dumps([system, {"role": "user", "content": original}], ensure_ascii=False).encode()
    )
    assert _json(packet) == original
    assert _hash(packet) == hashlib.sha256(original.encode()).hexdigest()


def test_legacy_wire_and_packet_keep_the_same_fields_and_hash(tmp_path):
    store, manager, _model, worker = prepared(
        tmp_path, [tool("file_read", {"path": "work/input.txt"}), response(delivery())]
    )
    asyncio.run(manager.run(worker["id"]))
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        packet = _packet(store, connection, row)
        assert "review_envelope" not in packet["contract"]
        before = _json(packet), _hash(packet)
        system = {"role": "system", "content": "Legacy unchanged"}
        assert _review_messages(packet, system) == _legacy_review_messages(packet, system)
        assert (
            _json(_packet(store, connection, row)),
            _hash(_packet(store, connection, row)),
        ) == before


def test_packet_limits_count_complete_sources_artifacts_and_delivery_in_bytes():
    packet = {
        "receipts": [{"output": {"content": "中文" * 300}}],
        "artifacts": [{"content": {"x": "y" * 1000}}],
        "delivery": delivery(),
    }
    env = envelope()
    sizes = validate_review_packet(packet, env)
    assert sizes["source_bytes"] > 1800
    for component in sizes:
        with pytest.raises(ReviewEnvelopeError) as caught:
            validate_review_packet(packet, {**env, "max_" + component: sizes[component] - 1})
        assert caught.value.code == "review_" + component + "_envelope_exceeded"


def test_unparsed_artifact_size_is_explicit_and_unknown_is_rejected():
    packet = {"receipts": [], "delivery": delivery(), "artifacts": [{"format_error": {}}]}
    with pytest.raises(ReviewEnvelopeError, match="review_artifact_size_unknown"):
        validate_review_packet(packet, envelope())
    packet["artifacts"][0]["canonical_content_bytes"] = 3073
    with pytest.raises(ReviewEnvelopeError, match="review_artifact_bytes_envelope_exceeded"):
        validate_review_packet(packet, envelope())


def test_new_envelope_never_shrinks_its_complete_verdict_allowance():
    from test_worker_intelligence_phase2 import ProtocolModel

    provider = ProtocolModel([])
    request = ModelRequest(model_id="fixture", messages=[], max_output_tokens=3000)
    row = {"model_context_window": 1000, "token_limit": 59000, "token_spent": 0}
    with pytest.raises(_ReviewBoundaryError, match="bounded input limit"):
        _review_reservation(provider, request, row, 0, envelope=envelope(verdict_tokens=2048))


def test_native_input_bound_rejects_before_call_admission_or_counter_change(tmp_path, monkeypatch):
    env = envelope(max_review_input_tokens=1)
    store, manager, model, worker = new_pending(tmp_path, monkeypatch, env=env)
    posted = []
    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(lambda request: posted.append(request)),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=200000, concurrency_limit=1),
    )
    manager.providers = ProviderPool({"protocol": gateway})
    before = manager.get(worker["id"])
    with pytest.raises(ReviewEnvelopeError, match="review_native_input_envelope_exceeded"):
        asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert manager.get(worker["id"]) == before and not posted and len(model.requests) == 2
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM model_budget_attempts").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT count(*) FROM events WHERE event_type='review.started'"
            ).fetchone()[0]
            == 0
        )


def test_malformed_artifact_new_contract_counts_complete_bytes_and_keeps_syntax_repair(
    tmp_path, monkeypatch
):
    env = envelope(correction_calls=2)
    content = '{"value":'
    store, manager, model, worker = new_pending(
        tmp_path,
        monkeypatch,
        env=env,
        budget=99000,
        responses=[
            tool("file_write", {"path": "work/result.json", "content": content}),
            response(delivery()),
        ],
        tools=["file_write", "file_revise"],
        criteria=["json_review:work/result.json"],
    )
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        packet = _packet(store, connection, row, parse_diagnostics=True)
        assert packet["artifacts"][0]["canonical_content_bytes"] == len(_json(content).encode())
        assert validate_review_packet(packet, env)["artifact_bytes"] > len(content)
    result = asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert result["review_context"] == "deterministic_artifact_validation"
    assert result["revision_applied"] and len(model.requests) == 2


def test_native_artifact_revision_and_delivery_consume_two_bounded_correction_calls(
    tmp_path, monkeypatch
):
    env = envelope(correction_calls=2, correction_input_tokens=20000)
    store, manager, _model, worker = new_pending(
        tmp_path,
        monkeypatch,
        env=env,
        budget=99000,
        responses=[
            tool("file_write", {"path": "work/result.json", "content": '{"value":1}'}),
            response(delivery("Artifact work/result.json contains the complete result.")),
        ],
        tools=["file_write", "file_revise"],
        criteria=["json_review:work/result.json"],
    )
    before = manager.get(worker["id"])
    posted, stages = [], []

    def handle(request):
        body = json.loads(request.content)
        posted.append(body)
        ordinal = len(posted)
        if ordinal == 1:
            message = {
                "content": _json(
                    {
                        "verdict": "NEEDS_REVISION",
                        "findings": ["Value is wrong."],
                        "revision_goals": ["Change value to 2 and submit a complete delivery."],
                        "confidence": 0.8,
                    }
                )
            }
        elif ordinal == 2:
            references = next(
                item["content"]
                for item in reversed(body["messages"])
                if item.get("content", "").startswith("Core artifact receipt references")
            )
            source = json.loads(references.split(": ", 1)[1])[0]
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "revise-1",
                        "type": "function",
                        "function": {
                            "name": "file_revise",
                            "arguments": _json(
                                {
                                    "path": "work/result.json",
                                    "prior_receipt_id": source["receipt_id"],
                                    "expected_sha256": source["sha256"],
                                    "content": '{"value":2}',
                                }
                            ),
                        },
                    }
                ],
            }
        elif ordinal == 3:
            with store.connection() as connection:
                stages.append(review_cycle_state(connection, worker["id"], env))
            message = {"content": _json(delivery("Corrected result is in work/result.json."))}
        else:
            assert ordinal == 4
            assert not body.get("tools")
            assert '"value":2' in body["messages"][-1]["content"]
            message = {
                "content": _json(
                    {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
                )
            }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": message, "finish_reason": "tool_calls" if ordinal == 2 else "stop"}
                ],
                "usage": {"prompt_tokens": 25, "completion_tokens": 15},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(handle),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=200000, concurrency_limit=1),
    )
    manager.providers = ProviderPool({"protocol": gateway})
    service = IndependentReviewService(manager)
    first = asyncio.run(service.review(worker["id"]))
    assert first["verdict"] == "NEEDS_REVISION" and first["revision_applied"]
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    assert json.loads((tmp_path / "work/result.json").read_text()) == {"value": 2}
    # During the second correction's admitted request its stage is consumed;
    # the remaining independent review stays reserved.
    assert stages[0]["correction_calls_remaining"] == 0
    assert stages[0]["reviews_remaining"] == 1
    final = asyncio.run(service.review(worker["id"]))
    assert final["verdict"] == "PASS" and final["packet_sha256"] != first["packet_sha256"]
    after = manager.get(worker["id"])
    assert after["token_limit"] == before["token_limit"] == 99000
    assert after["token_spent"] == before["token_spent"] + 160
    assert after["steps"] == before["steps"] + 4 and after["repairs"] == 1
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM action_receipts").fetchone()[0] == 2
        native = connection.execute("SELECT * FROM model_budget_attempts").fetchall()
        assert len(native) == 4 and all(row["state"] == "settled" for row in native)
        assert review_cycle_state(connection, worker["id"], env)["future_reserve_tokens"] == 0
        assert not review_pass_matches(store, connection, after, "json_review:work/result.json")


def test_native_review_then_revision_then_fresh_pass_within_original_cycle(tmp_path, monkeypatch):
    store, manager, _model, worker = new_pending(tmp_path, monkeypatch)
    original = manager.get(worker["id"])
    posted = []
    reviewed = []

    def handle(request):
        body = json.loads(request.content)
        posted.append(body)
        if body.get("tools"):
            message = delivery("Inspected complete work/input.txt; unchanged evidence retained.")
        else:
            reviewed.append(body)
            message = (
                {
                    "verdict": "NEEDS_REVISION",
                    "findings": ["Clarify the exact inspected source."],
                    "revision_goals": [
                        "State the complete inspected source name and preserve evidence."
                    ],
                    "confidence": 0.8,
                }
                if len(reviewed) == 1
                else {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
            )
            assert body["max_tokens"] == 1024
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": _json(message)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 15},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
        ),
        credential_resolver=lambda _: "fixture",
        transport=httpx.MockTransport(handle),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=200000, concurrency_limit=1),
    )
    manager.providers = ProviderPool({"protocol": gateway})
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        packet = _packet(store, connection, row)
        old_hash = _hash(packet)
        measured = measure_review_envelope(connection, row, gateway, packet)
        assert measured["native_input_reservation"] < envelope()["max_review_input_tokens"]
        assert measured["raw_packet_bytes"] < 65536
        assert connection.total_changes == 0
        assert review_cycle_state(connection, worker["id"], envelope())["reviews_remaining"] == 2
    service = IndependentReviewService(manager)
    first = asyncio.run(service.review(worker["id"]))
    assert first["verdict"] == "NEEDS_REVISION" and first["revision_applied"]
    with store.connection() as connection:
        stage = review_cycle_state(connection, worker["id"], envelope())
        assert stage["reviews_remaining"] == stage["correction_calls_remaining"] == 1
        assert stage["correction_started"]
    # The correction and both reviews use actual Gateway/ledger code over offline
    # HTTP. No manual attempt records or artificial usage settlement is supplied.
    assert asyncio.run(manager.run(worker["id"]))["state"] == "review_pending"
    manager.providers = ProviderPool({"protocol": gateway})
    with store.connection() as connection:
        connection.execute("BEGIN")
        stage = review_cycle_state(connection, worker["id"], envelope())
        assert stage["reviews_remaining"] == 1 and stage["correction_calls_remaining"] == 0
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        assert _hash(_packet(store, connection, row)) != old_hash
    final = asyncio.run(service.review(worker["id"]))
    assert final["verdict"] == "PASS" and final["packet_sha256"] != old_hash
    after = manager.get(worker["id"])
    assert after["token_limit"] == original["token_limit"] == 59000
    assert after["token_spent"] == original["token_spent"] + 120
    assert after["steps"] == original["steps"] + 3
    assert after["repairs"] == original["repairs"] + 1
    with store.connection() as connection:
        connection.execute("BEGIN")
        native = [dict(row) for row in connection.execute("SELECT * FROM model_budget_attempts")]
        assert len(native) == 3 and all(row["state"] == "settled" for row in native)
        assert native[0]["input_reserved"] == measured["native_input_reservation"]
        assert (
            review_cycle_state(connection, worker["id"], envelope())["future_reserve_tokens"] == 0
        )
        # MockTransport never becomes a live acceptance proof.
        assert not review_pass_matches(store, connection, after, "delivery_review")


def test_budget_for_one_review_cannot_consume_reserved_correction_and_second_review(
    tmp_path, monkeypatch
):
    store, manager, model, worker = new_pending(tmp_path, monkeypatch)
    # Synthetic previous settled spending isolates the new cycle-policy gate.
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET token_spent=token_limit-20000 WHERE id=?", (worker["id"],)
        )
    before = manager.get(worker["id"])
    with pytest.raises(ReviewEnvelopeError) as caught:
        asyncio.run(IndependentReviewService(manager).review(worker["id"]))
    assert caught.value.code == "review_cycle_budget_not_preserved"
    assert manager.get(worker["id"]) == before
    assert len(model.requests) == 2
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM events WHERE event_type='review.started'"
            ).fetchone()[0]
            == 0
        )


def test_unsent_review_release_retains_cycle_stage_without_resetting_usage(tmp_path, monkeypatch):
    store, manager, _model, worker = new_pending(tmp_path, monkeypatch)
    before = manager.get(worker["id"])
    for event in (
        ForgeEvent(
            event_type="review.started",
            worker_id=worker["worker_id"],
            payload={"run_id": worker["id"], "call_id": "unsent"},
        ),
        ForgeEvent(
            event_type="review.released",
            worker_id=worker["worker_id"],
            payload={"run_id": worker["id"], "call_id": "unsent", "reservation_refunded": True},
        ),
    ):
        store.append_event(event)
    with store.connection() as connection:
        stage = review_cycle_state(connection, worker["id"], envelope())
        assert stage["reviews_remaining"] == 2 and stage["correction_calls_remaining"] == 1
    assert manager.get(worker["id"]) == before


@pytest.mark.parametrize("mutation", [None, "goal", "inherited_state", "source_state", "preview"])
def test_approved_completed_lineage_rebuilds_exact_old_packet_with_normal_hash_guards(
    tmp_path, mutation
):
    store, manager, _model, worker = prepared(
        tmp_path, [tool("file_read", {"path": "work/input.txt"}), response(delivery())]
    )
    asyncio.run(manager.run(worker["id"]))
    # A state fixture isolates read-only lineage reconstruction. Mock execution
    # is deliberately never accepted as live completed-task or PASS evidence.
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='succeeded' WHERE id=?", (worker["id"],)
        )
        work.connection.execute(
            "UPDATE task_nodes SET status='succeeded' WHERE plan_id=?", (worker["plan_id"],)
        )
        work.connection.execute(
            "UPDATE workers SET status='succeeded' WHERE id=?", (worker["worker_id"],)
        )
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        original_hash = _hash(_packet(store, connection, row))
        original_run = dict(row)
        original_graph = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE id=?", (worker["plan_id"],)
            ).fetchone()[0]
        )
        version = connection.execute(
            "SELECT version FROM quests WHERE id=?", (worker["quest_id"],)
        ).fetchone()[0]
    graphs = QuestGraphService(store)
    proposed = graphs.propose_remaining(
        worker["quest_id"], original_graph, expected_version=version
    )
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT plan_revision FROM quests WHERE id=?", (worker["quest_id"],)
            ).fetchone()[0]
            == worker["plan_revision"]
        )
        version = connection.execute(
            "SELECT version FROM quests WHERE id=?", (worker["quest_id"],)
        ).fetchone()[0]
    graphs.approve(
        worker["quest_id"],
        proposed["digest"],
        proposed["plan_revision"],
        expected_version=version,
        actor="local_user",
    )
    with store.unit_of_work() as work:
        if mutation == "goal":
            work.connection.execute(
                "UPDATE quests SET goal='changed overall goal' WHERE id=?", (worker["quest_id"],)
            )
        elif mutation == "inherited_state":
            work.connection.execute(
                "UPDATE task_nodes SET status='failed' WHERE plan_id=?", (proposed["id"],)
            )
        elif mutation == "source_state":
            work.connection.execute(
                "UPDATE worker_runs SET state='review_pending' WHERE id=?", (worker["id"],)
            )
    with store.connection() as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (worker["id"],)).fetchone()
        if mutation == "preview":
            with pytest.raises(DomainError):
                _review_assignment(store, connection, row, preview=True)
        elif mutation:
            with pytest.raises(DomainError):
                _packet(store, connection, row)
        else:
            assert _hash(_packet(store, connection, row)) == original_hash
            assert dict(row) == original_run
        assert not review_pass_matches(store, connection, row, "delivery_review")
