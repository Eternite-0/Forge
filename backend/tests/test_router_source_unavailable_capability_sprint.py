"""Scripted Router tool recovery; SQLite verification is real, Provider is Mock."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from test_persistent_router import make_router

from forge_core.context_service import ContextService
from forge_core.domain import ConversationMessage, DomainError

DECISION = {
    "intent": "chat",
    "reply": "Observed context only",
    "title": None,
    "goal": None,
    "quest_id": None,
    "candidates": [],
}


class ReadThenDecide:
    execution_mode = "mock"

    def __init__(self, source_id, *, before_read=None):
        self.source_id, self.before_read = source_id, before_read
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            if self.before_read:
                self.before_read()
            return SimpleNamespace(
                text="",
                tool_calls=[
                    {
                        "id": "read",
                        "name": "read_context_source",
                        "arguments": {"source_id": self.source_id},
                    }
                ],
                usage={},
            )
        return SimpleNamespace(text=json.dumps(DECISION), tool_calls=[], usage={})


@pytest.mark.parametrize("target", ["missing", "live_message", "foreign_archive"])
def test_missing_or_unadvertised_read_does_not_abort_request_or_disclose_content(tmp_path, target):
    store, _, router = make_router(tmp_path, DECISION)
    source = ConversationMessage(role="user", content="PRIVATE_UNARCHIVED_OR_FOREIGN_TEXT")
    if target == "foreign_archive":
        store.add_message(
            ConversationMessage(role="assistant", content="Permitted archived context")
        )
        ContextService(store).compact_router(keep_recent=0)
        with store.unit_of_work() as work:
            work.connection.execute("INSERT INTO conversations VALUES ('other','fixture')")
            work.add_message(source.model_copy(update={"conversation_id": "other"}))
        source_id = source.id
    elif target == "live_message":
        store.add_message(source)
        source_id = source.id
    else:
        source_id = "msg_not_presented"
    model = ReadThenDecide(source_id)
    router.provider = model
    result = asyncio.run(
        router.handle_user_message("A self-contained new request", request_id=target)
    )
    assert result["intent"] == "chat" and len(model.requests) == 2
    assert ("read_context_source" in {tool["name"] for tool in model.requests[0].tools}) == (
        target == "foreign_archive"
    )
    observation = json.loads(model.requests[1].messages[-1]["content"])
    assert (
        observation["available"] is False
        and observation["error_code"] == "context_source_unavailable"
    )
    assert "content" not in observation and len(json.dumps(observation).encode()) < 256


def test_missing_id_with_a_real_archive_returns_unavailable_and_valid_source_still_reads(tmp_path):
    store, _, router = make_router(tmp_path, DECISION)
    source = ConversationMessage(role="assistant", content="Exact archived evidence")
    store.add_message(source)
    ContextService(store).compact_router(keep_recent=0)
    for source_id, valid in [("msg_unknown", False), (source.id, True)]:
        model = ReadThenDecide(source_id)
        router.provider = model
        result = asyncio.run(
            router.handle_user_message(
                "Continue from exact archived evidence", request_id=source_id
            )
        )
        assert result["intent"] == "chat"
        assert "read_context_source" in {tool["name"] for tool in model.requests[0].tools}
        observation = json.loads(model.requests[1].messages[-1]["content"])
        if valid:
            assert observation["content"] == source.content
            assert (
                observation["source_sha256"] == hashlib.sha256(source.content.encode()).hexdigest()
            )
        else:
            assert observation["available"] is False and "content" not in observation


def test_indexed_source_changed_after_presentation_fails_closed(tmp_path):
    store, _, router = make_router(tmp_path, DECISION)
    source = ConversationMessage(role="assistant", content="Original immutable evidence")
    store.add_message(source)
    ContextService(store).compact_router(keep_recent=0)

    def tamper():
        with store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE messages SET content='CHANGED_SOURCE' WHERE id=?", (source.id,)
            )

    model = ReadThenDecide(source.id, before_read=tamper)
    router.provider = model
    with pytest.raises(DomainError, match="integrity"):
        asyncio.run(router.handle_user_message("Read original evidence", request_id="tampered"))
    assert len(model.requests) == 1
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM router_turns").fetchone()[0] == 0


def test_archive_read_does_not_disclose_another_project_source(tmp_path):
    store, _, router = make_router(tmp_path, DECISION)
    source = ConversationMessage(role="assistant", content="PROJECT_PRIVATE_ARCHIVED_TEXT")
    store.add_message(source)
    ContextService(store).compact_router(keep_recent=0)
    with store.connection() as c:
        snapshot = router._snapshot(c, "Current project task")
    snapshot["project_context"] = {"focused_project_id": "other-project"}
    observation, original = router._read_archived_source(snapshot, {"source_id": source.id})
    assert observation["available"] is False and original is None
    assert "PROJECT_PRIVATE_ARCHIVED_TEXT" not in json.dumps(observation)
