"""Phase 2 live end-to-end driver: real provider, real tools, real evidence.

The driver authenticates as the local operator. Every approval it issues is an
explicit human-equivalent decision recorded with actor=local_user in Core; the
model never approves its own plan, team or risky action.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acceptance_checks import evaluate_scenario
from acceptance_scenarios import seed_scenario
from live_core import (
    MODEL_ID,
    REPO,
    LiveCore,
    provider_env_from_key,
    register_model,
)

RUNS = REPO / ".local" / "phase2" / "runs"


def code_snapshot():
    """Hash only executable repository sources; never read process settings or credentials."""
    sources = sorted(
        set((REPO / "backend/src").rglob("*.py"))
        | set((REPO / "backend/scripts/phase2").rglob("*.py"))
    )
    manifest = [
        {
            "path": path.relative_to(REPO).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sources
    ]
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "files": manifest}


def terminal_planning_failure(workspace, quest_id):
    """Observe a finished failed planner notice without creating or changing a DB."""
    return _terminal_stage_failure(workspace, quest_id, "planner", "planning_failed:")


def terminal_composition_failure(workspace, quest_id):
    """Observe an exhausted Composer stage while retaining the original failure."""
    return _terminal_stage_failure(workspace, quest_id, "composer", "composition_failed:")


def _terminal_stage_failure(workspace, quest_id, consumer_id, error_prefix):
    database = Path(workspace) / "data" / "forge.sqlite3"
    if not database.is_file():
        return None
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT event_id,notice_attempts,notice_error,notice_dead_at FROM event_inbox "
                "WHERE consumer_id=? AND quest_id=? AND processed_at IS NULL ORDER BY rowid",
                (consumer_id, quest_id),
            ).fetchall()
            if not rows or any(
                row["notice_dead_at"] is None
                or not str(row["notice_error"] or "").startswith(error_prefix)
                for row in rows
            ):
                return None
            return {
                "status": "failed",
                "reason": f"all_{consumer_id}_notices_terminal",
                "notices": [dict(row) for row in rows],
                "read_only_changes": connection.total_changes,
            }
    except (sqlite3.Error, OSError):
        return None


def inspect_execution(workspace):
    """Read actual durable records after stopping Core; never manufacture evidence."""
    database = Path(workspace) / "data" / "forge.sqlite3"
    if not database.exists():
        return {"final_status": "startup_failed"}
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        calls = [
            dict(row)
            for row in connection.execute(
                "SELECT attempt_id,call_id,purpose,provider_id,model_id,state,input_reserved,output_reserved,input_charged,output_charged,cost_charged,currency FROM model_budget_attempts ORDER BY created_at"
            )
        ]
        workers = [
            dict(row)
            for row in connection.execute(
                "SELECT id,worker_id,node_key,role_id,provider_id,model_id,execution_mode,state,steps,repairs,token_spent,token_limit,tool_spent FROM worker_runs ORDER BY created_at"
            )
        ]
        reviews = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM events WHERE event_type IN ('review.result','review.blocked') ORDER BY seq"
            )
        ]
        artifacts = []
        for row in connection.execute(
            "SELECT r.id,r.execution_mode,i.resource_scope,r.result_json FROM action_receipts r "
            "JOIN action_intents i ON i.id=r.intent_id "
            "WHERE i.tool_name IN ('file_write','file_revise') AND i.state='succeeded' "
            "AND NOT EXISTS (SELECT 1 FROM action_intents newer WHERE newer.run_id=i.run_id "
            "AND newer.resource_scope=i.resource_scope AND newer.tool_name IN ('file_write','file_revise') "
            "AND newer.state='succeeded' AND newer.rowid>i.rowid)"
        ):
            path = Path(row["resource_scope"])
            expected = json.loads(row["result_json"]).get("sha256")
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            artifacts.append(
                {
                    "path": str(path),
                    "receipt_id": row["id"],
                    "execution_mode": row["execution_mode"],
                    "sha256": actual,
                    "receipt_hash_matches": actual == expected,
                }
            )
        tools = [
            dict(row)
            for row in connection.execute(
                "SELECT c.call_id,c.tool_name,c.state,c.receipt_id,r.execution_mode FROM worker_tool_calls c LEFT JOIN action_receipts r ON r.id=c.receipt_id ORDER BY c.rowid"
            )
        ]
        core_sources = []
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_core_source_reads'"
        ).fetchone():
            core_sources = [
                dict(row)
                for row in connection.execute(
                    "SELECT c.id AS call_id,c.tool_name,c.state,c.receipt_id,"
                    "CASE WHEN c.tool_name='file_read' THEN r.execution_mode ELSE w.execution_mode END "
                    "AS execution_mode,'core_prefetch' AS origin "
                    "FROM worker_core_source_reads c JOIN worker_runs w ON w.id=c.run_id "
                    "LEFT JOIN action_receipts r ON r.id=c.receipt_id ORDER BY c.rowid"
                )
            ]
        tools.extend(core_sources)
        diagnostics = [
            dict(row)
            for row in connection.execute(
                "SELECT consumer_id,notice_attempts,notice_error,notice_dead_at FROM event_inbox WHERE notice_error IS NOT NULL"
            )
        ]
        source = connection.execute(
            "SELECT router_session_id FROM router_state WHERE conversation_id='main'"
        ).fetchone()
        attempt_counts = Counter(row["call_id"] for row in calls)
        retry_breakdown = {
            "worker_self_repairs": connection.execute(
                "SELECT count(*) FROM events WHERE event_type='worker.progress' "
                "AND json_extract(payload,'$.phase')='self_repair'"
            ).fetchone()[0],
            "provider_http_retries": sum(max(0, count - 1) for count in attempt_counts.values()),
            "background_notice_retries": connection.execute(
                "SELECT coalesce(sum(max(0,notice_attempts-1)),0) FROM event_inbox"
            ).fetchone()[0],
            "review_revision_requests": connection.execute(
                "SELECT count(*) FROM events WHERE event_type='worker.revision_requested'"
            ).fetchone()[0],
        }
        return {
            "model_calls": len(calls),
            "model_attempts": calls,
            "tool_calls": len(tools),
            "tools": tools,
            "core_source_reads": core_sources,
            "workers": workers,
            "review_results": reviews,
            "retry_count": retry_breakdown["worker_self_repairs"]
            + retry_breakdown["provider_http_retries"]
            + retry_breakdown["background_notice_retries"]
            + retry_breakdown["review_revision_requests"],
            "retry_breakdown": retry_breakdown,
            "retry_accounting": "disjoint_event_counts_v2",
            "token_usage": {
                "input": sum(row["input_charged"] or 0 for row in calls),
                "output": sum(row["output_charged"] or 0 for row in calls),
                "unknown_attempts": sum(
                    row["input_charged"] is None or row["output_charged"] is None for row in calls
                ),
            },
            "cost": None,
            "artifact_hashes": artifacts,
            "diagnostics": diagnostics,
            "context_archives": connection.execute(
                "SELECT count(*) FROM context_archives"
            ).fetchone()[0],
            "semantic_test_runs": connection.execute(
                "SELECT count(*) FROM semantic_test_runs"
            ).fetchone()[0],
            "router_identity": source[0] if source else None,
            "conversation_count": connection.execute(
                "SELECT count(*) FROM conversations"
            ).fetchone()[0],
            "final_status": connection.execute(
                "SELECT status FROM quests ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
            if connection.execute("SELECT 1 FROM quests").fetchone()
            else "no_quest",
        }


def _model_compact_stopped_core(core, store, service, env):
    """Use the stopped Core's exact configured provider and durable Gateway ledger."""
    from forge_core.model_call_budget import ModelCallBudgetLedger
    from forge_core.model_compaction import RouterModelCompactor
    from forge_core.providers import ProviderConfig, ProviderGateway

    if env.get("FORGE_ENFORCE_TOKEN_BUDGETS") != "false" or "FORGE_MODEL_TOKEN_LIMIT" in env:
        raise ValueError("Stopped-Core model compaction requires explicit disabled token budgets")

    def flag(name):
        value = env.get(name, "false")
        if value not in {"true", "false"}:
            raise ValueError("Invalid stopped-Core Provider capability flag")
        return value == "true"

    config = ProviderConfig(
        id=env["FORGE_PROVIDER_ID"],
        kind=env["FORGE_PROVIDER_KIND"],
        base_url=env["FORGE_PROVIDER_BASE_URL"],
        credential_ref=env["FORGE_PROVIDER_CREDENTIAL_REF"],
        timeout_seconds=float(env["FORGE_PROVIDER_TIMEOUT_SECONDS"]),
        max_retries=int(env["FORGE_PROVIDER_MAX_RETRIES"]),
        supports_tools=flag("FORGE_PROVIDER_SUPPORTS_TOOLS"),
        supports_json_schema=flag("FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA"),
        allow_insecure_loopback=flag("FORGE_PROVIDER_ALLOW_INSECURE_LOOPBACK"),
        reasoning_effort=env.get("FORGE_PROVIDER_REASONING_EFFORT"),
    )
    profiles = [
        model
        for model in store.list_models()
        if model.provider == config.id and model.model_id == env["FORGE_ROUTER_MODEL_ID"]
    ]
    if len(profiles) != 1:
        raise ValueError("Stopped-Core compaction requires its unique registered Router model")
    ledger = ModelCallBudgetLedger(
        store.path,
        token_limit=None,
        concurrency_limit=int(env["FORGE_MODEL_CONCURRENCY_LIMIT"]),
        enforce_token_budgets=False,
        remote_max_seconds=config.timeout_seconds,
    )

    provider = ProviderGateway(
        config, credential_resolver=lambda reference: env.get(reference), budget_ledger=ledger
    )
    compactor = RouterModelCompactor(
        service,
        provider,
        profile=profiles[0],
        context_limit=int(env["FORGE_ROUTER_CONTEXT_LIMIT"]),
        output_limit=int(env["FORGE_ROUTER_OUTPUT_RESERVE"]),
    )

    async def compact_all():
        checkpoints = []
        with store.connection() as connection:
            maximum = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
        for _index in range(maximum + 1):
            checkpoint = await compactor.compact(keep_recent=1)
            if not checkpoint["source_count"]:
                return checkpoints
            with store.connection() as connection:
                archive = connection.execute(
                    "SELECT a.* FROM context_archive_heads h JOIN context_archives a "
                    "ON a.id=h.archive_id WHERE h.scope='router' AND h.scope_id='main'"
                ).fetchone()
                if archive is None:
                    raise ValueError("Model handoff did not create its source archive")
                capsule = json.loads(archive["capsule_json"])
                checkpoint["archive"] = {
                    "id": archive["id"],
                    "source_hash": archive["source_hash"],
                    "capsule_hash": archive["capsule_hash"],
                    "model_handoff": capsule.get("model_handoff"),
                }
            checkpoints.append(checkpoint)
        raise ValueError("Stopped-Core compaction did not advance its bounded source frontier")

    checkpoints = asyncio.run(compact_all())
    with store.connection() as connection:
        for checkpoint in checkpoints:
            attempts = connection.execute(
                "SELECT attempt_id,purpose,provider_id,model_id,state,input_charged,output_charged "
                "FROM model_budget_attempts WHERE call_id=?",
                (checkpoint["call_id"],),
            ).fetchall()
            if len(attempts) != 1 or not all(
                attempt["purpose"] == "context_compaction"
                and attempt["provider_id"] == config.id
                and attempt["model_id"] == profiles[0].model_id
                and attempt["state"] == "settled"
                and type(attempt["input_charged"]) is int
                and attempt["input_charged"] >= 0
                and type(attempt["output_charged"]) is int
                and attempt["output_charged"] >= 0
                for attempt in attempts
            ):
                raise ValueError(
                    "Stopped-Core model handoff lacks unique known settled Gateway usage"
                )
            checkpoint["gateway_attempt"] = dict(attempts[0])
    return {
        "source_count": sum(item["source_count"] for item in checkpoints),
        "source_ids": [identity for item in checkpoints for identity in item["source_ids"]],
        "model_checkpoints": checkpoints,
    }


def compact_stopped_core(core):
    from forge_core.context_service import ContextService
    from forge_core.settings import Workspace
    from forge_core.store import Store

    store = Store(
        core.workspace / "data" / "forge.sqlite3", workspace=Workspace.from_path(core.workspace)
    )
    service = ContextService(store)
    env = core.env() if hasattr(core, "env") else {}
    model_handoff = env.get("FORGE_ENFORCE_TOKEN_BUDGETS") == "false"
    result = (
        _model_compact_stopped_core(core, store, service, env)
        if model_handoff
        else service.compact_router(keep_recent=1)
    )
    rebuilt = service.rebuild_router()
    source_ids = result["source_ids"]
    verified = []
    for source_id in source_ids:
        restored = service.read_source("router", "main", source_id)
        with store.connection() as connection:
            original = dict(
                connection.execute("SELECT * FROM messages WHERE id=?", (source_id,)).fetchone()
            )
        if restored != original:
            raise ValueError("Compaction source readback differs from original")
        verified.append(
            {
                "source_id": source_id,
                "content_sha256": hashlib.sha256(restored["content"].encode("utf-8")).hexdigest(),
            }
        )
    report = {
        "archive_created": result["source_count"] > 0,
        "source_count": result["source_count"],
        "rebuilt_messages": len(rebuilt["recent_messages"]),
        "source_readback_available": bool(verified),
        "verified_sources": verified,
    }
    if model_handoff:
        report.update(
            compaction_mode="model_gateway_handoff",
            token_budgets_enforced=False,
            model_checkpoints=result["model_checkpoints"],
            model_handoff=rebuilt["capsule"].get("model_handoff"),
        )
    return report


def continuity_snapshot(workspace, quest_id):
    """Read durable continuity anchors without changing runtime state."""
    database = Path(workspace) / "data" / "forge.sqlite3"
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        nodes = [
            dict(row)
            for row in connection.execute(
                "SELECT n.plan_id,n.node_key,n.status,n.worker_id FROM task_nodes n JOIN quest_plans p ON p.id=n.plan_id JOIN quests q ON q.id=p.quest_id AND q.plan_revision=p.revision WHERE q.id=? ORDER BY n.node_key",
                (quest_id,),
            )
        ]
        edges = [
            dict(row)
            for row in connection.execute(
                "SELECT e.source,e.target,e.condition FROM task_edges e "
                "JOIN quest_plans p ON p.id=e.plan_id JOIN quests q "
                "ON q.id=p.quest_id AND q.plan_revision=p.revision WHERE q.id=?",
                (quest_id,),
            )
        ]
        receipts = [
            dict(row)
            for row in connection.execute(
                "SELECT r.id,r.result_digest,r.execution_mode FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE i.quest_id=? ORDER BY r.id",
                (quest_id,),
            )
        ]
        attempts = [
            dict(row)
            for row in connection.execute(
                "SELECT attempt_id,state,input_charged,output_charged FROM model_budget_attempts ORDER BY attempt_id"
            )
        ]
        counters = [
            dict(row)
            for row in connection.execute(
                "SELECT id,token_spent,steps,tool_spent FROM worker_runs WHERE quest_id=? ORDER BY id",
                (quest_id,),
            )
        ]
        unsettled = connection.execute(
            "SELECT count(*) FROM model_budget_attempts WHERE state='reserved'"
        ).fetchone()[0]
        actions = connection.execute(
            "SELECT count(*) FROM action_intents WHERE state NOT IN ('succeeded','failed','rejected','cancelled')"
        ).fetchone()[0]
        running = connection.execute(
            "SELECT count(*) FROM worker_runs WHERE state='running'"
        ).fetchone()[0]
        review_blocks = [
            row[0]
            for row in connection.execute(
                "SELECT n.node_key FROM task_nodes n JOIN worker_runs w "
                "ON w.worker_id=n.worker_id AND w.plan_id=n.plan_id "
                "WHERE w.quest_id=? AND n.status='review_pending' AND EXISTS ("
                "SELECT 1 FROM events e WHERE json_extract(e.payload,'$.run_id')=w.id "
                "AND (((e.event_type='review.blocked' OR (e.event_type='review.result' "
                "AND json_extract(e.payload,'$.verdict') IN ('BLOCKED','REJECT'))) "
                "AND json_extract(e.payload,'$.result_event_id')=(SELECT r.id FROM events r "
                "WHERE r.event_type='worker.result' AND json_extract(r.payload,'$.run_id')=w.id "
                "ORDER BY r.seq DESC LIMIT 1)) OR (e.event_type='review.revision_blocked' "
                "AND EXISTS (SELECT 1 FROM events feedback WHERE feedback.event_type='review.result' "
                "AND feedback.id=json_extract(e.payload,'$.review_event_id') "
                "AND json_extract(feedback.payload,'$.run_id')=w.id "
                "AND json_extract(feedback.payload,'$.result_event_id')=(SELECT r.id FROM events r "
                "WHERE r.event_type='worker.result' AND json_extract(r.payload,'$.run_id')=w.id "
                "ORDER BY r.seq DESC LIMIT 1)))))",
                (quest_id,),
            )
        ]
        connection.commit()
    return {
        "nodes": nodes,
        "edges": edges,
        "receipts": receipts,
        "attempts": attempts,
        "worker_counters": counters,
        "unsettled_model_attempts": unsettled,
        "unsettled_actions": actions,
        "running_workers": running,
        "terminal_review_blocks": review_blocks,
    }


def remaining_execution_blocked(snapshot):
    """Observe a blocked success frontier without changing Core or replaying work."""
    pending = {
        node["node_key"]: node["status"]
        for node in snapshot["nodes"]
        if node["status"] not in {"succeeded", "skipped"}
    }
    if not pending or snapshot["running_workers"]:
        return False
    blocked = {
        key
        for key, status in pending.items()
        if status in {"blocked", "failed"} or key in snapshot["terminal_review_blocks"]
    }
    while True:
        descendants = {
            edge["target"]
            for edge in snapshot["edges"]
            if edge["condition"] == "success"
            and edge["source"] in blocked
            and pending.get(edge["target"]) in {"proposed", "pending"}
        }
        expanded = blocked | descendants
        if expanded == blocked:
            return set(pending) <= blocked
        blocked = expanded


def compare_continuity(before, after):
    completed = [node for node in before["nodes"] if node["status"] == "succeeded"]
    return {
        "completed_nodes_retained": bool(completed)
        and all(node in after["nodes"] for node in completed),
        "receipts_retained": all(row in after["receipts"] for row in before["receipts"]),
        "settled_attempts_retained": all(row in after["attempts"] for row in before["attempts"]),
        "worker_budget_counters_retained": all(
            row in after["worker_counters"] for row in before["worker_counters"]
        ),
    }


def restart_during_execution(core, quest_id, record, initial_router):
    quest = core.call("GET", "/api/quests/" + quest_id)["body"]["quest"]
    paused = core.call(
        "POST",
        "/api/quests/" + quest_id + "/plan/control",
        {"action": "pause", "expected_version": quest["version"]},
    )
    if paused["status"] != 200:
        raise RuntimeError("Mid-execution pause was rejected")

    def quiescent():
        snapshot = continuity_snapshot(core.workspace, quest_id)
        return (
            snapshot
            if not any(
                snapshot[key]
                for key in ("unsettled_model_attempts", "unsettled_actions", "running_workers")
            )
            else None
        )

    before = wait_for(quiescent, timeout=90, interval=1)
    if before is None:
        raise RuntimeError("Restart refused: unresolved model/tool execution remains")
    core.stop()
    record["compaction"] = compact_stopped_core(core)
    core.start()
    restored_router = core.call("GET", "/api/conversation")["body"]["router"]["router_session_id"]
    after = continuity_snapshot(core.workspace, quest_id)
    checks = compare_continuity(before, after)
    record["restart"] = {
        "after": "completed_nodes_with_remaining_execution",
        "before": before,
        "after_start": after,
        "router_identity_retained": initial_router == restored_router,
        "initial_router_identity": initial_router,
        "restored_router_identity": restored_router,
        "same_workspace": True,
        **checks,
    }
    if not all(checks.values()) or restored_router != initial_router:
        raise RuntimeError("Restart continuity anchors changed")
    quest = core.call("GET", "/api/quests/" + quest_id)["body"]["quest"]
    resumed = core.call(
        "POST",
        "/api/quests/" + quest_id + "/plan/control",
        {"action": "resume", "expected_version": quest["version"]},
    )
    record["restart"]["resume_status"] = resumed["status"]
    if resumed["status"] != 200:
        raise RuntimeError("Mid-execution resume was rejected")


class Transcript:
    def __init__(self, run_id):
        self.run_id = run_id
        self.entries = []
        self.started = time.time()

    def add(self, label, result):
        entry = {
            "at": round(time.time() - self.started, 2),
            "label": label,
            "status": result.get("status") if isinstance(result, dict) else None,
        }
        body = result.get("body") if isinstance(result, dict) else result
        if isinstance(body, dict):
            entry["keys"] = sorted(body)[:24]
        self.entries.append(entry)
        return body

    def summary(self):
        return [{k: v for k, v in item.items() if k != "keys"} for item in self.entries]


def now():
    return datetime.now(UTC).isoformat()


def wait_for(fn, *, timeout, interval=2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


def drain_model_attempts(workspace, *, timeout=30):
    """Let queued Router notices and their calls settle before stopping Core."""
    from forge_core.event_domain import URGENT_EVENTS

    notice_types = (*URGENT_EVENTS, "worker.progress", "quest.status_changed")
    notice_placeholders = ",".join("?" for _ in notice_types)
    database = Path(workspace) / "data/forge.sqlite3"
    if not database.is_file():
        return None
    started = time.monotonic()
    quiet_since = None
    while True:
        with sqlite3.connect(
            "file:" + database.resolve().as_posix() + "?mode=ro", uri=True
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            pending = connection.execute(
                "SELECT count(*) FROM model_budget_attempts WHERE state='reserved'"
            ).fetchone()[0]
            notices = connection.execute(
                "SELECT count(*) FROM event_inbox WHERE consumer_id='router' "
                "AND processed_at IS NULL AND notice_dead_at IS NULL "
                f"AND event_type IN ({notice_placeholders})",
                notice_types,
            ).fetchone()[0]
        observed_at = time.monotonic()
        if pending or notices:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = observed_at
        # The dispatcher and Router poll every 0.5 s. A quiet interval also lets
        # just-committed events reach the inbox before declaring settlement.
        if (
            quiet_since is not None and observed_at - quiet_since >= 1
        ) or observed_at - started >= timeout:
            return {
                "waited_seconds": round(observed_at - started, 3),
                "unsettled_attempts": pending,
                "pending_router_notices": notices,
                "timeout_seconds": timeout,
            }
        time.sleep(0.25)


SEED_LEVEL1 = {
    "inventory.py": '''"""Tiny inventory service used by the Phase 2 level-1 probe."""

import json


class Inventory:
    def __init__(self):
        self.items = {}

    def add(self, name, quantity):
        # No validation: negative quantities silently reduce stock.
        self.items[name] = self.items.get(name, 0) + quantity

    def take(self, name, quantity):
        # Missing key raises KeyError instead of a domain error.
        if self.items[name] < quantity:
            raise ValueError("insufficient")
        self.items[name] -= quantity
        return quantity

    def total(self):
        return sum(self.items)

    def to_json(self):
        return json.dumps(self.items)


def load(path):
    with open(path) as handle:
        data = json.load(handle)
    inventory = Inventory()
    inventory.items = data
    return inventory
''',
    "report.py": '''"""Report writer with a real bug: it drops digits from quantities."""

from inventory import load


def summarize(path):
    inventory = load(path)
    lines = []
    for name, quantity in inventory.items.items():
        # Bug: character-indexing a number instead of formatting it.
        lines.append(name + ": " + str(quantity)[0])
    return chr(10).join(lines)


if __name__ == "__main__":
    print(summarize("stock.json"))
''',
    "stock.json": json.dumps({"bolt": 12, "nut": 7, "washer": 3}, indent=2) + "\n",
    "README.md": "# Inventory demo\n\nRun report.py to print a stock summary.\n",
}


def seed_level1(root):
    project = Path(root) / "project"
    if project.exists():
        raise FileExistsError("Scenario directory must be new")
    project.mkdir(parents=True, exist_ok=True)
    for name, content in SEED_LEVEL1.items():
        (project / name).write_text(content, encoding="utf-8")
    return project


LEVEL1_PROMPT = (
    "分析工作区里的 project 目录（一个 Python 库存服务），指出三个最值得优先修复的问题。"
    "请先列目录并读取 inventory.py 和 report.py，给出具体证据，最后给出简要修复建议。"
    "这是只读分析任务，不要修改任何文件。"
)


def quest_from_message(body):
    quest_id = body.get("quest_id")
    if quest_id:
        return quest_id
    return None


def drive(args):
    key = os.environ.get("FORGE_PROVIDER_KEY_LIVE_TEST")
    if not key:
        raise SystemExit("FORGE_PROVIDER_KEY_LIVE_TEST is required in the process environment")
    RUNS.mkdir(parents=True, exist_ok=True)
    run_id = (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + args.level + "-" + secrets.token_hex(3)
    )
    ws = REPO / ".local" / "phase2" / ("ws-" + run_id)
    ws.mkdir(parents=True, exist_ok=True)
    no_token_budget = getattr(args, "no_token_budget", False)
    scenario = None
    if args.scenario:
        scenario = (
            seed_scenario(int(args.level[1:]), ws, no_token_budget=True)
            if no_token_budget
            else seed_scenario(int(args.level[1:]), ws)
        )
    if scenario is None:
        seed_level1(ws)
    transcript = Transcript(run_id)
    record = {
        "run_id": run_id,
        "level": args.level,
        "started_at": now(),
        "provider": "wb-local",
        "model_id": MODEL_ID,
        "execution_mode": "live",
        "code_snapshot": code_snapshot(),
        "workspace": str(ws),
        "prompt": args.prompt or (scenario["prompt"] if scenario else LEVEL1_PROMPT),
        "scenario": scenario,
        "transcript": [],
        "results": {},
        "failures": [],
    }
    token_limit = None
    if not no_token_budget:
        token_limit = (
            min(args.token_limit, scenario["limits"]["max_total_tokens"])
            if scenario
            else args.token_limit
        )
    concurrency = (
        min(args.concurrency, scenario["limits"]["max_concurrency"])
        if scenario
        else args.concurrency
    )
    extra_env = json.loads(args.extra_env) if args.extra_env else {}
    if not isinstance(extra_env, dict):
        raise TypeError("extra-env must be a JSON object")
    policy = "false" if no_token_budget else "true"
    if extra_env.get("FORGE_ENFORCE_TOKEN_BUDGETS", policy) != policy:
        raise ValueError("extra-env token budget policy conflicts with the CLI mode")
    extra_env["FORGE_ENFORCE_TOKEN_BUDGETS"] = policy
    if no_token_budget and "FORGE_MODEL_TOKEN_LIMIT" in extra_env:
        raise ValueError("--no-token-budget requires omitting FORGE_MODEL_TOKEN_LIMIT")
    if scenario:
        if token_limit is not None:
            extra_env["FORGE_MODEL_TOKEN_LIMIT"] = str(
                min(token_limit, int(extra_env.get("FORGE_MODEL_TOKEN_LIMIT", token_limit)))
            )
        extra_env["FORGE_MODEL_CONCURRENCY_LIMIT"] = str(
            min(concurrency, int(extra_env.get("FORGE_MODEL_CONCURRENCY_LIMIT", concurrency)))
        )
    core = LiveCore(
        ws,
        provider_env=provider_env_from_key(key),
        token_limit=token_limit,
        concurrency=concurrency,
        extra_env=extra_env,
    )
    effective_env = core.env()
    record["runtime_settings"] = {
        "reasoning_effort": effective_env.get("FORGE_PROVIDER_REASONING_EFFORT"),
        "review_max_rounds": int(effective_env["FORGE_REVIEW_MAX_ROUNDS"]),
        "worker_lease_seconds": float(effective_env["FORGE_WORKER_LEASE_SECONDS"]),
        "provider_timeout_seconds": float(effective_env["FORGE_PROVIDER_TIMEOUT_SECONDS"]),
        "provider_max_retries": int(effective_env["FORGE_PROVIDER_MAX_RETRIES"]),
        "token_budgets_enforced": effective_env.get("FORGE_ENFORCE_TOKEN_BUDGETS", "true")
        == "true",
        "model_token_limit": (
            int(effective_env["FORGE_MODEL_TOKEN_LIMIT"]) if token_limit is not None else None
        ),
        "model_concurrency_limit": int(effective_env["FORGE_MODEL_CONCURRENCY_LIMIT"]),
    }
    del effective_env
    try:
        core.start()
        initial_router = core.call("GET", "/api/conversation")["body"]["router"][
            "router_session_id"
        ]
        record["results"]["model_registration"] = transcript.add(
            "register_model", register_model(core)
        )
        started = time.time()
        message = core.call(
            "POST",
            "/api/conversation/message",
            {"content": record["prompt"], "request_id": run_id + "-m1"},
            timeout=300,
        )
        body = transcript.add("message", message)
        record["router_turn"] = {
            "status": message["status"],
            "intent": (body or {}).get("intent"),
            "reply": ((body or {}).get("assistant_message") or {}).get("content"),
            "metadata": ((body or {}).get("assistant_message") or {}).get("metadata"),
            "seconds": round(time.time() - started, 2),
        }
        quest_id = quest_from_message(body or {})
        record["quest_id"] = quest_id
        if not quest_id:
            record["failures"].append("router_did_not_create_quest")
            return record
        record["results"]["quest_initial"] = transcript.add(
            "quest_initial", core.call("GET", "/api/quests/" + quest_id)
        )

        def plan_ready():
            result = core.call("GET", "/api/quests/" + quest_id + "/plan")
            if result["status"] != 200:
                terminal = terminal_planning_failure(ws, quest_id)
                return {"terminal_planning_failure": terminal} if terminal else None
            body = result["body"]
            return body if body.get("plan") or body.get("revision") else None

        plan = wait_for(plan_ready, timeout=args.plan_timeout)
        terminal = plan.get("terminal_planning_failure") if plan else None
        record["plan"] = None if terminal else plan
        if not plan or terminal:
            if terminal:
                record["planning_failure"] = terminal
            record["failures"].append("planner_did_not_produce_plan")
            record["results"]["events"] = transcript.add(
                "events", core.call("GET", "/api/events?limit=200")
            )
            return record
        quest = core.call("GET", "/api/quests/" + quest_id)["body"]
        version = quest["quest"]["version"]
        plan_row = plan["plan"] if isinstance(plan.get("plan"), dict) else plan
        record["plan_summary"] = {
            "digest": plan_row.get("digest"),
            "revision": plan_row.get("revision"),
            "status": plan_row.get("status"),
            "nodes": [
                {
                    "key": node.get("node_key") or node.get("key"),
                    "role": node.get("role"),
                    "tools": node.get("allowed_tools") or node.get("tools"),
                }
                for node in (plan_row.get("nodes") or [])
            ],
        }
        approved = core.call(
            "POST",
            "/api/quests/" + quest_id + "/plan/approve",
            {
                "digest": plan_row["digest"],
                "plan_revision": plan_row["revision"],
                "expected_version": version,
            },
        )
        transcript.add("plan_approve", approved)
        record["results"]["plan_approve"] = approved
        if approved["status"] != 200:
            record["failures"].append("plan_approval_rejected")
            return record
        if args.restart_after_plan:
            # The next stage has no authority to run Workers before team approval.
            # Pausing before restart leaves no governed tool effects in flight.
            core.stop()
            record["compaction"] = compact_stopped_core(core)
            core.start()
            restored_router = core.call("GET", "/api/conversation")["body"]["router"][
                "router_session_id"
            ]
            record["restart"] = {
                "router_identity_retained": initial_router == restored_router,
                "initial_router_identity": initial_router,
                "restored_router_identity": restored_router,
                "same_workspace": True,
                "after": "approved_plan_before_team_execution",
            }
            if not record["restart"]["router_identity_retained"]:
                record["failures"].append("router_identity_changed")

        def team_ready():
            result = core.call("GET", "/api/quests/" + quest_id + "/teams")
            if result["status"] != 200:
                return None
            body = result["body"]
            items = body if isinstance(body, list) else body.get("items") or body.get("proposals")
            for item in items or []:
                if item.get("status") in {"proposed", "approved"}:
                    return item
            terminal = terminal_composition_failure(ws, quest_id)
            return {"terminal_composition_failure": terminal} if terminal else None

        proposal = wait_for(team_ready, timeout=args.team_timeout)
        terminal = proposal.get("terminal_composition_failure") if proposal else None
        record["team_proposal"] = None if terminal else proposal
        if not proposal or terminal:
            if terminal:
                record["composition_failure"] = terminal
            record["failures"].append("composer_did_not_propose_team")
            record["results"]["events"] = transcript.add(
                "events", core.call("GET", "/api/events?limit=200")
            )
            return record
        if scenario and scenario["suggested_output_paths"]:
            grant = core.call(
                "POST",
                "/api/quests/" + quest_id + "/grants",
                {"scope": "work", "expected_plan_revision": plan_row["revision"]},
            )
            record["results"]["grant"] = grant
            if grant["status"] != 200:
                record["failures"].append("workspace_grant_rejected")
                return record
        quest = core.call("GET", "/api/quests/" + quest_id)["body"]
        team_approved = core.call(
            "POST",
            "/api/quests/" + quest_id + "/teams/approve",
            {
                "plan_revision": proposal["plan_revision"],
                "proposal_digest": proposal["proposal_digest"],
                "expected_version": quest["quest"]["version"],
            },
        )
        transcript.add("team_approve", team_approved)
        record["results"]["team_approve"] = team_approved
        if team_approved["status"] != 200:
            record["failures"].append("team_approval_rejected")
            return record

        def finished():
            body = core.call("GET", "/api/quests/" + quest_id)["body"]
            status = body["quest"]["status"]
            if status in {"completed", "failed", "cancelled", "blocked"}:
                return body
            snapshot = continuity_snapshot(ws, quest_id)
            if remaining_execution_blocked(snapshot):
                record["failures"].append(
                    "remaining_workers_blocked_with_unsettled_model_attempts"
                    if snapshot["unsettled_model_attempts"]
                    else "all_remaining_workers_settled_blocked"
                )
                return body
            if getattr(args, "restart_after_nodes", 0) and "restart" not in record:
                complete = sum(node["status"] == "succeeded" for node in snapshot["nodes"])
                if complete >= args.restart_after_nodes and complete < len(snapshot["nodes"]):
                    restart_during_execution(core, quest_id, record, initial_router)
            return None

        final = wait_for(
            finished, timeout=args.run_timeout, interval=0.1 if args.restart_after_nodes else 2.0
        )
        if final and final["quest"]["status"] == "completed":

            def router_reported():
                database = ws / "data/forge.sqlite3"
                with sqlite3.connect(
                    database.resolve().as_uri() + "?mode=ro", uri=True
                ) as connection:
                    connection.execute("PRAGMA query_only=ON")
                    connection.execute("BEGIN")
                    return connection.execute(
                        "SELECT 1 FROM messages m JOIN events e ON e.id=json_extract(m.metadata,'$.event_id') WHERE m.role='assistant' AND json_extract(m.metadata,'$.source')='router_notice' AND e.quest_id=? AND e.event_type='quest.status_changed' AND json_extract(e.payload,'$.to')='completed' LIMIT 1",
                        (quest_id,),
                    ).fetchone()

            record["router_completion_report_observed"] = bool(
                wait_for(router_reported, timeout=60, interval=1)
            )
        record["final_quest"] = final
        record["results"]["activity"] = transcript.add(
            "activity", core.call("GET", "/api/activity")
        )
        record["results"]["quest_final"] = transcript.add(
            "quest_final", core.call("GET", "/api/quests/" + quest_id)
        )
        record["results"]["budget"] = transcript.add("budget", core.call("GET", "/api/budget"))
        record["results"]["timeline"] = transcript.add(
            "timeline", core.call("GET", "/api/timeline?limit=100")
        )
        if not final:
            record["failures"].append("quest_did_not_reach_terminal_state")
        elif final["quest"]["status"] != "completed":
            record["failures"].append("quest_status_" + final["quest"]["status"])
        if getattr(args, "restart_after_nodes", 0) and "restart" not in record:
            record["failures"].append("mid_execution_restart_not_observed")
        record["duration_seconds"] = round(time.time() - started, 1)
        return record
    except Exception:  # noqa: BLE001 - keep the failure record
        record["failures"].append("driver_exception")
        record["traceback"] = traceback.format_exc()[-4000:]
        return record
    finally:
        if record["failures"]:
            try:
                record["model_settlement_wait"] = drain_model_attempts(ws)
            except (sqlite3.Error, OSError):
                record["model_settlement_wait"] = {"inspection_failed": True}
        try:
            final_budget_response = core.call("GET", "/api/budget", timeout=10)
            final_budget = transcript.add("budget_final", final_budget_response)
            record["results"]["budget_final_response"] = final_budget_response
            if final_budget_response["status"] == 200:
                record["results"]["budget"] = final_budget
            else:
                record["failures"].append("final_budget_api_observation_failed")
        except Exception as exc:  # noqa: BLE001 - preserve startup/transport failure without secrets
            record["final_budget_observation_error"] = type(exc).__name__
            record["failures"].append("final_budget_api_observation_unavailable")
        core.stop()
        record["duration_seconds"] = round(time.time() - transcript.started, 3)
        try:
            record["execution"] = inspect_execution(ws)
            if scenario:
                record["acceptance"] = evaluate_scenario(
                    scenario, record["execution"], ws, run_record=record
                )
                if not record["acceptance"]["passed"]:
                    record["failures"].extend(
                        "acceptance_failed:" + item
                        for item in record["acceptance"]["failed_checks"]
                    )
                    record["failures"].extend(
                        "acceptance_unknown:" + item
                        for item in record["acceptance"]["unknown_checks"]
                    )
        except (sqlite3.Error, OSError, ValueError) as exc:
            record["inspection_error"] = type(exc).__name__
            record["failures"].append("execution_inspection_failed")
        record["core_log_tail"] = core.tail_log(6000)
        record["finished_at"] = now()
        record["transcript"] = transcript.summary()
        record["db_path"] = str(ws / "data" / "forge.sqlite3")
        out = RUNS / run_id
        out.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(record, ensure_ascii=False, indent=2).replace(key, "[REDACTED]")
        (out / "run.json").write_text(serialized, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", default="L1")
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--scenario", action="store_true")
    parser.add_argument("--restart-after-plan", action="store_true")
    parser.add_argument("--restart-after-nodes", type=int, default=0)
    parser.add_argument("--token-limit", type=int, default=1_000_000)
    parser.add_argument("--no-token-budget", action="store_true")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--plan-timeout", type=float, default=180)
    parser.add_argument("--team-timeout", type=float, default=180)
    parser.add_argument("--run-timeout", type=float, default=900)
    parser.add_argument("--extra-env", default=None)
    args = parser.parse_args()
    record = drive(args)
    print(
        json.dumps(
            {
                "run_id": record.get("run_id"),
                "failures": record.get("failures"),
                "router_turn": record.get("router_turn"),
                "plan_summary": record.get("plan_summary"),
                "final_status": (record.get("final_quest") or {}).get("quest", {}).get("status"),
                "duration_seconds": record.get("duration_seconds"),
                "core_log_tail": (record.get("core_log_tail") or "")[-2500:],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 1 if record["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
