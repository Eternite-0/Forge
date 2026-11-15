"""Compact publisher tests; no credentials, Providers or actual live runs used."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def publisher():
    path = Path(__file__).resolve().parents[1] / "scripts/phase2/publish_evidence.py"
    spec = importlib.util.spec_from_file_location("phase2_publisher_tests", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def synthetic_record():
    return {
        "run_id": "synthetic-run",
        "level": "L2",
        "execution_mode": "live",
        "prompt": "SECRET_SENTINEL",
        "core_log_tail": "SECRET_SENTINEL",
        "router_turn": {"reply": "SECRET_SENTINEL", "status": 200},
        "failures": ["planner_did_not_produce_plan"],
        "execution": {
            "final_status": "awaiting_approval",
            "cost": None,
            "token_usage": {"input": 15, "output": 3, "unknown_attempts": 1},
            "review_results": [{"verdict": "BLOCKED", "findings": ["SECRET_SENTINEL"]}],
            "artifact_hashes": [
                {
                    "path": "C:/private/account/work/report.json",
                    "sha256": "a" * 64,
                    "receipt_hash_matches": True,
                }
            ],
        },
    }


def test_compaction_omits_all_free_content_and_private_absolute_paths(publisher):
    compact = publisher.compact_record(synthetic_record())
    text = json.dumps(compact)
    assert "SECRET_SENTINEL" not in text
    assert "private/account" not in text
    assert compact["artifact_hashes"][0]["path"] == "work/report.json"
    assert compact["cost"] is None
    assert compact["token_usage"]["unknown_attempts"] == 1
    assert compact["actual_final_status"] == "awaiting_approval"


def test_publication_keeps_measured_retries_and_fixed_review_reason_only(publisher):
    record = synthetic_record()
    record["execution"].update(
        retry_count=4,
        retry_breakdown={
            "worker_self_repairs": 2,
            "review_revision_requests": 1,
            "credential": "SECRET_SENTINEL",
        },
        review_results=[
            {
                "verdict": "BLOCKED",
                "reason_code": "review_context_limit",
                "findings": ["SECRET_SENTINEL"],
            }
        ],
    )
    compact = publisher.compact_record(record)
    assert compact["retry_count"] == 4
    assert compact["retry_breakdown"]["worker_self_repairs"] == 2
    assert compact["retry_breakdown"]["review_revision_requests"] == 1
    assert compact["review_results"][0]["reason_code"] == "review_context_limit"
    assert "SECRET_SENTINEL" not in json.dumps(compact)


def test_publication_hashes_exact_source_and_output_without_overwriting(tmp_path, publisher):
    source = tmp_path / "runs"
    run = source / "synthetic-run"
    run.mkdir(parents=True)
    raw = json.dumps(synthetic_record()).encode()
    (run / "run.json").write_bytes(raw)
    target = tmp_path / "publication"
    index = publisher.publish_runs(source, target)
    entry = index["runs"][0]
    assert entry["source_snapshot"]["sha256"] == hashlib.sha256(raw).hexdigest()
    publication = (target / entry["published_snapshot"]["path"]).read_bytes()
    assert entry["published_snapshot"]["sha256"] == hashlib.sha256(publication).hexdigest()
    assert entry["failures"] == ["planner_did_not_produce_plan"]
    with pytest.raises(FileExistsError):
        publisher.publish_runs(source, target)
    assert (run / "run.json").read_bytes() == raw


def test_empty_source_does_not_publish_a_fake_empty_evidence_set(tmp_path, publisher):
    source = tmp_path / "runs"
    source.mkdir()
    target = tmp_path / "publication"
    with pytest.raises(ValueError, match="No finished"):
        publisher.publish_runs(source, target)
    assert not target.exists()


def test_identifier_fields_cannot_smuggle_auth_tokens_or_urls(publisher):
    record = synthetic_record()
    record["failures"].append("sk-synthetic-not-a-real-key")
    record["execution"]["model_attempts"] = [
        {"provider_id": "https://host.invalid/api", "state": "settled"}
    ]
    compact = publisher.compact_record(record)
    assert compact["failures"][-1] == "[OMITTED]"
    assert compact["model_attempts"][0]["provider_id"] == "[OMITTED]"


@pytest.mark.parametrize(
    "digest,expected",
    [("a" * 64, "a" * 64), ("unsafe-text", None), ("sk-synthetic", None), (None, None)],
)
def test_code_snapshot_publishes_only_a_sha256_digest(publisher, digest, expected):
    record = synthetic_record()
    record["code_snapshot"] = {"sha256": digest, "files": [{"path": "SECRET_SENTINEL"}]}
    compact = publisher.compact_record(record)
    assert compact["code_snapshot_sha256"] == expected
    assert "SECRET_SENTINEL" not in json.dumps(compact)


def test_runtime_settings_publish_only_explicit_allowlisted_fields(publisher):
    record = synthetic_record()
    record["runtime_settings"] = {
        "reasoning_effort": "none",
        "review_max_rounds": 3,
        "worker_lease_seconds": 150,
        "credential": "SECRET_SENTINEL",
    }
    compact = publisher.compact_record(record)
    assert compact["runtime_settings"]["reasoning_effort"] == "none"
    assert compact["runtime_settings"]["review_max_rounds"] == 3
    assert "SECRET_SENTINEL" not in json.dumps(compact)
    record["runtime_settings"]["reasoning_effort"] = "SECRET_SENTINEL"
    assert publisher.compact_record(record)["runtime_settings"]["reasoning_effort"] is None
