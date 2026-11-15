from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from forge_core.domain import DomainError, Quest, QuestStatus, now_iso
from forge_core.quest_service import QuestService
from forge_core.store import Store


def test_stale_compare_and_swap_cannot_overwrite_terminal_state(tmp_path):
    path = str(tmp_path / "stale.sqlite3")
    first = Store(path)
    second = Store(path)
    quest = Quest(title="Race", goal="Do not overwrite terminal state")
    first.save_quest(quest)
    stale = first.get_quest(quest.id)
    changed = QuestService(second).transition(quest.id, QuestStatus.REJECTED)
    assert changed["version"] == 1
    assert not first.transition_quest(
        quest.id,
        "awaiting_approval",
        now_iso(),
        expected_version=stale["version"],
        expected_status=stale["status"],
    )
    assert first.get_quest(quest.id)["status"] == "rejected"
    assert first.get_quest(quest.id)["version"] == 1
    assert len(first.list_events()) == 1


@pytest.mark.parametrize("trial", range(5))
def test_real_competing_connections_only_commit_one_transition(tmp_path, trial):
    path = str(tmp_path / f"concurrency-{trial}.sqlite3")
    store = Store(path)
    quest = Quest(title="Competing commands", goal="One version wins")
    store.save_quest(quest)
    services = [QuestService(Store(path)), QuestService(Store(path))]
    barrier = Barrier(2)

    def compete(index):
        barrier.wait(timeout=10)
        target = (QuestStatus.REJECTED, QuestStatus.AWAITING_APPROVAL)[index]
        try:
            services[index].transition(quest.id, target, expected_version=0)
            return "committed"
        except DomainError as exc:
            assert "version" in str(exc).lower()
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(compete, range(2)))
    assert sorted(outcomes) == ["committed", "conflict"]
    assert store.get_quest(quest.id)["version"] == 1
    assert len(store.list_events()) == 1
    assert len(store.list_outbox()) == 1


@pytest.mark.parametrize(
    "status",
    [QuestStatus.COMPLETED, QuestStatus.FAILED, QuestStatus.CANCELLED, QuestStatus.REJECTED],
)
def test_terminal_status_rejects_transition_without_writes(tmp_path, status):
    store = Store(str(tmp_path / "terminal.sqlite3"))
    quest = Quest(title="Terminal fixture", goal="Reject transition", status=status)
    store.save_quest(quest.model_copy(update={"status": QuestStatus.PROPOSED}))
    # Historical databases can contain terminal records; emulate migration input.
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status=? WHERE id=?", (status.value, quest.id))
    with pytest.raises(DomainError, match="Illegal Quest transition"):
        QuestService(store).transition(quest.id, QuestStatus.RUNNING)
    assert store.get_quest(quest.id)["status"] == status.value
    assert store.list_events() == []


def test_api_returns_clear_conflict_for_stale_version(tmp_path, monkeypatch):
    import hashlib
    import hmac
    import importlib
    import secrets

    from fastapi.testclient import TestClient

    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "conflict-api.sqlite3"))
    secret = secrets.token_hex(32)
    monkeypatch.setenv("FORGE_CORE_SECRET", secret)
    main = importlib.import_module("forge_api.main")
    importlib.reload(main)
    quest = Quest(title="API conflict", goal="Reject stale version")
    main.store.save_quest(quest)
    with TestClient(
        main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345)
    ) as client:
        session = client.get("/health").json()["session"]
        token = hmac.new(bytes.fromhex(secret), session.encode(), hashlib.sha256).hexdigest()
        client.headers.update({"Authorization": f"Bearer {token}"})
        first = client.post(
            f"/api/quests/{quest.id}/transition",
            json={"status": "awaiting_approval", "expected_version": 0},
        )
        second = client.post(
            f"/api/quests/{quest.id}/transition", json={"status": "rejected", "expected_version": 0}
        )
        assert first.status_code == 200
        assert first.json()["version"] == 1
        assert second.status_code == 409
        assert "version" in second.json()["detail"].lower()
    assert main.store.get_quest(quest.id)["status"] == "awaiting_approval"
