"""Bounded Core observations, kept separate from manually supplied ability scores.

Transport success is never interpreted as task success. These snapshots describe
recorded executions, not a benchmark estimate of general model capabilities.
"""

import hashlib
import json
import math
import time


def _rate(successes, samples):
    if not samples:
        return None
    # Wilson 95% interval is intentionally wide for small samples.
    z = 1.959963984540054
    value = successes / samples
    divisor = 1 + z * z / samples
    center = (value + z * z / (2 * samples)) / divisor
    half = z * math.sqrt(value * (1 - value) / samples + z * z / (4 * samples**2)) / divisor
    return {
        "value": value,
        "n_samples": samples,
        "confidence": {
            "method": "wilson_95",
            "lower": max(0, center - half),
            "upper": min(1, center + half),
        },
    }


def performance_snapshot(connection, provider_id, model_id, *, mode="live", limit=256):
    """Read a bounded recent sample in the caller's transaction without editing profiles.

    All joins bind provider, model and execution mode. Unknown token prices,
    missing usage, task quality and schema outcomes remain unknown. Source IDs
    and a digest make the exact proposal-time snapshot auditable after restart.
    """
    if mode not in {"live", "mock"} or type(limit) is not int or not 1 <= limit <= 1024:
        raise ValueError("Invalid performance snapshot bounds or provenance")
    observations = connection.execute(
        """SELECT * FROM model_observations WHERE provider_id=? AND model_id=? AND mode=?
        ORDER BY created_at DESC,id DESC LIMIT ?""",
        (provider_id, model_id, mode, limit + 1),
    ).fetchall()
    runs = connection.execute(
        """SELECT * FROM worker_runs WHERE provider_id=? AND model_id=? AND execution_mode=?
        ORDER BY created_at DESC,id DESC LIMIT ?""",
        (provider_id, model_id, mode, limit + 1),
    ).fetchall()
    omitted = {"gateway": len(observations) > limit, "workers": len(runs) > limit}
    observations, runs = observations[:limit], runs[:limit]
    metrics = {}
    rates = {
        "transport_success_rate": _rate(
            sum(row["outcome"] == "success" for row in observations), len(observations)
        ),
        "transport_error_rate": _rate(
            sum(row["outcome"] == "error" for row in observations), len(observations)
        ),
        "transport_timeout_rate": _rate(
            sum(row["outcome"] == "timeout" for row in observations), len(observations)
        ),
        "task_success_rate": _rate(
            sum(row["state"] == "succeeded" for row in runs),
            sum(row["state"] in {"succeeded", "failed"} for row in runs),
        ),
        "worker_blocked_rate": _rate(
            sum(row["state"] == "blocked" for row in runs),
            sum(row["state"] in {"succeeded", "failed", "blocked"} for row in runs),
        ),
    }
    elapsed = sorted(
        row["latency_seconds"] for row in observations if row["latency_seconds"] is not None
    )
    if elapsed:
        metrics["average_latency_seconds"] = {
            "value": sum(elapsed) / len(elapsed),
            "n_samples": len(elapsed),
            "confidence": None,
        }
        metrics["p95_latency_seconds"] = {
            "value": elapsed[math.ceil(0.95 * len(elapsed)) - 1],
            "n_samples": len(elapsed),
            "confidence": None,
        }
    generation_rates = [
        row["output_tokens"] / (row["latency_seconds"] - row["ttft_seconds"])
        for row in observations
        if row["outcome"] == "success"
        and row["output_tokens"] is not None
        and row["ttft_seconds"] is not None
        and row["latency_seconds"] is not None
        and row["latency_seconds"] > row["ttft_seconds"]
    ]
    if generation_rates:
        metrics["tokens_per_second"] = {
            "value": sum(generation_rates) / len(generation_rates),
            "n_samples": len(generation_rates),
            "confidence": None,
            "coverage": "successful_streams_with_observed_first_token_only",
        }
    source_rows = [dict(row) for row in observations]
    source_rows.extend(dict(row) for row in runs)
    refs = [f"model_observation:{row['id']}" for row in observations]
    refs.extend(f"worker_run:{row['id']}" for row in runs)
    if runs:
        run_ids = [row["id"] for row in runs]
        worker_ids = [row["worker_id"] for row in runs]
        placeholders = ",".join("?" for _ in run_ids)
        tools = connection.execute(
            f"SELECT * FROM worker_tool_calls WHERE run_id IN ({placeholders}) ORDER BY run_id,call_id LIMIT ?",
            (*run_ids, limit + 1),
        ).fetchall()
        attempts = connection.execute(
            f"""SELECT * FROM model_budget_attempts WHERE worker_run_id IN ({placeholders})
            AND provider_id=? AND model_id=? ORDER BY created_at DESC,attempt_id DESC LIMIT ?""",
            (*run_ids, provider_id, model_id, limit + 1),
        ).fetchall()
        events = connection.execute(
            f"""SELECT * FROM events WHERE worker_id IN ({placeholders})
            AND event_type IN ('worker.result','worker.retry','worker.progress','worker.blocked','worker.failed')
            ORDER BY seq DESC LIMIT ?""",
            (*worker_ids, limit + 1),
        ).fetchall()
        reviews = connection.execute(
            f"""SELECT * FROM node_verification_attempts WHERE worker_id IN ({placeholders})
            ORDER BY plan_id,node_key,worker_id LIMIT ?""",
            (*worker_ids, limit + 1),
        ).fetchall()
        verdicts = connection.execute(
            f"""SELECT * FROM events WHERE worker_id IN ({placeholders})
            AND event_type='review.result' AND json_extract(payload,'$.provider_id')=?
            AND json_extract(payload,'$.model_id')=?
            AND json_extract(payload,'$.execution_mode')=? ORDER BY seq DESC LIMIT ?""",
            (*worker_ids, provider_id, model_id, mode, limit + 1),
        ).fetchall()
        omitted.update(
            {
                "tools": len(tools) > limit,
                "attempts": len(attempts) > limit,
                "events": len(events) > limit,
                "reviews": len(reviews) > limit,
                "review_verdicts": len(verdicts) > limit,
            }
        )
        verdicts = [
            row for row in verdicts[:limit] if json.loads(row["payload"]).get("run_id") in run_ids
        ]
        tools, attempts, events, reviews = (
            tools[:limit],
            attempts[:limit],
            events[:limit],
            reviews[:limit],
        )
        settled_tools = [row for row in tools if row["state"] in {"succeeded", "failed"}]
        rates["tool_call_success_rate"] = _rate(
            sum(row["state"] == "succeeded" for row in settled_tools), len(settled_tools)
        )
        settled_reviews = [row for row in reviews if row["state"] in {"verified", "blocked"}]
        rates["review_pass_rate"] = _rate(
            sum(row["state"] == "verified" for row in settled_reviews), len(settled_reviews)
        )
        judged = [
            json.loads(row["payload"])
            for row in verdicts
            if json.loads(row["payload"]).get("error_code") is None
            and json.loads(row["payload"]).get("verdict") in {"PASS", "NEEDS_REVISION", "BLOCKED"}
        ]
        rates["independent_review_verdict_pass_rate"] = _rate(
            sum(item["verdict"] == "PASS" for item in judged), len(judged)
        )
        rates["independent_review_error_rate"] = _rate(
            sum(json.loads(row["payload"]).get("error_code") is not None for row in verdicts),
            len(verdicts),
        )
        retry_workers = set()
        valid_schema = invalid_schema = retry_count = 0
        for event in events:
            payload = json.loads(event["payload"])
            if payload.get("run_id") not in run_ids:
                continue
            repair = (
                event["event_type"] == "worker.progress" and payload.get("phase") == "self_repair"
            )
            retry = event["event_type"] == "worker.retry" or repair
            if retry:
                retry_count += 1
                retry_workers.add(event["worker_id"])
            valid_schema += event["event_type"] == "worker.result"
            invalid_schema += payload.get("reason_code") == "invalid_output_contract" and (
                repair or event["event_type"] in {"worker.blocked", "worker.failed"}
            )
        rates["schema_compliance"] = _rate(valid_schema, valid_schema + invalid_schema)
        rates["retry_rate"] = _rate(len(retry_workers), len(runs))
        metrics["retry_count"] = {"value": retry_count, "n_samples": len(runs), "confidence": None}
        charged = [
            row
            for row in attempts
            if row["input_charged"] is not None and row["output_charged"] is not None
        ]
        if charged:
            metrics["token_consumption"] = {
                "value": sum(row["input_charged"] + row["output_charged"] for row in charged),
                "n_samples": len(charged),
                "confidence": None,
                "coverage": "known_charged_attempts_only",
            }
        currencies = {row["currency"] for row in charged}
        if (
            charged
            and all(row["cost_charged"] is not None for row in charged)
            and len(currencies) == 1
            and None not in currencies
        ):
            metrics["cost_microunits"] = {
                "value": sum(row["cost_charged"] for row in charged),
                "currency": next(iter(currencies)),
                "n_samples": len(charged),
                "confidence": None,
                "coverage": "known_charged_attempts_only",
            }
        refs.extend(f"worker_tool_call:{row['run_id']}:{row['call_id']}" for row in tools)
        refs.extend(f"model_attempt:{row['attempt_id']}" for row in attempts)
        refs.extend(f"event:{row['id']}" for row in events)
        refs.extend(f"event:{row['id']}" for row in verdicts)
        refs.extend(
            f"node_verification:{row['plan_id']}:{row['node_key']}:{row['worker_id']}"
            for row in reviews
        )
        for group in (tools, attempts, events, reviews, verdicts):
            source_rows.extend(dict(row) for row in group)
    metrics.update({name: value for name, value in rates.items() if value is not None})
    latest = max(
        [
            row["created_at"]
            for row in [
                *observations,
                *runs,
                *(events if runs else []),
                *(verdicts if runs else []),
            ]
        ],
        default=None,
    )
    for measurement in metrics.values():
        measurement.update(
            source="core_execution_observation", execution_mode=mode, measured_at=latest
        )
    digest = hashlib.sha256(
        json.dumps(source_rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "provider_id": provider_id,
        "model_id": model_id,
        "execution_mode": mode,
        "source": "core_execution_observation",
        "measurements": metrics,
        "source_refs": refs[:32],
        "source_refs_omitted": max(0, len(refs) - 32),
        "snapshot_sha256": digest,
        "sample_limit": limit,
        "sample_truncated": omitted,
        "unknown_usage_attempts": sum(
            row["input_charged"] is None or row["output_charged"] is None for row in attempts
        )
        if runs
        else 0,
        "limitations": "Recent bounded execution observations; transport success is not task success; no general capability estimate.",
    }


def model_health_snapshot(
    connection, provider_id, model_id, *, mode="live", limit=32, failure_threshold=3, now=None
):
    """A sourced health signal, never authority to retry an unknown operation.

    Gateway observations carry no request ID. They establish transport health;
    unresolved accounting is therefore inspected independently through Worker-bound
    reservations with matching execution provenance, never guessed from failures.
    A strictly proven completed HTTP rejection remains usage-unknown and fully
    reserved, but is distinct from an in-flight request or an unknown operation.
    """
    if (
        mode not in {"live", "mock"}
        or type(limit) is not int
        or not 1 <= limit <= 256
        or type(failure_threshold) is not int
        or not 1 <= failure_threshold <= 10
        or not isinstance(provider_id, str)
        or not provider_id
        or not isinstance(model_id, str)
        or not model_id
    ):
        raise ValueError("Invalid model health bounds or identity")
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError("Invalid model health clock")
    observations = connection.execute(
        "SELECT * FROM model_observations WHERE provider_id=? AND model_id=? AND mode=? "
        "ORDER BY created_at DESC,id DESC LIMIT ?",
        (provider_id, model_id, mode, limit),
    ).fetchall()
    unknown = connection.execute(
        "SELECT s.call_id,s.run_id,s.state,r.state AS run_state,r.lease_until,r.deadline "
        "FROM worker_model_reservations s JOIN worker_runs r ON r.id=s.run_id "
        "WHERE s.provider_id=? AND s.model_id=? AND s.execution_mode=? AND s.state='reserved' "
        "ORDER BY s.created_at DESC,s.call_id DESC LIMIT ?",
        (provider_id, model_id, mode, limit + 1),
    ).fetchall()
    attempts = connection.execute(
        "SELECT a.attempt_id,a.call_id,a.worker_run_id,a.state,a.recover_after,"
        "r.state AS run_state,r.lease_until,r.deadline FROM model_budget_attempts a "
        "JOIN worker_runs r ON r.id=a.worker_run_id "
        "WHERE a.provider_id=? AND a.model_id=? AND r.execution_mode=? "
        "AND a.state IN ('reserved','overrun') ORDER BY a.created_at DESC LIMIT ?",
        (provider_id, model_id, mode, limit + 1),
    ).fetchall()
    cooldown = connection.execute(
        "SELECT retry_after FROM model_provider_cooldowns WHERE provider_id=?", (provider_id,)
    ).fetchone()
    cooling = bool(cooldown and cooldown[0] > now)
    failures = 0
    for observation in observations:
        if observation["outcome"] == "success":
            break
        failures += 1

    def in_flight(row):
        return (
            row["state"] == "reserved"
            and row["deadline"] > now
            and (
                row["run_state"] == "running"
                and row["lease_until"] is not None
                and row["lease_until"] > now
                or row["run_state"] == "review_pending"
            )
            and (
                "recover_after" not in row.keys()  # noqa: SIM118 - sqlite3.Row membership tests values.
                or row["recover_after"] is None
                or row["recover_after"] > now
            )
        )

    from .worker_model_usage import completed_gateway_rejection_proof

    rejected = {
        row["call_id"]: proof
        for row in unknown
        if (proof := completed_gateway_rejection_proof(connection, row["call_id"])) is not None
    }
    unknown_calls = {
        row["call_id"]
        for row in [*unknown, *attempts]
        if row["call_id"] not in rejected and not in_flight(row)
    }
    in_flight_calls = {
        row["call_id"]
        for row in [*unknown, *attempts]
        if row["call_id"] not in rejected and in_flight(row)
    }
    recommendation = (
        "blocked_unknown"
        if unknown_calls
        else "degraded"
        if cooling or failures >= failure_threshold
        else "available"
        if observations
        else "unknown"
    )
    sources = {
        "observations": [dict(row) for row in observations],
        "unsettled_worker_calls": [dict(row) for row in unknown],
        "unsettled_worker_attempts": [dict(row) for row in attempts],
        "cooldown": dict(cooldown) if cooldown else None,
        "failure_threshold": failure_threshold,
        "recommendation": recommendation,
    }
    refs = [f"model_observation:{row['id']}" for row in observations]
    refs.extend(f"model_call:{row['call_id']}" for row in unknown)
    refs.extend(f"model_attempt:{row['attempt_id']}" for row in attempts)
    return {
        "provider_id": provider_id,
        "model_id": model_id,
        "execution_mode": mode,
        "source": "core_execution_observation",
        "recommendation": recommendation,
        "consecutive_failures": failures,
        "failure_threshold": failure_threshold,
        "transport_failure_rate": _rate(
            sum(row["outcome"] != "success" for row in observations), len(observations)
        ),
        "transport_timeout_rate": _rate(
            sum(row["outcome"] == "timeout" for row in observations), len(observations)
        ),
        "unknown_attempts": len(unknown_calls),
        "in_flight_calls": len(in_flight_calls),
        "known_rejected_calls": len(rejected),
        "usage_unknown_calls": len(unknown_calls | in_flight_calls | rejected.keys()),
        "known_rejection_proofs": list(rejected.values()),
        "unknown_sample_truncated": len(unknown) > limit or len(attempts) > limit,
        "provider_cooldown_until": cooldown[0] if cooling else None,
        "source_refs": refs[:32],
        "source_refs_omitted": max(0, len(refs) - 32),
        "snapshot_sha256": hashlib.sha256(
            json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "sample_limit": limit,
        "observed_at": observations[0]["created_at"] if observations else None,
        "limitations": "Transport health and Worker-bound unsettled accounting only; provenance of non-Worker Gateway attempts is not inferred. Not model quality or permission to replay any call.",
    }
