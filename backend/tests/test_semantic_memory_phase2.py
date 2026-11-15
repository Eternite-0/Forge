"""Semantic selection is a budgeted model proposal over revalidated source IDs."""

import asyncio
import json
import sqlite3

import httpx
import pytest

from forge_core.domain import Quest
from forge_core.memory import MemoryService
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderError, ProviderGateway
from forge_core.runtime import ModelResponse
from forge_core.settings import Workspace
from forge_core.store import Store


class Selector:
    execution_mode = "mock"

    def __init__(self, select):
        self.select = select
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        candidates = json.loads(request.messages[1]["content"])["candidates"]
        result = self.select(candidates)
        if isinstance(result, Exception):
            raise result
        return ModelResponse(text=json.dumps(result), tool_calls=[], usage={}, finish_reason="mock")


@pytest.fixture
def memory(tmp_path):
    store = Store(tmp_path / "memory.sqlite3", workspace=Workspace.from_path(tmp_path))
    return MemoryService(store)


def call(memory, selector, query="avoid spilling confidential material", **options):
    return asyncio.run(
        memory.retrieve_semantic_context(
            query, provider=selector, model_id="mock-model", context_limit=32768, **options
        )
    )


def add(memory, statement, **extra):
    return memory.remember(
        statement,
        kind="decision",
        layer="L2",
        source_refs=["message:prior-verified-reference"],
        **extra,
    )


def test_semantic_zero_overlap_preserves_source_and_trust(memory):
    ident = add(memory, "Never disclose passwords or credentials in application logs.")
    add(memory, "Use a blue border for the activity panel.")
    assert memory.retrieve_context("avoid spilling confidential material")["items"] == []
    selector = Selector(lambda candidates: {"candidate_ids": ["memory:" + ident]})
    result = call(memory, selector)
    assert [item["id"] for item in result["items"]] == [ident]
    assert result["items"][0]["status"] == "hypothesis"
    assert result["items"][0]["source_refs"] == ["message:prior-verified-reference"]
    assert result["semantic"]["status"] == "selected"
    request = selector.requests[0]
    assert request.max_retries == 0
    assert request.budget_context.purpose == "memory_semantic_retrieval"
    assert request.budget_context.workspace_id == memory.store.workspace.id
    assert request.tools is None


@pytest.mark.parametrize(
    "returned",
    [
        {"candidate_ids": ["memory:invented"]},
        {"candidate_ids": [], "fact": "fabricated"},
        {"candidate_ids": "memory:bad"},
        {"candidate_ids": [1]},
        ["memory:bad"],
    ],
)
def test_model_cannot_invent_identity_or_add_text(memory, returned):
    add(memory, "one source")
    result = call(memory, Selector(lambda candidates: returned))
    assert result["items"] == []
    assert result["semantic"]["status"] == "invalid_response"


def test_duplicate_selection_is_rejected(memory):
    ident = "memory:" + add(memory, "one source")
    result = call(memory, Selector(lambda candidates: {"candidate_ids": [ident, ident]}))
    assert result["semantic"]["status"] == "invalid_response"


@pytest.mark.parametrize("change", ["revoke", "invalidate", "edit", "expire"])
def test_source_changes_during_await_are_not_returned(memory, change):
    ident = add(memory, "keep the confidential archive")

    def select(candidates):
        if change == "revoke":
            memory.revoke_source("message:prior-verified-reference")
        elif change == "invalidate":
            memory.invalidate(ident)
        else:
            with memory.store.unit_of_work() as work:
                if change == "edit":
                    work.connection.execute(
                        "UPDATE memories SET statement='changed source' WHERE id=?", (ident,)
                    )
                else:
                    work.connection.execute(
                        "UPDATE memories SET valid_until='2000-01-01T00:00:00+00:00' WHERE id=?",
                        (ident,),
                    )
        return {"candidate_ids": ["memory:" + ident]}

    result = call(memory, Selector(select))
    assert result["items"] == []
    assert result["semantic"]["dropped_changed"] == 1


def test_core_quest_mutation_after_selection_rejected(memory):
    quest = Quest(title="old experiment", goal="infer latency from retained measurements")
    memory.store.save_quest(quest)
    memory.sync_sources()

    def select(candidates):
        assert "quest:" + quest.id in candidates
        with memory.store.unit_of_work() as work:
            work.connection.execute("UPDATE quests SET goal='changed' WHERE id=?", (quest.id,))
        return {"candidate_ids": ["quest:" + quest.id]}

    result = call(memory, Selector(select))
    assert result["items"] == []
    assert result["semantic"]["dropped_changed"] == 1


@pytest.mark.parametrize("selected_id", ["quest_a", "quest_z"])
def test_exact_reference_preserves_selected_quest_across_sort_orders(memory, selected_id):
    for ident in ("quest_a", "quest_z"):
        memory.store.save_quest(Quest(id=ident, title="Quest indexed shared term", goal=ident))
    memory.sync_sources()
    ref = "quest:" + selected_id
    selected = memory.retrieve_context(
        "Quest",
        source_refs=[ref],
        quest_ids=["quest_a", "quest_z"],
        exact_source_refs=True,
        limit=1,
    )
    assert [item["id"] for item in selected["items"]] == [ref]
    assert selected["items"][0]["related_quest_ids"] == [selected_id]

    def choose(candidates):
        assert set(candidates) >= {"quest:quest_a", "quest:quest_z"}
        assert all(
            value["id"] == identity
            for identity, value in candidates.items()
            if identity.startswith("quest:")
        )
        return {"candidate_ids": [ref]}

    actual = call(memory, Selector(choose))
    assert actual["semantic"]["selected"] == 1
    assert [item["id"] for item in actual["items"]] == [ref]


def test_exact_reference_cannot_fall_back_to_other_quests(memory):
    memory.store.save_quest(Quest(id="quest_a", title="Quest retained", goal="shared query"))
    memory.store.save_quest(Quest(id="quest_z", title="Quest revoked", goal="shared query"))
    memory.sync_sources()
    memory.revoke_source("quest:quest_z")
    for ref in ("quest:quest_z", "quest:missing"):
        assert (
            memory.retrieve_context(
                "shared query", quest_ids=["quest_a"], source_refs=[ref], exact_source_refs=True
            )["items"]
            == []
        )
    assert (
        memory.retrieve_context("shared query", quest_ids=["quest_a"], exact_source_refs=True)[
            "items"
        ]
        == []
    )
    assert memory.retrieve_context("shared query", source_refs=["quest:missing"])["items"]
    with pytest.raises(ValueError, match="exact source selector"):
        memory.retrieve_context("query", exact_source_refs=1)


def test_expired_revoked_and_filter_mismatches_never_enter_prompt(memory):
    valid = add(memory, "keep it", related_quest_ids=["old-quest"])
    add(memory, "wrong quest", related_quest_ids=["other"])
    add(memory, "expired", valid_until="2000-01-01T00:00:00+00:00")
    selector = Selector(lambda candidates: {"candidate_ids": list(candidates)})
    result = call(memory, selector, quest_ids=["old-quest"])
    assert [x["id"] for x in result["items"]] == [valid]
    assert result["semantic"]["candidates"] == 1


def test_input_and_output_bounds_do_not_truncate_source(memory):
    ident = add(memory, "密码" * 900)
    selector = Selector(lambda candidates: {"candidate_ids": ["memory:" + ident]})
    result = asyncio.run(
        memory.retrieve_semantic_context(
            "secret", provider=selector, model_id="mock", context_limit=1024
        )
    )
    assert result["semantic"]["status"] == "context_limit"
    assert not selector.requests
    result = call(memory, selector, max_bytes=10)
    assert not result["items"]
    assert result["omitted"] == 1
    prompted = json.loads(selector.requests[0].messages[1]["content"])["candidates"]
    assert prompted["memory:" + ident]["statement"] == "密码" * 900


def test_paging_exposes_bounded_scan_and_continuation(memory):
    for n in range(7):
        add(memory, f"separate source {n}")
    selector = Selector(lambda candidates: {"candidate_ids": list(candidates)})
    seen = set()
    cursor = None
    for _ in range(4):
        result = call(memory, selector, candidate_limit=2, limit=2, cursor=cursor)
        assert result["semantic"]["candidates"] <= 2
        seen.update(x["id"] for x in result["items"])
        cursor = result["cursor"]
        if cursor is None:
            break
    assert len(seen) == 7 and cursor is None
    assert len(selector.requests) == 4


def test_filtered_history_still_advances_bounded_candidate_cursor(memory):
    identities = sorted(add(memory, f"record {n}") for n in range(3))
    memory.invalidate(identities[0])
    selector = Selector(lambda candidates: {"candidate_ids": list(candidates)})
    first = call(memory, selector, candidate_limit=1, limit=1)
    assert first["items"] == [] and selector.requests == []
    assert first["cursor"] == "memory:" + identities[0]
    second = call(memory, selector, candidate_limit=1, limit=1, cursor=first["cursor"])
    assert [x["id"] for x in second["items"]] == [identities[1]]
    assert len(selector.requests) == 1


def test_one_provider_failure_is_reported_without_retry_or_truth(memory):
    add(memory, "source")
    selector = Selector(lambda candidates: ProviderError("budget_exhausted"))
    result = call(memory, selector)
    assert len(selector.requests) == 1
    assert result["items"] == []
    assert result["semantic"]["status"] == "unavailable"
    assert result["semantic"]["error_code"] == "budget_exhausted"


def test_live_service_requires_the_original_budgeted_gateway(memory):
    add(memory, "source")
    selector = Selector(lambda candidates: pytest.fail("No direct live provider call"))
    selector.execution_mode = "live"
    result = call(memory, selector)
    assert result["semantic"]["error_code"] == "budget_ledger_required"
    assert not selector.requests


def test_semantic_read_does_not_mutate_memory_database(memory):
    ident = add(memory, "source")
    with sqlite3.connect(memory.path) as db:
        before = list(db.iterdump())
    call(memory, Selector(lambda candidates: {"candidate_ids": ["memory:" + ident]}))
    with sqlite3.connect(memory.path) as db:
        assert list(db.iterdump()) == before


@pytest.mark.parametrize("token_cap", [0, 15000])
def test_native_gateway_accounts_exact_request_and_never_bypasses_budget(memory, token_cap):
    ident = add(memory, "Never disclose passwords or credentials in application logs.")
    sent = []

    def handler(request):
        wire = json.loads(request.content)
        sent.append(wire)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps({"candidate_ids": ["memory:" + ident]})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 31, "completion_tokens": 9},
            },
        )

    gateway = ProviderGateway(
        ProviderConfig(
            id="semantic-test",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_json_schema=True,
        ),
        credential_resolver=lambda _: "test-placeholder",
        transport=httpx.MockTransport(handler),
        budget_ledger=ModelCallBudgetLedger(
            memory.path, token_limit=token_cap, concurrency_limit=1
        ),
    )
    result = call(
        memory,
        gateway,
        provider_id="semantic-test",
        capabilities={"json_schema": True},
        capability_sources={"json_schema": "test_fixture"},
    )
    with memory.store.connection() as db:
        attempts = [dict(row) for row in db.execute("SELECT * FROM model_budget_attempts")]
    if token_cap == 0:
        assert sent == [] and result["items"] == []
        assert result["semantic"]["error_code"] == "budget_exhausted"
        assert all(row["state"] == "rejected" for row in attempts)
    else:
        assert len(sent) == len(attempts) == 1
        assert result["semantic"]["status"] == "selected"
        assert attempts[0]["purpose"] == "memory_semantic_retrieval"
        assert attempts[0]["state"] == "settled"
        assert attempts[0]["input_charged"] == 31 and attempts[0]["output_charged"] == 9
        assert attempts[0]["input_reserved"] == result["semantic"]["input_reservation"]
        assert (
            attempts[0]["input_reserved"]
            == len(json.dumps(sent[0], ensure_ascii=False).encode()) + 64
        )
        assert [x["id"] for x in result["items"]] == [ident]
