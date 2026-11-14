from concurrent.futures import ThreadPoolExecutor

import pytest

from forge_core.domain import DomainError, Quest
from forge_core.governance import GovernanceService
from forge_core.store import Store


def setup_governance(tmp_path):
    store = Store(tmp_path / "governance.sqlite3")
    quest = Quest(title="Approval", goal="Govern action")
    store.save_quest(quest)
    return store, quest, GovernanceService(store)


def test_approval_binds_parameters_and_is_single_use(tmp_path):
    _store, quest, service = setup_governance(tmp_path)
    approval = service.request_approval(
        quest.id, "publish", {"path": "a"}, "remote", "R2", "release"
    )
    service.decide_approval(approval["id"], "APPROVED", "user")
    with pytest.raises(DomainError):
        service.authorize_action(quest.id, "publish", {"path": "b"}, "remote", approval["id"])
    assert service.get_approval(approval["id"])["status"] == "REVOKED"
    with pytest.raises(DomainError):
        service.authorize_action(quest.id, "publish", {"path": "a"}, "remote", approval["id"])


def test_approval_concurrent_consumption(tmp_path):
    store, quest, service = setup_governance(tmp_path)
    approval = service.request_approval(
        quest.id, "publish", {"path": "a"}, "remote", "R2", "release"
    )
    service.decide_approval(approval["id"], "APPROVED", "user")

    def consume(_):
        try:
            GovernanceService(store).authorize_action(
                quest.id, "publish", {"path": "a"}, "remote", approval["id"]
            )
            return True
        except DomainError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(consume, range(2))) == [False, True]


def test_default_deny_and_risk_cannot_be_downgraded(tmp_path):
    _, quest, service = setup_governance(tmp_path)
    with pytest.raises(DomainError):
        service.authorize_action(quest.id, "publish", {}, "remote")
    with pytest.raises(DomainError):
        service.request_approval(quest.id, "publish", {}, "remote", "R0", "downgrade")
    with pytest.raises(DomainError):
        service.request_approval(quest.id, "delete", {}, "workspace", "R3", "delete")


def test_workspace_grant_and_protected_paths(tmp_path):
    store, quest, service = setup_governance(tmp_path)
    root = store.workspace.root
    # Isolate the actual workspace path selected by Store settings.
    target = root / "project" / "file.txt"
    assert service.authorize_action(quest.id, "read", {"path": str(target)}, str(target))
    with pytest.raises(DomainError):
        service.authorize_action(quest.id, "workspace_write", {"path": str(target)}, str(target))
    service.grant_workspace(quest.id, str(root / "project"), "user")
    assert service.authorize_action(quest.id, "workspace_write", {"path": str(target)}, str(target))
    assert len(service.list_workspace_grants(quest.id)) == 1
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (quest.id,)
        )
    with pytest.raises(DomainError):
        service.authorize_action(quest.id, "workspace_write", {"path": str(target)}, str(target))
    service.grant_workspace(quest.id, str(root / "project"), "user")
    service.revoke_workspace(quest.id, str(root / "project"), "user")
    with pytest.raises(DomainError):
        service.authorize_action(quest.id, "workspace_write", {"path": str(target)}, str(target))
    for protected in (
        str(store.path),
        str(root / ".env"),
        str(root / "project" / ".env.local"),
        str(root.parent / "outside"),
    ):
        with pytest.raises(DomainError):
            service.authorize_action(quest.id, "read", {"path": protected}, protected)
    with pytest.raises(DomainError):
        service.authorize_action(quest.id, "read", {"path": str(root / "other")}, str(target))


def test_rejected_revoked_and_expired_approvals(tmp_path):
    from datetime import UTC, datetime, timedelta

    store, quest, service = setup_governance(tmp_path)

    def request():
        return service.request_approval(quest.id, "publish", {}, "remote", "R2", "release")

    rejected = request()
    assert service.decide_approval(rejected["id"], "REJECTED", "user")["status"] == "REJECTED"
    revoked = request()
    assert service.revoke_approval(revoked["id"], "user")["status"] == "REVOKED"
    expired = request()
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE approvals SET expires_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), expired["id"]),
        )
    with pytest.raises(DomainError):
        service.decide_approval(expired["id"], "APPROVED", "user")
    assert service.get_approval(expired["id"])["status"] == "EXPIRED"
    assert len(store.list_events()) == len(store.list_outbox())


def test_approval_invalidation_on_binding_changes(tmp_path):
    store, quest, service = setup_governance(tmp_path)
    other = Quest(title="other", goal="other")
    store.save_quest(other)
    for other_quest, parameters, scope in [
        (other.id, {}, "remote"),
        (quest.id, {"x": 1}, "remote"),
        (quest.id, {}, "elsewhere"),
    ]:
        approval = service.request_approval(quest.id, "publish", {}, "remote", "R2", "release")
        service.decide_approval(approval["id"], "APPROVED", "user")
        with pytest.raises(DomainError):
            service.authorize_action(other_quest, "publish", parameters, scope, approval["id"])
        assert service.get_approval(approval["id"])["status"] == "REVOKED"


def test_approval_and_outbox_rollback_together(tmp_path):
    import sqlite3

    store, quest, service = setup_governance(tmp_path)
    with store.connection() as connection:
        connection.execute("""CREATE TRIGGER abort_approval_event BEFORE INSERT ON outbox
            BEGIN SELECT RAISE(ABORT, 'outbox failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="outbox failure"):
        service.request_approval(quest.id, "publish", {}, "remote", "R2", "release")
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert store.list_events() == []
    assert store.list_outbox() == []


def test_changed_policy_invalidates_existing_approval(tmp_path):
    store, quest, service = setup_governance(tmp_path)
    approval = service.request_approval(quest.id, "publish", {}, "remote", "R2", "release")
    service.decide_approval(approval["id"], "APPROVED", "user")
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE approvals SET policy_version='old' WHERE id=?", (approval["id"],)
        )
    with pytest.raises(DomainError):
        service.authorize_action(quest.id, "publish", {}, "remote", approval["id"])
    assert service.get_approval(approval["id"])["status"] == "REVOKED"


def test_plan_revision_invalidates_approval_but_status_version_does_not(tmp_path):
    store, quest, service = setup_governance(tmp_path)
    approval = service.request_approval(quest.id, "publish", {}, "remote", "R2", "release")
    service.decide_approval(approval["id"], "APPROVED", "user")
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET version=version+1 WHERE id=?", (quest.id,))
    assert service.authorize_action(quest.id, "publish", {}, "remote", approval["id"])
    approval = service.request_approval(quest.id, "publish", {}, "remote", "R2", "release")
    service.decide_approval(approval["id"], "APPROVED", "user")
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET plan_revision=plan_revision+1 WHERE id=?", (quest.id,)
        )
    with pytest.raises(DomainError):
        service.authorize_action(quest.id, "publish", {}, "remote", approval["id"])
    assert service.get_approval(approval["id"])["status"] == "REVOKED"
