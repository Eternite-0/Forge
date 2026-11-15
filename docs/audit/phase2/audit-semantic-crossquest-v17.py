"""Independent read-only interpretation of the one retained semantic probe.

No FORGE imports, Store construction, model calls, retries or result rewriting.
"""

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / ".local/phase2/semantic-crossquest-v17-fixed-01"
OUTPUT = Path(__file__).with_name("semantic-crossquest-v17-independent.json")


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    if OUTPUT.exists() or OUTPUT.with_suffix(".md").exists():
        raise FileExistsError("Independent outputs are immutable")
    fixed_path, result_path = ROOT / "fixed-input.json", ROOT / "result.json"
    fixed, result = json.loads(fixed_path.read_bytes()), json.loads(result_path.read_bytes())
    database = ROOT / "data/forge.sqlite3"
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    messages = {row["id"]: dict(row) for row in db.execute("SELECT * FROM messages")}
    constraints = [dict(row) for row in db.execute("SELECT * FROM constraint_records")]
    memories = [dict(row) for row in db.execute("SELECT * FROM memories")]
    candidates, provenance, row_hashes = {}, [], {}
    target = fixed["records"][0]
    for row in constraints:
        source = messages.get(row["source_message_id"])
        valid = bool(source) and source["role"] == "user" and source["conversation_id"] == "main"
        valid = valid and sha(canonical(source)) == row["source_sha256"]
        valid = valid and row["statement"] in source["content"] and row["status"] == "active"
        valid = valid and row["origin"] == "explicit_user" and row["valid_until"] is None
        valid = valid and row["confirmation_message_id"] is None
        valid = valid and not db.execute(
            "SELECT 1 FROM revoked_memory_sources WHERE source_ref=?", ("message:" + source["id"],)
        ).fetchone()
        expected_id = sha(canonical([row["source_message_id"], row["quest_id"], row["dimension"], row["statement"], row["origin"]]))
        valid = valid and row["id"] == expected_id
        conflicted = any(other["id"] != row["id"] and other["status"] == "active"
                         and other["dimension"] == row["dimension"] and other["quest_id"] == row["quest_id"]
                         and other["statement"] != row["statement"] for other in constraints)
        ref = "constraint:" + row["id"]
        item = {
            "id": ref, "statement": row["statement"], "kind": "constraint",
            "layer": "L1" if row["quest_id"] else "L2", "status": "hypothesis",
            "confirmation_status": row["origin"], "source_refs": ["message:" + source["id"]],
            "source_hash": row["source_sha256"], "created_at": row["created_at"], "valid_until": None,
            "related_quest_ids": [row["quest_id"]] if row["quest_id"] else [], "key": row["dimension"],
            "freshness": "unknown", "conflicted": conflicted, "source_type": "user_instruction",
            "trust_scope": "user instruction only; no tool permission or domain truth",
        }
        candidates[ref] = item
        row_hashes[ref] = sha(canonical(row))
        provenance.append({"ref": ref, "source_message_id": source["id"], "source_row_sha256": sha(canonical(source)),
                           "source_row_bytes": len(canonical(source)), "constraint_row_sha256": row_hashes[ref], "valid": bool(valid)})
    for row in memories:
        refs, quests = json.loads(row["source_refs"]), json.loads(row["related_quest_ids"])
        valid = row["status"] == "hypothesis" and row["valid_until"] is None and row["memory_key"] is None
        valid = valid and len(refs) == 1 and refs[0].startswith("message:") and refs[0][8:] in messages
        source = messages[refs[0][8:]]
        valid = valid and row["statement"] == source["content"]
        valid = valid and not db.execute("SELECT 1 FROM revoked_memory_sources WHERE source_ref=?", (refs[0],)).fetchone()
        ref = "memory:" + row["id"]
        candidates[ref] = {"id": row["id"], "statement": row["statement"], "kind": row["kind"], "layer": row["layer"],
                           "status": row["status"], "source_refs": refs, "created_at": row["created_at"], "valid_until": None,
                           "related_quest_ids": quests, "key": None, "freshness": "unknown", "conflicted": False}
        row_hashes[ref] = sha(canonical(row))
        provenance.append({"ref": ref, "source_message_id": source["id"], "source_row_sha256": sha(canonical(source)),
                           "source_row_bytes": len(canonical(source)), "memory_row_sha256": row_hashes[ref], "valid": bool(valid)})
    selectors = (("memory_source_index", "source_ref", ""), ("memories", "id", "memory:"),
                 ("constraint_records", "id", "constraint:"), ("constraint_message_index", "message_id", "message:"))
    indexed_refs = sorted({prefix + row[0] for table, key, prefix in selectors
                           for row in db.execute(f"SELECT {key} FROM {table} WHERE {key}>'' ORDER BY {key} LIMIT 25")})[:25]
    selected_refs, selected_checks = [], []
    for item in result["result"]["items"]:
        matches = [ref for ref, expected in candidates.items() if expected == item]
        ref = matches[0] if len(matches) == 1 else None
        selected_refs.append(ref)
        original = item["source_refs"] == ["message:" + target["source_message_id"]]
        selected_checks.append({"ref": ref, "exact_reconstructed_item": len(matches) == 1,
                                "target_source_only": original, "row_sha256": row_hashes.get(ref)})
    message_checks = {ident: sha(canonical(messages[ident])) == digest and messages[ident] == fixed["source_messages"][ident]
                      for ident, digest in fixed["source_message_row_sha256"].items()}
    terms = re.findall(r"[a-z0-9_./\\:-]+|[a-z0-9_]+", fixed["query"].casefold())
    for run in re.findall(r"[\u4e00-\u9fff]+", fixed["query"]):
        terms.extend(run[index:index + 2] for index in range(len(run) - 1))
    lexical = sorted({row[0] for term in set(terms) for row in db.execute("SELECT ref FROM memory_terms WHERE term=?", (term,))})
    states = {row["id"]: row["status"] for row in db.execute("SELECT id,status FROM quests")}
    attempts = [dict(row) for row in db.execute("SELECT * FROM model_budget_attempts ORDER BY created_at")]
    probe = result["result"]["semantic"]
    snapshot = sha(canonical(candidates))
    checks = {
        "original_probe_result_preserved_false": result["passed"] is False,
        "candidate_rows_valid": all(item["valid"] for item in provenance),
        "all_candidates_on_original_bounded_index_page": set(candidates) <= set(indexed_refs[:24]),
        "six_candidate_snapshot_exact": len(candidates) == probe["candidates"] == 6 and snapshot == probe["source_snapshot_sha256"],
        "all_four_fixture_snapshots_unchanged": all(candidates.get(ref) == item for ref, item in fixed["candidate_snapshots"].items()),
        "all_three_selected_items_exact_target_sources": len(selected_refs) == probe["selected"] == 3 and len(set(selected_refs)) == 3
        and all(item["exact_reconstructed_item"] and item["target_source_only"] for item in selected_checks),
        "source_messages_exact_row_bytes_and_hashes": all(message_checks.values()),
        "both_seeded_quests_still_proposed": len(states) == 2 and set(states.values()) == {"proposed"},
        "lexical_index_has_no_match": not lexical and not fixed["lexical_baseline"]["items"],
        "one_settled_call_with_exact_usage": len(attempts) == result["model_calls"] == 1 and attempts[0]["state"] == "settled"
        and attempts[0]["call_id"] == probe["call_id"] and (attempts[0]["input_charged"], attempts[0]["output_charged"]) == (1713, 113)
        and result["token_usage"] == {"input": 1713, "output": 113, "unknown_attempts": 0},
    }
    changes = db.total_changes
    db.rollback()
    db.close()
    checks["read_snapshot_never_mutated"] = changes == 0
    omitted_from_fixture = sorted(set(candidates) - set(fixed["candidate_snapshots"]))
    report = {"generated_at": datetime.now(UTC).isoformat(), "audit_script_sha256": sha(Path(__file__).read_bytes()),
              "read_mode": "SQLite URI mode=ro; PRAGMA query_only=ON; explicit BEGIN", "read_snapshot_total_changes": changes,
              "original_result_sha256": sha(result_path.read_bytes()), "fixed_input_sha256": sha(fixed_path.read_bytes()), "database_sha256": sha(database.read_bytes()),
              "original_probe_passed": result["passed"], "independent_source_retrieval_checks_passed": all(checks.values()), "checks": checks,
              "original_oracle_defect": "Fixture candidate_snapshots/expected_refs enumerate four manually seeded refs. sync_sources also indexed two literal global constraints. Semantic selection used the actual six-item page; the selected target global constraint was absent from the fixture map and converted to null by verify().",
              "candidate_snapshot_sha256": snapshot, "fixture_candidate_count": len(fixed["candidate_snapshots"]), "actual_candidate_count": len(candidates),
              "refs_omitted_from_fixture": omitted_from_fixture, "returned_refs_independently_resolved": selected_refs,
              "selected_checks": selected_checks, "candidate_provenance": provenance, "source_message_checks": message_checks, "quest_states": states,
              "limitations": ["This new independent interpretation preserves the original false verdict and does not rewrite or relabel it.",
                              "One bounded live semantic-selection sample with two seeded proposed Quests; no successful autonomous execution, multi-Quest execution, generalized retrieval quality or upstream model identity is established.",
                              "Semantic ranking returns duplicate representations of the same source. Distinct IDs are valid, but no source-diversity or deduplication claim is made."]}
    with OUTPUT.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with OUTPUT.with_suffix(".md").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("# Independent semantic probe interpretation — V17\n\n"
                     + f"Original probe result remains false. Independent source and retrieval checks {'pass' if all(checks.values()) else 'fail'}.\n\n"
                     + "The original oracle listed four manually seeded records; automatic source sync produced two additional literal global constraints. The live selector used six candidates and returned three exact records from the target user message. The additional selected global constraint was missing from the fixture map, causing its null identity and three false oracle checks.\n\n"
                     + f"All six candidate rows and both original message row bytes/hashes were rechecked. Candidate snapshot SHA-256: `{snapshot}`. One settled call used 1,826 tokens, with no unknown usage. Read snapshot total_changes={changes}.\n\n"
                     + "This is one exploratory semantic-selection sample over two seeded proposed Quests. It does not establish successful task execution or general memory quality. The original result and failed oracle are preserved.\n")
    print(json.dumps({"output": str(OUTPUT), "checks_passed": all(checks.values()), "snapshot": snapshot, "changes": changes}))


if __name__ == "__main__":
    main()
