"""Publish compact, content-free snapshots of already-redacted live run records.

No credentials, configuration, databases, logs, prompts, model replies or artifact
contents are read. Input is the live driver's sanitized run.json files only.
Existing outputs are never overwritten; every source and publication is hashed.
"""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _identifier(value):
    if value is None:
        return None
    if (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", value)
        and not re.search(r"(?i)(?:sk-|bearer|api[_-]?key|credential|secret|://)", value)
    ):
        return value
    return "[OMITTED]"


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _pick(record, keys, convert=_identifier):
    return {key: convert(record.get(key)) for key in keys}


def _boolean(value):
    return value if type(value) is bool else None


def _relative_artifact(path):
    if not isinstance(path, str):
        return None
    normalized = path.replace("\\", "/")
    marker = "/work/"
    relative = "work/" + normalized.rsplit(marker, 1)[1] if marker in normalized else normalized
    if relative.startswith("work/") and all(
        part not in {"", ".", ".."} for part in relative.split("/")
    ):
        return _identifier(relative)
    return "[OUTSIDE_OUTPUT_SCOPE]"


def compact_record(record):
    """Allowlist measured values only; omitted text cannot leak through nesting."""
    execution = record.get("execution") or {}
    scenario = record.get("scenario") or {}
    final = ((record.get("final_quest") or {}).get("quest") or {}).get("status")
    acceptance = record.get("acceptance") or record.get("acceptance_checks") or {}
    restart = record.get("restart") or {}
    compaction = record.get("compaction") or {}
    runtime_settings = record.get("runtime_settings") or {}
    effort = runtime_settings.get("reasoning_effort")
    attempts = execution.get("model_attempts") or []
    artifacts = execution.get("artifact_hashes") or []
    snapshot = record.get("code_snapshot")
    code_digest = snapshot.get("sha256") if isinstance(snapshot, dict) else None
    if not isinstance(code_digest, str) or re.fullmatch(r"[0-9a-f]{64}", code_digest) is None:
        code_digest = None
    return {
        "schema_version": 1,
        "run_id": _identifier(record.get("run_id")),
        "level": _identifier(record.get("level")),
        "scenario_id": _identifier(scenario.get("scenario_id")),
        "token_budget_policy": {
            **_pick(
                scenario.get("token_budget_policy") or {},
                ("mode", "protocol_change", "historical_scenario_id", "usage_accounting"),
            ),
            "enforced": _boolean((scenario.get("token_budget_policy") or {}).get("enforced")),
        },
        "execution_mode": _identifier(record.get("execution_mode")),
        "code_snapshot_sha256": code_digest,
        "runtime_settings": {
            "token_budgets_enforced": _boolean(runtime_settings.get("token_budgets_enforced")),
            "reasoning_effort": effort
            if isinstance(effort, str)
            and effort in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
            else None,
            **_pick(
                runtime_settings,
                (
                    "review_max_rounds",
                    "worker_lease_seconds",
                    "provider_timeout_seconds",
                    "provider_max_retries",
                    "model_token_limit",
                    "model_concurrency_limit",
                ),
                _number,
            ),
        },
        "actual_final_status": _identifier(execution.get("final_status", final)),
        "driver_final_status": _identifier(final),
        "duration_seconds": _number(record.get("duration_seconds")),
        "model_settlement_wait": _pick(
            record.get("model_settlement_wait") or {},
            ("waited_seconds", "unsettled_attempts", "timeout_seconds"),
            _number,
        ),
        "failures": [_identifier(value) for value in record.get("failures", [])[:100]],
        "inspection_error": _identifier(record.get("inspection_error")),
        "router_status": _number((record.get("router_turn") or {}).get("status")),
        "acceptance": {
            "status": _identifier(acceptance.get("status")),
            "passed": _boolean(acceptance.get("passed")),
            "failed_checks": [
                _identifier(value) for value in acceptance.get("failed_checks", [])[:100]
            ],
            "unknown_checks": [
                _identifier(value) for value in acceptance.get("unknown_checks", [])[:100]
            ],
            "checks": {
                _identifier(key): _identifier(value.get("status"))
                for key, value in list(acceptance.get("checks", {}).items())[:100]
                if isinstance(value, dict)
            },
        },
        "model_calls": _number(execution.get("model_calls")),
        "model_attempts": [
            {
                **_pick(
                    attempt,
                    (
                        "attempt_id",
                        "call_id",
                        "purpose",
                        "provider_id",
                        "model_id",
                        "state",
                        "currency",
                    ),
                ),
                **_pick(
                    attempt,
                    (
                        "input_reserved",
                        "output_reserved",
                        "input_charged",
                        "output_charged",
                        "cost_charged",
                    ),
                    _number,
                ),
            }
            for attempt in attempts[:1000]
        ],
        "model_attempts_omitted": max(0, len(attempts) - 1000),
        "token_usage": _pick(
            execution.get("token_usage") or {}, ("input", "output", "unknown_attempts"), _number
        ),
        "cost": _number(execution.get("cost")),
        "tool_calls": _number(execution.get("tool_calls")),
        "retry_count": _number(execution.get("retry_count")),
        "retry_accounting": _identifier(execution.get("retry_accounting")),
        "retry_breakdown": _pick(
            execution.get("retry_breakdown") or {},
            (
                "worker_self_repairs",
                "provider_http_retries",
                "background_notice_retries",
                "review_revision_requests",
            ),
            _number,
        ),
        "tools": [
            _pick(item, ("call_id", "tool_name", "state", "receipt_id", "execution_mode"))
            for item in (execution.get("tools") or [])[:1000]
        ],
        "workers": [
            {
                **_pick(
                    item,
                    (
                        "id",
                        "worker_id",
                        "node_key",
                        "role_id",
                        "provider_id",
                        "model_id",
                        "execution_mode",
                        "state",
                    ),
                ),
                **_pick(
                    item, ("steps", "repairs", "token_spent", "token_limit", "tool_spent"), _number
                ),
            }
            for item in (execution.get("workers") or [])[:100]
        ],
        "review_results": [
            _pick(
                item,
                (
                    "run_id",
                    "call_id",
                    "verdict",
                    "execution_mode",
                    "error_code",
                    "reason_code",
                    "review_context",
                ),
            )
            for item in (execution.get("review_results") or [])[:100]
        ],
        "artifact_hashes": [
            {
                "path": _relative_artifact(item.get("path")),
                **_pick(item, ("receipt_id", "execution_mode", "sha256")),
                "receipt_hash_matches": _boolean(item.get("receipt_hash_matches")),
            }
            for item in artifacts[:100]
        ],
        "restart": {
            **_pick(restart, ("after", "initial_router_identity", "restored_router_identity")),
            **_pick(restart, ("router_identity_retained", "same_workspace"), _boolean),
        },
        "compaction": {
            **_pick(compaction, ("archive_created", "source_readback_available"), _boolean),
            **_pick(compaction, ("source_count", "rebuilt_messages"), _number),
        },
        "context_archives": _number(execution.get("context_archives")),
        "semantic_test_runs": _number(execution.get("semantic_test_runs")),
        "conversation_count": _number(execution.get("conversation_count")),
        "publication_scope": "Allowlisted summaries of already-redacted live-driver records. Prompt/reply/log/artifact content omitted; unknown usage/cost preserved. A completed Quest alone does not establish scenario acceptance.",
    }


def _write(path, value):
    data = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    with path.open("xb") as stream:
        stream.write(data)
    return {"path": path.name, "sha256": _sha(data), "bytes": len(data)}


def publish_runs(source_root, destination):
    source, target = Path(source_root).resolve(), Path(destination).resolve()
    if not source.is_dir():
        raise ValueError("Run-record source directory does not exist")
    if target.exists():
        raise FileExistsError(
            "Publication destination already exists; choose a new snapshot directory"
        )
    records = []
    for path in sorted(source.glob("*/run.json")):
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError("Run snapshot escapes the source directory")
        with path.open("rb") as stream:
            data = stream.read(16_000_001)
        if len(data) > 16_000_000:
            raise ValueError("Run snapshot exceeds publication limit")
        record = json.loads(data)
        if not isinstance(record, dict):
            raise TypeError("Run snapshot must be a JSON object")
        name = path.parent.name
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", name):
            raise ValueError("Unsafe source snapshot name")
        records.append(
            (
                name,
                compact_record(record),
                {
                    "path": path.relative_to(source).as_posix(),
                    "sha256": _sha(data),
                    "bytes": len(data),
                },
            )
        )
    if not records:
        raise ValueError("No finished run.json snapshots found")
    target.mkdir(parents=True, exist_ok=False)
    entries = []
    for name, compact, original in records:
        published = _write(target / (name + ".json"), compact)
        entries.append(
            {
                "run_id": compact["run_id"],
                "source_snapshot": original,
                "published_snapshot": published,
                "actual_final_status": compact["actual_final_status"],
                "acceptance_status": compact["acceptance"]["status"],
                "failures": compact["failures"],
            }
        )
    index = {
        "schema_version": 1,
        "run_count": len(entries),
        "runs": entries,
        "limitations": "Historical snapshots preserve their then-current evidence; later code changes do not retroactively promote failed or unaccepted runs. No Provider requests were made by publication.",
    }
    _write(target / "index.json", index)
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    index = publish_runs(arguments.source, arguments.output)
    print(json.dumps({"run_count": index["run_count"], "output": str(arguments.output.resolve())}))


if __name__ == "__main__":
    main()
