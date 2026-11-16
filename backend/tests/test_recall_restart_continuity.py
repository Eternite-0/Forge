"""Offline execution proofs: restarted Router models must actually read Recall tools.

The scripted providers below learn refs and answers only from model-visible context
and real tool results. Direct MemoryService calls test pagination/integrity separately;
they are not evidence that an Agent consumed historical sources or a live-model gate.
"""

import asyncio
import hashlib
import json
import secrets

import pytest
from test_superagent_loop import MockProvider, conversational_decision, response
from test_superagent_loop import rig as base_rig

from forge_core.context_service import ContextService, _hash, _router_handoff_input
from forge_core.domain import ConversationMessage, DomainError, Quest
from forge_core.memory import MemoryService
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.workspace_discovery import WorkspaceDiscovery


@pytest.fixture(autouse=True)
def forbid_real_provider(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Recall regressions must never call a real Provider")

    monkeypatch.setattr("forge_core.router.ProviderGateway.generate", forbidden)


@pytest.fixture
def rig(tmp_path):
    return base_rig.__wrapped__(tmp_path)


def _restart(store):
    restored = Store(store.path, workspace=Workspace.from_path(store.workspace.root))
    router = RouterService(
        restored,
        TeamComposer(ModelRegistry(restored)),
        model_id="mock-model",
        provider_id="mock-provider",
        context_limit=131072,
        autonomous=True,
    )
    return restored, router


def _compact(store, *, handoff="Historical details remain in original sources."):
    service = ContextService(store)
    prepared = service.prepare_router_compaction(keep_recent=0)
    result = service.compact_router(
        keep_recent=0,
        snapshot=prepared["snapshot"],
        model_handoff={
            "text": handoff,
            "input_digest": _hash(
                _router_handoff_input(
                    prepared["snapshot"], prepared["eligible"], prepared["previous"]
                )
            ),
        },
    )
    assert result["source_count"] == len(prepared["eligible"]) > 0
    return prepared["eligible"]


def _source(store, history, **kwargs):
    # Facts lie beyond the deterministic capsule excerpt; the script cannot
    # answer by copying the initial context or a handoff summary.
    return store.add_message(
        ConversationMessage(
            role="user",
            content=json.dumps({"padding": "background " * 120, "history": history}),
            **kwargs,
        )
    )


def _tools(**calls):
    value = response({})
    value.text = ""
    value.tool_calls = [
        {"id": identity, "name": "recall_object", "arguments": arguments}
        for identity, arguments in calls.items()
    ]
    assert 1 <= len(value.tool_calls) <= 4
    return value


def _pages(request):
    return {
        message["tool_call_id"]: json.loads(message["content"])
        for message in request.messages
        if message["role"] == "tool"
    }


def _next(page, relation):
    return [item["ref"] for item in page["relations"] if item["relation"] == relation]


def _record(page):
    assert page["complete"], "A summary or partial source is not a complete read"
    assert page["offset"] == 0
    return json.loads(page["content"])


def _assert_execution(provider, *, rounds=4):
    assert len(provider.requests) == rounds <= 4
    assert provider.requests[-1].tools is None
    final = provider.requests[-1]
    assistant_calls = [
        call
        for message in final.messages
        if message["role"] == "assistant"
        for call in message.get("tool_calls", [])
    ]
    assert assistant_calls
    assert {call["function"]["name"] for call in assistant_calls} == {"recall_object"}
    assert len({call["id"] for call in assistant_calls}) == len(assistant_calls)
    assert {call["id"] for call in assistant_calls} == set(_pages(final))


def _archive_reader(request, count):
    """Three real tool rounds: head -> previous+current -> previous originals."""
    pages = _pages(request)
    if count == 1:
        context = json.loads(request.messages[1]["content"])
        assert context["recall"]["root"] == "conversation:main"
        return _tools(head={"reference": context["recall"]["archive_ref"]})
    if count == 2:
        (previous,) = _next(pages["head"], "previous_archive")
        (current,) = _next(pages["head"], "archived_message")
        return _tools(previous={"reference": previous}, current={"reference": current})
    if count == 3:
        (original,) = _next(pages["previous"], "archived_message")
        return _tools(original={"reference": original})
    assert count == 4
    histories = [
        json.loads(_record(pages[key])["content"])["history"] for key in ("original", "current")
    ]
    facts = {key: value for history in histories for key, value in history.items()}
    return response({**conversational_decision("chat"), "reply": json.dumps(facts)})


def test_restarted_router_reads_previous_archive_and_original_decision_and_todo(rig):
    store, old_router, _events = rig
    expected = {
        "project": "project-" + secrets.token_hex(8),
        "decision": "choice-" + secrets.token_hex(12),
        "pending": "todo-" + secrets.token_hex(12),
    }
    original = _source(store, {key: expected[key] for key in ("project", "decision")})
    _compact(store)
    current = _source(store, {"pending": expected["pending"]})
    _compact(store)
    identity = store.ensure_router()["router_session_id"]
    restored, router = _restart(store)
    assert restored is not store and router is not old_router
    assert restored.ensure_router()["router_session_id"] == identity
    assert ContextService(restored).rebuild_router()["recent_messages"] == []
    provider = router.provider = MockProvider(_archive_reader)

    result = asyncio.run(router.handle_user_message("Recover the historical record."))

    assert result["intent"] == "chat"
    assert json.loads(result["assistant_message"]["content"]) == expected
    assert all(
        value not in provider.requests[0].messages[1]["content"] for value in expected.values()
    )
    pages = _pages(provider.requests[-1])
    assert pages["original"]["ref"] == "message:" + original.id
    assert pages["current"]["ref"] == "message:" + current.id
    assert pages["previous"]["ref"] != pages["head"]["ref"]
    _assert_execution(provider)


def _paged_original_reader(request, count):
    pages = _pages(request)
    if count == 1:
        context = json.loads(request.messages[1]["content"])
        return _tools(head={"reference": context["recall"]["archive_ref"]})
    if count == 2:
        (original,) = _next(pages["head"], "archived_message")
        return _tools(source_start={"reference": original})
    if count == 3:
        first = pages["source_start"]
        assert not first["complete"] and first["next_offset"] > 0
        return _tools(
            source_end={
                "reference": first["ref"],
                "offset": first["next_offset"],
                "expected_hash": first["source_hash"],
            }
        )
    assert count == 4
    first, last = pages["source_start"], pages["source_end"]
    assert first["source_hash"] == last["source_hash"]
    assert first["next_offset"] == last["offset"]
    assert last["complete"]
    complete = first["content"] + last["content"]
    assert len(complete.encode("utf-8")) == last["content_bytes"] == last["next_offset"]
    facts = json.loads(json.loads(complete)["content"])["history"]
    return response({**conversational_decision("chat"), "reply": json.dumps(facts)})


@pytest.mark.parametrize("mutation", [None, "source", "revocation"])
def test_router_tools_page_unicode_original_and_reject_inter_page_changes(rig, mutation):
    store, _old_router, _events = rig
    expected = {key: secrets.token_hex(12) for key in ("project", "decision", "pending")}
    store.add_message(
        ConversationMessage(
            role="user",
            content=json.dumps(
                {"padding": "背景🙂" * 330, "history": expected}, ensure_ascii=False
            ),
        )
    )
    _compact(store)
    restored, router = _restart(store)

    def read_with_fault_injection(request, count):
        if count == 3 and mutation:
            reference = _pages(request)["source_start"]["ref"]
            if mutation == "revocation":
                MemoryService(restored).revoke_source(reference)
            else:
                with restored.unit_of_work() as work:
                    work.connection.execute(
                        "UPDATE messages SET content='changed' WHERE id=?",
                        (reference.split(":", 1)[1],),
                    )
        return _paged_original_reader(request, count)

    provider = router.provider = MockProvider(read_with_fault_injection)
    if mutation:
        with pytest.raises(DomainError, match="changed" if mutation == "source" else "revoked"):
            asyncio.run(router.handle_user_message("Recover the historical record."))
        assert len(provider.requests) == 3
        assert "source_end" not in _pages(provider.requests[-1])
        with restored.connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM router_turns").fetchone()[0] == 0
    else:
        result = asyncio.run(router.handle_user_message("Recover the historical record."))
        assert json.loads(result["assistant_message"]["content"]) == expected
        _assert_execution(provider)
    assert all(
        value not in provider.requests[0].messages[1]["content"] for value in expected.values()
    )


def _project_reader(request, count):
    """No fixture IDs/facts: choose only refs presented in actual tool results."""
    pages = _pages(request)
    if count == 1:
        context = json.loads(request.messages[1]["content"])
        assert context["project_context"]["grants_tool_access"] is False
        assert "archive_ref" not in context["recall"]
        return _tools(project={"reference": context["recall"]["root"]})
    if count == 2:
        (quest,) = _next(pages["project"], "project_task")
        return _tools(quest={"reference": quest})
    if count == 3:
        (original,) = _next(pages["quest"], "metadata.original_request_message_id")
        (update,) = _next(pages["quest"], "metadata.goal_update_message_id")
        return _tools(original={"reference": original}, update={"reference": update})
    assert count == 4
    facts = {"project_ref": pages["project"]["ref"]}
    for identity in ("original", "update"):
        facts.update(json.loads(_record(pages[identity])["content"])["history"])
    quest_id = _record(pages["quest"])["id"]
    return response(
        {
            **conversational_decision("continue_quest", quest_id=quest_id),
            "reply": json.dumps(facts),
        }
    )


def _project_history(store, tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    facts = {"decision": secrets.token_hex(12), "pending": secrets.token_hex(12)}
    original = _source(store, {"decision": facts["decision"]})
    update = _source(store, {"pending": facts["pending"]})
    quest = Quest(
        title="Historical task",
        goal="Read historical sources before resuming.",
        metadata={
            "original_request_message_id": original.id,
            "original_request_sha256": hashlib.sha256(original.content.encode()).hexdigest(),
            "goal_update_message_id": update.id,
            "goal_update_sha256": hashlib.sha256(update.content.encode()).hexdigest(),
        },
    )
    store.save_quest(quest)
    project = WorkspaceDiscovery(store).associate(quest.id, root)
    return quest, project, facts


@pytest.mark.parametrize("selected_index", [0, 1])
def test_restarted_focused_router_reads_only_correct_projects_originals(
    rig, tmp_path, selected_index
):
    store, _old_router, _events = rig
    projects = [_project_history(store, tmp_path, name) for name in ("alpha", "beta")]
    _compact(store)
    restored, router = _restart(store)
    quest, project, facts = projects[selected_index]
    other_quest, other_project, other_facts = projects[1 - selected_index]
    provider = router.provider = MockProvider(_project_reader)

    result = asyncio.run(router.handle_user_message(f"Continue work in `{project['root']}`."))

    assert result["intent"] == "continue_quest" and result["quest_id"] == quest.id
    assert json.loads(result["assistant_message"]["content"]) == {
        "project_ref": "project:" + project["id"],
        **facts,
    }
    first_context = json.loads(provider.requests[0].messages[1]["content"])
    assert first_context["recall"]["root"] == "project:" + project["id"]
    assert all(value not in json.dumps(first_context) for value in facts.values())
    for request in provider.requests:
        assert all(value not in json.dumps(request.messages) for value in other_facts.values())
    _assert_execution(provider)
    with restored.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM task_grants").fetchone()[0] == 0
    # Focus is a relevance fence, not permission to follow another project or
    # the mixed global archive, even if a model guesses a valid persistent ref.
    memory = MemoryService(restored)
    for reference in (
        "quest:" + other_quest.id,
        "project:" + other_project["id"],
        "conversation:main",
        "archive:" + str(ContextService(restored).router_snapshot()["archive_head"]["archive_id"]),
    ):
        with pytest.raises(DomainError, match="project context"):
            memory.recall_object(reference, project_id=project["id"])


@pytest.mark.parametrize("target", ["other_project", "other_source", "mixed_archive"])
def test_focused_router_tool_rejects_guessed_out_of_project_references(rig, tmp_path, target):
    store, _old_router, _events = rig
    _quest, focused, _facts = _project_history(store, tmp_path, "alpha")
    other, project, _other_facts = _project_history(store, tmp_path, "beta")
    _compact(store)
    restored, router = _restart(store)
    head = ContextService(restored).router_snapshot()["archive_head"]
    reference = {
        "other_project": "project:" + project["id"],
        "other_source": "message:" + other.metadata["original_request_message_id"],
        "mixed_archive": "archive:" + str(head["archive_id"]),
    }[target]
    provider = router.provider = MockProvider(lambda *_: _tools(guessed={"reference": reference}))
    with pytest.raises(DomainError, match="project context"):
        asyncio.run(router.handle_user_message(f"Continue work in `{focused['root']}`."))
    assert len(provider.requests) == 1 and _pages(provider.requests[0]) == {}
    with restored.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM router_turns").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM task_grants").fetchone()[0] == 0


def _wire_bytes(value):
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def test_archive_content_and_relation_pages_survive_restart_without_missing_sources(rig):
    store, _router, _events = rig
    first = store.add_message(ConversationMessage(role="user", content="earlier source"))
    _compact(store)
    sources = [
        store.add_message(ConversationMessage(role="user", content=f"来源 {i}")) for i in range(45)
    ]
    _compact(store, handoff="历史交接🙂" * 700)
    restored, _router = _restart(store)
    head = ContextService(restored).router_snapshot()["archive_head"]
    reference = "archive:" + str(head["archive_id"])
    memory = MemoryService(restored)
    offset, digest, pieces, relation_offset, seen = 0, None, [], 0, set()
    content_pages = 0
    for _ in range(100):
        page = memory.recall_object(reference, offset=offset, expected_hash=digest)
        assert _wire_bytes(page) <= 4096
        assert page["offset"] == offset
        assert page["next_offset"] == offset + len(page["content"].encode("utf-8"))
        digest = page["source_hash"]
        pieces.append(page["content"])
        content_pages += 1
        if page["complete"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    else:
        pytest.fail("Archive content pagination did not terminate")
    assert content_pages > 1
    record = json.loads("".join(pieces))
    assert [item["id"] for item in record["source_json"]] == [source.id for source in sources]
    assert record["capsule_json"]["model_handoff"]["text"] == "历史交接🙂" * 700
    relation_pages = 0
    for _ in range(100):
        page = memory.recall_object(
            reference, relation_offset=relation_offset, expected_hash=digest
        )
        assert _wire_bytes(page) <= 4096
        links = {(item["relation"], item["ref"]) for item in page["relations"]}
        assert not seen.intersection(links), "Relation pages must not silently repeat refs"
        seen.update(links)
        relation_pages += 1
        if not page["relations_omitted"]:
            assert page["next_relation_offset"] is None
            break
        assert page["next_relation_offset"] > relation_offset
        relation_offset = page["next_relation_offset"]
    else:
        pytest.fail("Archive relation pagination did not terminate")
    assert relation_pages > 1
    assert {ref for relation, ref in seen if relation == "archived_message"} == {
        "message:" + source.id for source in sources
    }
    (previous,) = [ref for relation, ref in seen if relation == "previous_archive"]
    previous_page = memory.recall_object(previous)
    assert _next(previous_page, "archived_message") == ["message:" + first.id]


@pytest.mark.parametrize(
    "protection",
    [
        "revoke_old_original",
        "revoke_current_original",
        "revoke_previous_archive",
        "revoke_head",
        "change_old_original",
        "change_old_metadata",
        "change_previous_capsule",
        "rehash_previous_capsule",
    ],
)
def test_restarted_router_never_presents_invalid_archive_dependency_to_model(rig, protection):
    store, _old_router, _events = rig
    original = _source(store, {"decision": secrets.token_hex(12)})
    _compact(store)
    current = _source(store, {"pending": secrets.token_hex(12)})
    _compact(store)
    head = ContextService(store).router_snapshot()["archive_head"]
    with store.connection() as connection:
        previous = dict(
            connection.execute("SELECT * FROM context_archives ORDER BY id LIMIT 1").fetchone()
        )
    if protection.startswith("revoke_"):
        reference = {
            "revoke_old_original": "message:" + original.id,
            "revoke_current_original": "message:" + current.id,
            "revoke_previous_archive": "archive:" + str(previous["id"]),
            "revoke_head": "archive:" + str(head["archive_id"]),
        }[protection]
        MemoryService(store).revoke_source(reference)
    else:
        with store.unit_of_work() as work:
            if protection == "change_old_original":
                work.connection.execute(
                    "UPDATE messages SET content='changed' WHERE id=?", (original.id,)
                )
            elif protection == "change_old_metadata":
                work.connection.execute(
                    "UPDATE messages SET metadata=? WHERE id=?",
                    (json.dumps({"tampered": True}), original.id),
                )
            else:
                capsule = json.loads(previous["capsule_json"])
                capsule["model_handoff"]["text"] = "tampered historical handoff"
                work.connection.execute(
                    "UPDATE context_archives SET capsule_json=?,capsule_hash=? WHERE id=?",
                    (
                        json.dumps(capsule),
                        _hash(capsule)
                        if protection.startswith("rehash_")
                        else previous["capsule_hash"],
                        previous["id"],
                    ),
                )
    restored, router = _restart(store)
    provider = router.provider = MockProvider(_archive_reader)
    pattern = "revoked" if protection.startswith("revoke_") else "integrity"
    with pytest.raises(DomainError, match=pattern):
        MemoryService(restored).recall_object("archive:" + str(head["archive_id"]))
    # Router context building also verifies persisted source integrity. Depending
    # on the failure it must stop before inference or at the actual Recall tool,
    # never give an invalid archive/source result to the scripted Agent.
    with pytest.raises(DomainError, match=pattern):
        asyncio.run(router.handle_user_message("Recover the historical record."))
    assert len(provider.requests) <= 1
    assert all(not _pages(request) for request in provider.requests)
    with restored.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM router_turns").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["source", "revocation", "capsule"])
def test_archive_page_continuation_rechecks_sources_and_expected_hash(rig, change):
    store, _router, _events = rig
    source = store.add_message(ConversationMessage(role="user", content="原始历史🙂" * 500))
    _compact(store, handoff="交接文字🙂" * 500)
    restored, _router = _restart(store)
    reference = "archive:" + str(
        ContextService(restored).router_snapshot()["archive_head"]["archive_id"]
    )
    memory = MemoryService(restored)
    first = memory.recall_object(reference)
    assert not first["complete"] and first["next_offset"] > 0
    if change == "revocation":
        memory.revoke_source("message:" + source.id)
    else:
        with restored.unit_of_work() as work:
            if change == "source":
                work.connection.execute(
                    "UPDATE messages SET content='changed' WHERE id=?", (source.id,)
                )
            else:
                row = work.connection.execute("SELECT * FROM context_archives").fetchone()
                capsule = json.loads(row["capsule_json"])
                capsule["model_handoff"]["text"] = "changed but internally rehashed"
                work.connection.execute(
                    "UPDATE context_archives SET capsule_json=?,capsule_hash=? WHERE id=?",
                    (json.dumps(capsule), _hash(capsule), row["id"]),
                )
    pattern = {"revocation": "revoked", "source": "integrity", "capsule": "changed"}[change]
    with pytest.raises(DomainError, match=pattern):
        memory.recall_object(
            reference, offset=first["next_offset"], expected_hash=first["source_hash"]
        )
