"""Recall rejects revoked or changed inputs anywhere in an archive's Core chain."""

import json

import pytest
from test_superagent_events import worker

from forge_core.context_service import ContextService, _hash, _router_handoff_input
from forge_core.domain import ConversationMessage, DomainError
from forge_core.memory import MemoryService
from forge_core.settings import Workspace
from forge_core.store import Store


def router_archives(tmp_path):
    store = Store(workspace=Workspace.from_path(tmp_path))
    service = ContextService(store)
    messages = []
    for index in range(3):
        messages.append(
            store.add_message(ConversationMessage(role="user", content=f"source {index}"))
        )
        prepared = service.prepare_router_compaction(keep_recent=0)
        service.compact_router(
            keep_recent=0,
            snapshot=prepared["snapshot"],
            model_handoff={
                "text": f"untrusted cumulative summary {index}",
                "input_digest": _hash(
                    _router_handoff_input(
                        prepared["snapshot"], prepared["eligible"], prepared["previous"]
                    )
                ),
            },
        )
    with store.connection() as connection:
        archives = [
            dict(row) for row in connection.execute("SELECT * FROM context_archives ORDER BY id")
        ]
    return store, messages, archives


@pytest.mark.parametrize("dependency", ["current_message", "oldest_message", "previous_archive"])
def test_router_archive_recall_rejects_revoked_dependency_across_previous_chain(
    tmp_path, dependency
):
    store, messages, archives = router_archives(tmp_path)
    memory = MemoryService(store)
    root = f"archive:{archives[-1]['id']}"
    before = memory.recall_object(root, max_bytes=8192)
    assert before["kind"] == "archive" and before["complete"]
    revoked = {
        "current_message": f"message:{messages[-1].id}",
        "oldest_message": f"message:{messages[0].id}",
        "previous_archive": f"archive:{archives[0]['id']}",
    }[dependency]
    memory.revoke_source(revoked)
    with pytest.raises(DomainError, match="revoked"):
        memory.recall_object(root)
    chain = memory.recall_chain([root])
    assert chain["nodes"] == [] and chain["unavailable"] == [root]
    assert "untrusted cumulative summary" not in json.dumps(chain)


@pytest.mark.parametrize("change", ["old_original", "old_capsule", "rehashed_old_capsule"])
def test_archive_recall_rejects_changed_previous_chain_even_when_head_hashes_match(
    tmp_path, change
):
    store, messages, archives = router_archives(tmp_path)
    with store.unit_of_work() as work:
        if change == "old_original":
            work.connection.execute(
                "UPDATE messages SET content='changed' WHERE id=?", (messages[0].id,)
            )
        else:
            capsule = json.loads(archives[0]["capsule_json"])
            capsule["model_handoff"]["text"] = "changed older summary"
            work.connection.execute(
                "UPDATE context_archives SET capsule_json=?,capsule_hash=? WHERE id=?",
                (
                    json.dumps(capsule),
                    _hash(capsule)
                    if change == "rehashed_old_capsule"
                    else archives[0]["capsule_hash"],
                    archives[0]["id"],
                ),
            )
    with pytest.raises(DomainError, match="integrity"):
        MemoryService(store).recall_object(f"archive:{archives[-1]['id']}")


def test_legacy_capsule_cannot_recall_revoked_inherited_message(tmp_path):
    store = Store(workspace=Workspace.from_path(tmp_path))
    first = store.add_message(ConversationMessage(role="user", content="first requirement"))
    ContextService(store).compact_router(keep_recent=0)
    store.add_message(ConversationMessage(role="user", content="second requirement"))
    ContextService(store).compact_router(keep_recent=0)
    with store.connection() as connection:
        latest = connection.execute("SELECT MAX(id) FROM context_archives").fetchone()[0]
    memory = MemoryService(store)
    assert memory.recall_object(f"archive:{latest}", max_bytes=8192)["complete"]
    memory.revoke_source(f"message:{first.id}")
    with pytest.raises(DomainError, match="revoked"):
        memory.recall_object(f"archive:{latest}")


def test_worker_archive_recall_rejects_revoked_ordinal_in_previous_chain(tmp_path):
    store = Store(workspace=Workspace.from_path(tmp_path))
    owner = worker(store)
    service = ContextService(store)
    previous = None
    with store.unit_of_work() as work:
        for ordinal in (2, 3):
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,'assistant',?)",
                (
                    owner["id"],
                    ordinal,
                    json.dumps({"role": "assistant", "content": f"source {ordinal}"}),
                ),
            )
            original = dict(
                work.connection.execute(
                    "SELECT * FROM worker_messages WHERE run_id=? AND ordinal=?",
                    (owner["id"], ordinal),
                ).fetchone()
            )
            service._record_archive(
                work.connection,
                "worker",
                owner["id"],
                [{"id": ordinal, "hash": _hash(original)}],
                {
                    "model_handoff": {
                        "text": f"worker summary {ordinal}",
                        "previous_archive": previous,
                    }
                },
                ordinal,
            )
            archived = work.connection.execute(
                "SELECT * FROM context_archives ORDER BY id DESC LIMIT 1"
            ).fetchone()
            previous = {
                "archive_id": archived["id"],
                "source_hash": archived["source_hash"],
                "capsule_hash": archived["capsule_hash"],
            }
    reference = f"archive:{previous['archive_id']}"
    memory = MemoryService(store)
    assert memory.recall_object(reference, run_id=owner["id"])["kind"] == "archive"
    stranger = worker(store)
    with pytest.raises(DomainError, match="scope"):
        memory.recall_object(reference, run_id=stranger["id"])
    # Core's internal raw transcript revocation identity is run plus ordinal;
    # it does not allow direct raw transcript recall or cross-run traversal.
    memory.revoke_source(f"worker-message:{owner['id']}:2")
    with pytest.raises(DomainError, match="revoked"):
        memory.recall_object(reference, run_id=owner["id"])
