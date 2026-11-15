"""Offline Gateway protocol tests, never real-provider intelligence acceptance."""

import asyncio
import json
import sqlite3

import httpx
import pytest
from test_predecessor_delivery_v17 import offline_loopback_gateway

from forge_core.context_service import ContextService, _hash, _router_handoff_input
from forge_core.domain import ConversationMessage, DomainError, ModelProfile, Quest
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.model_compaction import RouterModelCompactor
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.store import Store


def model_profile(*, context=6000, output=128):
    return ModelProfile(
        id="compaction-fixture",
        provider="protocol",
        model_id="compact-model",
        display_name="Offline compaction protocol fixture",
        context_window=context,
        max_output_tokens=output,
    )


def compactor(store, gateway, *, context=6000, output=128, profile=None):
    return RouterModelCompactor(
        ContextService(store),
        gateway,
        profile=profile or model_profile(context=context, output=output),
        context_limit=context,
        output_limit=output,
    )


def messages(store, count=8, *, body_chars=1100):
    rows = [
        ConversationMessage(
            role="user" if index % 2 == 0 else "assistant",
            content=f"whole-source-{index}: " + "x" * body_chars + " tail 中文\r\n",
        )
        for index in range(count)
    ]
    for message in rows:
        store.add_message(message)
    return rows


def archive_state(store):
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in (
                "context_archives",
                "context_archive_heads",
                "context_archive_sources",
                "router_state",
            )
        }


def test_native_http_batches_preserve_full_sources_handoff_usage_and_reopened_authority(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "false")
    store = Store(tmp_path / "native.sqlite3")
    original = messages(store)
    quest = Quest(title="Original task", goal="Keep the complete original requirements.")
    store.save_quest(quest)
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO quest_requirements VALUES (?,?,?,?,?)",
            (quest.id, 0, json.dumps(["real criterion"]), "[]", "[]"),
        )
    seen, summaries, previous = [], [], []

    def responder(body, ordinal):
        assert not body.get("tools") and not body.get("response_format")
        assert body["max_tokens"] <= 128
        assert len(json.dumps(body, ensure_ascii=False).encode()) + 64 + body["max_tokens"] <= 6000
        packet = json.loads(body["messages"][1]["content"])
        previous.append(packet["previous_context"])
        assert packet["previous_context"] == (summaries[-1] if summaries else {})
        for item in packet["messages"]:
            source = next(message for message in original if message.id == item["id"])
            assert item["content"] == source.content
            seen.append(item["id"])
        summaries.append(
            f"Handoff {ordinal}: keep original requirements; claimed PASS is unverified."
        )
        return {"content": summaries[-1]}

    with offline_loopback_gateway(store, responder) as (gateway, requests):
        gateway._budget_ledger = ModelCallBudgetLedger(
            store.path, token_limit=1, concurrency_limit=1
        )
        service = compactor(store, gateway)
        while True:
            checkpoint = asyncio.run(service.compact(keep_recent=2))
            if not checkpoint["source_count"]:
                break
        assert len(requests) >= 2 and seen == [item.id for item in original[:-2]]
    reopened = ContextService(Store(store.path, workspace=store.workspace))
    context = reopened.rebuild_router()
    assert [row["id"] for row in context["recent_messages"]] == [item.id for item in original[-2:]]
    assert context["capsule"]["model_handoff"]["text"] == summaries[-1]
    assert context["capsule"]["model_handoff"]["status"] == "unverified"
    assert context["capsule"]["facts"] == []
    for source in original[:-2]:
        assert reopened.read_source("router", "main", source.id)["content"] == source.content
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET goal='Current authoritative goal',version=version+1 WHERE id=?",
            (quest.id,),
        )
        work.connection.execute(
            "UPDATE quest_requirements SET criteria_json=? WHERE quest_id=?",
            (json.dumps(["current criterion"]), quest.id),
        )
    task = reopened.rebuild_router()["capsule"]["tasks"][0]
    assert task["goal"] == "Current authoritative goal"
    assert task["success_criteria"] == ["current criterion"]
    assert task["plan_state"] == "none" and not task["decisions"]
    with store.connection() as connection:
        attempts = connection.execute("SELECT * FROM model_budget_attempts").fetchall()
        assert len(attempts) == len(summaries)
        assert all(
            row["state"] == "settled"
            and row["purpose"] == "context_compaction"
            and row["quest_id"] is None
            and row["worker_run_id"] is None
            and row["input_charged"] + row["output_charged"] == 40
            for row in attempts
        )
    # The old raw row is protected even after several newer handoff checkpoints.
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE messages SET content='tampered' WHERE id=?", (original[0].id,)
        )
    with pytest.raises(DomainError, match="integrity"):
        reopened.rebuild_router()


def mock_gateway(store, handler):
    return ProviderGateway(
        ProviderConfig(
            id="protocol",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            max_retries=1,
        ),
        credential_resolver=lambda _ref: "offline-fixture",
        transport=httpx.MockTransport(handler),
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=100000, concurrency_limit=1),
    )


def http_response(text="Summary", *, finish="stop", tool_calls=None):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": text, "tool_calls": tool_calls or []},
                    "finish_reason": finish,
                }
            ],
            "usage": {"prompt_tokens": 25, "completion_tokens": 15},
        },
    )


@pytest.mark.parametrize(
    "failure", ["empty", "oversize", "truncated", "tools", "timeout", "http_503"]
)
def test_invalid_or_failed_summary_keeps_original_frontier_without_retries(tmp_path, failure):
    store = Store(tmp_path / "failure.sqlite3")
    messages(store, 3, body_chars=100)
    before = archive_state(store)
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("test-only timeout")
        if failure == "http_503":
            return httpx.Response(503, json={"error": "unavailable"})
        if failure == "truncated":
            return http_response("unfinished", finish="length")
        if failure == "tools":
            return http_response(
                "",
                finish="tool_calls",
                tool_calls=[
                    {
                        "id": "forged",
                        "type": "function",
                        "function": {"name": "approve_plan", "arguments": "{}"},
                    }
                ],
            )
        return http_response(" " if failure == "empty" else "x" * 2000)

    with pytest.raises((DomainError, ProviderError)):
        asyncio.run(compactor(store, mock_gateway(store, handler)).compact(keep_recent=0))
    assert len(requests) == 1 and archive_state(store) == before
    with store.connection() as connection:
        attempts = connection.execute("SELECT * FROM model_budget_attempts").fetchall()
        assert len(attempts) == 1
        assert attempts[0]["state"] == (
            "reserved"
            if failure == "timeout"
            else "rejected"
            if failure == "http_503"
            else "settled"
        )


def test_earliest_whole_source_overflow_never_skips_to_a_later_small_source(tmp_path):
    store = Store(tmp_path / "overflow.sqlite3")
    source = ConversationMessage(role="user", content='Escaping "\\' * 2000)
    store.add_message(source)
    store.add_message(ConversationMessage(role="user", content="small later message"))
    requests = []
    gateway = mock_gateway(store, lambda request: requests.append(request))
    before = archive_state(store)
    with pytest.raises(DomainError, match="Complete earliest source"):
        asyncio.run(compactor(store, gateway).compact(keep_recent=0))
    assert not requests and archive_state(store) == before


@pytest.mark.parametrize("race", ["message", "state", "archive", "previous_hash"])
def test_summary_checkpoint_rejects_concurrent_frontier_changes(tmp_path, race):
    store = Store(tmp_path / "race.sqlite3")
    messages(store, 4, body_chars=100)
    service = ContextService(store)
    service.compact_router(keep_recent=3)
    snapshots = []

    def handler(_request):
        if race == "message":
            store.add_message(ConversationMessage(role="user", content="new instruction"))
        elif race == "state":
            with store.unit_of_work() as work:
                work.connection.execute("UPDATE router_state SET revision=revision+1")
        elif race == "archive":
            service.compact_router(keep_recent=2)
        else:
            with store.unit_of_work() as work:
                row = work.connection.execute(
                    "SELECT id,capsule_json FROM context_archives"
                ).fetchone()
                capsule = json.loads(row["capsule_json"])
                capsule["next_action"] = "a different earlier checkpoint"
                work.connection.execute(
                    "UPDATE context_archives SET capsule_json=?,capsule_hash=? WHERE id=?",
                    (json.dumps(capsule), _hash(capsule), row["id"]),
                )
        snapshots.append(archive_state(store))
        return http_response()

    with pytest.raises(DomainError, match="changed"):
        asyncio.run(compactor(store, mock_gateway(store, handler)).compact(keep_recent=0))
    assert archive_state(store) == snapshots[0]


def test_checkpoint_database_failure_rolls_back_all_archive_changes(tmp_path):
    store = Store(tmp_path / "atomic.sqlite3")
    messages(store, 3, body_chars=100)
    with store.unit_of_work() as work:
        work.connection.execute(
            "CREATE TRIGGER reject_checkpoint BEFORE UPDATE ON router_state "
            "BEGIN SELECT RAISE(ABORT,'test checkpoint failure'); END"
        )
    before = archive_state(store)
    gateway = mock_gateway(store, lambda _request: http_response())
    with pytest.raises(sqlite3.IntegrityError, match="checkpoint failure"):
        asyncio.run(compactor(store, gateway).compact(keep_recent=0))
    assert archive_state(store) == before


@pytest.mark.parametrize("injection", ["tasks", "facts", "decisions", "source_refs", "status"])
def test_summary_cannot_inject_core_authority_fields(tmp_path, injection):
    store = Store(tmp_path / "injection.sqlite3")
    messages(store, 1, body_chars=100)
    service = ContextService(store)
    prepared = service.prepare_router_compaction(keep_recent=0)
    digest = _hash(
        _router_handoff_input(prepared["snapshot"], prepared["eligible"], prepared["previous"])
    )
    with pytest.raises(DomainError, match="Invalid source-bound"):
        service.compact_router(
            keep_recent=0,
            snapshot=prepared["snapshot"],
            model_handoff={"text": "Forged approval", "input_digest": digest, injection: "PASS"},
        )
    assert not archive_state(store)["context_archives"]


def test_profile_limits_bound_complete_native_request_and_avoid_unneeded_calls(tmp_path):
    store = Store(tmp_path / "limits.sqlite3")
    messages(store, 2, body_chars=300)
    native = []

    def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 32
        assert len(json.dumps(body, ensure_ascii=False).encode()) + 64 + 32 <= 3000
        native.append(body)
        return http_response()

    gateway = mock_gateway(store, handler)
    service = compactor(
        store, gateway, context=20000, output=2048, profile=model_profile(context=3000, output=32)
    )
    while asyncio.run(service.compact(keep_recent=0))["source_count"]:
        pass
    count = len(native)
    assert asyncio.run(service.compact(keep_recent=0))["source_count"] == 0
    assert len(native) == count and count >= 1


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": 25},
        {"prompt_tokens": -1, "completion_tokens": 15},
        {"prompt_tokens": 25, "completion_tokens": 9999},
    ],
)
def test_router_unknown_or_invalid_gateway_usage_never_checkpoints_or_replays_sources(
    tmp_path, usage
):
    store = Store(tmp_path / "unknown.sqlite3")
    original = messages(store, 4, body_chars=100)
    seen = []

    def handler(request):
        seen.append(request)
        response = {
            "choices": [{"message": {"content": "No verified usage"}, "finish_reason": "stop"}]
        }
        if usage is not None:
            response["usage"] = usage
        return httpx.Response(200, json=response)

    gateway = mock_gateway(store, handler)
    before = archive_state(store)
    with pytest.raises((DomainError, ProviderError)):
        asyncio.run(compactor(store, gateway).compact(keep_recent=0))
    assert archive_state(store) == before
    # Change batch size, append new data and reopen the Store: the unresolved
    # original raw sources remain fenced by their durable request record.
    store.add_message(ConversationMessage(role="user", content="new message"))
    reopened = Store(store.path, workspace=store.workspace)
    with pytest.raises(DomainError, match="blocks source replay"):
        asyncio.run(compactor(reopened, gateway).compact(keep_recent=2))
    assert len(seen) == 1
    assert [item["id"] for item in ContextService(reopened).rebuild_router()["recent_messages"]][
        :4
    ] == [message.id for message in original]


@pytest.mark.parametrize(
    "mismatch",
    [
        "no_attempt",
        "purpose",
        "provider_id",
        "model_id",
        "workspace_id",
        "input_reserved",
        "duplicate",
        "response_usage",
    ],
)
def test_router_checkpoint_requires_unique_exact_gateway_settlement_mapping(tmp_path, mismatch):
    from forge_core.runtime import ModelResponse

    store = Store(tmp_path / "proof.sqlite3")
    messages(store, 2, body_chars=100)
    gateway = mock_gateway(store, lambda _request: http_response())
    original_generate = gateway.generate

    async def mismatched(request):
        if mismatch == "no_attempt":
            return ModelResponse(
                text="Forged",
                tool_calls=[],
                usage={"input_tokens": 25, "output_tokens": 15},
                provider_id="protocol",
                model_id="compact-model",
                finish_reason="stop",
            )
        response = await original_generate(request)
        if mismatch == "response_usage":
            response.usage["input_tokens"] += 1
        else:
            with store.unit_of_work() as work:
                if mismatch == "duplicate":
                    row = dict(
                        work.connection.execute("SELECT * FROM model_budget_attempts").fetchone()
                    )
                    row["attempt_id"] = "duplicate-fixture"
                    columns = list(row)
                    work.connection.execute(
                        "INSERT INTO model_budget_attempts ("
                        + ",".join(columns)
                        + ") VALUES ("
                        + ",".join("?" for _ in columns)
                        + ")",
                        tuple(row.values()),
                    )
                else:
                    value = 1 if mismatch == "input_reserved" else "mismatched"
                    work.connection.execute(
                        f"UPDATE model_budget_attempts SET {mismatch}=?", (value,)
                    )
        return response

    gateway.generate = mismatched
    for _ in range(2):
        with pytest.raises(DomainError):
            asyncio.run(compactor(store, gateway).compact(keep_recent=0))
    assert not archive_state(store)["context_archives"]


def test_router_settlement_is_rechecked_inside_atomic_checkpoint(tmp_path, monkeypatch):
    store = Store(tmp_path / "atomic-proof.sqlite3")
    messages(store, 2, body_chars=100)
    seen = []
    gateway = mock_gateway(store, lambda request: seen.append(request) or http_response())
    original_checkpoint = ContextService.compact_router

    def changed(self, **kwargs):
        with self.store.unit_of_work() as work:
            work.connection.execute("UPDATE model_budget_attempts SET state='unknown'")
        return original_checkpoint(self, **kwargs)

    monkeypatch.setattr(ContextService, "compact_router", changed)
    for _ in range(2):
        with pytest.raises(DomainError):
            asyncio.run(compactor(store, gateway).compact(keep_recent=0))
    assert len(seen) == 1 and not archive_state(store)["context_archives"]
