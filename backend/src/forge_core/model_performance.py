"""Bounded Core observations, kept separate from manually supplied ability scores.

Transport success is never interpreted as task success. These snapshots describe
recorded executions, not a benchmark estimate of general model capabilities.
"""

import hashlib
import json
import math


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
        "task_success_rate": _rate(
            sum(row["state"] == "succeeded" for row in runs),
            sum(row["state"] in {"succeeded", "failed"} for row in runs),
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
        omitted.update(
            {
                "tools": len(tools) > limit,
                "attempts": len(attempts) > limit,
                "events": len(events) > limit,
                "reviews": len(reviews) > limit,
            }
        )
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
        refs.extend(
            f"node_verification:{row['plan_id']}:{row['node_key']}:{row['worker_id']}"
            for row in reviews
        )
        for group in (tools, attempts, events, reviews):
            source_rows.extend(dict(row) for row in group)
    metrics.update({name: value for name, value in rates.items() if value is not None})
    latest = max(
        [row["created_at"] for row in [*observations, *runs, *(events if runs else [])]],
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
