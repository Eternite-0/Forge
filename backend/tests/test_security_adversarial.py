"""Adversarial regressions for the local Core trust boundary."""

import importlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from forge_core.domain import Quest


@pytest.mark.parametrize(
    "origin", ["https://attacker.invalid", "null", "http://localhost:5173.evil.invalid"]
)
def test_cross_origin_simple_post_cannot_create_worker(tmp_path, monkeypatch, origin):
    monkeypatch.setenv("FORGE_DB_PATH", str(tmp_path / "cross-origin.sqlite3"))
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    from forge_api import main

    importlib.reload(main)
    quest = Quest(title="Cross-origin", goal="Reject unsolicited writes")
    main.store.save_quest(quest)
    with TestClient(
        main.app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 12345)
    ) as client:
        before_events = main.store.list_events()
        before_outbox = main.store.list_outbox(pending_only=False)
        before_quest = main.store.get_quest(quest.id)
        assert [event["event_type"] for event in before_events] == ["core.started"]
        response = client.post(
            f"/api/quests/{quest.id}/workers?role=untrusted-site",
            headers={"Origin": origin, "Authorization": f"Bearer {main.guard._token}"},
        )
        assert main.store.list_events() == before_events
        assert [row["event_id"] for row in main.store.list_outbox(pending_only=False)] == [
            row["event_id"] for row in before_outbox
        ]
        assert main.store.get_quest(quest.id) == before_quest
    assert response.status_code == 403
    assert main.store.list_workers(quest.id) == []
    events = main.store.list_events()
    assert [event["event_type"] for event in events] == ["core.started", "core.stopped"]
    assert (
        json.loads(events[0]["payload"])["generation"]
        == json.loads(events[1]["payload"])["generation"]
    )
    assert {event["id"] for event in events} == {
        row["event_id"] for row in main.store.list_outbox(pending_only=False)
    }
