"""Measured future Composer stages with exact plan binding and durable settlement."""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .composer_request import VERSION, canonical
from .domain import DomainError


class CompositionMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal["bounded-semantic-composition-v1"]
    provider_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=256)
    context_limit: int = Field(gt=0, le=10000000)
    operator_max_concurrency: int = Field(gt=0, le=128)
    model_max_output_tokens: int = Field(gt=0, le=1000000)
    model_capability_sources: dict[str, str]
    max_retries: Literal[0]
    semantic_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    initial_call_bound: int = Field(gt=0, le=10000000)
    repair_call_bound: int = Field(gt=0, le=10000000)
    output_bound: int = Field(gt=0, le=8192)
    repair_message_bytes: int = Field(gt=0, le=2048)
    total_future_bound: int = Field(gt=0, le=20000000)


def provider_for_graph(providers, graph, *, fallback=None):
    contract = graph.get("capacity_contract") or {}
    record = contract.get("composition_capacity")
    if not record:
        return fallback
    if fallback is not None and getattr(
        getattr(fallback, "config", None), "id", None
    ) == record.get("provider_id"):
        return fallback
    if providers is None:
        raise DomainError("Approved composition capacity Provider is unavailable")
    try:
        return providers.get(record["provider_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError("Approved composition capacity Provider is unavailable") from exc


def _typed_measurement(value):
    result = CompositionMeasurement.model_validate(value).model_dump()
    if (
        result["total_future_bound"] != result["initial_call_bound"] + result["repair_call_bound"]
        or max(result["initial_call_bound"], result["repair_call_bound"]) > result["context_limit"]
        or result["output_bound"] > result["model_max_output_tokens"]
        or result["model_capability_sources"].keys() != {"json_schema"}
    ):
        raise DomainError("Invalid complete future Composer measurement")
    return result


def _attempt_floor(connection):
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_budget_attempts'"
    ).fetchone()
    return (
        connection.execute("SELECT COALESCE(MAX(rowid),0) FROM model_budget_attempts").fetchone()[0]
        if exists
        else 0
    )


def bind_measurement(store, connection, quest, graph, provider, measurer):
    """Create the observation floor once; later validation never moves it."""
    contract = graph.get("capacity_contract") or {}
    if contract.get("composition_capacity_policy") != VERSION:
        raise DomainError("Unknown future Composer capacity policy")
    if measurer is None or provider is None:
        raise DomainError("Explicit composition capacity requires its actual request measurer")
    measured = _typed_measurement(measurer(store, connection, quest, graph, provider))
    previous = contract.get("composition_capacity")
    if previous is not None:
        validate_binding(store, connection, quest, graph, provider, measurer)
        return previous
    result = {**measured, "attempt_rowid_floor": _attempt_floor(connection)}
    contract["composition_capacity"] = result
    return result


def validate_binding(store, connection, quest, graph, provider, measurer, *, resource_sizing=False):
    contract = graph.get("capacity_contract") or {}
    policy = contract.get("composition_capacity_policy")
    record = contract.get("composition_capacity")
    if policy is None and record is None:
        return None
    if policy != VERSION:
        raise DomainError("Unknown future Composer capacity policy")
    if resource_sizing and record is None:
        # Only the private deterministic sizing loop may delay this binding.
        # All stored proposals and approvals require a complete observation.
        return None
    if not isinstance(record, dict) or set(record) != set(CompositionMeasurement.model_fields) | {
        "attempt_rowid_floor"
    }:
        raise DomainError("Explicit composition capacity lacks its measured binding")
    floor = record["attempt_rowid_floor"]
    if type(floor) is not int or not 0 <= floor <= _attempt_floor(connection):
        raise DomainError("Invalid composition capacity attempt floor")
    original = _typed_measurement(
        {key: value for key, value in record.items() if key != "attempt_rowid_floor"}
    )
    if measurer is None or provider is None:
        raise DomainError("Explicit composition capacity requires its actual request measurer")
    current = _typed_measurement(measurer(store, connection, quest, graph, provider))
    if current != original:
        raise DomainError("Composer capacity differs from its approved request binding")
    return record


def remaining_composition_capacity(connection, quest_id, graph):
    """Release a stage only after all its exact bound call attempts settle."""
    contract = graph.get("capacity_contract") or {}
    record = contract.get("composition_capacity")
    if record is None:
        if contract.get("composition_capacity_policy") is not None:
            raise DomainError("Explicit composition capacity lacks its measured binding")
        return None
    if contract.get("composition_capacity_policy") != VERSION:
        raise DomainError("Unknown future Composer capacity policy")
    original = _typed_measurement(
        {key: value for key, value in record.items() if key != "attempt_rowid_floor"}
    )
    floor = record.get("attempt_rowid_floor")
    if type(floor) is not int or floor < 0:
        raise DomainError("Invalid composition capacity attempt floor")
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_budget_attempts'"
    ).fetchone()
    rows = (
        connection.execute(
            "SELECT call_id,state,input_charged,output_charged FROM model_budget_attempts "
            "WHERE rowid>? AND quest_id=? AND provider_id=? AND model_id=? "
            "AND parent_call_id=? AND purpose='team_composer' ORDER BY rowid",
            (
                floor,
                quest_id,
                original["provider_id"],
                original["model_id"],
                original["semantic_request_sha256"],
            ),
        ).fetchall()
        if exists
        else []
    )
    calls = {}
    for row in rows:
        calls.setdefault(row["call_id"], []).append(row)
    if len(calls) > 2:
        raise DomainError("Composition capacity permits only its initial call and one repair")
    settled = []
    for attempts in calls.values():
        if len(attempts) != 1:
            raise DomainError("Bounded composition cannot silently admit transport retries")
        settled.append(
            all(
                row["state"] == "settled"
                and type(row["input_charged"]) is int
                and type(row["output_charged"]) is int
                for row in attempts
            )
        )
    completed = 0
    for state in settled:
        if not state:
            break
        completed += 1
    future = (
        original["total_future_bound"]
        if completed == 0
        else original["repair_call_bound"]
        if completed == 1
        else 0
    )
    # A valid semantic team proposal ends this composition cycle. Its digest
    # must refer to this exact full plan, not merely the same Quest/revision.
    graph_digest = hashlib.sha256(canonical(graph).encode()).hexdigest()
    accepted = connection.execute(
        "SELECT 1 FROM team_proposals WHERE quest_id=? AND plan_digest=? "
        "AND status IN ('proposed','approved') LIMIT 1",
        (quest_id, graph_digest),
    ).fetchone()
    if accepted and completed and all(settled):
        future = 0
    return {
        "version": VERSION,
        "initial_call_bound": original["initial_call_bound"],
        "repair_call_bound": original["repair_call_bound"],
        "settled_composer_calls": completed,
        "unresolved_composer_calls": len(settled) - sum(settled),
        "future_composer_tokens": future,
        "accepted_semantic_team": bool(accepted),
    }
