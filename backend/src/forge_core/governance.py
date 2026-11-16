"""Fail-closed governance for actions; no model-controlled permission registration."""

import hashlib
import json
import ntpath
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

from .domain import DomainError, ForgeEvent, new_id, now_iso
from .security_domain import EvidenceKind

POLICY_VERSION = "1"
ACTION_RISKS = MappingProxyType(
    {
        "read": "R0",
        "workspace_write": "R1",
        "publish": "R2",
        "browser_get": "R2",
        "mcp_call": "R2",
        "python_unittest": "R2",
        "delete": "R3",
    }
)


def parameter_digest(parameters):
    try:
        encoded = json.dumps(
            parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise DomainError("Parameters must be canonical JSON") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _risk(action_type):
    try:
        return ACTION_RISKS[action_type]
    except KeyError as exc:
        raise DomainError("Unknown action is denied") from exc


class GovernanceService:
    def __init__(self, store):
        self.store = store

    def _workspace_scope(self, scope):
        if isinstance(scope, os.PathLike):
            scope = os.fspath(scope)
        if not isinstance(scope, str) or not scope or "\x00" in scope:
            raise DomainError("Invalid workspace path")
        drive, tail = ntpath.splitdrive(scope)
        if (
            (
                drive
                and (
                    os.name != "nt"
                    or not re.fullmatch(r"[A-Za-z]:", drive)
                    or not tail.startswith(("\\", "/"))
                )
            )
            or ":" in tail
            or scope.startswith(("\\\\?\\", "\\\\.\\"))
        ):
            raise DomainError("Invalid workspace path")
        parts = re.split(r"[\\/]", tail)
        if any(
            part.endswith((".", " "))
            and part not in {".", ".."}
            or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
            for part in parts
            if part
        ):
            raise DomainError("Invalid workspace path")
        root = Path(self.store.workspace.root).resolve()
        path = Path(scope)
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not path.is_relative_to(root):
            raise DomainError("Resource outside workspace")
        if any(
            part.lower() in {".env", ".git", ".ssh", "credentials", "credentials.json"}
            or part.lower().startswith(".env.")
            for part in path.parts
        ):
            raise DomainError("Resource includes protected configuration")
        database = Path(self.store.path).resolve()
        protected = {
            database,
            Path(str(database) + "-wal"),
            Path(str(database) + "-shm"),
            root / ".env",
            root / "data" / "operator-settings.json",
            root / ".forge-restore-fence.json",
        }
        if any(item == path or item.is_relative_to(path) for item in protected):
            raise DomainError("Resource includes protected Core state")
        return str(path)

    def grant_workspace(self, quest_id, scope, actor, *, expected_plan_revision=None):
        """Called by the authenticated user boundary, never by model output."""
        if not actor:
            raise DomainError("Grant requires an actor")
        scope = self._workspace_scope(scope)
        with self.store.unit_of_work() as work:
            quest = work.get_quest(quest_id)
            if quest is None:
                raise KeyError(quest_id)
            if quest["status"] in {"completed", "failed", "cancelled", "rejected"}:
                raise DomainError("Terminal Quest cannot receive grants")
            if (
                expected_plan_revision is not None
                and quest["plan_revision"] != expected_plan_revision
            ):
                raise DomainError("Quest plan revision changed")
            work.connection.execute(
                """INSERT INTO task_grants VALUES (?,?,?,?,?,?,NULL)
                ON CONFLICT(quest_id,workspace_id,resource_scope) DO UPDATE SET
                plan_revision=excluded.plan_revision, granted_by=excluded.granted_by,
                created_at=excluded.created_at,revoked_at=NULL""",
                (
                    quest_id,
                    self.store.workspace.id,
                    quest["plan_revision"],
                    scope,
                    actor,
                    now_iso(),
                ),
            )
            work.append_event(
                ForgeEvent(
                    event_type="governance.workspace_granted",
                    quest_id=quest_id,
                    payload={"scope": scope, "actor": actor},
                )
            )
        return scope

    def revoke_workspace(self, quest_id, scope, actor, *, expected_plan_revision=None):
        if not actor:
            raise DomainError("Revocation requires an actor")
        scope = self._workspace_scope(scope)
        with self.store.unit_of_work() as work:
            quest = work.get_quest(quest_id)
            if quest is None:
                raise KeyError(quest_id)
            if (
                expected_plan_revision is not None
                and quest["plan_revision"] != expected_plan_revision
            ):
                raise DomainError("Quest plan revision changed")
            cursor = work.connection.execute(
                "UPDATE task_grants SET revoked_at=? WHERE quest_id=? AND workspace_id=? AND resource_scope=? AND revoked_at IS NULL",
                (now_iso(), quest_id, self.store.workspace.id, scope),
            )
            if cursor.rowcount != 1:
                raise DomainError("Active workspace grant not found")
            work.append_event(
                ForgeEvent(
                    event_type="governance.workspace_revoked",
                    quest_id=quest_id,
                    payload={"scope": scope, "actor": actor},
                )
            )
        return scope

    def list_workspace_grants(self, quest_id):
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM task_grants WHERE quest_id=? AND workspace_id=? ORDER BY created_at",
                (quest_id, self.store.workspace.id),
            ).fetchall()
            return [dict(row) for row in rows]

    def request_approval(
        self,
        quest_id,
        action_type,
        parameters,
        resource_scope,
        risk,
        reason,
        *,
        expires_at=None,
        _work=None,
    ):
        expected = _risk(action_type)
        if risk != expected or expected == "R3":
            raise DomainError("Risk mismatch or action denied by policy")
        if not resource_scope or not reason:
            raise DomainError("Resource scope and reason are required")
        digest = parameter_digest(parameters)
        now = datetime.now(UTC)
        if expires_at is None:
            expires = now + timedelta(minutes=15)
        else:
            try:
                expires = datetime.fromisoformat(expires_at)
                if expires.tzinfo is None or expires <= now:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise DomainError("Expiry must be a future timezone-aware timestamp") from exc
        approval_id = new_id("approval")
        from contextlib import nullcontext

        with self.store.unit_of_work() if _work is None else nullcontext(_work) as work:
            quest = work.get_quest(quest_id)
            if quest is None:
                raise KeyError(quest_id)
            if quest["status"] in {"completed", "failed", "cancelled", "rejected"}:
                raise DomainError("Terminal Quest cannot request approval")
            work.connection.execute(
                """INSERT INTO approvals(id,quest_id,plan_revision,action_type,parameter_digest,resource_scope,risk,
                policy_version,reason,status,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    approval_id,
                    quest_id,
                    quest["plan_revision"],
                    action_type,
                    digest,
                    resource_scope,
                    risk,
                    POLICY_VERSION,
                    reason,
                    "PENDING",
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
            work.append_event(
                ForgeEvent(
                    event_type="approval.requested",
                    quest_id=quest_id,
                    payload={"approval_id": approval_id},
                )
            )
            result = dict(
                work.connection.execute(
                    "SELECT * FROM approvals WHERE id=?", (approval_id,)
                ).fetchone()
            )
        return result

    def get_approval(self, approval_id):
        with self.store.unit_of_work() as work:
            row = work.connection.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if (
                row["status"] in {"PENDING", "APPROVED"}
                and row["consumed_at"] is None
                and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)
            ):
                work.connection.execute(
                    "UPDATE approvals SET status='EXPIRED' WHERE id=?", (approval_id,)
                )
                work.append_event(
                    ForgeEvent(
                        event_type="approval.expired",
                        quest_id=row["quest_id"],
                        payload={"approval_id": approval_id},
                    )
                )
                row = work.connection.execute(
                    "SELECT * FROM approvals WHERE id=?", (approval_id,)
                ).fetchone()
            return dict(row)

    def list_approvals(self, quest_id):
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT id FROM approvals WHERE quest_id=? ORDER BY created_at,id", (quest_id,)
            ).fetchall()
        return [self.get_approval(row["id"]) for row in rows]

    def decide_approval(
        self, approval_id, decision, actor, *, expected_digest=None, expected_plan_revision=None
    ):
        if decision not in {"APPROVED", "REJECTED"} or not actor:
            raise DomainError("Invalid approval decision")
        expired = False
        with self.store.unit_of_work() as work:
            row = work.connection.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if row["status"] != "PENDING":
                raise DomainError("Approval is no longer pending")
            quest = work.get_quest(row["quest_id"])
            if quest is None or quest["status"] in {"completed", "failed", "cancelled", "rejected"}:
                raise DomainError("Terminal Quest cannot receive approval")
            if (
                row["plan_revision"] != quest["plan_revision"]
                or (expected_digest is not None and expected_digest != row["parameter_digest"])
                or (
                    expected_plan_revision is not None
                    and expected_plan_revision != row["plan_revision"]
                )
            ):
                raise DomainError("Approval binding changed")
            expired = datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)
            work.connection.execute(
                "UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE id=? AND status='PENDING'",
                ("EXPIRED" if expired else decision, now_iso(), actor, approval_id),
            )
            work.append_event(
                ForgeEvent(
                    event_type="approval.decided",
                    quest_id=row["quest_id"],
                    payload={
                        "approval_id": approval_id,
                        "status": "EXPIRED" if expired else decision,
                        "actor": actor,
                    },
                )
            )
        if expired:
            raise DomainError("Approval expired")
        return self.get_approval(approval_id)

    def revoke_approval(self, approval_id, actor):
        if not actor:
            raise DomainError("Revocation requires an actor")
        with self.store.unit_of_work() as work:
            row = work.connection.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if row["status"] not in {"PENDING", "APPROVED"} or row["consumed_at"]:
                raise DomainError("Approval cannot be revoked")
            work.connection.execute(
                "UPDATE approvals SET status='REVOKED' WHERE id=?", (approval_id,)
            )
            work.append_event(
                ForgeEvent(
                    event_type="approval.revoked",
                    quest_id=row["quest_id"],
                    payload={"approval_id": approval_id, "actor": actor},
                )
            )
        return self.get_approval(approval_id)

    def authorize_action(self, quest_id, action_type, parameters, resource_scope, approval_id=None):
        risk = _risk(action_type)
        digest = parameter_digest(parameters)
        if risk == "R3":
            raise DomainError("R3 actions are denied")
        with self.store.unit_of_work() as work:
            quest = work.get_quest(quest_id)
            if quest is None:
                raise KeyError(quest_id)
            if quest["status"] in {"completed", "failed", "cancelled", "rejected"}:
                raise DomainError("Terminal Quest cannot authorize actions")
            if risk in {"R0", "R1"}:
                scope = self._workspace_scope(resource_scope)
                if not isinstance(parameters, dict) or not isinstance(parameters.get("path"), str):
                    raise DomainError("File action requires a path parameter")
                target = self._workspace_scope(parameters["path"])
                if not Path(target).is_relative_to(Path(scope)):
                    raise DomainError("Action path differs from authorized scope")
            if risk == "R0":
                return True
            if risk == "R1":
                grants = work.connection.execute(
                    "SELECT resource_scope FROM task_grants WHERE quest_id=? AND workspace_id=? AND plan_revision=? AND revoked_at IS NULL",
                    (quest_id, self.store.workspace.id, quest["plan_revision"]),
                ).fetchall()
                if not any(
                    Path(scope).is_relative_to(Path(grant["resource_scope"])) for grant in grants
                ):
                    raise DomainError("Action outside authorized task scope")
                return True
            if not approval_id:
                raise DomainError("Action lacks authorization")
            row = work.connection.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if row is None or row["status"] != "APPROVED" or row["consumed_at"] is not None:
                raise DomainError("Approval unavailable")
            mismatch = (
                row["quest_id"] != quest_id
                or row["plan_revision"] != quest["plan_revision"]
                or row["action_type"] != action_type
                or row["parameter_digest"] != digest
                or row["resource_scope"] != resource_scope
                or row["risk"] != risk
                or row["policy_version"] != POLICY_VERSION
            )
            expired = datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)
            if mismatch or expired:
                work.connection.execute(
                    "UPDATE approvals SET status=? WHERE id=?",
                    ("EXPIRED" if expired else "REVOKED", approval_id),
                )
            else:
                work.connection.execute(
                    "UPDATE approvals SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                    (now_iso(), approval_id),
                )
            work.append_event(
                ForgeEvent(
                    event_type="approval.invalidated"
                    if mismatch or expired
                    else "approval.consumed",
                    quest_id=row["quest_id"],
                    payload={"approval_id": approval_id},
                )
            )
        if mismatch or expired:
            raise DomainError("Approval binding changed or expired")
        return True


class EvidenceService:
    def __init__(self, store):
        self.store = store

    def record_model_claim(self, quest_id, payload):
        evidence_id = new_id("evidence")
        with self.store.unit_of_work() as work:
            if work.get_quest(quest_id) is None:
                raise KeyError(quest_id)
            work.connection.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                (
                    evidence_id,
                    quest_id,
                    "model_claim",
                    0,
                    json.dumps(payload, ensure_ascii=False, allow_nan=False),
                    now_iso(),
                ),
            )
        return {
            "id": evidence_id,
            "quest_id": quest_id,
            "kind": "model_claim",
            "trusted": False,
            "payload": payload,
        }


class ArtifactVerifier:
    """Internal verifier: derive artifact and review records from a real file read.

    The caller is Core code, not a serialized model/HTTP request. No caller-supplied
    pass flag is accepted. This does not assert that a worker executed or a test ran.
    """

    def __init__(self, store):
        self.store = store

    def verify(self, quest_id, path):
        scope = GovernanceService(self.store)._workspace_scope(path)
        file = Path(scope)
        if not file.is_file():
            raise DomainError("Artifact must be an existing file")
        if file.stat().st_size > 1024 * 1024:
            raise DomainError("Artifact exceeds verifier limit")
        data = file.read_bytes()
        if len(data) > 1024 * 1024:
            raise DomainError("Artifact exceeds verifier limit")
        digest = hashlib.sha256(data).hexdigest()
        artifact_id, review_id = new_id("evidence"), new_id("evidence")
        payload = {"path": scope, "sha256": digest}
        with self.store.unit_of_work() as work:
            if work.get_quest(quest_id) is None:
                raise KeyError(quest_id)
            if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                raise DomainError("Artifact changed during verification")
            for evidence_id, kind, data in (
                (artifact_id, EvidenceKind.ARTIFACT, payload),
                (
                    review_id,
                    EvidenceKind.REVIEW,
                    {"artifact_id": artifact_id, "scope": "artifact_integrity", **payload},
                ),
            ):
                work.connection.execute(
                    "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                    (evidence_id, quest_id, kind.value, 1, json.dumps(data), now_iso()),
                )
            work.append_event(
                ForgeEvent(
                    event_type="evidence.artifact_verified",
                    quest_id=quest_id,
                    payload={"artifact_id": artifact_id, "review_id": review_id},
                )
            )
        return {"artifact_id": artifact_id, "review_id": review_id, **payload}
