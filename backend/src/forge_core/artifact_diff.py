"""Explicit UI reads of verified immutable file versions, never current Git state."""

import difflib
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .artifact_revision import verify_file_revision_chain
from .domain import DomainError

MAX_VERSION_BYTES = 262144  # Existing governed file-write ceiling.
MAX_CHAIN_BYTES = 4 * 1024 * 1024
MAX_DIFF_LINES = 2000
MAX_DIFF_BYTES = 512 * 1024
_SENSITIVE_PATH = re.compile(r"(?i)(secret|token|credential|password|\.env|\.key|\.pem)")
_RECEIPT_ID = re.compile(r"receipt_[A-Za-z0-9]{1,100}")


class ArtifactDiffUnavailable(ValueError):
    """Unknown, private, unsupported or unverifiable history has one public error."""


class ArtifactDiffInvalid(ValueError):
    """Malformed public identity."""


class ArtifactDiffTooLarge(ValueError):
    """A verified request exceeds the bounded UI diff budget."""


def _relative_path(store, scope):
    try:
        root = store.workspace.root.resolve()
        path = Path(scope)
        if not path.is_absolute():
            raise ValueError
        relative = path.resolve().relative_to(root).as_posix()
        if (
            relative in {"", "."}
            or len(relative) > 512
            or not relative.isprintable()
            or _SENSITIVE_PATH.search(relative)
        ):
            raise ValueError
        return relative
    except (TypeError, ValueError, OSError):
        raise ArtifactDiffUnavailable from None


def _bounded_versions(connection, run_id, receipt_id):
    """Bound parsing and hashing before the existing full binding validator runs."""
    versions, seen, total = {}, set(), 0
    current = receipt_id
    while current and len(seen) < 64:
        if not isinstance(current, str) or current in seen or not _RECEIPT_ID.fullmatch(current):
            raise ArtifactDiffUnavailable
        seen.add(current)
        row = connection.execute(
            "SELECT r.id AS receipt_id,r.created_at,r.execution_mode AS receipt_mode,"
            "length(i.parameters_json) AS parameter_size,length(r.result_json) AS result_size,"
            "i.run_id,i.quest_id,i.state,i.tool_name,i.resource_scope "
            "FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE r.id=?",
            (current,),
        ).fetchone()
        if (
            row is None
            or row["run_id"] != run_id
            or row["state"] != "succeeded"
            or row["tool_name"] not in {"file_write", "file_revise"}
            or row["receipt_mode"] not in {"live", "mock"}
            or row["result_size"] > 8192
        ):
            raise ArtifactDiffUnavailable
        if row["parameter_size"] > MAX_VERSION_BYTES * 6 + 8192:
            raise ArtifactDiffTooLarge
        raw = connection.execute(
            "SELECT i.parameters_json FROM action_receipts r "
            "JOIN action_intents i ON i.id=r.intent_id WHERE r.id=?",
            (current,),
        ).fetchone()[0]
        parameters = json.loads(raw)
        content = parameters.get("content") if isinstance(parameters, dict) else None
        if not isinstance(content, str) or "\x00" in content:
            raise ArtifactDiffUnavailable
        size = len(content.encode("utf-8"))
        total += size
        if size > MAX_VERSION_BYTES or total > MAX_CHAIN_BYTES:
            raise ArtifactDiffTooLarge
        versions[current] = {**dict(row), "content": content, "parameters": parameters}
        if row["tool_name"] == "file_write":
            return versions
        current = parameters.get("prior_receipt_id")
    raise ArtifactDiffUnavailable


def _origin(connection, quest_id):
    """Expose verified source IDs, not source text or inferred execution causality."""
    quest = connection.execute(
        "SELECT conversation_id,metadata FROM quests WHERE id=?", (quest_id,)
    ).fetchone()
    if quest is None:
        return None
    try:
        metadata = json.loads(quest["metadata"])
        if not isinstance(metadata, dict):
            return None
        origin = {}
        for id_key, hash_key in (
            ("original_request_message_id", "original_request_sha256"),
            ("goal_update_message_id", "goal_update_sha256"),
        ):
            identity, expected = metadata.get(id_key), metadata.get(hash_key)
            origin[id_key] = None
            if (
                not isinstance(identity, str)
                or not 1 <= len(identity) <= 256
                or not isinstance(expected, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected)
            ):
                continue
            message = connection.execute(
                "SELECT content FROM messages WHERE id=? AND role='user' AND conversation_id=?",
                (identity, quest["conversation_id"]),
            ).fetchone()
            if (
                message is not None
                and isinstance(message["content"], str)
                and 1 <= len(message["content"]) <= 20000
                and hashlib.sha256(message["content"].encode("utf-8")).hexdigest() == expected
            ):
                origin[id_key] = identity
        return origin if any(origin.values()) else None
    except (TypeError, ValueError, UnicodeError):
        return None


def _diff(before, after, path, created):
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    # SequenceMatcher's worst case is quadratic. This bounds its work independently
    # of the byte ceiling; default autojunk is retained for repeated source lines.
    if len(old) > MAX_DIFF_LINES or len(new) > MAX_DIFF_LINES:
        raise ArtifactDiffTooLarge
    matcher = difflib.SequenceMatcher(None, old, new)
    opcodes = matcher.get_opcodes()
    added = sum(end - start for tag, _, _, start, end in opcodes if tag in {"insert", "replace"})
    removed = sum(end - start for tag, start, end, _, _ in opcodes if tag in {"delete", "replace"})
    parts, size, truncated = [], 0, False
    for line in difflib.unified_diff(
        old, new, fromfile="/dev/null" if created else f"a/{path}", tofile=f"b/{path}"
    ):
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"
        encoded = line.encode("utf-8")
        if size + len(encoded) > MAX_DIFF_BYTES:
            truncated = True
            break
        parts.append(line)
        size += len(encoded)
    return added, removed, "".join(parts), truncated


def read_artifact_diff(store, receipt_id):
    """Read exact receipt history; successful reads cannot change durable state."""
    if not isinstance(receipt_id, str) or not _RECEIPT_ID.fullmatch(receipt_id):
        raise ArtifactDiffInvalid
    try:
        with store.connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            binding = connection.execute(
                "SELECT id,root FROM workspace WHERE singleton=1"
            ).fetchone()
            if (
                binding is None
                or binding["id"] != store.workspace.id
                or Path(binding["root"]).resolve() != store.workspace.root.resolve()
            ):
                raise ArtifactDiffUnavailable
            initial = connection.execute(
                "SELECT i.run_id,i.resource_scope FROM action_receipts r "
                "JOIN action_intents i ON i.id=r.intent_id WHERE r.id=?",
                (receipt_id,),
            ).fetchone()
            if initial is None:
                raise ArtifactDiffUnavailable
            path = _relative_path(store, initial["resource_scope"])
            versions = _bounded_versions(connection, initial["run_id"], receipt_id)
            verified = verify_file_revision_chain(connection, initial["run_id"], receipt_id)
            current = versions[receipt_id]
            quest = connection.execute(
                "SELECT conversation_id FROM quests WHERE id=?", (current["quest_id"],)
            ).fetchone()
            if quest is None or quest["conversation_id"] != "main":
                raise ArtifactDiffUnavailable
            prior_id = verified["prior_receipt_id"]
            prior = versions[prior_id] if prior_id else None
            before, after = prior["content"] if prior else "", current["content"]
            added, removed, text, truncated = _diff(before, after, path, prior is None)
            return {
                "receipt_id": receipt_id,
                "base_receipt_id": prior_id,
                "quest_id": current["quest_id"],
                "run_id": current["run_id"],
                "path": path,
                "execution_mode": current["receipt_mode"],
                "change": "created" if prior is None else "revised",
                "created_at": current["created_at"],
                "sha256": verified["sha256"],
                "base_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest()
                if prior
                else None,
                "added": added,
                "removed": removed,
                "text": text,
                "truncated": truncated,
                "origin": _origin(connection, current["quest_id"]),
            }
    except (ArtifactDiffTooLarge, ArtifactDiffUnavailable):
        raise
    except (
        DomainError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OSError,
        UnicodeError,
        sqlite3.Error,
    ):
        raise ArtifactDiffUnavailable from None
