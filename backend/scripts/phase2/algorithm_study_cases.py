"""Fixed public tasks and a separate deterministic answer oracle for live studies.

The oracle is never passed to a candidate or reviewer. These small cases measure
bounded task answers, citations and obligation retention, not general intelligence.
"""

import hashlib
import json

SUITE = "forge-live-algorithm-microstudy-v1"


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _source(source_id, lines):
    text = "\n".join(lines) + "\n"
    return {
        "source_id": source_id,
        "content": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "lines": len(lines),
    }


def cases():
    """Create six deterministic tasks; expected outputs are kept in oracle()."""
    rows = []
    for difficulty in (1, 2):
        rows.append(
            {
                "case_id": f"code-{difficulty}",
                "domain": "code_contract",
                "difficulty": difficulty,
                "prompt": "Evaluate select for every supplied input pair in order; return its integer outputs. Follow the exact branch order, including equality and negative values.",
                "sources": [
                    _source(
                        "function",
                        [
                            "def select(a, b):",
                            "    if a < 0:",
                            "        return -1",
                            "    if a <= b:",
                            "        return a + b",
                            "    return a - b",
                        ],
                    ),
                    _source(
                        "inputs",
                        [
                            "Input pairs, in order:",
                            canonical(
                                [[0, 0], [2, 5]]
                                if difficulty == 1
                                else [[-3, -4], [0, -2], [4, 4], [7, 3], [-1, 8]]
                            ),
                        ],
                    ),
                ],
                "constraints": ["integer_arithmetic", "preserve_input_order"],
            }
        )
        rows.append(
            {
                "case_id": f"schedule-{difficulty}",
                "domain": "integer_scheduling",
                "difficulty": difficulty,
                "prompt": "For each job choose the eligible machine with the lowest cost. Eligibility means capacity >= job size. Ties select the smaller machine ID. Return machine IDs in job order, or -1 if none qualifies.",
                "sources": [
                    _source(
                        "machines",
                        [
                            "Rows are [machine_id, capacity, cost]:",
                            canonical(
                                [[1, 4, 8], [2, 8, 5], [3, 8, 5]]
                                if difficulty == 1
                                else [[9, 0, 0], [7, 5, 2], [3, 5, 2], [1, 12, 9]]
                            ),
                        ],
                    ),
                    _source(
                        "jobs",
                        [
                            "Job sizes, in order:",
                            canonical([1, 7, 9] if difficulty == 1 else [0, 5, 6, 13, 1]),
                        ],
                    ),
                ],
                "constraints": ["lowest_cost_then_id", "explicit_no_candidate"],
            }
        )
        rows.append(
            {
                "case_id": f"governance-{difficulty}",
                "domain": "approval_architecture",
                "difficulty": difficulty,
                "prompt": "Process each event stream from an empty set of approved IDs. approve adds its ID; revoke removes it. A write is admitted only if its ID is currently approved. Return the admitted-write count for each stream in order. A failed write never grants approval.",
                "sources": [
                    _source(
                        "semantics",
                        [
                            "approve and revoke affect only the same exact ID.",
                            "Repeated approvals do not create extra authority.",
                            "Writing does not consume or create approval.",
                        ],
                    ),
                    _source(
                        "streams",
                        [
                            "Each stream is an ordered list of [event_type, id]:",
                            canonical(
                                [
                                    [["write", "a"], ["approve", "a"], ["write", "a"]],
                                    [["approve", "b"], ["revoke", "b"], ["write", "b"]],
                                ]
                                if difficulty == 1
                                else [
                                    [
                                        ["approve", "a"],
                                        ["approve", "b"],
                                        ["write", "a"],
                                        ["revoke", "a"],
                                        ["write", "a"],
                                        ["write", "b"],
                                    ],
                                    [
                                        ["approve", "x"],
                                        ["approve", "x"],
                                        ["write", "y"],
                                        ["write", "x"],
                                        ["revoke", "x"],
                                        ["write", "x"],
                                    ],
                                    [
                                        ["write", "q"],
                                        ["revoke", "q"],
                                        ["approve", "q"],
                                        ["write", "q"],
                                        ["write", "q"],
                                    ],
                                ]
                            ),
                        ],
                    ),
                ],
                "constraints": ["exact_identifier_binding", "preserve_event_order"],
            }
        )
    return rows


def oracle(case):
    """Independent fixed algorithms; no model verdict contributes to truth."""
    values = json.loads(case["sources"][1]["content"].splitlines()[1])
    if case["domain"] == "code_contract":
        return [-1 if a < 0 else a + b if a <= b else a - b for a, b in values]
    if case["domain"] == "integer_scheduling":
        machines = json.loads(case["sources"][0]["content"].splitlines()[1])
        result = []
        for size in values:
            eligible = [(cost, ident) for ident, capacity, cost in machines if capacity >= size]
            result.append(min(eligible)[1] if eligible else -1)
        return result
    result = []
    for stream in values:
        approved, admitted = set(), 0
        for action, ident in stream:
            if action == "approve":
                approved.add(ident)
            elif action == "revoke":
                approved.discard(ident)
            elif action == "write":
                admitted += ident in approved
            else:
                raise ValueError("Unknown fixed event")
        result.append(admitted)
    return result


ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "completed": {"type": "boolean"},
        "answers": {"type": "array", "items": {"type": "integer"}, "maxItems": 16},
        "retained_constraints": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "citations": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "sha256": {"type": "string"},
                    "line_start": {"type": "integer", "minimum": 1},
                    "line_end": {"type": "integer", "minimum": 1},
                },
                "required": ["source_id", "sha256", "line_start", "line_end"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["completed", "answers", "retained_constraints", "citations"],
    "additionalProperties": False,
}


def score(case, candidate):
    """Grade task results, source identity/range and exact required obligations."""
    import jsonschema

    try:
        jsonschema.validate(candidate, ANSWER_SCHEMA)
        if any(type(value) is not int for value in candidate["answers"]):
            raise ValueError("Boolean is not an integer answer")
    except (jsonschema.ValidationError, TypeError, ValueError):
        return {
            "task_success": False,
            "answer_correct": False,
            "schema_valid": False,
            "evidence_complete": False,
            "obligations_retained": 0,
            "violations": ["answer_schema"],
        }
    sources = {source["source_id"]: source for source in case["sources"]}
    valid_sources = set()
    bad = False
    for citation in candidate["citations"]:
        source = sources.get(citation["source_id"])
        if (
            source is None
            or citation["sha256"] != source["sha256"]
            or not (
                type(citation["line_start"]) is int
                and type(citation["line_end"]) is int
                and 1 <= citation["line_start"] <= citation["line_end"] <= source["lines"]
            )
        ):
            bad = True
        else:
            valid_sources.add(source["source_id"])
    evidence = not bad and valid_sources == set(sources)
    retained = len(set(candidate["retained_constraints"]).intersection(case["constraints"]))
    correct = candidate["answers"] == oracle(case)
    violations = []
    for check, name in (
        (correct, "wrong_answer"),
        (evidence, "source_evidence"),
        (retained == len(case["constraints"]), "lost_obligation"),
        (candidate["completed"] is True, "not_completed"),
    ):
        if not check:
            violations.append(name)
    return {
        "task_success": not violations,
        "answer_correct": correct,
        "schema_valid": True,
        "evidence_complete": evidence,
        "obligations_retained": retained,
        "violations": violations,
    }
