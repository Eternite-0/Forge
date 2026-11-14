"""Core-owned lifecycle observations; these records are not authorization."""

import hashlib
import json
import os
import re
import secrets

from .domain import DomainError, ForgeEvent


def start(store, *, session_challenge):
    """Append a process generation while the caller owns the workspace lock."""
    if not isinstance(session_challenge, str) or not re.fullmatch(
        r"[0-9a-f]{64}", session_challenge
    ):
        raise DomainError("Invalid session challenge")
    generation = secrets.token_hex(16)
    with store.unit_of_work() as work:
        router = work.connection.execute(
            "SELECT router_session_id FROM router_state WHERE conversation_id='main'"
        ).fetchone()
        if router is None:
            raise DomainError("Router identity missing")
        work.append_event(
            ForgeEvent(
                event_type="core.started",
                payload={
                    "generation": generation,
                    "workspace_id": store.workspace.id,
                    "router_session_id": router["router_session_id"],
                    "pid": os.getpid(),
                    "session_challenge_sha256": hashlib.sha256(
                        session_challenge.encode()
                    ).hexdigest(),
                },
            )
        )
    return generation


def stop(store, generation):
    """Append a matching stop only for this process's recorded start."""
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
        raise DomainError("Invalid lifecycle generation")
    with store.unit_of_work() as work:
        row = work.connection.execute(
            "SELECT event_type,payload FROM events WHERE event_type IN ('core.started','core.stopped') AND json_extract(payload,'$.generation')=? ORDER BY seq DESC LIMIT 1",
            (generation,),
        ).fetchone()
        if row is None:
            raise DomainError("Lifecycle start missing")
        payload = json.loads(row["payload"])
        if (
            row["event_type"] != "core.started"
            or payload.get("pid") != os.getpid()
            or payload.get("workspace_id") != store.workspace.id
        ):
            raise DomainError("Lifecycle stop does not match this process")
        work.append_event(ForgeEvent(event_type="core.stopped", payload=payload))
