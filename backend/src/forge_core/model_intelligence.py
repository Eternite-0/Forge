"""Interpret historic model profiles without discarding invalid source records."""

import json
import math

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .domain import ModelProfile, new_id, now_iso


def decode_profile(raw, model_id):
    try:
        profile = ModelProfile.model_validate_json(raw)
        if profile.id != model_id:
            raise ValueError("Stored identity differs from row identity")
        return profile
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
        return ModelProfile.model_construct(
            id=model_id,
            provider="unknown",
            display_name=model_id,
            enabled=False,
            quarantined_raw=raw,
            quarantine_reason="Stored profile is invalid; correct and save it before use",
        )


class GatewayObservation(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    outcome: str
    latency_seconds: float | None = Field(default=None, ge=0)
    ttft_seconds: float | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_observation(self):
        if self.outcome not in {"success", "timeout", "error"}:
            raise ValueError("Unknown outcome")
        if self.ttft_seconds is not None and (
            self.latency_seconds is None or self.ttft_seconds > self.latency_seconds
        ):
            raise ValueError("TTFT cannot exceed total latency")
        return self


class ObservationStore:
    """Internal receiver for gateway observations, separated by execution provenance."""

    def __init__(self, store):
        self.store = store

    def record(self, gateway, model_id, provider_id, observation):
        mode = getattr(gateway, "execution_mode", None)
        if mode not in {"mock", "live"} or not model_id or not provider_id:
            raise ValueError("Gateway provenance and model identity required")
        item = GatewayObservation.model_validate(observation)
        identifier = new_id("observation")
        with self.store.unit_of_work() as work:
            work.connection.execute(
                "INSERT INTO model_observations VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    model_id,
                    provider_id,
                    mode,
                    item.outcome,
                    item.latency_seconds,
                    item.ttft_seconds,
                    item.output_tokens,
                    now_iso(),
                ),
            )
        return identifier

    def summarize(self, model_id, provider_id, *, mode):
        if mode not in {"mock", "live"}:
            raise ValueError("Unknown execution provenance")
        with self.store.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM model_observations WHERE model_id=? AND provider_id=? AND mode=? ORDER BY created_at,id",
                (model_id, provider_id, mode),
            ).fetchall()
        if not rows:
            return None
        elapsed = sorted(
            row["latency_seconds"] for row in rows if row["latency_seconds"] is not None
        )
        first = [row["ttft_seconds"] for row in rows if row["ttft_seconds"] is not None]
        speeds = [
            row["output_tokens"] / (row["latency_seconds"] - row["ttft_seconds"])
            for row in rows
            if row["outcome"] == "success"
            and row["output_tokens"] is not None
            and row["latency_seconds"] is not None
            and row["ttft_seconds"] is not None
            and row["latency_seconds"] > row["ttft_seconds"]
        ]

        def percentile(q):
            return elapsed[max(0, math.ceil(q * len(elapsed)) - 1)] if elapsed else None

        values = {
            "reliability": sum(row["outcome"] == "success" for row in rows) / len(rows),
            "timeout_rate": sum(row["outcome"] == "timeout" for row in rows) / len(rows),
            "ttft_seconds": sum(first) / len(first) if first else None,
            "tokens_per_second": sum(speeds) / len(speeds) if speeds else None,
            "latency_p50_seconds": percentile(0.5),
            "latency_p95_seconds": percentile(0.95),
        }
        counts = {
            "reliability": len(rows),
            "timeout_rate": len(rows),
            "ttft_seconds": len(first),
            "tokens_per_second": len(speeds),
            "latency_p50_seconds": len(elapsed),
            "latency_p95_seconds": len(elapsed),
        }
        return {
            "source": "gateway_observation",
            "mode": mode,
            "n_samples": len(rows),
            "observed_at": rows[-1]["created_at"],
            **values,
            "measurements": {
                name: {
                    "value": value,
                    "source": "gateway_observation",
                    "mode": mode,
                    "n_samples": counts[name],
                    "observed_at": rows[-1]["created_at"],
                    "confidence": None,
                    "benchmark_version": None,
                }
                for name, value in values.items()
                if value is not None
            },
        }
