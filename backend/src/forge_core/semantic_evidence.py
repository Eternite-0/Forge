"""Independent fixed semantic suite bound to an approved live Worker receipt."""

import hashlib
import json
import os
import sys
from pathlib import Path

from .domain import DomainError, new_id, now_iso
from .governance import GovernanceService, parameter_digest
from .semantic_suite import suite_contract
from .team_composition import TeamCompositionService
from .tool_engine import ToolEngine

_PREFIX = "semantic_numeric:scheduler-v1:"
_METHOD = "fixed_numeric_suite_v1"
_MAX_SOURCE = 16000


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _executable_digest(path):
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise DomainError("Semantic executor cannot be verified") from exc


def _provenance_id(payload):
    return "semantic_provenance_" + _sha(_canonical(payload).encode("utf-8"))


def _read(path):
    try:
        with open(path, "rb") as stream:
            value = stream.read(_MAX_SOURCE + 1)
        if len(value) > _MAX_SOURCE or not value:
            raise DomainError("Semantic source exceeds limits")
        value.decode("utf-8")
        return value
    except (OSError, UnicodeError) as exc:
        raise DomainError("Semantic source cannot be read") from exc


class SemanticVerifier:
    def __init__(self, store):
        self.store = store

    def _binding(
        self,
        connection,
        *,
        quest_id,
        worker_id,
        run_id,
        plan_id,
        plan_revision,
        receipt_id,
        source_path,
        historical=False,
    ):
        quest = connection.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
        plan = connection.execute(
            "SELECT * FROM quest_plans WHERE id=? AND quest_id=?", (plan_id, quest_id)
        ).fetchone()
        worker = connection.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone()
        receipt = connection.execute(
            "SELECT * FROM action_receipts WHERE id=?", (receipt_id,)
        ).fetchone()
        if quest is None or plan is None or worker is None or receipt is None:
            raise DomainError("Semantic evidence binding missing")
        intent = connection.execute(
            "SELECT * FROM action_intents WHERE id=?", (receipt["intent_id"],)
        ).fetchone()
        call = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND receipt_id=?", (run_id, receipt_id)
        ).fetchone()
        node = connection.execute(
            "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?", (plan_id, worker["node_key"])
        ).fetchone()
        if intent is None or call is None or node is None:
            raise DomainError("Semantic receipt binding missing")
        inherited = False
        if (
            historical
            and quest["plan_revision"] != plan_revision
            and worker["state"] == "succeeded"
        ):
            from .plan_lineage import validated_lineage_source

            current_plan = connection.execute(
                "SELECT * FROM quest_plans WHERE quest_id=? AND revision=?",
                (quest_id, quest["plan_revision"]),
            ).fetchone()
            if current_plan is None:
                raise DomainError("Semantic current lineage is unavailable")
            source = validated_lineage_source(connection, quest, current_plan, worker["node_key"])
            inherited = (
                source["source_run"]["id"] == run_id
                and source["source_plan"]["id"] == plan_id
                and source["source_run"]["worker_id"] == worker_id
            )
        if (
            quest["status"]
            not in (
                {"approved", "queued", "running", "reviewing", "completed"}
                if inherited
                else (("running", "completed") if historical else ("running",))
            )
            or (quest["plan_revision"] != plan_revision and not inherited)
            or plan["revision"] != plan_revision
            or plan["status"] not in ({"approved", "superseded"} if inherited else {"approved"})
            or worker["quest_id"] != quest_id
            or worker["worker_id"] != worker_id
            or worker["plan_id"] != plan_id
            or worker["plan_revision"] != plan_revision
            or worker["plan_digest"] != plan["digest"]
            or worker["state"] not in ("review_pending", "succeeded")
            or worker["execution_mode"] != "live"
            or node["worker_id"] != worker_id
            or node["status"] not in ("review_pending", "succeeded")
            or intent["quest_id"] != quest_id
            or intent["worker_id"] != worker_id
            or intent["run_id"] != run_id
            or intent["plan_id"] != plan_id
            or intent["plan_revision"] != plan_revision
            or intent["tool_name"] not in {"file_write", "file_revise"}
            or intent["state"] != "succeeded"
            or intent["execution_mode"] != "live"
            or receipt["execution_mode"] != "live"
            or call["state"] != "succeeded"
            or call["call_id"] != intent["call_id"]
            or call["tool_name"] != intent["tool_name"]
        ):
            raise DomainError("Semantic evidence is outside the current approved execution")
        try:
            params = json.loads(intent["parameters_json"])
            call_params = json.loads(call["arguments_json"])
            call_result = json.loads(call["result_json"])
            output = json.loads(receipt["result_json"])
            criteria = json.loads(node["criteria_json"])
            graph = json.loads(plan["graph_json"])
            if _sha(_canonical(graph).encode()) != plan["digest"]:
                raise ValueError("plan digest")
            path = Path(GovernanceService(self.store)._workspace_scope(source_path))
            expected_path = Path(GovernanceService(self.store)._workspace_scope(params["path"]))
            if (
                not isinstance(params, dict)
                or set(params)
                != (
                    {"path", "content"}
                    if intent["tool_name"] == "file_write"
                    else {"path", "content", "prior_receipt_id", "expected_sha256"}
                )
                or not isinstance(params["content"], str)
                or params != call_params
                or parameter_digest(params) != intent["parameter_digest"]
                or path != expected_path
                or Path(intent["resource_scope"]) != path
                or Path(output["path"]) != path
                or _sha(receipt["result_json"].encode("utf-8")) != receipt["result_digest"]
                or call_result["receipt_id"] != receipt_id
                or call_result["state"] != "succeeded"
                or call_result["output"] != output
                or not any(
                    isinstance(criterion, str)
                    and criterion.startswith(_PREFIX)
                    and Path(
                        GovernanceService(self.store)._workspace_scope(criterion[len(_PREFIX) :])
                    )
                    == path
                    for criterion in criteria
                )
            ):
                raise ValueError("receipt")
            data = _read(path)
            if (
                _sha(data) != output["sha256"]
                or _sha(params["content"].encode("utf-8")) != _sha(data)
                or output["bytes"] != len(data)
            ):
                raise ValueError("artifact hash")
            service = TeamCompositionService(self.store)
            team = (
                service.get_historical_approved(
                    quest_id,
                    plan_revision,
                    worker["team_digest"],
                    connection=connection,
                    run_id=run_id,
                )
                if historical
                else service.get_approved(
                    quest_id, plan_revision, worker["team_digest"], connection=connection
                )
            )
            member = team["members"][worker["member_index"]]
            if (
                member["role_id"] != worker["role_id"]
                or member["task_node_key"] != worker["node_key"]
                or intent["tool_name"] not in member["tool_scope"]
            ):
                raise ValueError("team")
            if intent["tool_name"] == "file_revise":
                from .artifact_revision import verify_file_revision_chain

                verify_file_revision_chain(connection, run_id, receipt_id)
        except (KeyError, TypeError, ValueError, IndexError, json.JSONDecodeError) as exc:
            raise DomainError("Semantic evidence failed integrity validation") from exc
        return {
            "quest": dict(quest),
            "plan": dict(plan),
            "worker": dict(worker),
            "node": dict(node),
            "intent": dict(intent),
            "receipt": dict(receipt),
            "call": dict(call),
            "source_path": str(path),
            "source_sha256": _sha(data),
            "source": data,
        }

    @staticmethod
    def _valid_report(report, source_sha256, *, require_pass=True):
        expected = suite_contract()
        if (
            not isinstance(report, dict)
            or report.get("suite_id") != expected["suite_id"]
            or report.get("suite_version") != expected["suite_version"]
            or report.get("suite_digest") != expected["suite_digest"]
            or report.get("source_sha256") != source_sha256
            or type(report.get("passed")) is not bool
        ):
            return False
        cases = report.get("cases")
        if not isinstance(cases, list) or len(cases) != len(expected["cases"]):
            return False
        for actual, contract in zip(cases, expected["cases"], strict=True):
            if (
                not isinstance(actual, dict)
                or any(actual.get(key) != value for key, value in contract.items())
                or type(actual.get("passed")) is not bool
            ):
                return False
            if actual.get("error") == "candidate_rejected":
                if (
                    actual.get("actual") is not None
                    or actual.get("actual_sha256") is not None
                    or actual["passed"]
                ):
                    return False
            elif (
                actual.get("error") is not None
                or _sha(_canonical(actual.get("actual")).encode()) != actual.get("actual_sha256")
                or actual["passed"]
                != (
                    type(actual.get("actual")) is int
                    and actual["actual_sha256"] == contract["expected_sha256"]
                )
            ):
                return False
        return report["passed"] == all(case["passed"] for case in cases) and (
            report["passed"] or not require_pass
        )

    def verify(
        self, quest_id, worker_id, source_path, *, run_id, plan_id, plan_revision, receipt_id
    ):
        options = {
            "quest_id": quest_id,
            "worker_id": worker_id,
            "source_path": source_path,
            "run_id": run_id,
            "plan_id": plan_id,
            "plan_revision": plan_revision,
            "receipt_id": receipt_id,
        }
        with self.store.connection() as connection:
            connection.execute("BEGIN")
            before = self._binding(connection, **options)
            connection.commit()
        command = [sys.executable, "-I", "-m", "forge_core.semantic_suite"]
        executor_digest = _executable_digest(command[0])
        data = _canonical(
            {"source": before["source"].decode("utf-8"), "suite_id": "scheduler-v1"}
        ).encode("utf-8")
        env = {
            key: os.environ[key]
            for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }
        result = ToolEngine._run_process(command, str(self.store.workspace.root), env, data)
        if _executable_digest(command[0]) != executor_digest:
            raise DomainError("Semantic executor changed during verification")
        if result["exit_code"] != 0 or len(result["stdout"].encode("utf-8")) > 65536:
            raise DomainError("Independent semantic validation failed")
        try:
            report = json.loads(result["stdout"])
        except (TypeError, ValueError) as exc:
            raise DomainError("Invalid independent semantic report") from exc
        if _sha(result["stdout"].encode("utf-8")) != result[
            "stdout_sha256"
        ] or not self._valid_report(report, before["source_sha256"], require_pass=False):
            raise DomainError("Independent semantic validation failed")
        semantic_id = new_id("semantic")
        artifact_id = new_id("evidence")
        created = now_iso()
        with self.store.unit_of_work() as work:
            after = self._binding(work.connection, **options)
            if before != after:
                raise DomainError("Semantic evidence changed during verification")
            work.connection.execute(
                "INSERT INTO semantic_test_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    semantic_id,
                    quest_id,
                    plan_id,
                    plan_revision,
                    worker_id,
                    run_id,
                    receipt_id,
                    after["source_path"],
                    after["source_sha256"],
                    report["suite_id"],
                    report["suite_version"],
                    report["suite_digest"],
                    _canonical(command),
                    result["exit_code"],
                    result["stdout_sha256"],
                    result["stderr_sha256"],
                    result["stdout"],
                    created,
                ),
            )
            provenance = {
                "version": 1,
                "semantic_run_id": semantic_id,
                "worker_run_id": run_id,
                "receipt_id": receipt_id,
                "suite_id": report["suite_id"],
                "suite_version": report["suite_version"],
                "suite_digest": report["suite_digest"],
                "source_sha256": after["source_sha256"],
                "report_sha256": result["stdout_sha256"],
                "argv_sha256": _sha(_canonical(command).encode("utf-8")),
                "executor_sha256": executor_digest,
            }
            work.connection.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                (
                    _provenance_id(provenance),
                    quest_id,
                    "semantic_provenance",
                    1,
                    _canonical(provenance),
                    created,
                ),
            )
            if not report["passed"]:
                return {
                    "passed": False,
                    "suite_id": report["suite_id"],
                    "semantic_run_id": semantic_id,
                    "source_sha256": after["source_sha256"],
                }
            artifact = {
                "path": after["source_path"],
                "sha256": after["source_sha256"],
                "plan_revision": plan_revision,
                "receipt_id": receipt_id,
                "worker_run_id": run_id,
            }
            work.connection.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                (artifact_id, quest_id, "artifact", 1, _canonical(artifact), created),
            )
            payload = {
                "check_id": _PREFIX + after["source_path"],
                "semantic_run_id": semantic_id,
                "artifact_id": artifact_id,
                "plan_revision": plan_revision,
                "method": _METHOD,
            }
            for kind in ("test", "review"):
                work.connection.execute(
                    "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
                    (new_id("evidence"), quest_id, kind, 1, _canonical(payload), created),
                )
        return {
            "passed": True,
            "suite_id": report["suite_id"],
            "semantic_run_id": semantic_id,
            "artifact_id": artifact_id,
            "source_sha256": after["source_sha256"],
        }

    def validate_evidence(
        self,
        connection,
        *,
        quest_id,
        plan_id,
        plan_revision,
        worker_id,
        worker_run_id,
        semantic_run_id,
    ):
        row = connection.execute(
            "SELECT * FROM semantic_test_runs WHERE id=?", (semantic_run_id,)
        ).fetchone()
        if row is None or (
            row["quest_id"],
            row["plan_id"],
            row["plan_revision"],
            row["worker_id"],
            row["worker_run_id"],
        ) != (quest_id, plan_id, plan_revision, worker_id, worker_run_id):
            raise DomainError("Semantic run binding mismatch")
        current = self._binding(
            connection,
            quest_id=quest_id,
            worker_id=worker_id,
            run_id=worker_run_id,
            plan_id=plan_id,
            plan_revision=plan_revision,
            receipt_id=row["receipt_id"],
            source_path=row["artifact_path"],
            historical=True,
        )
        try:
            report = json.loads(row["report_json"])
            command = json.loads(row["command_json"])
            records = connection.execute(
                """SELECT id,payload FROM evidence WHERE quest_id=? AND kind='semantic_provenance'
                AND trusted=1 AND json_valid(payload) AND json_extract(payload,'$.semantic_run_id')=?
                LIMIT 2""",
                (quest_id, semantic_run_id),
            ).fetchall()
            matching = []
            for record in records:
                try:
                    proof = json.loads(record["payload"])
                    if isinstance(proof, dict) and proof.get("semantic_run_id") == semantic_run_id:
                        matching.append((record["id"], proof))
                except (TypeError, ValueError):
                    continue
            if len(matching) != 1:
                raise ValueError("missing semantic provenance")
            proof_id, proof = matching[0]
            expected = {
                "version": 1,
                "semantic_run_id": semantic_run_id,
                "worker_run_id": worker_run_id,
                "receipt_id": row["receipt_id"],
                "suite_id": row["suite_id"],
                "suite_version": row["suite_version"],
                "suite_digest": row["suite_digest"],
                "source_sha256": row["source_sha256"],
                "report_sha256": row["stdout_sha256"],
                "argv_sha256": _sha(_canonical(command).encode("utf-8")),
                "executor_sha256": proof.get("executor_sha256"),
            }
            if (
                row["exit_code"] != 0
                or row["source_sha256"] != current["source_sha256"]
                or _sha(row["report_json"].encode("utf-8")) != row["stdout_sha256"]
                or not isinstance(command, list)
                or len(command) != 4
                or not isinstance(command[0], str)
                or not os.path.isabs(command[0])
                or command[1:] != ["-I", "-m", "forge_core.semantic_suite"]
                or proof != expected
                or not isinstance(proof["executor_sha256"], str)
                or len(proof["executor_sha256"]) != 64
                or any(ch not in "0123456789abcdef" for ch in proof["executor_sha256"])
                or proof_id != _provenance_id(proof)
                or row["suite_id"] != report["suite_id"]
                or row["suite_version"] != report["suite_version"]
                or row["suite_digest"] != report["suite_digest"]
                or not self._valid_report(report, current["source_sha256"])
            ):
                raise ValueError("invalid semantic result")
        except (ValueError, TypeError, KeyError) as exc:
            raise DomainError("Semantic evidence failed revalidation") from exc
        return True
