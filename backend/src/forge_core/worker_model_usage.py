"""Exactly-once local accounting; settled usage never authorizes a response."""

import json

from .domain import DomainError, now_iso

_CONTEXT_FIELDS = (
    "quest_id",
    "worker_id",
    "plan_id",
    "node_key",
    "plan_revision",
    "plan_digest",
    "team_digest",
)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def reserve_worker_model_call(
    connection, row, call_id, reserve, output_limit, cost, ledgers, *, purpose="worker"
):
    """Bind the local increment in the caller's same write transaction."""
    context = {key: row[key] for key in _CONTEXT_FIELDS}
    context["workspace_id"] = connection.execute(
        "SELECT id FROM workspace WHERE singleton=1"
    ).fetchone()[0]
    if purpose not in {"worker", "context_compaction"}:
        raise DomainError("Invalid Worker model reservation purpose")
    if purpose != "worker":
        context["purpose"] = purpose
    scopes = [{key: ledger[key] for key in ("scope", "scope_id", "currency")} for ledger in ledgers]
    connection.execute(
        "INSERT INTO worker_model_reservations(call_id,run_id,generation,lease_token,provider_id,"
        "model_id,execution_mode,reserved_tokens,output_limit,reserved_cost_microunits,currency,"
        "price_bounds_json,cost_scopes_json,context_json,state,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'reserved',?)",
        (
            call_id,
            row["id"],
            row["generation"],
            row["lease_token"],
            row["provider_id"],
            row["model_id"],
            row["execution_mode"],
            reserve,
            output_limit,
            cost,
            row["currency"],
            row["price_bounds_json"],
            _json(scopes),
            _json(context),
            now_iso(),
        ),
    )


def _bound_run(connection, record):
    row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (record["run_id"],)).fetchone()
    if row is None:
        return None
    context = json.loads(record["context_json"])
    workspace = connection.execute("SELECT id FROM workspace WHERE singleton=1").fetchone()
    if (
        set(context)
        not in ({*_CONTEXT_FIELDS, "workspace_id"}, {*_CONTEXT_FIELDS, "workspace_id", "purpose"})
        or context.get("purpose", "worker") not in {"worker", "context_compaction"}
        or workspace is None
        or context["workspace_id"] != workspace[0]
        or any(context[key] != row[key] for key in _CONTEXT_FIELDS)
        or any(
            record[key] != row[key]
            for key in ("provider_id", "model_id", "execution_mode", "currency")
        )
        or row["generation"] < record["generation"]
    ):
        return None
    return row


def _usage(record, value):
    if not isinstance(value, dict):
        return None
    inp, out = value.get("input_tokens"), value.get("output_tokens")
    if (
        type(inp) is not int
        or type(out) is not int
        or not 0 <= inp <= record["reserved_tokens"] - record["output_limit"]
        or not 0 <= out <= record["output_limit"]
    ):
        return None
    return inp, out


def _gateway_usage(connection, record, row):
    attempts = connection.execute(
        "SELECT * FROM model_budget_attempts WHERE call_id=?", (record["call_id"],)
    ).fetchall()
    if len(attempts) != 1:
        return None, bool(attempts)
    attempt = attempts[0]
    context = json.loads(record["context_json"])
    if (
        attempt["state"] != "settled"
        or attempt["purpose"] != context.get("purpose", "worker")
        or attempt["workspace_id"] != context["workspace_id"]
        or attempt["quest_id"] != row["quest_id"]
        or attempt["worker_run_id"] != row["id"]
        or attempt["provider_id"] != record["provider_id"]
        or attempt["model_id"] != record["model_id"]
    ):
        return None, True
    value = _usage(
        record,
        {"input_tokens": attempt["input_charged"], "output_tokens": attempt["output_charged"]},
    )
    if (
        value is None
        or value[0] > attempt["input_reserved"]
        or value[1] > attempt["output_reserved"]
    ):
        return None, True
    if record["reserved_cost_microunits"] is not None:
        price = json.loads(record["price_bounds_json"])
        if (
            attempt["currency"] != record["currency"]
            or attempt["input_price"] != price["input_microunits_per_million"]
            or attempt["output_price"] != price["output_microunits_per_million"]
            or attempt["price_version"] != price["version"]
            or attempt["cost_charged"] != _actual_cost(record, *value)
        ):
            return None, True
    return value, True


def completed_gateway_rejection_proof(connection, call_id):
    """Prove a completed rejection without settling or refunding unknown usage.

    The Gateway writes ``rejected`` only after fully receiving the HTTP rejection.
    A unique, exact Worker/context-bound record distinguishes that known outcome
    from a network timeout, interrupted body or still-running request. Local and
    Gateway reserves remain charged in full; this grants no response/tool/replay
    authority. Compaction requests deliberately retain their existing fences.
    Native Gateway framing and the local request estimate may differ; both
    original reservations are returned and retained, never equated or refunded.
    """
    try:
        record = connection.execute(
            "SELECT * FROM worker_model_reservations WHERE call_id=? AND state='reserved'",
            (call_id,),
        ).fetchone()
        if record is None:
            return None
        row = _bound_run(connection, record)
        context = json.loads(record["context_json"])
        if row is None or context.get("purpose", "worker") != "worker":
            return None
        attempts = connection.execute(
            "SELECT * FROM model_budget_attempts WHERE call_id=?", (call_id,)
        ).fetchall()
        if len(attempts) != 1:
            return None
        attempt = attempts[0]
        if (
            attempt["state"] != "rejected"
            or attempt["purpose"] != "worker"
            or attempt["workspace_id"] != context["workspace_id"]
            or attempt["quest_id"] != row["quest_id"]
            or attempt["worker_run_id"] != row["id"]
            or attempt["provider_id"] != record["provider_id"]
            or attempt["model_id"] != record["model_id"]
            or any(attempt[name] is not None for name in ("input_charged", "output_charged", "cost_charged"))
            or type(attempt["input_reserved"]) is not int
            or attempt["input_reserved"] <= 0
            or attempt["output_reserved"] != record["output_limit"]
        ):
            return None
        return {
            "call_id": call_id,
            "run_id": row["id"],
            "attempt_id": attempt["attempt_id"],
            "execution_mode": record["execution_mode"],
            "operation_status": "completed_rejection",
            "usage_status": "unknown",
            "reservation_retained": True,
            "local_reserved_tokens": record["reserved_tokens"],
            "gateway_input_reserved": attempt["input_reserved"],
            "gateway_output_reserved": attempt["output_reserved"],
            "gateway_cost_reserved": attempt["cost_reserved"],
            "source_reference": "model_attempt:" + attempt["attempt_id"],
        }
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def _actual_cost(record, inp, out):
    if record["reserved_cost_microunits"] is None:
        return None
    price = json.loads(record["price_bounds_json"])
    return (
        inp * price["input_microunits_per_million"]
        + out * price["output_microunits_per_million"]
        + 999999
    ) // 1000000


def _cost_refund(connection, record, row, actual):
    if record["reserved_cost_microunits"] is None:
        return [] if actual is None else None
    refund = record["reserved_cost_microunits"] - actual
    scopes = json.loads(record["cost_scopes_json"])
    if (
        type(actual) is not int
        or refund < 0
        or row["cost_spent_microunits"] is None
        or row["cost_spent_microunits"] < refund
        or not isinstance(scopes, list)
        or not scopes
    ):
        return None
    keys = []
    for scope in scopes:
        key = (scope["scope"], scope["scope_id"], scope["currency"])
        if (
            key in keys
            or key[2] != record["currency"]
            or key[:2] not in {("quest", row["quest_id"]), ("global", "global")}
        ):
            return None
        ledger = connection.execute(
            "SELECT reserved_microunits FROM worker_cost_ledger "
            "WHERE scope=? AND scope_id=? AND currency=?",
            key,
        ).fetchone()
        if ledger is None or ledger[0] < refund:
            return None
        keys.append(key)
    return [(key, refund) for key in keys]


def settle_worker_model_call(connection, call_id, *, usage=None, current_lease_token=None):
    """Settle this exact local reserve once; return no execution authority.

    Recovery accepts only one matching settled Gateway attempt. Synchronous
    adapters retain their response-usage contract only when no attempt exists
    and the caller has independently fenced this lease in the same transaction.
    Any existing unproven attempt prevents this current-owner fallback.
    """
    try:
        record = connection.execute(
            "SELECT * FROM worker_model_reservations WHERE call_id=?", (call_id,)
        ).fetchone()
        if record is None or record["state"] == "refunded":
            return False
        row = _bound_run(connection, record)
        if row is None:
            return False
        supplied = None if usage is None else _usage(record, usage)
        if usage is not None and supplied is None:
            return False
        if record["state"] == "settled":
            return supplied is None or supplied == (record["input_tokens"], record["output_tokens"])
        value, has_attempt = _gateway_usage(connection, record, row)
        if value is None:
            if (
                has_attempt
                or not current_lease_token
                or record["lease_token"] != current_lease_token
                or row["lease_token"] != current_lease_token
                or row["generation"] != record["generation"]
                or row["state"] != "running"
                or supplied is None
            ):
                return False
            value = supplied
        if supplied is not None and supplied != value:
            return False
        inp, out = value
        actual = _actual_cost(record, inp, out)
        costs = _cost_refund(connection, record, row, actual)
        refund = record["reserved_tokens"] - inp - out
        if costs is None or row["token_spent"] < refund:
            return False
        changed = connection.execute(
            "UPDATE worker_model_reservations SET state='settled',input_tokens=?,output_tokens=?,"
            "actual_cost_microunits=?,settled_at=? WHERE call_id=? AND state='reserved'",
            (inp, out, actual, now_iso(), call_id),
        )
        if changed.rowcount != 1:
            return False
        connection.execute(
            "UPDATE worker_runs SET token_spent=token_spent-? WHERE id=?", (refund, row["id"])
        )
        _apply_cost_refund(connection, row["id"], costs)
        return True
    except (ValueError, TypeError, KeyError, IndexError):
        return False


def _apply_cost_refund(connection, run_id, costs):
    if not costs:
        return
    connection.execute(
        "UPDATE worker_runs SET cost_spent_microunits=cost_spent_microunits-? WHERE id=?",
        (costs[0][1], run_id),
    )
    for key, refund in costs:
        connection.execute(
            "UPDATE worker_cost_ledger SET reserved_microunits=reserved_microunits-? "
            "WHERE scope=? AND scope_id=? AND currency=?",
            (refund, *key),
        )


def refund_unsent_worker_model_call(connection, call_id, lease_token):
    """Current owner only; caller must prove _budget_post_started is False.

    Absence of an attempt alone never proves a request was not sent.
    """
    record = connection.execute(
        "SELECT * FROM worker_model_reservations WHERE call_id=?", (call_id,)
    ).fetchone()
    if record is None or record["state"] != "reserved":
        return False
    row = _bound_run(connection, record)
    if (
        row is None
        or row["state"] != "running"
        or row["lease_token"] != lease_token
        or record["lease_token"] != lease_token
        or record["generation"] != row["generation"]
        or row["steps"] < 1
        or row["token_spent"] < record["reserved_tokens"]
        or connection.execute(
            "SELECT 1 FROM model_budget_attempts WHERE call_id=?", (call_id,)
        ).fetchone()
    ):
        return False
    costs = _cost_refund(
        connection, record, row, 0 if record["reserved_cost_microunits"] is not None else None
    )
    if costs is None:
        return False
    changed = connection.execute(
        "UPDATE worker_model_reservations SET state='refunded',input_tokens=0,output_tokens=0,"
        "actual_cost_microunits=?,settled_at=? WHERE call_id=? AND state='reserved'",
        (0 if record["reserved_cost_microunits"] is not None else None, now_iso(), call_id),
    )
    if changed.rowcount != 1:
        raise DomainError("Unsent Worker reservation changed")
    connection.execute(
        "UPDATE worker_runs SET token_spent=token_spent-?,steps=steps-1 WHERE id=?",
        (record["reserved_tokens"], row["id"]),
    )
    _apply_cost_refund(connection, row["id"], costs)
    return True


def reconcile_worker_model_calls(connection, run_id):
    """Recover accounting only; no historical rows or responses are fabricated."""
    records = connection.execute(
        "SELECT call_id FROM worker_model_reservations WHERE run_id=? AND state='reserved' "
        "ORDER BY created_at,call_id LIMIT 64",
        (run_id,),
    ).fetchall()
    return sum(settle_worker_model_call(connection, row[0]) for row in records)


def reconcile_worker_model_batch(connection, after_call_id):
    """Bounded accounting across all states; cursor carries no execution rights.

    Unknown attempts do not fill this batch. The cursor also advances past any
    invalid settled proofs, so they cannot starve later exact settlements.
    """
    records = connection.execute(
        "SELECT r.call_id FROM worker_model_reservations r WHERE r.state='reserved' "
        "AND r.call_id>? AND EXISTS (SELECT 1 FROM model_budget_attempts a "
        "WHERE a.call_id=r.call_id AND a.state='settled') ORDER BY r.call_id LIMIT 64",
        (after_call_id,),
    ).fetchall()
    count = sum(settle_worker_model_call(connection, row[0]) for row in records)
    return count, records[-1][0] if records else ""
