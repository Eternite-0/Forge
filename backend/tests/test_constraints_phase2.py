"""Offline source/provenance and bounded-continuity checks; no live model calls."""

import json

import pytest

from forge_core.constraints import ConstraintService
from forge_core.context_service import ContextService
from forge_core.domain import ConversationMessage, DomainError, Quest
from forge_core.memory import MemoryService
from forge_core.store import Store


def message(store, text, **kwargs):
    item = ConversationMessage(role="user", content=text, **kwargs)
    store.add_message(item)
    return item


def test_model_extraction_remains_pending_and_confirmation_has_own_source(tmp_path):
    store = Store(tmp_path / "constraints.sqlite3")
    source = message(store, "Budget must stay below 2000 tokens.")
    constraints = ConstraintService(store)
    ident = constraints.record_extraction("at most 2000 tokens", source_message_id=source.id)
    record = constraints.context()["items"][0]
    assert record["confirmation_status"] == "pending_model"
    assert record["source_refs"] == [f"message:{source.id}"]
    assert len(record["source_hash"]) == 64
    with pytest.raises(ValueError, match="separate user"):
        constraints.record_extraction(
            "at most 2000 tokens", source_message_id=source.id, origin="user_confirmed"
        )
    with pytest.raises(DomainError, match="verbatim"):
        constraints.record_extraction(
            "unlimited budget", source_message_id=source.id, origin="explicit_user"
        )
    vague = message(store, "Confirm the project.")
    with pytest.raises(DomainError, match="literal"):
        constraints.confirm(ident, confirmation_message_id=vague.id)
    confirmation = message(store, "I confirm: at most 2000 tokens")
    constraints.confirm(ident, confirmation_message_id=confirmation.id)
    record = constraints.context()["items"][0]
    assert record["confirmation_status"] == "user_confirmed"
    assert record["status"] == "hypothesis"  # An instruction is not domain evidence.
    assert record["source_refs"] == [f"message:{source.id}", f"message:{confirmation.id}"]
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE messages SET content='changed' WHERE id=?", (confirmation.id,)
        )
    assert not constraints.context()["items"]


def test_conflicts_expiry_revocation_and_tamper_do_not_become_authority(tmp_path):
    store = Store(tmp_path / "conflicts.sqlite3")
    service = ConstraintService(store)
    first = message(store, "Budget must be 10 tokens")
    second = message(store, "Budget must be 20 tokens")
    service.index_message(first.id)
    service.index_message(second.id)
    assert all(item["conflicted"] for item in service.context()["items"])
    service.record_extraction(
        "old budget", source_message_id=first.id, valid_until="2000-01-01T00:00:00+00:00"
    )
    assert all(item["statement"] != "old budget" for item in service.context()["items"])
    MemoryService(store).revoke_source(f"message:{first.id}")
    records = service.context()["items"]
    assert len(records) == 1 and not records[0]["conflicted"]
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET content='tampered' WHERE id=?", (second.id,))
    assert not service.context()["items"]


def test_quest_association_is_explicit_and_current_message_index_is_idempotent(tmp_path):
    store = Store(tmp_path / "binding.sqlite3")
    first, second = Quest(title="A", goal="A"), Quest(title="B", goal="B")
    store.save_quest(first)
    store.save_quest(second)
    original = message(store, "必须用中文撰写报告", metadata={"quest_id": second.id})
    service = ConstraintService(store)
    ids = service.index_message(original.id, quest_id=first.id)
    assert ids == service.index_message(original.id, quest_id=first.id)
    with pytest.raises(DomainError, match="another Quest"):
        service.index_message(original.id, quest_id=second.id)
    with store.unit_of_work() as work:
        service.index_messages(connection=work.connection)
    assert len(service.context(quest_id=first.id)["items"]) == 1
    assert not service.context(quest_id=second.id)["items"]
    assert service.context(quest_id=first.id)["items"][0]["related_quest_ids"] == [first.id]


def test_constraint_index_and_source_cursor_roll_back_together(tmp_path):
    store = Store(tmp_path / "atomic.sqlite3")
    source = message(store, "Never delete measurements.csv")
    service = ConstraintService(store)
    with pytest.raises(RuntimeError), store.unit_of_work() as work:
        service.index_messages(connection=work.connection)
        raise RuntimeError("interrupt")
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM constraint_records").fetchone()[0] == 0
        assert db.execute("SELECT message_rowid FROM constraint_index_cursor").fetchone()[0] == 0
    service.index_message(source.id)
    assert len(service.context()["items"]) == 1


def test_compaction_bounds_long_history_and_recalls_omitted_original(tmp_path):
    store = Store(tmp_path / "long.sqlite3")
    original = []
    service = ContextService(store)
    for batch in range(3):
        for index in range(80):
            ordinal = batch * 80 + index
            original.append(message(store, f"historic_record_{ordinal} " + "x" * 800))
        service.compact_router(keep_recent=0)
        capsule = ContextService(Store(store.path, workspace=store.workspace)).rebuild_router()[
            "capsule"
        ]
        assert len(json.dumps(capsule["hypotheses"]).encode("utf-8")) < 18000
        assert len(capsule["hypotheses"]) <= 80
    assert capsule["history_recall"]["omitted_highlights"] > 0
    assert capsule["history_recall"]["archived_message_count"] == 240
    omitted = original[30]
    assert not any(omitted.id in item["source_refs"] for item in capsule["hypotheses"])
    memory = MemoryService(Store(store.path, workspace=store.workspace))
    recalled = memory.retrieve_context("historic_record_30")
    assert any(f"message:{omitted.id}" in item["source_refs"] for item in recalled["items"])
    assert service.read_source("router", "main", omitted.id)["content"] == omitted.content
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 240


def test_duplicate_user_statements_keep_bounded_refs_without_deleting_sources(tmp_path):
    store = Store(tmp_path / "duplicates.sqlite3")
    for _ in range(30):
        message(store, "same instruction")
    service = ContextService(store)
    service.compact_router(keep_recent=0)
    hypothesis = service.rebuild_router()["capsule"]["hypotheses"][0]
    assert len(hypothesis["source_refs"]) == 4
    assert hypothesis["omitted"]
    assert len(store.list_messages()) == 30


def test_bilingual_recall_time_and_source_filters_and_source_hashes(tmp_path):
    store = Store(tmp_path / "retrieval.sqlite3")
    source = message(store, "预算不得超过12000 tokens", created_at="2026-11-15T00:00:00+00:00")
    constraints = ConstraintService(store)
    constraints.index_message(source.id)
    recalled = MemoryService(store).retrieve_context("spending", source_types=["user_instruction"])
    assert recalled["items"] and recalled["items"][0]["source_refs"] == [f"message:{source.id}"]
    assert len(recalled["items"][0]["source_hash"]) == 64
    assert not MemoryService(store).retrieve_context(
        "spending", created_before="2020-01-01T00:00:00+00:00"
    )["items"]
    with pytest.raises(ValueError, match="timezone"):
        MemoryService(store).retrieve_context("spending", created_after="2020-01-01")
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET content='injected' WHERE id=?", (source.id,))
    assert not MemoryService(store).retrieve_context("spending")["items"]


def test_constraint_context_reports_omissions_and_respects_utf8_byte_bound(tmp_path):
    store = Store(tmp_path / "bounded.sqlite3")
    service = ConstraintService(store)
    for index in range(25):
        item = message(store, f"必须保留实验记录{index}")
        service.index_message(item.id)
    context = service.context(limit=3, max_bytes=2200)
    encoded = json.dumps(
        context["items"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert len(encoded.encode("utf-8")) <= 2200
    assert 0 < len(context["items"]) <= 3 and context["omitted"] > 0
    assert service.context(max_bytes=1)["items"] == []
