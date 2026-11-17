"""Router wire compatibility and content-free JSON diagnostics.

Transport normalization never repairs JSON or grants authority to a decision.
The caller still applies the original local decision and recall validators.
"""

import copy
import hashlib
import json

_JSON_SPACE = " \t\r\n"
_ERROR_CODES = {
    "Expecting value": "expected_value",
    "Expecting property name enclosed in double quotes": "expected_property_name",
    "Illegal trailing comma before end of object": "expected_property_name",
    "Illegal trailing comma before end of array": "expected_value",
    "Expecting ':' delimiter": "expected_colon",
    "Expecting ',' delimiter": "expected_comma",
    "Extra data": "extra_data",
    "Unterminated string starting at": "unterminated_string",
    r"Invalid \escape": "invalid_escape",
    r"Invalid \uXXXX escape": "invalid_unicode_escape",
    "Invalid control character at": "invalid_control_character",
    "Unexpected UTF-8 BOM (decode using utf-8-sig)": "unexpected_bom",
}


def strict_router_wire_schema(schema):
    """Project only the advertised schema; preserve local defaults and types."""
    projected = copy.deepcopy(schema)

    def visit(node):
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        if node.get("type") == "object" or "properties" in node:
            node["required"] = list(node.get("properties", {}))
        # Visit schema positions, not arbitrary annotation/property-name maps.
        for key in ("$defs", "definitions", "properties", "patternProperties", "dependentSchemas"):
            for value in node.get(key, {}).values():
                visit(value)
        for key in (
            "items",
            "additionalProperties",
            "contains",
            "propertyNames",
            "not",
            "if",
            "then",
            "else",
            "unevaluatedItems",
            "unevaluatedProperties",
            "contentSchema",
        ):
            visit(node.get(key))
        for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            for value in node.get(key, []):
                visit(value)

    visit(projected)
    return projected


def _single_json_fence_body(text):
    """Only a whole lowercase JSON fence with exact LF delimiters is eligible."""
    if not isinstance(text, str) or not text.startswith("```json\n") or not text.endswith("\n```"):
        return None
    body = text[8:-4]
    if body.endswith("\r") or any(
        line.lstrip(" \t").startswith("```") for line in body.split("\n")
    ):
        return None
    return body


def normalize_router_json_transport(text):
    """Remove one exact wrapper, retaining hashes of both transport forms."""
    body = _single_json_fence_body(text)
    if body is None:
        return text, None
    raw_bytes, normalized_bytes = text.encode("utf-8"), body.encode("utf-8")
    return body, {
        "normalization": "exact_single_json_fence",
        "provenance": "router_json_transport_v1",
        "validation": "pending_original_decision_and_recall_validators",
        "raw_response_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "normalized_response_sha256": hashlib.sha256(normalized_bytes).hexdigest(),
        "raw_utf8_bytes": len(raw_bytes),
        "normalized_utf8_bytes": len(normalized_bytes),
    }


class _NonFiniteJSON(ValueError):
    pass


def _reject_nonfinite(_value):
    raise _NonFiniteJSON


def _strict_json_shape(text):
    """Return fixed codes and numeric locations, never parser messages/inputs."""
    # Python's JSON decoder recursion behavior differs by version/build. Keep
    # resource diagnostics deterministic, without counting brackets in strings.
    depth, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > 1000:
                return {"syntax": "invalid", "error_code": "nesting_limit"}
        elif character in "]}":
            depth -= 1
    try:
        parsed = json.loads(text, parse_constant=_reject_nonfinite)
    except json.JSONDecodeError as exc:
        return {
            "syntax": "invalid",
            "error_code": _ERROR_CODES.get(exc.msg, "json_syntax"),
            "position_chars": exc.pos,
            "line": exc.lineno,
            "column": exc.colno,
        }
    except _NonFiniteJSON:
        return {"syntax": "invalid", "error_code": "nonfinite_number"}
    except ValueError:
        return {"syntax": "invalid", "error_code": "json_value_limit"}
    except RecursionError:
        return {"syntax": "invalid", "error_code": "nesting_limit"}
    kind = {
        dict: "object",
        list: "array",
        str: "string",
        int: "number",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }[type(parsed)]
    return {"syntax": "valid", "root_kind": kind}


def _first_token_class(text):
    stripped = text.lstrip(_JSON_SPACE)
    if not stripped:
        return "empty"
    first = stripped[0]
    if first == "\ufeff":
        return "bom"
    if first.isspace():
        return "non_json_whitespace"
    if stripped.startswith("```"):
        return "code_fence"
    if first in '{["':
        return {"{": "object", "[": "array", '"': "string"}[first]
    if first in "-0123456789":
        return "number"
    if first in "tfn":
        return "literal_prefix"
    return "letter" if first.isalpha() else "other"


def json_response_diagnostics(text):
    """Describe syntax without text, excerpts, field names or parser messages.

    Positions count characters from zero; lines/columns start at one. Fenced
    coordinates refer to the body. This metadata makes no acceptance claim.
    """
    if not isinstance(text, str):
        return {
            "utf8_bytes": None,
            "first_token_class": "not_text",
            "single_json_fence": False,
            "json": {"syntax": "not_text", "error_code": "not_text"},
        }
    try:
        byte_count = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        byte_count = None
    body = _single_json_fence_body(text)
    result = {
        "utf8_bytes": byte_count,
        "first_token_class": _first_token_class(text),
        "single_json_fence": body is not None,
        "json": _strict_json_shape(text),
    }
    if body is not None:
        result["fenced_json"] = _strict_json_shape(body)
    return result
