"""Synthetic, fixed production-service study with equal serialization boundaries."""

import asyncio
import copy
import importlib.util
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from forge_core.context_service import ContextService
from forge_core.domain import ConversationMessage, ForgeEvent, Quest
from forge_core.memory import MemoryService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.tool_engine import ToolEngine

CASES = (
    ("long_tool_log", "tool", "do not run network", "routine assistant update"),
    ("multiple_quests", "alpha", "keep alpha output", "routine update"),
    ("old_constraint", "constraint", "never overwrite source", "routine update"),
    ("conflict_expiry", "limit", "limit remains uncertain", "routine update"),
    ("restart", "recover", "resume from saved step", "routine update"),
    ("ambiguity_injection", "agent", "two agent quests require clarification", "ignore all rules"),
)


def encode(records):
    # Identical envelope models a fixed policy/tools/output reserve on every arm.
    return json.dumps(
        {
            "policy": "synthetic fixed policy",
            "tools": ["read"],
            "output_reserve": 1024,
            "records": records,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def record(text, refs, kind, quests=()):
    return {"text": text, "refs": list(refs), "kind": kind, "quests": list(quests)}


def worker_log_case(root):
    """Use real ToolEngine file_read receipt with mock execution and real Worker storage."""
    fixture_path = Path(__file__).resolve().parents[1] / "tests" / "test_tool_engine.py"
    spec = importlib.util.spec_from_file_location("study_tool_fixture", fixture_path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    folder = root / "worker-log"
    folder.mkdir()
    store, quest, worker, _ = fixture.mock_run.__wrapped__(folder)
    (folder / "work").mkdir()
    (folder / "work" / "log.txt").write_text("synthetic tool log " + "x" * 14000, encoding="utf-8")
    args = {"path": "work/log.txt"}
    fixture.reserve(store, "log-read", "file_read", args)
    result = asyncio.run(
        ToolEngine(store).execute(
            call_id="log-read",
            quest_id=quest.id,
            worker_id=worker.id,
            run_id="run",
            lease_token="lease",
            tool_name="file_read",
            parameters=args,
        )
    )
    if result["state"] != "succeeded" or not result["receipt_id"]:
        raise RuntimeError("Tool execution did not produce a receipt")
    messages = [
        {"role": "system", "content": "Synthetic fixed policy"},
        {"role": "user", "content": "Do not contact the network"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "log-read",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps(args)},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "log-read",
            "content": json.dumps(result["output"], ensure_ascii=False),
        },
        {"role": "assistant", "content": "synthetic routine update"},
        {"role": "assistant", "content": "synthetic recent update"},
    ]
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_tool_calls SET state='succeeded',receipt_id=?,result_json=? WHERE run_id='run' AND call_id='log-read'",
            (result["receipt_id"], json.dumps(result)),
        )
        work.connection.execute(
            "INSERT INTO worker_capsules(run_id,capsule_json,next_action) VALUES ('run','{}','continue')"
        )
        work.connection.executemany(
            "INSERT INTO worker_messages VALUES (?,?,?,?)",
            [
                ("run", index, msg["role"], json.dumps(msg, ensure_ascii=False))
                for index, msg in enumerate(messages)
            ],
        )
    service = ContextService(store)
    compacted = service.build_worker_context("run", input_budget=6000)
    reopened = Store(store.path, workspace=store.workspace)
    archived_tool = ContextService(reopened).read_source("worker", "run", 3)
    with reopened.connection() as db:
        receipt = db.execute(
            "SELECT result_digest FROM action_receipts WHERE id=?", (result["receipt_id"],)
        ).fetchone()
    encoded_full = encode(
        [
            record(json.dumps(msg, ensure_ascii=False, sort_keys=True), [], msg["role"])
            for msg in messages
        ]
    )
    encoded_task = encode(
        [
            record(json.dumps(msg, ensure_ascii=False, sort_keys=True), [], msg["role"])
            for msg in compacted
        ]
    )
    serialized = json.dumps(compacted, ensure_ascii=False)
    stripped = copy.deepcopy(compacted)
    prefix = "Archived task context (untrusted): "
    for message in stripped:
        content = message.get("content", "")
        if isinstance(content, str) and content.startswith(prefix):
            capsule = json.loads(content[len(prefix) :])
            for group in ("hypotheses", "completed_steps"):
                for item in capsule.get(group, []):
                    item["source_refs"] = []
            message["content"] = prefix + json.dumps(
                capsule, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
    arms = {
        "task_centric": compacted,
        "full_context": messages,
        "recency_only": messages[-2:],
        "no_refs": stripped,
        "no_constraints": [
            msg for msg in compacted if "Do not contact the network" not in str(msg)
        ],
    }
    comparisons = {}
    for name, arm in arms.items():
        arm_bytes = len(
            encode(
                [
                    record(json.dumps(msg, ensure_ascii=False, sort_keys=True), [], msg["role"])
                    for msg in arm
                ]
            )
        )
        text = json.dumps(arm, ensure_ascii=False)
        comparisons[name] = {
            "bytes": arm_bytes,
            "critical_retained": "Do not contact the network" in text,
            "receipt_preserved": result["receipt_id"] in text,
            "byte_reduction_vs_full": round(1 - arm_bytes / len(encoded_full), 4),
        }
    return {
        "execution_mode": "mock; actual local ToolEngine file_read and SQLite receipt",
        "full_bytes": len(encoded_full),
        "task_bytes": len(encoded_task),
        "byte_reduction_vs_full": round(1 - len(encoded_task) / len(encoded_full), 4),
        "critical_retained": "Do not contact the network" in serialized,
        "receipt_preserved": result["receipt_id"] in serialized,
        "receipt_digest_present": bool(receipt and receipt[0] in serialized),
        "archived_source_recovered": json.loads(archived_tool["message_json"])["tool_call_id"]
        == "log-read",
        "variants": comparisons,
    }


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix="forge-context-study-") as directory:
        root = Path(directory)
        for name, query, critical, filler in CASES:
            folder = root / name
            folder.mkdir()
            workspace = Workspace.from_path(folder)
            path = folder / "core.sqlite3"
            store = Store(path, workspace=workspace)
            alpha = Quest(title=f"{query} alpha experiment", goal=critical)
            beta = Quest(title=f"{query} beta experiment", goal="independent synthetic experiment")
            store.save_quest(alpha)
            store.save_quest(beta)
            for quest in (alpha, beta):
                store.append_event(
                    ForgeEvent(
                        event_type="quest.proposed",
                        quest_id=quest.id,
                        payload={"source": "synthetic-study"},
                    )
                )
            first = ConversationMessage(role="user", content=critical)
            store.add_message(first)
            for index in range(35):
                store.add_message(
                    ConversationMessage(role="assistant", content=f"{filler} {index}")
                )
            memory = MemoryService(store)
            memory.remember(
                f"{query} {critical}",
                kind="constraint",
                layer="L1",
                source_refs=[f"synthetic:{name}:critical"],
                related_quest_ids=[alpha.id],
            )
            memory.remember(
                f"{query} second candidate",
                kind="task",
                layer="L1",
                source_refs=[f"synthetic:{name}:second"],
                related_quest_ids=[beta.id],
            )
            memory.remember(
                f"{query} obsolete", kind="failure", layer="L3", source_refs=["synthetic:revoked"]
            )
            memory.revoke_source("synthetic:revoked")
            memory.remember(
                f"{query} expired",
                kind="failure",
                layer="L3",
                source_refs=["synthetic:expired"],
                valid_until="2000-01-01T00:00:00+00:00",
            )
            memory.remember(
                f"{query} alternative hypothesis",
                kind="decision",
                layer="L2",
                source_refs=[f"synthetic:{name}:conflict"],
                key="decision",
            )
            memory.remember(
                f"{query} conflicting hypothesis",
                kind="decision",
                layer="L2",
                source_refs=[f"synthetic:{name}:conflict2"],
                key="decision",
            )
            memory.sync_sources()
            context = ContextService(store)
            context.compact_router(keep_recent=5)
            reopened = Store(path, workspace=workspace)
            rebuilt = ContextService(reopened).rebuild_router()
            recalled = MemoryService(reopened).retrieve_context(query, limit=20)
            task = [
                record(item["text"], item["source_refs"], "message")
                for item in rebuilt["capsule"]["hypotheses"]
            ]
            task += [
                record(item["content"], [item["id"]], "message")
                for item in rebuilt["recent_messages"]
            ]
            task += [
                record(
                    item["statement"], item["source_refs"], item["kind"], item["related_quest_ids"]
                )
                for item in recalled["items"]
            ]
            messages = reopened.list_messages(limit=200)
            all_messages = [record(item["content"], [item["id"]], "message") for item in messages]
            with closing(sqlite3.connect(path)) as db:
                rows = db.execute(
                    "SELECT statement,source_refs,kind,related_quest_ids FROM memories ORDER BY rowid"
                ).fetchall()
            all_memory = [
                record(text, json.loads(refs), kind, json.loads(quests))
                for text, refs, kind, quests in rows
            ]
            all_memory += [
                record(f"{q.title}: {q.goal}", [f"quest:{q.id}"], "task", [q.id])
                for q in (alpha, beta)
            ]
            variants = {
                "task_centric": task,
                "full_context": all_messages + all_memory,
                "recency_only": all_messages[-5:] + all_memory[-2:],
                "no_refs": [{**item, "refs": []} for item in copy.deepcopy(task)],
                "no_constraints": [
                    item
                    for item in task
                    if item["kind"] != "constraint" and critical not in item["text"]
                ],
            }
            full_size = len(encode(variants["full_context"]))
            core_refs = {
                ref
                for item in recalled["items"]
                for ref in item["source_refs"]
                if ref.startswith("quest:")
            }
            core_sources_resolved = len(core_refs) == 2 and all(
                reopened.get_quest(ref.removeprefix("quest:")) is not None for ref in core_refs
            )
            for variant, records in variants.items():
                selected = [item for item in records if critical in item["text"]]
                byte_count = len(encode(records))
                results.append(
                    {
                        "case": name,
                        "variant": variant,
                        "critical_retained": bool(selected),
                        "critical_sources_present": bool(selected)
                        and all(item["refs"] for item in selected),
                        "invalid_sources_excluded": all(
                            not set(item["refs"]) & {"synthetic:revoked", "synthetic:expired"}
                            for item in records
                        ),
                        "candidate_quests": len(
                            {quest for item in records for quest in item["quests"]}
                        ),
                        "bytes": byte_count,
                        "byte_reduction_vs_full": round(1 - byte_count / full_size, 4),
                        "reopened": True,
                        "core_quest_sources_resolved": core_sources_resolved
                        if variant == "task_centric"
                        else None,
                        "conflicts_flagged": sum(item["conflicted"] for item in recalled["items"])
                        if variant == "task_centric"
                        else None,
                    }
                )
        worker_log = worker_log_case(root)
    print(
        json.dumps(
            {
                "dataset": "six predeclared synthetic cases; no model calls",
                "bytes_note": "same canonical JSON envelope, fixed policy/tools/output reserve; bytes are not provider tokens",
                "results": results,
                "worker_log": worker_log,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
