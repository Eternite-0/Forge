"""Evidence derived by Core verifiers and a fail-closed completion gate."""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .domain import DomainError, ForgeEvent, new_id, now_iso
from .governance import POLICY_VERSION, GovernanceService, parameter_digest

_VALIDATOR = "import json,sys; json.load(sys.stdin, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))"
_METHOD = "fixed_json_validator_v1"
_MAX_BYTES = 1024 * 1024


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _read_artifact(path):
    try:
        with path.open("rb") as stream:
            data = stream.read(_MAX_BYTES + 1)
    except OSError as exc:
        raise DomainError("Artifact cannot be read") from exc
    if len(data) > _MAX_BYTES:
        raise DomainError("Artifact exceeds verifier limit")
    return data


def _approval_valid(approval, quest):
    try:
        decided = datetime.fromisoformat(approval["decided_at"])
        consumed = datetime.fromisoformat(approval["consumed_at"])
        expires = datetime.fromisoformat(approval["expires_at"])
        return (
            approval["quest_id"] == quest["id"]
            and approval["plan_revision"] == quest["plan_revision"]
            and approval["status"] == "APPROVED"
            and approval["policy_version"] == POLICY_VERSION
            and decided.tzinfo is not None
            and consumed.tzinfo is not None
            and expires.tzinfo is not None
            and decided <= consumed < expires
        )
    except (KeyError, TypeError, ValueError):
        return False


def _verify_worker_context_call(store, connection, run_id, call):
    """Recheck Core's read-only context tool against its own archived source."""
    from .worker_runtime import _json, _read_action_receipt, _read_worker_source_slice

    try:
        if call["state"] != "succeeded" or call["receipt_id"] is not None:
            return False
        arguments = json.loads(call["arguments_json"])
        result = _json(
            _read_worker_source_slice(store, run_id, arguments, connection=connection)
            if call["tool_name"] == "worker_read_context_source"
            else _read_action_receipt(store, connection, run_id, arguments)
        )
        if call["result_json"] != result:
            return False
        messages = connection.execute(
            "SELECT message_json FROM worker_messages WHERE run_id=? AND role='tool'",
            (run_id,),
        ).fetchall()
        for row in messages:
            message = json.loads(row["message_json"])
            if (
                isinstance(message, dict)
                and message.get("tool_call_id") == call["model_call_id"]
                and message.get("content") == result
            ):
                return True
        return False
    except (DomainError, KeyError, TypeError, ValueError):
        return False


class JsonVerifier:
    """Run one fixed, non-shell JSON check; callers cannot supply commands or results."""

    def __init__(self, store):
        self.store = store

    def verify(self, quest_id, worker_id, path):
        path = Path(GovernanceService(self.store)._workspace_scope(path))
        data = _read_artifact(path)
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-c", _VALIDATOR],
                input=data,
                capture_output=True,
                timeout=10,
                check=False,
                cwd=self.store.workspace.root,
                env={key: os.environ[key] for key in ("SYSTEMROOT",) if key in os.environ},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DomainError("JSON verifier failed to run") from exc
        run_id, artifact_id = new_id("run"), new_id("evidence")
        check_id = f"json_valid:{path}"
        with self.store.unit_of_work() as work:
            quest = work.get_quest(quest_id)
            worker = work.connection.execute(
                "SELECT * FROM workers WHERE id=? AND quest_id=?", (worker_id, quest_id)
            ).fetchone()
            if quest is None or worker is None:
                raise DomainError("Quest and associated worker are required")
            current = _read_artifact(path)
            if _hash(current) != _hash(data):
                raise DomainError("Artifact changed during verification")
            revision = quest["plan_revision"]
            work.connection.execute(
                """INSERT INTO execution_runs
                (id,quest_id,plan_revision,worker_id,kind,execution_mode,command_json,cwd,
                 provenance,exit_code,stdout_sha256,stderr_sha256,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    quest_id,
                    revision,
                    worker_id,
                    "json_validation",
                    "live",
                    json.dumps([sys.executable, "-I", "-c", _VALIDATOR]),
                    str(self.store.workspace.root),
                    _METHOD,
                    result.returncode,
                    _hash(result.stdout),
                    _hash(result.stderr),
                    now_iso(),
                ),
            )
            artifact = {"path": str(path), "sha256": _hash(data), "plan_revision": revision}
            work.connection.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                (artifact_id, quest_id, "artifact", 1, json.dumps(artifact), now_iso()),
            )
            if result.returncode == 0:
                payload = {
                    "check_id": check_id,
                    "artifact_id": artifact_id,
                    "run_id": run_id,
                    "plan_revision": revision,
                    "method": _METHOD,
                }
                for kind in ("test", "review"):
                    work.connection.execute(
                        "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                        (new_id("evidence"), quest_id, kind, 1, json.dumps(payload), now_iso()),
                    )
            work.append_event(
                ForgeEvent(
                    event_type="evidence.json_checked",
                    quest_id=quest_id,
                    worker_id=worker_id,
                    payload={"run_id": run_id},
                )
            )
        return {"run_id": run_id, "artifact_id": artifact_id, "exit_code": result.returncode}


class CompletionGate:
    """Check all declared requirements in the caller's completion transaction.

    Graph nodes require live Worker receipts and fixed independent checks.
    Legacy requirement-only completion remains closed without an injected authority.
    """

    def __init__(self, store, *, verify_model_run=None):
        self.store = store
        self.verify_model_run = verify_model_run

    def check(self, work, quest):
        plan = work.connection.execute(
            "SELECT * FROM quest_plans WHERE quest_id=? AND revision=? AND status='approved'",
            (quest["id"], quest["plan_revision"]),
        ).fetchone()
        if plan is not None:
            nodes = work.connection.execute(
                "SELECT * FROM task_nodes WHERE plan_id=? ORDER BY node_key", (plan["id"],)
            ).fetchall()
            if not nodes:
                raise DomainError("Approved plan has no tasks")
            states = {node["node_key"]: node for node in nodes}
            for node in nodes:
                if node["status"] == "succeeded":
                    self._check_live_node(work, quest, plan, node)
                elif node["status"] == "failed":
                    self._check_failed_node(work, quest, plan, node)
                    recovery = work.connection.execute(
                        "SELECT target FROM task_edges WHERE plan_id=? AND source=? AND condition='failure'",
                        (plan["id"], node["node_key"]),
                    ).fetchall()
                    if not any(
                        states[edge["target"]]["status"] == "succeeded" for edge in recovery
                    ):
                        raise DomainError("Failed task has no verified recovery")
                elif node["status"] == "skipped":
                    self._check_skipped_node(work, quest, plan, node, states, set())
                else:
                    raise DomainError("Every task requires independent verification")
            requirements = work.connection.execute(
                "SELECT * FROM quest_requirements WHERE quest_id=?", (quest["id"],)
            ).fetchone()
            if requirements is not None:
                if requirements["plan_revision"] != quest["plan_revision"]:
                    raise DomainError("Current requirements are required")
                try:
                    approvals = json.loads(requirements["required_approvals_json"])
                    if not isinstance(approvals, list) or any(
                        not isinstance(item, str) for item in approvals
                    ):
                        raise ValueError
                except (ValueError, TypeError) as exc:
                    raise DomainError("Invalid completion requirements") from exc
                for approval_id in approvals:
                    action = work.connection.execute(
                        """SELECT a.* FROM action_intents a JOIN action_receipts r ON r.intent_id=a.id
                        WHERE a.approval_id=? AND a.quest_id=? AND a.plan_revision=? AND a.state='succeeded'
                        AND a.execution_mode='live' AND r.execution_mode='live'""",
                        (approval_id, quest["id"], quest["plan_revision"]),
                    ).fetchone()
                    approval = work.connection.execute(
                        "SELECT * FROM approvals WHERE id=?", (approval_id,)
                    ).fetchone()
                    if (
                        action is None
                        or approval is None
                        or not _approval_valid(dict(approval), quest)
                    ):
                        raise DomainError("Required approval lacks a verified action receipt")
            return
        row = work.connection.execute(
            "SELECT * FROM quest_requirements WHERE quest_id=?", (quest["id"],)
        ).fetchone()
        if row is None or row["plan_revision"] != quest["plan_revision"]:
            raise DomainError("Current requirements are required")
        try:
            criteria = json.loads(row["criteria_json"])
            tests = json.loads(row["required_tests_json"])
            approvals = json.loads(row["required_approvals_json"])
            if (
                any(
                    not isinstance(items, list)
                    or any(not isinstance(x, str) or not x for x in items)
                    for items in (criteria, tests, approvals)
                )
                or not criteria
                or not tests
            ):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise DomainError("Invalid completion requirements") from exc
        runs = work.connection.execute(
            """SELECT r.* FROM execution_runs r JOIN workers w ON w.id=r.worker_id
            WHERE r.quest_id=? AND w.quest_id=r.quest_id AND r.plan_revision=?
            AND r.kind='model_worker' AND r.execution_mode='live' AND r.exit_code=0""",
            (quest["id"], quest["plan_revision"]),
        ).fetchall()
        if self.verify_model_run is None or not any(
            self.verify_model_run(dict(run)) for run in runs
        ):
            raise DomainError("Verified live Worker run required")
        for approval_id in approvals:
            approval = work.connection.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if approval is None or not _approval_valid(dict(approval), quest):
                raise DomainError("Required approval is unavailable")
            # A consumed approval is not an execution receipt. The governed action
            # runtime and its atomic approval-to-run binding do not exist yet.
            raise DomainError("Required approval lacks a verified action receipt")
        for kind, required in (("test", tests), ("review", criteria)):
            for check_id in required:
                if not self._verified_check(work, quest, kind, check_id):
                    raise DomainError(f"Required {kind} evidence is missing or invalid")

    def _verified_check(
        self, work, quest, kind, check_id, *, worker_id=None, worker_run_id=None, not_before=None
    ):
        rows = work.connection.execute(
            "SELECT payload,created_at FROM evidence WHERE quest_id=? AND kind=? AND trusted=1",
            (quest["id"], kind),
        ).fetchall()
        for row in rows:
            try:
                if not_before is not None and row["created_at"] < not_before:
                    continue
                payload = json.loads(row["payload"])
                if check_id.startswith("semantic_numeric:"):
                    if (
                        payload.get("check_id") != check_id
                        or payload.get("method") != "fixed_numeric_suite_v1"
                        or payload.get("plan_revision") != quest["plan_revision"]
                        or worker_id is None
                    ):
                        continue
                    from .semantic_evidence import SemanticVerifier

                    semantic = work.connection.execute(
                        "SELECT * FROM semantic_test_runs WHERE id=? AND quest_id=?",
                        (payload.get("semantic_run_id"), quest["id"]),
                    ).fetchone()
                    if (
                        semantic is None
                        or semantic["worker_id"] != worker_id
                        or semantic["worker_run_id"] != worker_run_id
                        or check_id
                        != f"semantic_numeric:{semantic['suite_id']}:{semantic['artifact_path']}"
                    ):
                        continue
                    if SemanticVerifier(self.store).validate_evidence(
                        work.connection,
                        quest_id=quest["id"],
                        plan_id=semantic["plan_id"],
                        plan_revision=quest["plan_revision"],
                        worker_id=worker_id,
                        worker_run_id=worker_run_id,
                        semantic_run_id=semantic["id"],
                    ):
                        return True
                    continue
                if (
                    payload["check_id"] != check_id
                    or payload["method"] != _METHOD
                    or payload["plan_revision"] != quest["plan_revision"]
                ):
                    continue
                run = work.connection.execute(
                    """SELECT r.* FROM execution_runs r JOIN workers w ON w.id=r.worker_id
                    WHERE r.id=? AND r.quest_id=? AND w.quest_id=r.quest_id""",
                    (payload["run_id"], quest["id"]),
                ).fetchone()
                artifact = work.connection.execute(
                    "SELECT payload FROM evidence WHERE id=? AND quest_id=? AND kind='artifact' AND trusted=1",
                    (payload["artifact_id"], quest["id"]),
                ).fetchone()
                if (
                    run is None
                    or artifact is None
                    or run["kind"] != "json_validation"
                    or run["execution_mode"] != "live"
                    or run["provenance"] != _METHOD
                    or run["exit_code"] != 0
                    or run["plan_revision"] != quest["plan_revision"]
                    or (worker_id is not None and run["worker_id"] != worker_id)
                ):
                    continue
                data = json.loads(artifact["payload"])
                path = Path(GovernanceService(self.store)._workspace_scope(data["path"]))
                if (
                    data["plan_revision"] == quest["plan_revision"]
                    and check_id == f"json_valid:{path}"
                    and _hash(_read_artifact(path)) == data["sha256"]
                ):
                    return True
            except (KeyError, ValueError, TypeError, OSError, DomainError):
                continue
        return False

    def _check_live_node(self, work, quest, plan, node):
        import jsonschema

        from .team_composition import TeamCompositionService

        run = work.connection.execute(
            "SELECT * FROM worker_runs WHERE quest_id=? AND plan_id=? AND node_key=? AND worker_id=? AND state IN ('review_pending','succeeded') AND execution_mode='live' ORDER BY created_at DESC LIMIT 1",
            (quest["id"], plan["id"], node["node_key"], node["worker_id"]),
        ).fetchone()
        if (
            run is None
            or run["plan_revision"] != quest["plan_revision"]
            or run["plan_digest"] != plan["digest"]
        ):
            raise DomainError("Verified live Worker run required")
        team = TeamCompositionService(self.store).get_historical_approved(
            quest["id"],
            run["plan_revision"],
            run["team_digest"],
            connection=work.connection,
            run_id=run["id"],
        )
        try:
            member = team["members"][run["member_index"]]
            if (
                member["task_node_key"] != node["node_key"]
                or member["role_id"] != run["role_id"]
                or member["provider_id"] != run["provider_id"]
                or member["model_id"] != run["model_id"]
            ):
                raise ValueError
            message = work.connection.execute(
                "SELECT message_json FROM worker_messages WHERE run_id=? AND role='assistant' ORDER BY ordinal DESC LIMIT 1",
                (run["id"],),
            ).fetchone()
            parsed = json.loads(message["message_json"])
            if parsed.get("tool_calls"):
                raise ValueError
            jsonschema.validate(json.loads(parsed["content"]), member["output_schema"])
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            jsonschema.ValidationError,
            jsonschema.SchemaError,
        ) as exc:
            raise DomainError("Worker result is invalid") from exc
        event = work.connection.execute(
            "SELECT created_at FROM events WHERE event_type='worker.result' AND quest_id=? AND worker_id=? AND json_extract(payload,'$.run_id')=? ORDER BY seq DESC LIMIT 1",
            (quest["id"], run["worker_id"], run["id"]),
        ).fetchone()
        if event is None:
            raise DomainError("Worker result provenance is missing")
        calls = work.connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=?", (run["id"],)
        ).fetchall()
        produced = {}
        for call in calls:
            if call["tool_name"] in {"worker_read_context_source", "worker_read_action_receipt"}:
                if not _verify_worker_context_call(self.store, work.connection, run["id"], call):
                    raise DomainError("Worker context source provenance is invalid")
                if (
                    call["tool_name"] == "worker_read_action_receipt"
                    and json.loads(call["result_json"])["execution_mode"] != "live"
                ):
                    raise DomainError("Mock receipt cannot prove live completion")
                continue
            if call["state"] != "succeeded" or not call["receipt_id"]:
                raise DomainError("Worker action receipt is unavailable")
            receipt = work.connection.execute(
                """SELECT r.*,a.state,a.call_id,a.tool_name,a.parameters_json,a.parameter_digest,a.resource_scope,a.approval_id,a.risk
                FROM action_receipts r JOIN action_intents a ON a.id=r.intent_id
                WHERE r.id=? AND a.run_id=? AND a.quest_id=? AND a.plan_revision=?""",
                (call["receipt_id"], run["id"], quest["id"], quest["plan_revision"]),
            ).fetchone()
            if (
                receipt is None
                or receipt["state"] != "succeeded"
                or receipt["execution_mode"] != "live"
                or receipt["call_id"] != call["call_id"]
                or _hash(receipt["result_json"].encode("utf-8")) != receipt["result_digest"]
            ):
                raise DomainError("Worker action receipt is invalid")
            try:
                if (
                    receipt["tool_name"] != call["tool_name"]
                    or parameter_digest(json.loads(receipt["parameters_json"]))
                    != receipt["parameter_digest"]
                    or parameter_digest(json.loads(call["arguments_json"]))
                    != receipt["parameter_digest"]
                ):
                    raise ValueError
                proof = work.connection.execute(
                    "SELECT payload FROM evidence WHERE quest_id=? AND kind='tool' AND trusted=1",
                    (quest["id"],),
                ).fetchall()
                if not any(
                    (data := json.loads(item["payload"])).get("receipt_id") == receipt["id"]
                    and data.get("result_digest") == receipt["result_digest"]
                    and data.get("execution_mode") == "live"
                    and data.get("state") == "succeeded"
                    for item in proof
                ):
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise DomainError("Worker action provenance is invalid") from exc
            if receipt["risk"] == "R2":
                approval = work.connection.execute(
                    "SELECT * FROM approvals WHERE id=?", (receipt["approval_id"],)
                ).fetchone()
                if (
                    approval is None
                    or not _approval_valid(dict(approval), quest)
                    or approval["parameter_digest"] != receipt["parameter_digest"]
                    or approval["resource_scope"] != receipt["resource_scope"]
                    or approval["action_type"] != receipt["tool_name"]
                ):
                    raise DomainError("Worker action approval is invalid")
            if receipt["tool_name"] == "file_write":
                produced[receipt["resource_scope"]] = (
                    json.loads(receipt["result_json"]).get("sha256"),
                    receipt["id"],
                )
        try:
            criteria = json.loads(node["criteria_json"])
        except (ValueError, TypeError) as exc:
            raise DomainError("Invalid task criteria") from exc
        if not criteria:
            raise DomainError("Task criteria are required")
        for criterion in criteria:
            if not isinstance(criterion, str):
                raise DomainError("Task criterion lacks an independent verifier")
            if criterion.startswith("json_valid:"):
                prefix = "json_valid:"
            elif criterion.startswith("semantic_numeric:scheduler-v1:"):
                prefix = "semantic_numeric:scheduler-v1:"
            else:
                raise DomainError("Task criterion lacks an independent verifier")
            path = str(
                Path(GovernanceService(self.store)._workspace_scope(criterion.removeprefix(prefix)))
            )
            if path not in produced or _hash(_read_artifact(Path(path))) != produced[path][0]:
                raise DomainError("Verified artifact was not produced by this Worker")
            check_id = f"{prefix}{path}"
            if prefix.startswith("semantic_numeric:"):
                semantic = work.connection.execute(
                    "SELECT 1 FROM semantic_test_runs WHERE quest_id=? AND plan_id=? AND worker_run_id=? AND receipt_id=? AND artifact_path=? AND source_sha256=? AND suite_id='scheduler-v1'",
                    (
                        quest["id"],
                        plan["id"],
                        run["id"],
                        produced[path][1],
                        path,
                        produced[path][0],
                    ),
                ).fetchone()
                if semantic is None:
                    raise DomainError("Semantic test does not match this Worker artifact")
            for kind in ("test", "review"):
                if not self._verified_check(
                    work,
                    quest,
                    kind,
                    check_id,
                    worker_id=run["worker_id"],
                    worker_run_id=run["id"],
                    not_before=event["created_at"],
                ):
                    raise DomainError(f"Required {kind} evidence is missing or invalid")
        return run

    def _check_failed_node(self, work, quest, plan, node):
        run = work.connection.execute(
            "SELECT * FROM worker_runs WHERE quest_id=? AND plan_id=? AND node_key=? AND worker_id=? AND state='failed' AND execution_mode='live' AND plan_revision=? AND plan_digest=?",
            (
                quest["id"],
                plan["id"],
                node["node_key"],
                node["worker_id"],
                quest["plan_revision"],
                plan["digest"],
            ),
        ).fetchone()
        if run is None:
            raise DomainError("Failed task lacks a verified live Worker")
        event = work.connection.execute(
            "SELECT 1 FROM events WHERE event_type='worker.failed' AND quest_id=? AND worker_id=? AND json_extract(payload,'$.run_id')=? LIMIT 1",
            (quest["id"], node["worker_id"], run["id"]),
        ).fetchone()
        unresolved = work.connection.execute(
            "SELECT 1 FROM action_intents WHERE run_id=? AND state IN ('prepared','executing','uncertain') LIMIT 1",
            (run["id"],),
        ).fetchone()
        if event is None or unresolved:
            raise DomainError("Failed task has unresolved execution")

    def _check_skipped_node(self, work, quest, plan, node, states, visiting):
        key = node["node_key"]
        if key in visiting:
            raise DomainError("Invalid conditional graph")
        visiting = visiting | {key}
        edges = work.connection.execute(
            "SELECT source,condition FROM task_edges WHERE plan_id=? AND target=?",
            (plan["id"], key),
        ).fetchall()
        if not edges:
            raise DomainError("Root task cannot be skipped")
        failed_condition = False
        for edge in edges:
            predecessor = states.get(edge["source"])
            if predecessor is None or predecessor["status"] not in {
                "succeeded",
                "failed",
                "skipped",
            }:
                raise DomainError("Skipped task has unfinished predecessor")
            if predecessor["status"] == "succeeded":
                self._check_live_node(work, quest, plan, predecessor)
            elif predecessor["status"] == "failed":
                self._check_failed_node(work, quest, plan, predecessor)
            else:
                self._check_skipped_node(work, quest, plan, predecessor, states, visiting)
            if (edge["condition"] == "success" and predecessor["status"] != "succeeded") or (
                edge["condition"] == "failure" and predecessor["status"] != "failed"
            ):
                failed_condition = True
        if not failed_condition:
            raise DomainError("Skipped task has no unsatisfied condition")


class NodeVerifier:
    """Run fixed independent checks and atomically advance only verified live tasks."""

    def __init__(self, store):
        self.store = store

    def tick(self):
        """Claim one durable, bounded independent verification attempt fairly.

        Unsupported or failed criteria stop after one attempt for manual review.
        Only crashed in-flight attempts retry, at most three times across restart.
        """
        token = secrets.token_hex(24)
        now = time.time()
        with self.store.unit_of_work() as work:
            work.connection.execute("""INSERT OR IGNORE INTO node_verification_attempts(plan_id,node_key,worker_id)
                SELECT n.plan_id,n.node_key,n.worker_id FROM task_nodes n JOIN quest_plans p ON p.id=n.plan_id
                JOIN quests q ON q.id=p.quest_id WHERE n.status='review_pending' AND n.worker_id IS NOT NULL
                AND q.status='running' AND p.status='approved' AND p.revision=q.plan_revision""")
            work.connection.execute(
                "UPDATE node_verification_attempts SET state='blocked',claim_token=NULL,error_code='attempts_exhausted' WHERE state='checking' AND next_attempt<=? AND attempts>=3",
                (now,),
            )
            row = work.connection.execute(
                """SELECT v.*,p.quest_id FROM node_verification_attempts v
                JOIN task_nodes n ON n.plan_id=v.plan_id AND n.node_key=v.node_key AND n.worker_id=v.worker_id
                JOIN quest_plans p ON p.id=v.plan_id JOIN quests q ON q.id=p.quest_id
                WHERE v.state IN ('pending','checking') AND v.attempts<3 AND v.next_attempt<=?
                AND n.status='review_pending' AND q.status='running' AND p.status='approved' AND p.revision=q.plan_revision
                ORDER BY v.attempts,v.next_attempt,v.plan_id,v.node_key LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return False
            work.connection.execute(
                "UPDATE node_verification_attempts SET attempts=attempts+1,state='checking',next_attempt=?,claim_token=? WHERE plan_id=? AND node_key=? AND worker_id=?",
                (now + 60, token, row["plan_id"], row["node_key"], row["worker_id"]),
            )
        state, error = "verified", None
        try:
            self.verify(
                row["quest_id"], row["node_key"], claim=(row["plan_id"], row["worker_id"], token)
            )
        except DomainError:
            state, error = "blocked", "criterion_or_provenance_failed"
        except Exception:  # noqa: BLE001 - bounded infrastructure retry without sensitive data
            state, error = "pending" if row["attempts"] < 2 else "blocked", "verifier_error"
        with self.store.unit_of_work() as work:
            work.connection.execute(
                "UPDATE node_verification_attempts SET state=?,error_code=?,claim_token=NULL,next_attempt=? WHERE plan_id=? AND node_key=? AND worker_id=? AND claim_token=?",
                (
                    state,
                    error,
                    time.time() + 2 ** (row["attempts"] + 1),
                    row["plan_id"],
                    row["node_key"],
                    row["worker_id"],
                    token,
                ),
            )
        return True

    def _settle_conditions(self, work, quest, plan):
        gate = CompletionGate(self.store)
        while True:
            rows = work.connection.execute(
                "SELECT * FROM task_nodes WHERE plan_id=?", (plan["id"],)
            ).fetchall()
            states = {row["node_key"]: row for row in rows}
            changed = False
            for node in rows:
                if node["status"] != "proposed":
                    continue
                try:
                    gate._check_skipped_node(work, quest, plan, node, states, set())
                except DomainError:
                    continue
                updated = work.connection.execute(
                    "UPDATE task_nodes SET status='skipped' WHERE plan_id=? AND node_key=? AND status='proposed'",
                    (plan["id"], node["node_key"]),
                )
                if updated.rowcount == 1:
                    work.append_event(
                        ForgeEvent(
                            event_type="task.skipped",
                            quest_id=quest["id"],
                            payload={"plan_id": plan["id"], "task_node_key": node["node_key"]},
                        )
                    )
                    changed = True
            if not changed:
                return

    def settle_conditions(self, quest_id):
        with self.store.unit_of_work() as work:
            quest = work.get_quest(quest_id)
            if quest is None:
                raise KeyError(quest_id)
            if quest["status"] != "running":
                raise DomainError("Quest is not running")
            plan = work.connection.execute(
                "SELECT * FROM quest_plans WHERE quest_id=? AND revision=? AND status='approved'",
                (quest_id, quest["plan_revision"]),
            ).fetchone()
            if plan is None:
                raise DomainError("Approved plan required")
            self._settle_conditions(work, quest, plan)

    def verify(self, quest_id, node_key, *, claim=None):
        with self.store.connection() as connection:
            connection.execute("BEGIN")
            quest = connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
            if quest is None:
                raise KeyError(quest_id)
            plan = connection.execute(
                "SELECT * FROM quest_plans WHERE quest_id=? AND revision=? AND status='approved'",
                (quest_id, quest["plan_revision"]),
            ).fetchone()
            node = connection.execute(
                "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?",
                (plan["id"] if plan else "", node_key),
            ).fetchone()
            if (
                quest["status"] != "running"
                or plan is None
                or node is None
                or node["status"] != "review_pending"
            ):
                raise DomainError("Task is not ready for independent verification")
            run = connection.execute(
                "SELECT * FROM worker_runs WHERE quest_id=? AND plan_id=? AND node_key=? AND worker_id=? AND state='review_pending' AND execution_mode='live'",
                (quest_id, plan["id"], node_key, node["worker_id"]),
            ).fetchone()
            if run is None:
                raise DomainError("Verified live Worker run required")
            criteria = json.loads(node["criteria_json"])
            if not criteria or any(
                not isinstance(item, str)
                or not item.startswith(("json_valid:", "semantic_numeric:scheduler-v1:"))
                for item in criteria
            ):
                raise DomainError("Task criterion lacks an independent verifier")
            plan_revision, worker_id = (
                quest["plan_revision"],
                node["worker_id"],
            )
            connection.commit()
        for criterion in criteria:
            if criterion.startswith("json_valid:"):
                result = JsonVerifier(self.store).verify(
                    quest_id, worker_id, criterion.removeprefix("json_valid:")
                )
                if result["exit_code"] != 0:
                    raise DomainError("Independent test failed")
            else:
                from .semantic_evidence import SemanticVerifier

                source = criterion.removeprefix("semantic_numeric:scheduler-v1:")
                path = str(Path(GovernanceService(self.store)._workspace_scope(source)))
                with self.store.connection() as connection:
                    receipt = connection.execute(
                        """SELECT r.id FROM action_receipts r JOIN action_intents a ON a.id=r.intent_id
                        WHERE a.run_id=? AND a.tool_name='file_write' AND a.resource_scope=?
                        AND a.state='succeeded' ORDER BY r.created_at DESC LIMIT 1""",
                        (run["id"], path),
                    ).fetchone()
                if receipt is None:
                    raise DomainError("Verified artifact was not produced by this Worker")
                result = SemanticVerifier(self.store).verify(
                    quest_id,
                    worker_id,
                    source,
                    run_id=run["id"],
                    plan_id=plan["id"],
                    plan_revision=plan_revision,
                    receipt_id=receipt["id"],
                )
                if not result["passed"]:
                    raise DomainError("Independent test failed")
        with self.store.unit_of_work() as work:
            if claim is not None:
                claim_plan, claim_worker, claim_token = claim
                active_claim = work.connection.execute(
                    "SELECT 1 FROM node_verification_attempts WHERE plan_id=? AND node_key=? AND worker_id=? AND claim_token=? AND state='checking' AND next_attempt>?",
                    (claim_plan, node_key, claim_worker, claim_token, time.time()),
                ).fetchone()
                if active_claim is None or claim_plan != plan["id"] or claim_worker != worker_id:
                    raise DomainError("Verification claim is stale")
            current = work.get_quest(quest_id)
            current_plan = work.connection.execute(
                "SELECT * FROM quest_plans WHERE id=?", (plan["id"],)
            ).fetchone()
            current_node = work.connection.execute(
                "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?", (plan["id"], node_key)
            ).fetchone()
            if (
                current is None
                or current["plan_revision"] != plan_revision
                or current["status"] != "running"
                or current_plan is None
                or current_plan["status"] != "approved"
                or current_plan["digest"] != plan["digest"]
                or current_node is None
                or current_node["status"] != "review_pending"
                or current_node["worker_id"] != worker_id
            ):
                raise DomainError("Task changed during verification")
            gate = CompletionGate(self.store)
            gate._check_live_node(work, current, current_plan, current_node)
            updated = work.connection.execute(
                "UPDATE task_nodes SET status='succeeded' WHERE plan_id=? AND node_key=? AND status='review_pending' AND worker_id=?",
                (plan["id"], node_key, worker_id),
            )
            if updated.rowcount != 1:
                raise DomainError("Task changed during verification")
            settled = work.connection.execute(
                "UPDATE worker_runs SET state='succeeded' WHERE id=? AND state='review_pending'",
                (run["id"],),
            )
            if settled.rowcount != 1:
                raise DomainError("Worker changed during verification")
            work.connection.execute(
                "UPDATE workers SET status='succeeded' WHERE id=?", (worker_id,)
            )
            work.connection.execute(
                "UPDATE quests SET version=version+1,updated_at=? WHERE id=? AND version=?",
                (now_iso(), quest_id, current["version"]),
            )
            work.append_event(
                ForgeEvent(
                    event_type="task.verified",
                    quest_id=quest_id,
                    worker_id=worker_id,
                    payload={"plan_id": plan["id"], "task_node_key": node_key},
                )
            )
            self._settle_conditions(work, current, current_plan)
            remaining = work.connection.execute(
                "SELECT COUNT(*) FROM task_nodes WHERE plan_id=? AND status NOT IN ('succeeded','failed','skipped')",
                (plan["id"],),
            ).fetchone()[0]
            if remaining == 0:
                current = work.get_quest(quest_id)
                gate.check(work, current)
                changed = work.connection.execute(
                    "UPDATE quests SET status='completed',version=version+1,updated_at=? WHERE id=? AND status='running' AND version=?",
                    (now_iso(), quest_id, current["version"]),
                )
                if changed.rowcount != 1:
                    raise DomainError("Quest changed during completion")
                work.append_event(
                    ForgeEvent(
                        event_type="quest.status_changed",
                        quest_id=quest_id,
                        payload={
                            "from": "running",
                            "to": "completed",
                            "version": current["version"] + 1,
                        },
                    )
                )
            return work.get_quest(quest_id)
