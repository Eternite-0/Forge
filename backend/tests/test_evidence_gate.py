import pytest

from forge_core.domain import DomainError, Quest, QuestStatus, now_iso
from forge_core.quest_service import QuestService
from forge_core.store import Store


def test_initial_execution_state_and_direct_cas_are_blocked(tmp_path):
    store = Store(tmp_path / "gate.sqlite3")
    with pytest.raises(DomainError):
        store.save_quest(Quest(title="fake", goal="fake", status=QuestStatus.COMPLETED))
    quest = Quest(title="real", goal="real")
    store.save_quest(quest)
    QuestService(store).transition(quest.id, QuestStatus.AWAITING_APPROVAL)
    with pytest.raises(DomainError):
        store.transition_quest(
            quest.id, "approved", now_iso(), expected_version=1, expected_status="awaiting_approval"
        )
    with pytest.raises(DomainError):
        QuestService(store).transition(quest.id, QuestStatus.APPROVED)


def test_model_cannot_assert_trusted_test_evidence(tmp_path):
    from forge_core.governance import EvidenceService

    store = Store(tmp_path / "evidence.sqlite3")
    quest = Quest(title="test", goal="test")
    store.save_quest(quest)
    evidence = EvidenceService(store).record_model_claim(quest.id, {"passed": True})
    assert evidence["kind"] == "model_claim"
    assert not evidence["trusted"]
    with pytest.raises(DomainError):
        QuestService(store).complete(quest.id)


def test_artifact_verifier_hashes_actual_file_and_limits_review_scope(tmp_path):
    import hashlib
    import json

    from forge_core.governance import ArtifactVerifier
    from forge_core.settings import Workspace

    store = Store(workspace=Workspace.from_path(tmp_path))
    quest = Quest(title="artifact", goal="verify real bytes")
    store.save_quest(quest)
    artifact = tmp_path / "result.txt"
    artifact.write_bytes(b"real result")
    result = ArtifactVerifier(store).verify(quest.id, artifact)
    assert result["sha256"] == hashlib.sha256(b"real result").hexdigest()
    with store.connection() as connection:
        review = connection.execute(
            "SELECT * FROM evidence WHERE id=?", (result["review_id"],)
        ).fetchone()
    assert json.loads(review["payload"])["scope"] == "artifact_integrity"
    with pytest.raises(DomainError):
        QuestService(store).complete(quest.id)
