import asyncio
import hashlib
import json

import pytest
from test_tool_engine import mock_run as mock_run  # noqa: PLC0414 - shared pytest fixture
from test_tool_engine import reserve

from forge_core.context_service import ContextService
from forge_core.domain import ConversationMessage, DomainError, ForgeEvent, Quest
from forge_core.memory import MemoryService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.superagent_events import TriggerService
from forge_core.tool_engine import ToolEngine
from forge_core.workspace_discovery import WorkspaceDiscovery


def _wire_bytes(value):
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def test_chain_follows_task_worker_execution_receipt_and_preserves_evidence(mock_run):
    store, quest, worker, _ = mock_run
    parameters = {"source": "result = sum(data)", "inputs": {"data": [1, 2, 3]}}
    reserve(store, "recall-numeric", "numeric_ast", parameters)
    result = asyncio.run(
        ToolEngine(store).execute(
            call_id="recall-numeric",
            quest_id=quest.id,
            worker_id=worker.id,
            run_id="run",
            lease_token="lease",
            tool_name="numeric_ast",
            parameters=parameters,
        )
    )
    memory = MemoryService(store)
    chain = memory.recall_chain([f"quest:{quest.id}"], max_depth=4, max_nodes=64, max_bytes=65536)
    references = {node["ref"] for node in chain["nodes"]}
    assert {f"worker:{worker.id}", "run:run", f"receipt:{result['receipt_id']}"} <= references
    assert all("no current permission" in node["trust"] for node in chain["nodes"])
    restored = MemoryService(Store(store.path, workspace=store.workspace))
    receipt = restored.recall_object(f"receipt:{result['receipt_id']}")
    assert json.loads(receipt["content"])["result_json"]["result_json"] == "6"
    assert not any("lease_token" in json.dumps(node) for node in chain["nodes"])


def test_chain_cycles_and_unicode_readback_are_bounded_and_hash_bound(tmp_path):
    store = Store(workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="回忆", goal="保留来源" * 2000)
    store.save_quest(quest)
    memory = MemoryService(store)
    root = f"quest:{quest.id}"
    result = memory.recall_chain([root, root], max_depth=4, max_nodes=3, max_bytes=2048)
    assert _wire_bytes(result) <= 2048
    assert len({node["ref"] for node in result["nodes"]}) == len(result["nodes"])
    offset, pieces, digest = 0, [], None
    while True:
        page = memory.recall_object(root, max_bytes=2048, offset=offset, expected_hash=digest)
        assert _wire_bytes(page) <= 2048
        digest = page["source_hash"]
        pieces.append(page["content"])
        if page["complete"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert json.loads("".join(pieces))["goal"] == quest.goal
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET goal='changed' WHERE id=?", (quest.id,))
    with pytest.raises(DomainError, match="changed"):
        memory.recall_object(root, expected_hash=digest)


def test_recall_conversation_and_worker_scope_rejects_unrelated_objects(mock_run):
    store, quest, _, _ = mock_run
    other = Quest(title="Other", goal="Do not reveal")
    store.save_quest(other)
    message = store.add_message(ConversationMessage(role="user", content="private global input"))
    memory = MemoryService(store)
    assert memory.recall_object(f"quest:{quest.id}", run_id="run")["ref"] == f"quest:{quest.id}"
    for reference in (
        f"quest:{other.id}",
        f"message:{message.id}",
        "conversation:main",
        "router:main",
    ):
        with pytest.raises(DomainError, match="scope"):
            memory.recall_object(reference, run_id="run")
    with store.unit_of_work() as work:
        work.connection.execute("INSERT INTO conversations VALUES ('elsewhere','now')")
        work.connection.execute(
            "UPDATE quests SET conversation_id='elsewhere' WHERE id=?", (other.id,)
        )
    with pytest.raises(DomainError, match="scope"):
        memory.recall_object(f"quest:{other.id}")
    with pytest.raises(DomainError):
        memory.recall_object(f"workspace:{store.workspace.id}", run_id="not-a-run")


def test_reference_pagination_reaches_old_events_without_silent_omission(tmp_path):
    store = Store(workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="History", goal="Recover old decisions")
    store.save_quest(quest)
    events = [
        ForgeEvent(event_type="decision.recorded", quest_id=quest.id, payload={"n": i})
        for i in range(45)
    ]
    for event in events:
        store.append_event(event)
    memory, offset, seen = MemoryService(store), 0, set()
    while True:
        page = memory.recall_object(f"quest:{quest.id}", relation_offset=offset, max_bytes=8192)
        seen.update(link["ref"] for link in page["relations"])
        if page["next_relation_offset"] is None:
            break
        assert page["next_relation_offset"] > offset
        offset = page["next_relation_offset"]
    assert {f"event:{event.id}" for event in events} <= seen


def test_worker_core_tool_can_follow_relation_continuations(mock_run):
    import jsonschema

    from forge_core.worker_superagent import RECALL_TOOL, read

    store, quest, _, _ = mock_run
    events = [ForgeEvent(event_type="decision.recorded", quest_id=quest.id) for _ in range(45)]
    for event in events:
        store.append_event(event)
    offset, seen = 0, set()
    with store.connection() as connection:
        while True:
            arguments = {"reference": f"quest:{quest.id}", "relation_offset": offset}
            jsonschema.validate(arguments, RECALL_TOOL["input_schema"])
            page = read(store, connection, "run", "recall_object", arguments)
            seen.update(link["ref"] for link in page["relations"])
            if page["next_relation_offset"] is None:
                break
            assert page["next_relation_offset"] > offset
            offset = page["next_relation_offset"]
    assert {f"event:{event.id}" for event in events} <= seen


def test_archive_recall_rejects_changed_original_and_revoked_source(tmp_path):
    store = Store(workspace=Workspace.from_path(tmp_path))
    message = store.add_message(ConversationMessage(role="user", content="original requirement"))
    ContextService(store).compact_router(keep_recent=0)
    memory = MemoryService(store)
    assert memory.recall_object("archive:1")["kind"] == "archive"
    memory.revoke_source(f"message:{message.id}")
    with pytest.raises(DomainError, match="revoked"):
        memory.recall_object(f"message:{message.id}")
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET content='tampered' WHERE id=?", (message.id,))
    with pytest.raises(DomainError, match="integrity"):
        memory.recall_object("archive:1")


def test_recall_is_read_only_inside_caller_transaction(tmp_path):
    store = Store(workspace=Workspace.from_path(tmp_path))
    memory = MemoryService(store)
    with pytest.raises(RuntimeError), store.unit_of_work() as work:
        work.connection.execute("INSERT INTO conversations VALUES ('pending','now')")
        before = work.connection.total_changes
        memory.recall_object(
            "conversation:pending", conversation_id="pending", connection=work.connection
        )
        assert work.connection.total_changes == before
        raise RuntimeError("rollback")
    with pytest.raises(DomainError):
        memory.recall_object("conversation:pending", conversation_id="pending")


def test_router_task_thread_trigger_project_chain_is_scoped_to_real_delivery(mock_run):
    store, quest, _, _ = mock_run
    service = TriggerService(store)
    service.register_source("test-events", kind="external")
    event = service.receive("test-events", "first", "thread", {"text": "complete event"})
    service.mark_routed(event["id"], quest.id)
    service.subscribe(event["thread_id"], "run")
    unrelated = service.receive("test-events", "second", "unrelated", {"text": "private"})
    project = WorkspaceDiscovery(store).associate(quest.id)
    memory = MemoryService(store)
    chain = memory.recall_chain(["router:main"], max_depth=4, max_nodes=64, max_bytes=65536)
    refs = {node["ref"] for node in chain["nodes"]}
    assert {
        f"quest:{quest.id}",
        "run:run",
        f"thread:{event['thread_id']}",
        f"trigger:{event['id']}",
        f"project:{project['id']}",
    } <= refs
    worker = memory.recall_chain(["run:run"], run_id="run", max_depth=3, max_bytes=32768)
    worker_refs = {node["ref"] for node in worker["nodes"]}
    assert f"trigger:{event['id']}" in worker_refs
    assert f"trigger:{unrelated['id']}" not in worker_refs
    for reference in (f"trigger:{unrelated['id']}", f"thread:{unrelated['thread_id']}"):
        with pytest.raises(DomainError, match="scope"):
            memory.recall_object(reference, run_id="run")
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE superagent_triggers SET payload_json='{}' WHERE id=?", (event["id"],)
        )
    with pytest.raises(DomainError, match="integrity"):
        memory.recall_object(f"trigger:{event['id']}", run_id="run")


def test_worker_can_read_only_hash_bound_original_and_updated_user_request(mock_run):
    store, quest, _, _ = mock_run
    original = store.add_message(ConversationMessage(role="user", content="original constraints"))
    updated = store.add_message(ConversationMessage(role="user", content="updated constraints"))
    unrelated = store.add_message(
        ConversationMessage(role="user", content="unrelated private message")
    )
    metadata = {
        "original_request_message_id": original.id,
        "original_request_sha256": hashlib.sha256(original.content.encode()).hexdigest(),
        "goal_update_message_id": updated.id,
        "goal_update_sha256": hashlib.sha256(updated.content.encode()).hexdigest(),
    }
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET metadata=? WHERE id=?", (json.dumps(metadata), quest.id)
        )
    memory = MemoryService(store)
    for message in (original, updated):
        assert (
            json.loads(memory.recall_object(f"message:{message.id}", run_id="run")["content"])[
                "content"
            ]
            == message.content
        )
    with pytest.raises(DomainError, match="scope"):
        memory.recall_object(f"message:{unrelated.id}", run_id="run")
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET content='changed' WHERE id=?", (original.id,))
    with pytest.raises(DomainError, match="changed"):
        memory.recall_object(f"message:{original.id}", run_id="run")


@pytest.mark.parametrize("reference", ["../quest", "unknown:a", "quest:", "quest:a\n", 3])
def test_recall_rejects_untyped_or_invalid_references(tmp_path, reference):
    memory = MemoryService(Store(workspace=Workspace.from_path(tmp_path)))
    with pytest.raises(ValueError):
        memory.recall_object(reference)
