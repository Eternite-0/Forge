"""Worker-side enforcement of explicitly approved review-cycle contracts."""

import hashlib
import json
from pathlib import Path

from .domain import DomainError
from .review_envelope import review_cycle_state, validate_review_envelope
from .token_policy import token_budgets_enforced


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def capacity_policy(contract):
    return contract.get("review_envelope") or contract.get("worker_capacity")


def validate_capacity_policy(policy):
    if policy.get("version") != "worker-capacity-v1":
        return validate_review_envelope(policy)
    keys = {
        "version",
        "max_source_bytes",
        "max_artifact_bytes",
        "max_delivery_bytes",
        "correction_input_tokens",
        "correction_output_tokens",
        "correction_calls",
    }
    if set(policy) != keys or any(
        type(policy[key]) is not int or not 1 <= policy[key] <= 65536 for key in keys - {"version"}
    ):
        raise DomainError("Invalid approved fixed-verification Worker capacity")
    if policy["correction_calls"] not in {1, 2} or policy["correction_output_tokens"] > 8192:
        raise DomainError("Invalid approved fixed-verification correction bound")
    return dict(policy)


def cycle_allowance(connection, run, envelope):
    if envelope.get("version") == "worker-capacity-v1":
        validate_capacity_policy(envelope)
        repair = connection.execute(
            "SELECT created_at,json_extract(payload,'$.correction_reservation_cursor') AS cursor "
            "FROM events WHERE event_type='worker.progress' "
            "AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.phase')='self_repair' "
            "ORDER BY seq LIMIT 1",
            (run["id"],),
        ).fetchone()
        consumed = 0
        if repair and type(repair["cursor"]) is int and repair["cursor"] >= 0:
            consumed = connection.execute(
                "SELECT count(*) FROM worker_model_reservations WHERE run_id=? AND rowid>? "
                "AND state!='refunded'",
                (run["id"], repair["cursor"]),
            ).fetchone()[0]
        elif repair:
            consumed = connection.execute(
                "SELECT count(*) FROM worker_model_reservations WHERE run_id=? AND created_at>=? "
                "AND state!='refunded'",
                (run["id"], repair[0]),
            ).fetchone()[0]
        calls = max(0, envelope["correction_calls"] - consumed)
        state = {
            "reviews_remaining": 0,
            "correction_calls_remaining": calls,
            "future_reserve_tokens": calls
            * (envelope["correction_input_tokens"] + envelope["correction_output_tokens"]),
            "correction_started": repair is not None,
        }
    else:
        state = review_cycle_state(connection, run["id"], envelope)
    held = state["future_reserve_tokens"]
    if state["correction_started"]:
        if not state["correction_calls_remaining"]:
            raise DomainError("Approved correction call count exhausted")
        held -= envelope["correction_input_tokens"] + envelope["correction_output_tokens"]
    return {
        **state,
        "held_after_current_request": held if token_budgets_enforced() else 0,
        "current_request_allowance": (
            max(0, run["token_limit"] - run["token_spent"] - held)
            if token_budgets_enforced()
            else None
        ),
    }


def verify_worker_envelope(store, member):
    envelope = capacity_policy(member)
    if envelope is None:
        return None
    from .capacity_preflight import verify_input_inventory

    envelope = validate_capacity_policy(envelope)
    if not isinstance(member.get("required_input_paths"), list):
        raise DomainError("Approved review envelope lacks declared inputs")
    verify_input_inventory(store, member["verified_input_inventory"])
    return envelope


def validate_capsule_binding(connection, run, member):
    from .worker_task_sources import input_acquisition_policy, verified_task_sources

    plan = connection.execute(
        "SELECT graph_json FROM quest_plans WHERE id=?", (run["plan_id"],)
    ).fetchone()
    if plan is None:
        raise DomainError("Worker approved plan is missing")
    graph = json.loads(plan[0])
    policy = input_acquisition_policy(graph, member)
    from .source_wire import approved_source_wire_format

    approved_source_wire_format(graph, member)
    from .hypothesis_sources import validate_member as validate_hypothesis_member

    validate_hypothesis_member(json.loads(plan[0]), member)
    row = connection.execute(
        "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (run["id"],)
    ).fetchone()
    if row is None:
        if capacity_policy(member) is None and policy is None:
            return
        raise DomainError("Approved review contract has no Worker capsule")
    capsule = json.loads(row[0])
    approved_source_wire_format(graph, capsule)
    if ("input_acquisition_policy" in capsule and policy is None) or capsule.get(
        "input_acquisition_policy"
    ) != policy:
        raise DomainError("Worker input acquisition policy differs from its approved start")
    if capacity_policy(member) is None and policy is None:
        return
    if policy is not None:
        quest = connection.execute("SELECT * FROM quests WHERE id=?", (run["quest_id"],)).fetchone()
        if quest is None or {
            key: capsule[key] for key in ("original_request", "updated_request") if key in capsule
        } != verified_task_sources(connection, quest, policy=policy):
            raise DomainError("Worker task sources differ from the approved start")
    if member.get("serial_capacity_policy") is not None:
        admission = capsule.get("serial_start_allowance")
        if (
            not isinstance(admission, dict)
            or admission.get("execution_token_limit") != run["token_limit"]
            or admission.get("approved_execution_token_limit")
            != member.get("execution_token_limit")
            or admission.get("baseline_tokens") != member["estimated_tokens"]
            or admission.get("shared_reserve_tokens")
            != member["serial_capacity_policy"]["shared_reserve_tokens"]
        ):
            raise DomainError("Worker serial admission differs from its approved start")
    if any(
        capsule.get(key) != member.get(key)
        for key in (
            "review_envelope",
            "worker_capacity",
            "required_input_paths",
            "verified_input_inventory",
            "required_predecessor_deliveries",
            "serial_capacity_policy",
            "action_evidence_allowance_bytes",
            "hypothesis_read_paths",
        )
    ):
        raise DomainError("Worker review contract differs from the approved team")


def verify_read_identity(store, connection, run, path, data):
    """Recheck source identity at the actual read, after approval and queuing."""
    from .team_composition import TeamCompositionService

    team = TeamCompositionService(store).get_approved(
        run["quest_id"], run["plan_revision"], run["team_digest"], connection=connection
    )
    member = team["members"][run["member_index"]]
    if capacity_policy(member) is None:
        return
    known = {
        str(Path(store.workspace.root) / item["path"]): item
        for item in member["verified_input_inventory"]
    }
    source = known.get(str(path))
    if source is not None and (
        len(data) != source["bytes"] or hashlib.sha256(data).hexdigest() != source["sha256"]
    ):
        raise DomainError("Approved source identity changed before the read")
    # A recalled reference has no approved content hash. Recheck the complete
    # receipt budget against this very read, rather than trusting the earlier
    # admission peek: the file may have changed while its call was queued.
    from .tool_engine import ToolEngine

    service = ToolEngine(store)
    hypotheses = {
        service._scope("file_read", {"path": selected})
        for selected in member.get("hypothesis_read_paths", [])
    }
    if str(path) in hypotheses:
        receipts, predecessors = _source_receipt_observations(connection, run)
        receipts.append(
            _projected_receipt(
                run,
                "file_read",
                str(path),
                {"content": data.decode("utf-8"), "sha256": hashlib.sha256(data).hexdigest()},
            )
        )
        _check_complete_source_bytes(capacity_policy(member), receipts, predecessors)


def _source_receipt_observations(connection, run):
    """One canonical native/Core source view for batch and actual-read gates."""
    receipts, predecessor_deliveries = [], []
    for row in connection.execute(
        "SELECT call_id,result_json FROM worker_tool_calls WHERE run_id=? "
        "AND tool_name='worker_read_completed_delivery' AND state='succeeded' ORDER BY rowid",
        (run["id"],),
    ):
        predecessor_deliveries.append(
            {"call_id": row["call_id"], "output": json.loads(row["result_json"])}
        )
    # Core-prefetched completed deliveries are distinct from model-selected calls,
    # but consume exactly the same complete cumulative source-receipt byte cap.
    from .worker_prefetch import has_table

    if has_table(connection):
        for row in connection.execute(
            "SELECT id,result_json FROM worker_core_source_reads WHERE run_id=? "
            "AND tool_name='worker_read_completed_delivery' AND state='succeeded' ORDER BY ordinal",
            (run["id"],),
        ):
            predecessor_deliveries.append(
                {"call_id": row["id"], "output": json.loads(row["result_json"])}
            )
    for row in connection.execute(
        "SELECT r.id,r.result_json,i.tool_name,i.resource_scope,r.result_digest,r.execution_mode "
        "FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id "
        "WHERE i.run_id=? AND i.state='succeeded' ORDER BY r.rowid",
        (run["id"],),
    ):
        output = json.loads(row["result_json"])
        receipts.append(
            {
                key: row[key]
                for key in ("id", "tool_name", "resource_scope", "result_digest", "execution_mode")
            }
            | {"output": output}
        )
    return receipts, predecessor_deliveries


def _projected_receipt(run, name, path, output):
    return {
        "id": "receipt_" + "0" * 32,
        "tool_name": name,
        "resource_scope": path,
        "result_digest": "0" * 64,
        "execution_mode": run["execution_mode"],
        "output": output,
    }


def _check_complete_source_bytes(envelope, receipts, predecessor_deliveries):
    source_bytes = len(canonical(receipts).encode())
    if predecessor_deliveries:
        source_bytes += len(canonical(predecessor_deliveries).encode())
    if source_bytes > envelope["max_source_bytes"]:
        raise DomainError("Tool batch would exceed approved complete source-receipt bytes")


def validate_batch(store, connection, run, capsule, calls):
    """Reject unbounded reads/writes before a pending call or side effect exists.

    This is a conservative admission policy for new contracts. Review reconstructs
    and validates the complete actual packet independently after delivery.
    """
    envelope = capacity_policy(capsule)
    if envelope is None:
        return
    validate_capacity_policy(envelope)
    from .tool_engine import ToolEngine

    service = ToolEngine(store)
    declared = {
        service._scope("file_read", {"path": path}) for path in capsule["required_input_paths"]
    }
    from .hypothesis_sources import validate_paths as validate_hypothesis_paths

    hypothesis_paths = validate_hypothesis_paths(capsule.get("hypothesis_read_paths", []))
    hypothesis_scopes = {service._scope("file_read", {"path": path}) for path in hypothesis_paths}
    receipts, predecessor_deliveries = _source_receipt_observations(connection, run)
    artifacts = {
        receipt["resource_scope"]: None
        for receipt in receipts
        if receipt["tool_name"] in {"file_write", "file_revise"}
    }
    for path in artifacts:
        content = Path(path).read_text(encoding="utf-8")
        try:
            artifacts[path] = json.loads(content)
        except ValueError:
            artifacts[path] = content
    for call in calls:
        name, args = call["name"], call["arguments"]
        if name == "worker_read_completed_delivery":
            from .predecessor_delivery import read_completed_delivery

            output = read_completed_delivery(store, connection, run["id"], args)
            predecessor_deliveries.append({"call_id": "call_" + "0" * 32, "output": output})
            continue
        if name not in {"file_read", "file_write", "file_revise"}:
            continue
        path = service._scope(name, args)
        if name == "file_read":
            if path not in declared and path not in artifacts and path not in hypothesis_scopes:
                raise DomainError(
                    "Read is outside the approved input inventory and owned artifacts"
                )
            with Path(path).open("rb") as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise DomainError("Declared input exceeds bounded read contract")
            output = {"content": data.decode("utf-8"), "sha256": hashlib.sha256(data).hexdigest()}
        else:
            text = args.get("content")
            if not isinstance(text, str):
                raise DomainError("Artifact content must be UTF-8 text")
            if len(text.encode("utf-8")) > envelope["max_artifact_bytes"]:
                raise DomainError("Tool batch would exceed approved complete artifact bytes")
            try:
                artifacts[path] = json.loads(text)
            except ValueError:
                artifacts[path] = text
            output = {
                "path": path,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "bytes": len(text.encode()),
            }
            if name == "file_revise":
                output.update(
                    prior_receipt_id=args.get("prior_receipt_id"),
                    prior_sha256=args.get("expected_sha256"),
                )
        receipts.append(_projected_receipt(run, name, path, output))
    _check_complete_source_bytes(envelope, receipts, predecessor_deliveries)
    if (
        sum(len(canonical(value).encode()) for value in artifacts.values())
        > envelope["max_artifact_bytes"]
    ):
        raise DomainError("Tool batch would exceed approved complete artifact bytes")


class DeliveryCapacityError(ValueError):
    """A complete parsed delivery exceeds its unchanged approved byte bound."""

    code = "delivery_byte_bound"

    def __init__(self, actual_bytes, max_bytes):
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes
        super().__init__("Complete delivery exceeds its explicitly approved byte bound")


def validate_delivery(capsule, value):
    envelope = capacity_policy(capsule)
    if envelope is not None:
        actual_bytes = len(canonical(value).encode("utf-8"))
        if actual_bytes > envelope["max_delivery_bytes"]:
            raise DeliveryCapacityError(actual_bytes, envelope["max_delivery_bytes"])
