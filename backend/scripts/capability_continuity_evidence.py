"""Read-only continuity evidence from actual Worker tools, never model claims.

Page reconstruction and relation traversal prove what source data was delivered
to subsequent Worker turns. They do not establish semantic understanding.
"""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from forge_core.evidence import _verify_worker_context_call
from forge_core.settings import Workspace


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def complete_pages(pages, *, byte_offsets):
    """Require contiguous, hash-consistent full content; duplicates fill no gap."""
    if not pages:
        return None
    segments = {}
    total, digest = pages[0]["total"], pages[0]["sha256"]
    if type(total) is not int or total < 1 or not isinstance(digest, str):
        return None
    for page in pages:
        start, end, text = page["offset"], page["end"], page["text"]
        if (
            type(start) is not int or type(end) is not int or not isinstance(text, str)
            or page["total"] != total or page["sha256"] != digest
            or not 0 <= start < end <= total
            or (len(text.encode("utf-8")) if byte_offsets else len(text)) != end - start
            or page["complete"] is not (end == total)
        ):
            return None
        previous = segments.get(start)
        if previous is not None and (previous["end"], previous["text"]) != (end, text):
            return None
        segments.setdefault(start, page)
    cursor, text, last_result, selected = 0, "", -1, []
    for start in sorted(segments):
        page = segments[start]
        if start != cursor or page["invocation_ordinal"] <= last_result:
            return None
        cursor, last_result = page["end"], page["result_ordinal"]
        text += page["text"]
        selected.append(page)
    if cursor != total or _sha(text) != digest:
        return None
    return {
        "source_sha256": digest,
        "source_size": total,
        "page_count": len(selected),
        "first_invocation": selected[0]["invocation_ordinal"],
        "last_result": selected[-1]["result_ordinal"],
        "relations": sorted({ref for page in selected for ref in page.get("relations", [])}),
        "call_ids": [page["call_id"] for page in selected],
    }


def relation_chain(objects, start, *, hops=2):
    """Every next object must follow an observed relation on a later model turn."""
    def visit(path):
        if len(path) == hops + 1:
            return path
        current = objects[path[-1]]
        for reference in current["relations"]:
            target = objects.get(reference)
            if (
                target is not None and reference not in path
                and target["first_invocation"] > current["last_result"]
            ):
                found = visit([*path, reference])
                if found:
                    return found
        return []

    return visit([start]) if start in objects else []


def _transcript(connection, run_id, call):
    rows = connection.execute(
        "SELECT ordinal,role,message_json FROM worker_messages WHERE run_id=? ORDER BY ordinal",
        (run_id,),
    ).fetchall()
    invoked, result, subsequent = [], [], []
    for row in rows:
        message = json.loads(row["message_json"])
        if row["role"] == "assistant":
            subsequent.append(row["ordinal"])
            if any(item.get("id") == call["model_call_id"] for item in message.get("tool_calls", [])):
                invoked.append(row["ordinal"])
        elif row["role"] == "tool" and message.get("tool_call_id") == call["model_call_id"]:
            result.append(row["ordinal"])
    if (
        len(invoked) != 1 or len(result) != 1 or invoked[0] >= result[0]
        or not any(ordinal > result[0] for ordinal in subsequent)
    ):
        return None
    return {"invocation_ordinal": invoked[0], "result_ordinal": result[0]}


def observe(database, workspace, mapping, deliveries, *, after_evidence_rowid=0):
    """Verify real Core proofs/transcripts, complete pages and same-run relations.

    No Store is opened and no migrations or file/tool actions execute. Published
    fields contain references, hashes and counts, never source contents.
    """
    database, workspace = Path(database).resolve(), Path(workspace).resolve()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        c.execute("BEGIN")
        identity = c.execute("SELECT id FROM workspace WHERE singleton=1").fetchone()[0]
        store = SimpleNamespace(workspace=Workspace.from_path(workspace, identity), path=database)
        runs = {row["id"]: dict(row) for row in c.execute("SELECT * FROM worker_runs")}
        sources, trigger_pages, invalid = {}, {}, 0
        for call in c.execute(
            "SELECT * FROM worker_tool_calls WHERE tool_name IN ('read_trigger','recall_object') "
            "AND state='succeeded' ORDER BY rowid"
        ).fetchall():
            run = runs.get(call["run_id"])
            if not run or run["execution_mode"] != "live" or run["quest_id"] not in mapping.values():
                continue
            proofs = c.execute(
                "SELECT rowid FROM evidence WHERE kind='worker_core_source' AND trusted=1 "
                "AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.call_id')=?",
                (call["run_id"], call["call_id"]),
            ).fetchall()
            if len(proofs) != 1 or proofs[0][0] <= after_evidence_rowid:
                continue
            transcript = _transcript(c, call["run_id"], call)
            if not transcript or not _verify_worker_context_call(store, c, call["run_id"], call):
                invalid += 1
                continue
            output, arguments = json.loads(call["result_json"]), json.loads(call["arguments_json"])
            shared = {**transcript, "call_id": call["call_id"]}
            if call["tool_name"] == "read_trigger":
                key = call["run_id"], arguments["trigger_id"]
                trigger_pages.setdefault(key, []).append({
                    **shared, "offset": output["offset_chars"],
                    "end": output["next_offset_chars"] or output["total_chars"],
                    "total": output["total_chars"], "sha256": output["payload_sha256"],
                    "text": output["text"], "complete": output["next_offset_chars"] is None,
                })
            else:
                key = call["run_id"], output["ref"], output["source_hash"]
                sources.setdefault(key, []).append({
                    **shared, "offset": output["offset"], "end": output["next_offset"],
                    "total": output["content_bytes"], "sha256": output["source_hash"],
                    "text": output["content"], "complete": output["complete"],
                    "relations": [item["ref"] for item in output["relations"]],
                })
        restored = {}
        for (run_id, reference, _digest), pages in sources.items():
            complete = complete_pages(pages, byte_offsets=True)
            if complete:
                restored.setdefault(run_id, {})[reference] = complete
        thread = []
        for delivery in deliveries:
            complete = []
            for run_id in delivery["run_ids"]:
                if runs.get(run_id, {}).get("quest_id") != mapping.get(delivery["level"]):
                    continue
                result = complete_pages(
                    trigger_pages.get((run_id, delivery["trigger_id"]), []), byte_offsets=False
                )
                if result:
                    complete.append({"run_id": run_id, **result})
            thread.append({**delivery, "complete_reads": complete, "passed": bool(complete)})
        chains = []
        delivered_runs = {run_id for item in deliveries for run_id in item["run_ids"]}
        for run_id, objects in restored.items():
            if deliveries and run_id not in delivered_runs:
                continue
            quest = c.execute("SELECT metadata FROM quests WHERE id=?", (runs[run_id]["quest_id"],)).fetchone()
            message_id = json.loads(quest[0]).get("original_request_message_id")
            if not message_id:
                message = c.execute(
                    "SELECT source_message_id FROM delegation_mandates WHERE quest_id=?",
                    (runs[run_id]["quest_id"],),
                ).fetchone()
                message_id = message[0] if message else None
            chain = relation_chain(objects, "message:" + message_id) if message_id else []
            if chain:
                chains.append({"run_id": run_id, "quest_id": runs[run_id]["quest_id"],
                               "references": chain, "sources": [objects[ref] for ref in chain]})
        return {
            "provenance": "programmatic_verification_of_real_worker_tool_proofs_and_transcripts",
            "after_evidence_rowid": after_evidence_rowid,
            "thread_consumption": thread,
            "complete_thread_consumption": bool(thread) and all(item["passed"] for item in thread),
            "complete_relationship_chains": chains,
            "typed_multihop_recall": bool(chains),
            "invalid_or_unconsumed_calls": invalid,
            "worker_archive_source_recovery_assessed": False,
            "limitations": "Source delivery to later model turns is verified; semantic understanding is not. "
            "Host Router compaction and host relation enumeration do not establish Worker archive recovery.",
        }
