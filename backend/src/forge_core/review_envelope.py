"""Explicit new review contracts; legacy packets never acquire these bounds."""

import json

from .domain import DomainError

REVIEW_ENVELOPE_VERSION = "review-envelope-v1"
_FIELDS = {
    "version",
    "max_source_bytes",
    "max_artifact_bytes",
    "max_delivery_bytes",
    "max_review_input_tokens",
    "verdict_tokens",
    "correction_input_tokens",
    "correction_output_tokens",
    "correction_calls",
}


class ReviewEnvelopeError(DomainError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def validate_review_envelope(value):
    """Return a copy of a fully explicit approved policy, without adding defaults."""
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ReviewEnvelopeError("review_envelope_fields_invalid")
    if value["version"] != REVIEW_ENVELOPE_VERSION:
        raise ReviewEnvelopeError("review_envelope_version_invalid")
    for key in _FIELDS - {"version"}:
        if type(value[key]) is not int or value[key] < (
            0 if key in {"max_source_bytes", "max_artifact_bytes"} else 1
        ):
            raise ReviewEnvelopeError("review_envelope_bounds_invalid")
    if (
        any(
            value[key] > 65536
            for key in (
                "max_source_bytes",
                "max_artifact_bytes",
                "max_delivery_bytes",
                "max_review_input_tokens",
                "correction_input_tokens",
            )
        )
        or not 1024 <= value["verdict_tokens"] <= 8192
        or value["correction_output_tokens"] > 8192
        or value["correction_calls"] not in {1, 2}
        or sum(
            value[key] for key in ("max_source_bytes", "max_artifact_bytes", "max_delivery_bytes")
        )
        >= 65536
    ):
        raise ReviewEnvelopeError("review_envelope_bounds_invalid")
    return dict(value)


def review_cycle_reserve(envelope, *, reviews_remaining=2, correction_calls_remaining=None):
    """Policy reserve for two reviews and one correction, within existing limits.

    Runtime callers pass the remaining stages after settled reviews/revision;
    this never charges, refunds or restores any previously consumed allowance.
    """
    envelope = validate_review_envelope(envelope)
    calls = (
        envelope["correction_calls"]
        if correction_calls_remaining is None
        else correction_calls_remaining
    )
    if (
        type(reviews_remaining) is not int
        or not 0 <= reviews_remaining <= 2
        or type(calls) is not int
        or not 0 <= calls <= envelope["correction_calls"]
    ):
        raise ReviewEnvelopeError("review_cycle_stage_invalid")
    return reviews_remaining * (
        envelope["max_review_input_tokens"] + envelope["verdict_tokens"]
    ) + calls * (envelope["correction_input_tokens"] + envelope["correction_output_tokens"])


def review_cycle_state(connection, run_id, envelope):
    """Read remaining stages from durable starts and the first correction request.

    Refunded unsent reviews/corrections do not consume a stage. Unknown admitted
    calls retain their charge and consume a stage; no unknown usage is zeroed.
    Initial self-repair and later review revision share the same approved
    correction calls. Starting a later repair never replenishes that allowance.
    """
    envelope = validate_review_envelope(envelope)
    starts = connection.execute(
        "SELECT payload FROM events e WHERE e.event_type='review.started' "
        "AND json_extract(e.payload,'$.run_id')=? AND NOT EXISTS("
        "SELECT 1 FROM events r WHERE r.event_type='review.released' "
        "AND json_extract(r.payload,'$.call_id')=json_extract(e.payload,'$.call_id') "
        "AND json_extract(r.payload,'$.reservation_refunded')=1 "
        "AND NOT EXISTS(SELECT 1 FROM events l WHERE l.event_type='review.late_settlement' "
        "AND json_extract(l.payload,'$.call_id')=json_extract(e.payload,'$.call_id')))",
        (run_id,),
    ).fetchall()
    revision = connection.execute(
        "SELECT e.created_at,json_extract(e.payload,'$.correction_reservation_cursor') AS cursor "
        "FROM events e LEFT JOIN events r "
        "ON r.id=json_extract(e.payload,'$.review_event_id') "
        "WHERE json_extract(e.payload,'$.run_id')=? AND ("
        "(e.event_type='worker.progress' AND json_extract(e.payload,'$.phase')='self_repair') "
        "OR (e.event_type='worker.revision_requested' AND r.event_type='review.result' "
        "AND json_extract(r.payload,'$.run_id')=? "
        "AND json_extract(r.payload,'$.review_context')='independent_same_model' "
        "AND json_extract(r.payload,'$.verdict')='NEEDS_REVISION')) ORDER BY e.seq LIMIT 1",
        (run_id, run_id),
    ).fetchone()
    corrections = 0
    if revision:
        # New requests bind the durable insertion frontier, so clock changes or
        # equal timestamps cannot count the preceding initial call as a repair.
        # Older events keep their original timestamp-based interpretation.
        frontier = revision["cursor"]
        if type(frontier) is int and frontier >= 0:
            corrections = connection.execute(
                "SELECT count(*) FROM worker_model_reservations WHERE run_id=? "
                "AND rowid>? AND state!='refunded'",
                (run_id, frontier),
            ).fetchone()[0]
        else:
            corrections = connection.execute(
                "SELECT count(*) FROM worker_model_reservations WHERE run_id=? "
                "AND created_at>=? AND state!='refunded'",
                (run_id, revision[0]),
            ).fetchone()[0]
    reviews_remaining = max(0, 2 - len(starts))
    calls_remaining = max(0, envelope["correction_calls"] - corrections)
    return {
        "reviews_remaining": reviews_remaining,
        "correction_calls_remaining": calls_remaining,
        "future_reserve_tokens": review_cycle_reserve(
            envelope,
            reviews_remaining=reviews_remaining,
            correction_calls_remaining=calls_remaining,
        ),
        "correction_started": revision is not None,
    }


def review_packet_sizes(packet):
    artifact_bytes = 0
    for item in packet["artifacts"]:
        if "content" in item:
            artifact_bytes += len(_json(item["content"]).encode("utf-8"))
        elif (
            type(item.get("canonical_content_bytes")) is int
            and item["canonical_content_bytes"] >= 0
        ):
            artifact_bytes += item["canonical_content_bytes"]
        else:
            raise ReviewEnvelopeError("review_artifact_size_unknown")
    source_bytes = len(_json(packet["receipts"]).encode("utf-8"))
    if "predecessor_deliveries" in packet:
        if not isinstance(packet["predecessor_deliveries"], list):
            raise ReviewEnvelopeError("review_predecessor_delivery_size_unknown")
        source_bytes += len(_json(packet["predecessor_deliveries"]).encode("utf-8"))
    return {
        "source_bytes": source_bytes,
        "artifact_bytes": artifact_bytes,
        "delivery_bytes": len(_json(packet["delivery"]).encode("utf-8")),
    }


def validate_review_packet(packet, envelope):
    envelope = validate_review_envelope(envelope)
    sizes = review_packet_sizes(packet)
    for component, size in sizes.items():
        if size > envelope["max_" + component]:
            raise ReviewEnvelopeError("review_" + component + "_envelope_exceeded")
    return sizes


def project_review_text(packet):
    """A reversible wire view; every original character remains available once.

    Choose a reference key absent from every original object, including hostile
    artifact data. This prevents original objects being mistaken for references.
    """
    keys = set()

    def collect(value):
        if isinstance(value, dict):
            keys.update(value)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(packet)
    marker = "$review_text"
    while marker in keys:
        marker += "_"
    texts, indices = [], {}

    def project(value):
        if isinstance(value, str) and len(value.encode("utf-8")) >= 512:
            if value not in indices:
                indices[value] = len(texts)
                texts.append(value)
            return {marker: indices[value]}
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items()}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    projected = project(packet)
    return {"reference_key": marker, "packet": projected}, texts


def restore_review_text(view, texts):
    """Deterministically recover the exact canonical packet from its wire view."""
    marker = view["reference_key"]

    def restore(value):
        if isinstance(value, dict):
            if set(value) == {marker}:
                index = value[marker]
                if type(index) is not int or not 0 <= index < len(texts):
                    raise ReviewEnvelopeError("review_text_reference_invalid")
                return texts[index]
            return {key: restore(item) for key, item in value.items()}
        if isinstance(value, list):
            return [restore(item) for item in value]
        return value

    return restore(view["packet"])
