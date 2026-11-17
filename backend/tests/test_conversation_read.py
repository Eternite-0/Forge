import pytest

from forge_core.conversation_read import ConversationCursorConflict, read_conversation
from forge_core.domain import ConversationMessage
from forge_core.settings import Workspace
from forge_core.store import Store


def _append(store, first, last):
    for number in range(first, last):
        store.add_message(ConversationMessage(role="user", content=f"message-{number}"))


def _contents(page):
    return [row["content"] for row in page["messages"]]


def test_empty_snapshot_resumes_across_restart(tmp_path):
    workspace = Workspace.from_path(tmp_path)
    store = Store(workspace=workspace)
    initial = read_conversation(store)
    assert initial["mode"] == "snapshot"
    assert initial["messages"] == []
    _append(store, 0, 3)
    store = Store(workspace=workspace)
    page = read_conversation(store, after=initial["resume_after"], limit=2)
    assert page["mode"] == "delta"
    assert page["router"] == initial["router"]
    assert page["has_newer"]
    assert _contents(page) == ["message-0", "message-1"]
    final = read_conversation(store, after=page["resume_after"], limit=2)
    assert not final["has_newer"]
    assert _contents(final) == ["message-2"]
    quiet = read_conversation(store, after=final["resume_after"])
    assert quiet["messages"] == []
    assert quiet["resume_after"] == final["resume_after"]


def test_forward_and_older_pagination_are_independent(tmp_path):
    store = Store(workspace=Workspace.from_path(tmp_path))
    _append(store, 0, 8)
    tail = read_conversation(store, limit=3)
    assert _contents(tail) == ["message-5", "message-6", "message-7"]
    assert tail["has_more"] and not tail["has_newer"]
    _append(store, 8, 13)
    updates = read_conversation(store, after=tail["resume_after"], limit=3)
    assert updates["has_newer"] and updates["next_before"] is None
    assert _contents(updates) == ["message-8", "message-9", "message-10"]
    older = read_conversation(store, before=tail["next_before"], limit=3)
    assert _contents(older) == ["message-2", "message-3", "message-4"]
    assert older["resume_after"] is None
    last = read_conversation(store, after=updates["resume_after"], limit=3)
    assert _contents(last) == ["message-11", "message-12"]
    assert not last["has_newer"]


def test_cursor_for_different_transcript_at_same_row_is_conflict(tmp_path):
    first = Store(workspace=Workspace.from_path(tmp_path / "first"))
    other = Store(workspace=Workspace.from_path(tmp_path / "restored"))
    _append(first, 0, 2)
    _append(other, 3, 9)
    token = read_conversation(first)["resume_after"]
    with pytest.raises(ConversationCursorConflict):
        read_conversation(other, after=token)


@pytest.mark.parametrize("token", ["", "NaN", "MQ", "%%%", " "])
def test_invalid_cursors_are_not_interpreted_as_an_empty_delta(tmp_path, token):
    store = Store(workspace=Workspace.from_path(tmp_path))
    with pytest.raises(ValueError):
        read_conversation(store, after=token)
    with pytest.raises(ValueError):
        read_conversation(store, before="MQ", after=token)
