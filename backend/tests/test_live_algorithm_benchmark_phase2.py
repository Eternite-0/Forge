"""Offline evidence for the fixed live study; no live Provider is contacted."""

import asyncio
import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "phase2"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "live_algorithm_benchmark", SCRIPTS / "live_algorithm_benchmark.py"
)
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def correct(case):
    return {
        "completed": True,
        "answers": study.oracle(case),
        "retained_constraints": case["constraints"],
        "citations": [
            {
                "source_id": item["source_id"],
                "sha256": item["sha256"],
                "line_start": 1,
                "line_end": item["lines"],
            }
            for item in case["sources"]
        ],
    }


def options(**changes):
    return SimpleNamespace(
        row_token_limit=16000,
        base_url="http://127.0.0.1:8788/v1",
        credential_env="FORGE_PROVIDER_KEY_TEST",
        **changes,
    )


def transport_for(value, sent, *, usage=True):
    def handler(request):
        wire = json.loads(request.content)
        sent.append(wire)
        schema = wire["response_format"]["json_schema"]["schema"]
        if "verdict" in schema["properties"]:
            candidate = {"verdict": "PASS", "goals": []}
        elif "summary" in schema["properties"]:
            candidate = {
                "summary": next(
                    item["content"] for item in wire["messages"] if item["role"] == "user"
                )
            }
        else:
            candidate = value
        payload = {
            "choices": [{"message": {"content": json.dumps(candidate)}, "finish_reason": "stop"}]
        }
        if usage:
            payload["usage"] = {"prompt_tokens": 40, "completion_tokens": 60}
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def run(tmp_path, family, variant, *, answer=None, usage=True, case=None):
    case = case or study.cases()[0]
    sent = []
    row = asyncio.run(
        study.run_row(
            tmp_path,
            case,
            family,
            variant,
            options(),
            transport=transport_for(correct(case) if answer is None else answer, sent, usage=usage),
            credential_resolver=lambda _: "test-placeholder",
        )
    )
    return row, sent


def test_fixed_oracles_and_total_design():
    expected = {
        "code-1": [0, 7],
        "code-2": [-1, 2, 8, 4, -1],
        "schedule-1": [2, 2, -1],
        "schedule-2": [9, 3, 1, -1, 3],
        "governance-1": [1, 0],
        "governance-2": [2, 1, 2],
    }
    assert {case["case_id"]: study.oracle(case) for case in study.cases()} == expected
    assert len(study.cases()) * sum(map(len, study.VARIANTS.values())) == 90
    assert 90 * 16000 == 1440000
    assert all(study.score(case, correct(case))["task_success"] for case in study.cases())


@pytest.mark.parametrize("mutation", ["wrong", "hash", "range", "obligation", "boolean", "extra"])
def test_fixed_grader_rejects_adversarial_claims(mutation):
    case = study.cases()[0]
    value = copy.deepcopy(correct(case))
    if mutation == "wrong":
        value["answers"][0] = 12345
    elif mutation == "hash":
        value["citations"][0]["sha256"] = "0" * 64
    elif mutation == "range":
        value["citations"][0]["line_end"] = 999
    elif mutation == "obligation":
        value["retained_constraints"] = []
    elif mutation == "boolean":
        value["answers"][0] = False
    else:
        value["forged_PASS"] = True
    assert not study.score(case, value)["task_success"]


@pytest.mark.parametrize("case", study.cases(), ids=lambda case: case["case_id"])
@pytest.mark.parametrize(
    "family,variant",
    [(family, variant) for family, variants in study.VARIANTS.items() for variant in variants],
)
def test_all_study_variants_use_original_ledger_and_native_reads(tmp_path, family, variant, case):
    row, sent = run(tmp_path, family, variant, case=case)
    assert row["status"] == "completed", row
    assert row["execution_mode"] == "mock"
    assert row["source_receipts_valid"] is True
    assert len(row["source_receipts"]) == 2
    assert row["score"]["task_success"]
    assert row["usage"]["model_calls"] == len(sent) == len(row["stages"])
    assert row["usage"]["unknown_attempts"] == 0
    assert row["usage"]["input_tokens"] == 40 * len(sent)
    assert row["usage"]["output_tokens"] == 60 * len(sent)
    assert all(attempt["state"] == "settled" for attempt in row["usage"]["attempts"])
    assert len({attempt["call_id"] for attempt in row["usage"]["attempts"]}) == len(sent)
    assert all(wire["max_tokens"] == 512 for wire in sent)
    store = study.Store(
        tmp_path / "data" / "forge.sqlite3", workspace=study.Workspace.from_path(tmp_path)
    )
    with store.connection() as db:
        assert db.execute("SELECT state FROM worker_runs").fetchone()[0] == "paused"
        assert (
            db.execute("SELECT COUNT(*) FROM worker_tool_calls WHERE state='succeeded'").fetchone()[
                0
            ]
            == 2
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM events WHERE event_type IN ('worker.result','task.verified')"
            ).fetchone()[0]
            == 0
        )
    if variant.startswith("task_centric"):
        assert row["actual_router_archives"] == 1
        assert row["actual_source_readbacks"] == int(variant.endswith("source_recall"))


@pytest.mark.parametrize(
    "variant,accepted",
    [
        ("model_claim_only", True),
        ("tool_receipt_verification", True),
        ("independent_reviewer", True),
        ("evidence_gated_completion", False),
    ],
)
def test_governance_retains_false_claims_and_false_reviews(tmp_path, variant, accepted):
    value = correct(study.cases()[0])
    value["answers"] = [9000]
    row, _ = run(tmp_path, "governance", variant, answer=value)
    assert row["status"] == "completed", row
    assert row["score"]["task_success"] is False
    assert row["policy_acceptance"] is accepted
    assert row["false_acceptance"] is accepted


def test_missing_usage_stays_unknown_and_blocks_second_stage(tmp_path):
    row, sent = run(tmp_path, "team", "static_team", usage=False)
    assert row["status"] == "model_or_budget_failed", row
    assert len(sent) == 1
    assert row["usage"]["unknown_attempts"] == 1
    assert row["usage"]["unknown_reserved_tokens"] > 0
    assert row["usage"]["input_tokens"] is None
    assert row["usage"]["output_tokens"] is None
    assert study.summarize([row])[0]["input_tokens"] is None


def test_native_budget_rejects_before_http(tmp_path):
    case = study.cases()[0]
    sent = []
    store, gateway, quest, receipts = asyncio.run(
        study._fixture(
            tmp_path,
            case,
            options(),
            transport=transport_for(correct(case), sent),
            credential_resolver=lambda _: "test-placeholder",
        )
    )
    gateway._budget_ledger.token_limit = 0
    row = {"stages": []}
    value = asyncio.run(
        study._call(
            store, gateway, quest, "candidate", study._task_body(case), study.ANSWER_SCHEMA, row
        )
    )
    assert value is None and sent == []
    assert row["stages"][0]["provider_error"] == "budget_exhausted"
    assert study.receipt_integrity(store, case, receipts)


def test_modified_source_breaks_actual_receipt_evidence(tmp_path):
    row, _ = run(tmp_path, "team", "single_model")
    assert row["source_receipts_valid"] is True
    (tmp_path / "inputs" / "function.txt").write_text("changed", encoding="utf-8")
    store = study.Store(
        tmp_path / "data" / "forge.sqlite3", workspace=study.Workspace.from_path(tmp_path)
    )
    assert not study.receipt_integrity(store, study.cases()[0], row["source_receipts"])


def test_duplicate_receipts_and_prior_output_are_rejected(tmp_path):
    row, _ = run(tmp_path, "team", "single_model")
    store = study.Store(
        tmp_path / "data" / "forge.sqlite3", workspace=study.Workspace.from_path(tmp_path)
    )
    assert not study.receipt_integrity(store, study.cases()[0], row["source_receipts"][:1] * 2)
    with pytest.raises(FileExistsError):
        study._write_new(tmp_path / "row.json", {"replace": True})


def test_dynamic_recomposition_is_one_bounded_real_model_stage(tmp_path):
    case = study.cases()[0]
    sent = []
    wrong = correct(case)
    wrong["answers"] = [999]
    base = transport_for(wrong, sent)

    async def handler(request):
        if sent:
            value = correct(case)
            sent.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(value)}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 60},
                },
            )
        return await base.handle_async_request(request)

    row = asyncio.run(
        study.run_row(
            tmp_path,
            case,
            "team",
            "dynamic_recomposition",
            options(),
            transport=httpx.MockTransport(handler),
            credential_resolver=lambda _: "test-placeholder",
        )
    )
    assert row["status"] == "completed", row
    assert row["recomposition_count"] == 1 and len(sent) == 2
    assert row["score"]["task_success"] is True
    assert len(row["remaining_assignments"]) == 1
    body = json.loads(
        next(item["content"] for item in sent[1]["messages"] if item["role"] == "user")
    )
    assert body["fixed_validation_failures"] == ["wrong_answer"]
    assert "expected_answers" not in body
