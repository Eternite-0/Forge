"""Offline retrieval evaluation with source IDs and current-source hashes.

Run from backend with .venv/Scripts/python.exe scripts/phase2/retrieval_evaluation.py
--output <new-report.json>. No Provider, credentials or network are accessed.
"""

import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path

from forge_core.constraints import ConstraintService
from forge_core.domain import ConversationMessage, Quest
from forge_core.memory import MemoryService
from forge_core.settings import Workspace
from forge_core.store import Store

DATASET = Path(__file__).resolve().parents[2] / "tests/fixtures/phase2_retrieval_v1.json"


def evaluate(dataset_path=DATASET):
    started = time.perf_counter()
    raw = Path(dataset_path).read_bytes()
    dataset = json.loads(raw)
    results = []
    with tempfile.TemporaryDirectory(prefix="forge-retrieval-eval-") as temporary:
        store = Store(workspace=Workspace.from_path(temporary))
        quests = {}
        for row in dataset["quests"]:
            quest = Quest(
                id=row["id"],
                title=row["title"],
                goal=row["goal"],
                created_at=dataset["recorded_at"],
                updated_at=dataset["recorded_at"],
            )
            store.save_quest(quest)
            quests[row["key"]] = quest
        constraints = ConstraintService(store)
        for row in dataset["messages"]:
            store.add_message(
                ConversationMessage(
                    id=row["id"],
                    role="user",
                    content=row["content"],
                    created_at=dataset["recorded_at"],
                )
            )
            constraints.index_message(row["id"], quest_id=quests[row["quest"]].id)
        memory = MemoryService(store)
        while memory.sync_sources()["has_more"]:
            pass
        # Reopen the database to evaluate retrieval after Core storage restart.
        recovered = MemoryService(Store(store.path, workspace=store.workspace))
        expected_hashes = {}
        with store.connection() as connection:
            for source in connection.execute("SELECT * FROM messages"):
                encoded = json.dumps(
                    dict(source), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                expected_hashes[f"message:{source['id']}"] = hashlib.sha256(
                    encoded.encode("utf-8")
                ).hexdigest()
            for source in connection.execute("SELECT * FROM quests"):
                encoded = json.dumps(
                    {
                        "title": source["title"],
                        "goal": source["goal"],
                        "created_at": source["created_at"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                expected_hashes[f"quest:{source['id']}"] = hashlib.sha256(
                    encoded.encode("utf-8")
                ).hexdigest()
        for case in dataset["cases"]:
            start = time.perf_counter()
            response = recovered.retrieve_context(
                case["query"],
                quest_ids=case.get("quest_ids", []),
                source_types=case.get("source_types", []),
                created_before=case.get("created_before"),
                limit=20,
                max_bytes=16384,
            )
            sources = {ref for item in response["items"] for ref in item["source_refs"]}
            matches = set(case["expected_sources"]) <= sources
            if case.get("expect_empty"):
                matches = not response["items"]
            returned_hashes = {
                ref: item.get("source_hash")
                for item in response["items"]
                for ref in item["source_refs"]
            }
            hash_complete = all(
                ref in expected_hashes and digest == expected_hashes[ref]
                for ref, digest in returned_hashes.items()
            )
            results.append(
                {
                    "case_id": case["id"],
                    "expected_sources": case["expected_sources"],
                    "returned_sources": sorted(sources),
                    "passed": matches and hash_complete,
                    "source_hash_complete": hash_complete,
                    "returned_source_hashes": returned_hashes,
                    "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                    "returned_bytes": response["bytes"],
                    "omitted": response["omitted"],
                }
            )
    passed = sum(case["passed"] for case in results)
    return {
        "dataset": dataset["version"],
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "execution_mode": "synthetic",
        "retrieval_method": "bounded lexical index with bilingual alias expansion",
        "model_calls": 0,
        "source_identity_cases": results,
        "passed": passed,
        "total": len(results),
        "success_rate": passed / len(results),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "limitations": [
            "Nine exploratory fixed synthetic cases; not a held-out generalization result.",
            "Measures source identity and provenance after storage restart, not domain truth.",
            "Alias expansion is limited lexical retrieval, not embedding or learned semantic retrieval.",
            "No real Provider calls or user task quality measurements.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(
        json.dumps(
            {"passed": report["passed"], "total": report["total"], "output": str(args.output)}
        )
    )
    raise SystemExit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
