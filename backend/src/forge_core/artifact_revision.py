"""Read-only validation of immutable, same-Worker artifact revision receipts."""

import hashlib
import json
from pathlib import Path

from .domain import DomainError
from .governance import parameter_digest


def verify_file_revision_chain(connection, run_id, receipt_id):
    """Validate every retained version; current bytes and authority are checked by callers."""
    try:
        run = connection.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone()
        root = connection.execute("SELECT root FROM workspace WHERE singleton=1").fetchone()[0]
        if run is None:
            raise ValueError
        allowed = json.loads(run["allowed_tools_json"])
        if not isinstance(allowed, list) or any(not isinstance(name, str) for name in allowed):
            raise ValueError
        seen, scope, expected, result = [], None, None, None
        while receipt_id and len(seen) < 64:
            if receipt_id in seen:
                raise ValueError
            seen.append(receipt_id)
            row = connection.execute(
                "SELECT r.id AS receipt_id,r.execution_mode AS receipt_mode,r.result_json,"
                "r.result_digest,i.* FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id "
                "WHERE r.id=?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                raise ValueError
            parameters, output = json.loads(row["parameters_json"]), json.loads(row["result_json"])
            name = row["tool_name"]
            call = connection.execute(
                "SELECT * FROM worker_tool_calls WHERE run_id=? AND call_id=?",
                (run_id, row["call_id"]),
            ).fetchone()
            content = parameters["content"].encode("utf-8")
            source_path = Path(parameters["path"])
            if not source_path.is_absolute():
                source_path = Path(root) / source_path
            digest = hashlib.sha256(content).hexdigest()
            if (
                name not in {"file_write", "file_revise"}
                or name not in allowed
                or row["state"] != "succeeded"
                or row["run_id"] != run_id
                or row["worker_id"] != run["worker_id"]
                or row["quest_id"] != run["quest_id"]
                or row["plan_id"] != run["plan_id"]
                or row["plan_revision"] != run["plan_revision"]
                or row["receipt_mode"] != row["execution_mode"]
                or row["execution_mode"] != run["execution_mode"]
                or row["risk"] != "R1"
                or len(content) > 262144
                or parameter_digest(parameters) != row["parameter_digest"]
                or hashlib.sha256(row["result_json"].encode("utf-8")).hexdigest()
                != row["result_digest"]
                or str(source_path.resolve()) != row["resource_scope"]
                or output.get("path") != row["resource_scope"]
                or output.get("sha256") != digest
                or output.get("bytes") != len(content)
                or (expected is not None and expected != digest)
                or (scope is not None and scope != row["resource_scope"])
                or call is None
                or call["state"] != "succeeded"
                or call["receipt_id"] != receipt_id
                or call["tool_name"] != name
                or parameter_digest(json.loads(call["arguments_json"])) != row["parameter_digest"]
            ):
                raise ValueError
            reply = json.loads(call["result_json"])
            if (
                reply.get("call_id") != row["call_id"]
                or reply.get("intent_id") != row["id"]
                or reply.get("receipt_id") != receipt_id
                or reply.get("state") != "succeeded"
                or reply.get("output") != output
            ):
                raise ValueError
            scope = row["resource_scope"]
            if result is None:
                result = {
                    "path": scope,
                    "sha256": digest,
                    "bytes": len(content),
                    "receipt_id": receipt_id,
                    "tool_name": name,
                    "prior_receipt_id": parameters.get("prior_receipt_id"),
                }
            if name == "file_write":
                if set(parameters) != {"path", "content"}:
                    raise ValueError
                return {**result, "revision_receipt_ids": seen}
            if (
                set(parameters) != {"path", "content", "prior_receipt_id", "expected_sha256"}
                or output.get("prior_receipt_id") != parameters["prior_receipt_id"]
                or output.get("prior_sha256") != parameters["expected_sha256"]
                or not isinstance(parameters["prior_receipt_id"], str)
            ):
                raise ValueError
            expected = parameters["expected_sha256"]
            receipt_id = parameters["prior_receipt_id"]
        raise ValueError
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError):
        raise DomainError("Artifact revision receipt chain is invalid") from None
