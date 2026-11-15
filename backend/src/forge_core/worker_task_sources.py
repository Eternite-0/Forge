"""Read the two exact user sources of a Quest without injecting its history."""

import hashlib
import json
import re

from .domain import DomainError

MAX_TASK_REQUEST_CHARS = 20000  # The existing authenticated message-input ceiling.
INPUT_ACQUISITION_POLICY = "native-input-roundtrip-v1"
CORE_PREFETCH_POLICY = "core-declared-source-prefetch-v1"
INPUT_POLICIES = (INPUT_ACQUISITION_POLICY, CORE_PREFETCH_POLICY)


def input_acquisition_policy(graph, member):
    contract = graph.get("capacity_contract") or {}
    key = "input_acquisition_policy"
    if key not in contract and key not in member:
        return None
    policy = contract.get(key)
    if not isinstance(policy, str) or policy not in INPUT_POLICIES or member.get(key) != policy:
        raise DomainError("Worker input acquisition policy differs from the approved plan")
    if policy == CORE_PREFETCH_POLICY:
        allowances = contract.get("action_evidence_allowances")
        node_key = member.get("task_node_key")
        allowance = member.get("action_evidence_allowance_bytes")
        if (
            not isinstance(allowances, dict)
            or node_key not in allowances
            or type(allowance) is not int
            or not 0 <= allowance <= 32768
            or type(allowances[node_key]) is not int
            or allowances[node_key] != allowance
        ):
            raise DomainError("Worker action evidence allowance differs from the approved plan")
    return policy


def verified_task_sources(connection, quest, *, policy=None):
    """Return complete bound sources, or fail before a Worker can reserve work."""
    if policy is None:
        return {}
    if not isinstance(policy, str) or policy not in INPUT_POLICIES:
        raise DomainError("Unknown Worker input acquisition policy")
    try:
        metadata = quest["metadata"]
        metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
    except (KeyError, TypeError, ValueError):
        raise DomainError("Worker task source metadata is invalid") from None
    if not isinstance(metadata, dict):
        raise DomainError("Worker task source metadata is invalid")
    sources = {}
    for field, id_key, hash_key in (
        ("original_request", "original_request_message_id", "original_request_sha256"),
        ("updated_request", "goal_update_message_id", "goal_update_sha256"),
    ):
        if id_key not in metadata and hash_key not in metadata:
            continue
        source_id, expected = metadata.get(id_key), metadata.get(hash_key)
        if (
            not isinstance(source_id, str)
            or not 1 <= len(source_id) <= 256
            or not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        ):
            raise DomainError("Worker task source binding is invalid")
        source = connection.execute(
            "SELECT id,content FROM messages WHERE id=? AND role='user' AND conversation_id=?",
            (source_id, quest["conversation_id"]),
        ).fetchone()
        if (
            source is None
            or not isinstance(source["content"], str)
            or not 1 <= len(source["content"]) <= MAX_TASK_REQUEST_CHARS
            or hashlib.sha256(source["content"].encode("utf-8")).hexdigest() != expected
        ):
            raise DomainError("Worker task source failed integrity validation")
        sources[field] = {**dict(source), "sha256": expected}
    return sources
