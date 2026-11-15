"""Integration checks for the independent semantic completion gate."""

import pytest
from test_semantic_evidence_m7 import _fixture, _verify

from forge_core.domain import DomainError
from forge_core.evidence import CompletionGate, NodeVerifier
from forge_core.timeline import read_timeline


def test_semantic_worker_completes_only_after_independent_test(tmp_path):
    store, quest, _, _, _ = _fixture(tmp_path)
    result = NodeVerifier(store).verify(quest.id, "n")
    assert result["status"] == "completed"
    before = None
    items = []
    while True:
        page = read_timeline(store, before=before, limit=2)
        items.extend(page["items"])
        if not page["has_more"]:
            break
        before = page["next_before"]
    semantic = [item for item in items if item["kind"] == "semantic_test"]
    assert len(semantic) == 1 and semantic[0]["status"] == "passed"


def test_bad_semantic_candidate_cannot_complete(tmp_path):
    store, quest, _, _, _ = _fixture(tmp_path, "result = 0")
    with pytest.raises(DomainError):
        NodeVerifier(store).verify(quest.id, "n")
    with store.connection() as connection:
        assert (
            connection.execute("SELECT status FROM quests WHERE id=?", (quest.id,)).fetchone()[0]
            != "completed"
        )
    semantic = [item for item in read_timeline(store)["items"] if item["kind"] == "semantic_test"]
    assert len(semantic) == 1 and semantic[0]["status"] == "failed"
    assert "result = 0" not in str(semantic)


@pytest.mark.parametrize("tamper", ["report", "source"])
def test_gate_rejects_changed_semantic_evidence(tmp_path, tamper):
    fixture = _fixture(tmp_path)
    store, quest, plan, _, _ = fixture
    _verify(fixture)
    if tamper == "source":
        (tmp_path / "work/scheduler.py").write_text("result = 0", encoding="utf-8")
    with store.unit_of_work() as work:
        if tamper == "report":
            work.connection.execute("UPDATE semantic_test_runs SET report_json='{}'")
        current = work.get_quest(quest.id)
        approved = work.connection.execute(
            "SELECT * FROM quest_plans WHERE id=?", (plan["id"],)
        ).fetchone()
        node = work.connection.execute(
            "SELECT * FROM task_nodes WHERE plan_id=?", (plan["id"],)
        ).fetchone()
        with pytest.raises(DomainError):
            CompletionGate(store)._check_live_node(work, current, approved, node)
