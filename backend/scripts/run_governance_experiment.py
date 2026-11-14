"""Actual ToolEngine matrix with explicit SQL-seeded mock Worker context; no external requests."""

import asyncio
import hashlib
import importlib.util
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from forge_core.domain import DomainError, ForgeEvent
from forge_core.evidence import NodeVerifier
from forge_core.governance import GovernanceService
from forge_core.tool_engine import ToolEngine


def main():
    source = Path(__file__).resolve().parents[1] / "tests" / "test_tool_engine.py"
    spec = importlib.util.spec_from_file_location("governance_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    rows = []
    executions = 0
    with tempfile.TemporaryDirectory(prefix="forge-tool-matrix-") as directory:
        store, quest, worker, plan = fixture.mock_run.__wrapped__(Path(directory))
        root = store.workspace.root
        (root / "work").mkdir()
        (root / "work/input").write_text("evidence", encoding="utf-8")
        governance = GovernanceService(store)
        engine = ToolEngine(store, network_hosts=["example.test"])

        def arguments(call_id, name, parameters):
            fixture.reserve(store, call_id, name, parameters)
            return {
                "call_id": call_id,
                "quest_id": quest.id,
                "worker_id": worker.id,
                "run_id": "run",
                "lease_token": "lease",
                "tool_name": name,
                "parameters": parameters,
            }

        def evaluate(name, expected, operation, boundary="prepare"):
            reason = None
            try:
                operation()
                actual = "allow"
            except DomainError as exc:
                actual = "deny"
                reason = str(exc)
            rows.append(
                {
                    "case": name,
                    "expected": expected,
                    "actual": actual,
                    "boundary": boundary,
                    "reason": reason,
                }
            )

        def execute(args):
            nonlocal executions
            receipt = asyncio.run(engine.execute(**args))
            if receipt["state"] != "succeeded":
                raise RuntimeError("Tool did not succeed")
            executions += 1
            return receipt

        evaluate(
            "r0_actual_file_read",
            "allow",
            lambda: execute(arguments("read", "file_read", {"path": "work/input"})),
            "execution",
        )
        write = arguments("write", "file_write", {"path": "work/output", "content": "{}"})
        evaluate("r1_missing_grant", "deny", lambda: engine.prepare(**write))
        governance.grant_workspace(quest.id, "work", "local_user")
        evaluate("r1_actual_granted_write", "allow", lambda: execute(write), "execution")
        evaluate("idempotent_receipt", "allow", lambda: execute(write), "receipt_replay")
        if (root / "work/output").read_text(encoding="utf-8") != "{}":
            raise RuntimeError("Write mismatch")
        swapped = arguments("swap", "file_read", {"path": "work/input"})
        swapped["parameters"] = {"path": "work/output"}
        evaluate("reserved_parameter_substitution", "deny", lambda: engine.prepare(**swapped))
        for index, path in enumerate(("../outside", ".env", "work/private.key")):
            attack = arguments(f"path-{index}", "file_read", {"path": path})
            evaluate(f"path_attack_{index}", "deny", lambda args=attack: engine.prepare(**args))
        revoke = arguments("revoke", "file_write", {"path": "work/revoked", "content": "blocked"})
        intent = engine.prepare(**revoke)
        governance.revoke_workspace(quest.id, "work", "local_user")
        evaluate(
            "grant_revoked_before_execution",
            "deny",
            lambda: engine._begin(intent["id"], "lease"),
            "begin",
        )
        if (root / "work/revoked").exists():
            raise RuntimeError("Revoked effect occurred")
        network = {"url": "https://example.test/item"}
        missing = arguments("network-none", "browser_get", network)
        evaluate("r2_missing_approval", "deny", lambda: engine.prepare(**missing))
        approval = governance.request_approval(
            quest.id, "browser_get", network, network["url"], "R2", "matrix"
        )
        governance.decide_approval(approval["id"], "APPROVED", "local_user")
        other = arguments(
            "network-substitute", "browser_get", {"url": "https://example.test/other"}
        )
        evaluate(
            "r2_approval_parameter_substitution",
            "deny",
            lambda: engine.prepare(**other, approval_id=approval["id"]),
        )
        concurrent = [arguments(name, "browser_get", network) for name in ("race-a", "race-b")]
        barrier = Barrier(2)

        def race(args):
            barrier.wait(timeout=5)
            try:
                engine.prepare(**args, approval_id=approval["id"])
                return "allow"
            except DomainError:
                return "deny"

        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = sorted(pool.map(race, concurrent))
        if decisions != ["allow", "deny"]:
            raise RuntimeError(f"Unexpected concurrent outcome: {decisions}")
        rows.extend(
            [
                {
                    "case": "r2_concurrent_winner",
                    "expected": "allow",
                    "actual": "allow",
                    "boundary": "prepare",
                },
                {
                    "case": "r2_concurrent_replay",
                    "expected": "deny",
                    "actual": "deny",
                    "boundary": "prepare",
                },
            ]
        )
        revoked = governance.request_approval(
            quest.id, "browser_get", network, network["url"], "R2", "revoked"
        )
        governance.decide_approval(revoked["id"], "APPROVED", "local_user")
        governance.revoke_approval(revoked["id"], "local_user")
        args = arguments("network-revoked", "browser_get", network)
        evaluate(
            "r2_revoked_approval", "deny", lambda: engine.prepare(**args, approval_id=revoked["id"])
        )
        with store.unit_of_work() as work:
            team = work.connection.execute(
                "SELECT * FROM team_proposals WHERE id='team-id'"
            ).fetchone()
            body = json.loads(team["body_json"])
            body["members"][0]["output_schema"] = {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": False,
            }
            encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            work.connection.execute(
                "UPDATE team_proposals SET body_json=?,proposal_digest=?,approved_at='2000-01-01T00:00:00+00:00' WHERE id='team-id'",
                (encoded, digest),
            )
            work.connection.execute(
                "UPDATE worker_runs SET state='review_pending',execution_mode='live',team_digest=? WHERE id='run'",
                (digest,),
            )
            work.connection.execute(
                "UPDATE task_nodes SET status='review_pending',criteria_json=? WHERE plan_id=?",
                (json.dumps([f"json_valid:{root / 'work/output'}"]), plan["id"]),
            )
            work.connection.execute(
                "UPDATE worker_tool_calls SET state='succeeded',receipt_id='forged-receipt' WHERE call_id='write'"
            )
            work.connection.execute("DELETE FROM worker_tool_calls WHERE call_id<>'write'")
            work.append_event(
                ForgeEvent(
                    event_type="worker.result",
                    quest_id=quest.id,
                    worker_id=worker.id,
                    payload={"run_id": "run"},
                )
            )
            work.connection.execute(
                "INSERT INTO worker_messages VALUES ('run',0,'assistant',?)",
                (
                    json.dumps(
                        {
                            "role": "assistant",
                            "content": json.dumps({"summary": "done"}),
                            "tool_calls": [],
                        }
                    ),
                ),
            )
        evaluate(
            "forged_receipt_cannot_complete",
            "deny",
            lambda: NodeVerifier(store).verify(quest.id, "n"),
            "independent_verifier",
        )
        if rows[-1]["reason"] != "Worker action receipt is invalid":
            raise RuntimeError(
                f"Forgery case did not reach receipt validation: {rows[-1]['reason']}"
            )
        with store.connection() as connection:
            receipts = connection.execute("SELECT COUNT(*) FROM action_receipts").fetchone()[0]
    denied = sum(row["expected"] == "deny" for row in rows)
    allowed = len(rows) - denied
    false_allows = sum(row["expected"] == "deny" and row["actual"] == "allow" for row in rows)
    false_denials = sum(row["expected"] == "allow" and row["actual"] == "deny" for row in rows)
    # Counterfactual decisions over the identical labelled cases. These do not
    # replace policy, execute tools, or estimate production attack prevalence.
    ablations = []
    for variant, omitted in {
        "model_claim_only": {row["case"] for row in rows},
        "no_binding": {
            "reserved_parameter_substitution",
            "r2_approval_parameter_substitution",
            "r2_concurrent_replay",
            "forged_receipt_cannot_complete",
        },
        "no_revocation_recheck": {"grant_revoked_before_execution", "r2_revoked_approval"},
    }.items():
        predictions = ["allow" if row["case"] in omitted else row["actual"] for row in rows]
        fa = sum(
            row["expected"] == "deny" and actual == "allow"
            for row, actual in zip(rows, predictions, strict=True)
        )
        fd = sum(
            row["expected"] == "allow" and actual == "deny"
            for row, actual in zip(rows, predictions, strict=True)
        )
        ablations.append(
            {
                "variant": variant,
                "mode": "synthetic_counterfactual_only",
                "cases": len(rows),
                "expected_deny_denominator": denied,
                "expected_allow_denominator": allowed,
                "false_allows": fa,
                "false_denials": fd,
                "false_allow_rate": fa / denied,
                "false_deny_rate": fd / allowed,
                "omitted_checks": sorted(omitted),
            }
        )
    print(
        json.dumps(
            {
                "dataset": "tool-governance-matrix-v2",
                "mode": "isolated-local-synthetic-worker",
                "external_requests": 0,
                "actual_execution_requests": executions,
                "unique_action_receipts": receipts,
                "cases": rows,
                "expected_deny_denominator": denied,
                "expected_allow_denominator": allowed,
                "false_allows": false_allows,
                "false_denials": false_denials,
                "false_allow_rate": false_allows / denied,
                "false_deny_rate": false_denials / allowed,
                "ablations": ablations,
                "limitations": "Small deterministic regression matrix, not attack prevalence. Worker/provider context is a SQL fixture; real local file tools execute. R2 stops at intent preparation.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
