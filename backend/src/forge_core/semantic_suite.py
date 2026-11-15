"""A fixed, bounded selection contract evaluated by FORGE's numeric AST interpreter.

This is an engineering verification suite, not a general capability benchmark.
"""

import hashlib
import json

from .numeric_harness import HarnessError, interpret

SUITE_ID = "scheduler-v1"
SUITE_VERSION = 1
# Each tuple is (eligible_a, eligible_b, cost_a, cost_b, expected selection).
# A and B are indices 0 and 1; -1 means neither is eligible. Equal costs select A.
_CASES = (
    (1, 1, 3, 7, 0),
    (1, 1, 9, 2, 1),
    (1, 1, 5, 5, 0),
    (1, 0, 12, 1, 0),
    (0, 1, 1, 12, 1),
    (0, 0, 0, 0, -1),
    (1, 1, 0, 1, 0),
    (1, 1, 1000000, 999999, 1),
    (0, 1, 1000000, 0, 1),
    (1, 0, 0, 1000000, 0),
)


def _hash(value):
    data = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def suite_contract():
    """Return only trusted fixed-case digests for report validation."""
    cases = []
    for index, (eligible_a, eligible_b, cost_a, cost_b, expected) in enumerate(_CASES):
        inputs = {
            "eligible_a": eligible_a,
            "eligible_b": eligible_b,
            "cost_a": cost_a,
            "cost_b": cost_b,
        }
        cases.append(
            {
                "case_id": f"case-{index + 1}",
                "input_sha256": _hash(inputs),
                "expected_sha256": _hash(expected),
            }
        )
    return {
        "suite_id": SUITE_ID,
        "suite_version": SUITE_VERSION,
        "suite_digest": _hash({"suite_id": SUITE_ID, "version": SUITE_VERSION, "cases": _CASES}),
        "cases": cases,
    }


def run_semantic_suite(source: str, suite_id: str = SUITE_ID) -> dict:
    if not isinstance(source, str):
        raise TypeError("Candidate source must be a string")
    if not source or len(source.encode("utf-8")) > 16000:
        raise ValueError("Candidate source exceeds suite bounds")
    if suite_id != SUITE_ID:
        raise ValueError("Unknown semantic suite")
    cases = []
    for index, (eligible_a, eligible_b, cost_a, cost_b, expected) in enumerate(_CASES):
        inputs = {
            "eligible_a": eligible_a,
            "eligible_b": eligible_b,
            "cost_a": cost_a,
            "cost_b": cost_b,
        }
        try:
            output = interpret(source, inputs)
            actual = output["result"]
            passed = type(actual) is int and actual == expected
            actual_digest = _hash(actual)
            error = None
        except (HarnessError, ValueError, TypeError, OverflowError, RecursionError):
            actual = None
            actual_digest = None
            passed = False
            error = "candidate_rejected"
        cases.append(
            {
                "case_id": f"case-{index + 1}",
                "input_sha256": _hash(inputs),
                "expected_sha256": _hash(expected),
                "actual": actual,
                "actual_sha256": actual_digest,
                "passed": passed,
                "error": error,
            }
        )
    return {
        "suite_id": SUITE_ID,
        "suite_version": SUITE_VERSION,
        "suite_digest": _hash({"suite_id": SUITE_ID, "version": SUITE_VERSION, "cases": _CASES}),
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "passed": all(case["passed"] for case in cases),
        "cases": cases,
    }


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate input field")
        result[key] = value
    return result


def main():
    import sys

    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("Input exceeds limit")
        payload = json.loads(
            raw,
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid number")),
        )
        if not isinstance(payload, dict) or set(payload) not in (
            {"source"},
            {"source", "suite_id"},
        ):
            raise ValueError("Invalid input contract")
        result = run_semantic_suite(payload["source"], payload.get("suite_id", SUITE_ID))
        sys.stdout.write(
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        )
    except (ValueError, TypeError, UnicodeError):
        sys.stderr.write("semantic suite rejected input")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
