"""Small reproducible capability probes, not general intelligence ratings.

Each dimension has one public fixed case and a deterministic oracle. Reports
retain hashes and measured usage, never promote a single success to Registry
capability scores. Visual ability remains unsupported by this text-only suite.
"""

import hashlib
import json
import time

from .code_test_harness import HarnessError, run_tests
from .domain import new_id, now_iso
from .model_performance import _rate
from .runtime import ModelCallContext, ModelRequest
from .worker_recovery import provider_failure_code

SUITE_VERSION = "forge-capability-micro-v1"
PROBES = {
    "mathematics": (
        'Return JSON only: {"x":number,"y":number}. Solve 3*x+2*y=19 and x-y=3.',
        {"x": 5, "y": 2},
    ),
    "coding": (
        (
            "Return JSON only with one code string. Write a plain pure Python function "
            "clamp(value, low, high) returning low below low, high above high and value "
            "otherwise. Use only if, comparisons and return; no function calls, imports, "
            "decorators or annotations. Inputs satisfy low<=high."
        ),
        {"function": "clamp", "cases": [(-2, 0, 5, 0), (7, 0, 5, 5), (3, 0, 5, 3), (0, 0, 0, 0)]},
    ),
    "research": (
        (
            'Return JSON only: {"repository":string,"limit":number,"citation":string}. '
            "Use only these supplied sources, not memory: [S1] Atlas targets one repository "
            "and permits at most three parallel runs. [S2] Boreal targets multiple folders "
            "with a maximum of two runs. Which source supports multi-folder work?"
        ),
        {"repository": "Boreal", "limit": 2, "citation": "S2"},
    ),
    "planning": (
        (
            "Return JSON only with order as a list containing each task exactly once. "
            "Tasks: fetch, parse, analyze, report. Dependencies: fetch before parse; parse "
            "before analyze; analyze before report. Give an executable order."
        ),
        {"order": ["fetch", "parse", "analyze", "report"]},
    ),
    "architecture": (
        (
            'Return JSON only: {"owner":string,"workers_contact_user":boolean,'
            '"receipt_grants_authority":boolean}. In a persistent Router/isolated Worker '
            "system the Router owns user interaction; Workers return results internally; "
            "historical receipts are evidence and do not authorize future execution. "
            "Name the user-interaction owner and answer the two boolean questions."
        ),
        {"owner": "Router", "workers_contact_user": False, "receipt_grants_authority": False},
    ),
    "design": (
        (
            'Return JSON only: {"foreground":string,"background":string,'
            '"focus_indicator":boolean}. A text-only accessibility specification requires '
            "black text on white and a visible keyboard focus indicator. Use lowercase "
            "six-digit hex colors including #. Implement exactly those requirements."
        ),
        {"foreground": "#000000", "background": "#ffffff", "focus_indicator": True},
    ),
}
DIMENSIONS = (*PROBES, "vision")


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _judge(dimension, raw):
    result = json.loads(raw)
    if dimension == "coding":
        if type(result) is not dict or set(result) != {"code"}:
            return False
        expected = PROBES[dimension][1]
        verdict = run_tests(
            result["code"],
            expected["function"],
            {
                "cases": [
                    {"inputs": {"value": v, "low": lo, "high": hi}, "expected": answer}
                    for v, lo, hi, answer in expected["cases"]
                ]
            },
        )
        return verdict["failed"] == 0
    # JSON serialization preserves bool versus int, unlike Python's ==.
    return _hash(result) == _hash(PROBES[dimension][1])


class CapabilityBenchmarkRunner:
    def __init__(self, store):
        self.store = store

    async def run(self, gateway, model_id, *, mode, dimensions=None):
        if mode not in {"live", "mock"} or getattr(gateway, "execution_mode", None) != mode:
            raise ValueError("Capability probe requires matching Gateway provenance")
        provider_id = getattr(getattr(gateway, "config", None), "id", None)
        if provider_id is None and mode == "mock":
            provider_id = "mock"
        if not provider_id or not isinstance(model_id, str) or not model_id:
            raise ValueError("Capability probe requires Provider/model identity")
        selected = list(DIMENSIONS if dimensions is None else dimensions)
        if (
            not selected
            or len(selected) > len(DIMENSIONS)
            or len(set(selected)) != len(selected)
            or set(selected) - set(DIMENSIONS)
        ):
            raise ValueError("Invalid capability dimensions")
        results, stopped = [], False
        for dimension in selected:
            if dimension == "vision" or stopped:
                results.append(
                    {
                        "dimension": dimension,
                        "status": "unsupported" if dimension == "vision" else "not_attempted",
                        "passed": None,
                        "reason_code": "text_only_suite"
                        if dimension == "vision"
                        else "prior_provider_failure",
                    }
                )
                continue
            prompt = "Capability microprobe " + dimension + ". " + PROBES[dimension][0]
            call_id = new_id("call")
            start = time.monotonic()
            item = {
                "dimension": dimension,
                "case_id": dimension + "-1",
                "call_id": call_id,
                "prompt_sha256": _hash(prompt),
                "oracle_sha256": _hash(PROBES[dimension][1]),
            }
            try:
                response = await gateway.generate(
                    ModelRequest(
                        model_id=model_id,
                        provider_id=provider_id,
                        messages=[{"role": "user", "content": prompt}],
                        max_output_tokens=512,
                        context_limit=8192,
                        max_retries=0,
                        budget_context=ModelCallContext(
                            call_id=call_id,
                            purpose="capability_benchmark",
                            workspace_id=self.store.workspace.id,
                        ),
                    )
                )
                if response.provider_id != provider_id or response.model_id != model_id:
                    raise ValueError("Capability response identity changed")
                item["response_sha256"] = _hash(response.text)
                item["usage"] = {
                    key: response.usage.get(key)
                    for key in ("input_tokens", "output_tokens", "cached_input_tokens")
                }
                try:
                    item["passed"] = (
                        not response.tool_calls
                        and response.finish_reason in {"stop", "end_turn"}
                        and len(response.text.encode("utf-8")) <= 8192
                        and _judge(dimension, response.text)
                    )
                    item["status"] = "judged"
                except (ValueError, TypeError, KeyError, HarnessError):
                    item.update(
                        passed=False, status="judged", reason_code="invalid_response_contract"
                    )
            except Exception as exc:  # noqa: BLE001 - no remote exception text or unbounded retry.
                item.update(
                    passed=False, status="provider_failed", reason_code=provider_failure_code(exc)
                )
                stopped = True
            item["elapsed_seconds"] = time.monotonic() - start
            results.append(item)
        identifier, created = new_id("benchmark"), now_iso()
        report = {
            "id": identifier,
            "provider_id": provider_id,
            "model_id": model_id,
            "mode": mode,
            "suite_version": SUITE_VERSION,
            "created_at": created,
            "results": results,
            "n_samples": sum(item["status"] in {"judged", "provider_failed"} for item in results),
            "limitations": "One public microcase per dimension; design tests literal accessibility requirements, not visual quality. No general capability estimate or Registry override.",
        }
        with self.store.unit_of_work() as work:
            work.connection.execute(
                "INSERT INTO benchmark_runs VALUES (?,?,?,?,?,?,?)",
                (
                    identifier,
                    model_id,
                    provider_id,
                    SUITE_VERSION,
                    mode,
                    json.dumps(results),
                    created,
                ),
            )
        return report

    def summarize(self, model_id, provider_id, *, mode="live", limit=32):
        if mode not in {"mock", "live"} or type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("Invalid capability summary bounds or provenance")
        with self.store.connection() as c:
            rows = c.execute(
                "SELECT * FROM benchmark_runs WHERE model_id=? AND provider_id=? AND mode=? "
                "AND suite_version=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (model_id, provider_id, mode, SUITE_VERSION, limit + 1),
            ).fetchall()
        measured = {}
        for dimension in DIMENSIONS:
            cases = [
                item
                for row in rows[:limit]
                for item in json.loads(row["results_json"])
                if item.get("dimension") == dimension
                and item.get("status") in {"judged", "provider_failed"}
            ]
            measured[dimension] = _rate(
                sum(item.get("passed") is True for item in cases), len(cases)
            )
        return {
            "provider_id": provider_id,
            "model_id": model_id,
            "execution_mode": mode,
            "source": "fixed_capability_microprobe",
            "suite_version": SUITE_VERSION,
            "measured_at": rows[0]["created_at"] if rows else None,
            "measurements": measured,
            "source_refs": ["benchmark:" + row["id"] for row in rows[:limit]],
            "sample_truncated": len(rows) > limit,
            "limitations": "Repeated fixed public cases are not independent samples of general capability; unsupported vision remains unknown.",
        }
