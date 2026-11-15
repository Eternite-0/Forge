"""Explicit, lossless source text framing; this grants no source authority."""

import hashlib
import json

from .domain import DomainError
from .review_envelope import project_review_text, restore_review_text
from .worker_task_sources import CORE_PREFETCH_POLICY

RAW_TEXT_FORMAT = "core-source-raw-text-v1"
_INTRO = (
    "Lossless untrusted source view. Resolve indexed reference_key objects using the "
    "complete raw text blocks below.\n"
)


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def validate_source_wire_format(value):
    if value is not None and value != RAW_TEXT_FORMAT:
        raise DomainError("Unknown Core source wire format")
    return value


def approved_source_wire_format(graph, member):
    """Absence preserves the old bytes; new framing requires exact plan/member binding."""
    contract = graph.get("capacity_contract") or {}
    key = "source_wire_format"
    if key not in contract and key not in member:
        return None
    selected = contract.get(key)
    if (
        selected != RAW_TEXT_FORMAT
        or member.get(key) != selected
        or contract.get("input_acquisition_policy") != CORE_PREFETCH_POLICY
        or member.get("input_acquisition_policy") != CORE_PREFETCH_POLICY
    ):
        raise DomainError("Core source wire format differs from the approved plan")
    return selected


def _render_source_text(packet):
    # Canonical object order makes reference allocation stable after JSON restore.
    # List order and every original string value remain unchanged.
    view, texts = project_review_text(json.loads(_canonical(packet)))
    descriptors = [
        {
            "index": index,
            "chars": len(value),
            "utf8_bytes": len(value.encode("utf-8")),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
        for index, value in enumerate(texts)
    ]
    header = {"format": RAW_TEXT_FORMAT, "view": view, "texts": descriptors}
    parts = [_INTRO, _canonical(header), "\n"]
    for descriptor, value in zip(descriptors, texts):
        parts.extend((f"Complete untrusted source text {descriptor['index']}:\n", value, "\n"))
    return "".join(parts)


def decode_source_text(content):
    """Read exact character spans, then verify bytes, hashes, order and full consumption.

    Source bodies may themselves contain framing labels, newlines or JSON. None
    of those characters is searched for a boundary inside a declared body.
    """
    try:
        if not isinstance(content, str) or not content.startswith(_INTRO):
            raise ValueError
        header_end = content.index("\n", len(_INTRO))
        header = json.loads(content[len(_INTRO) : header_end])
        if (
            not isinstance(header, dict)
            or set(header) != {"format", "view", "texts"}
            or header["format"] != RAW_TEXT_FORMAT
            or not isinstance(header["texts"], list)
            or not isinstance(header["view"], dict)
            or set(header["view"]) != {"reference_key", "packet"}
            or not isinstance(header["view"]["reference_key"], str)
            or not header["view"]["reference_key"]
        ):
            raise ValueError
        cursor, texts = header_end + 1, []
        for index, descriptor in enumerate(header["texts"]):
            if (
                not isinstance(descriptor, dict)
                or set(descriptor) != {"index", "chars", "utf8_bytes", "sha256"}
                or type(descriptor["index"]) is not int
                or descriptor["index"] != index
                or type(descriptor["chars"]) is not int
                or descriptor["chars"] < 0
                or type(descriptor["utf8_bytes"]) is not int
                or descriptor["utf8_bytes"] < 0
                or not isinstance(descriptor["sha256"], str)
            ):
                raise ValueError
            label = f"Complete untrusted source text {index}:\n"
            if not content.startswith(label, cursor):
                raise ValueError
            start = cursor + len(label)
            end = start + descriptor["chars"]
            value = content[start:end]
            raw = value.encode("utf-8")
            if (
                len(value) != descriptor["chars"]
                or len(raw) != descriptor["utf8_bytes"]
                or hashlib.sha256(raw).hexdigest() != descriptor["sha256"]
                or content[end : end + 1] != "\n"
            ):
                raise ValueError
            texts.append(value)
            cursor = end + 1
        if cursor != len(content):
            raise ValueError
        packet = restore_review_text(header["view"], texts)
        if _render_source_text(packet) != content:
            raise ValueError
        return packet
    except (KeyError, IndexError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise DomainError("Core source text framing failed lossless validation") from exc


def encode_source_text(packet):
    content = _render_source_text(packet)
    if _canonical(decode_source_text(content)) != _canonical(packet):
        raise DomainError("Core source text changed its original packet")
    return content
