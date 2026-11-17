"""Offline page/proof fixtures; a live-tagged script is not Provider evidence."""

import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_verify_superagent_capability import driver
from test_worker_intelligence_phase2 import ProtocolModel, delivery, response, tool

from forge_core.provider_pool import ProviderPool
from forge_core.superagent_events import TriggerService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager
from forge_core.workspace_discovery import WorkspaceDiscovery

_path = Path(__file__).resolve().parents[1] / "scripts" / "capability_continuity_evidence.py"
_spec = importlib.util.spec_from_file_location("capability_continuity_evidence_test", _path)
evidence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evidence)


def test_standalone_specialists_keeps_same_l2_and_requires_actual_distinct_roles():
    assert driver.scenario_levels("specialists") == driver.scenario_levels("single") == ["L2"]
    assert driver.scenario_levels("continuity") == ["L2", "L3"]
    observation = {
        "execution_team": None,
        "actual_worker_count": 2,
        "actual_roles": ["research_1", "research_2"],
    }
    assert not driver.strategy_observed(observation, "specialists")
    observation["actual_roles"] = ["research_1", "architect_2"]
    assert driver.strategy_observed(observation, "specialists")
    with pytest.raises(ValueError):
        driver.scenario_levels("unbounded")


def page(text, start, end, *, ordinal=1, byte_offsets=False):
    body = text.encode() if byte_offsets else text
    selected = body[start:end]
    selected = selected.decode() if byte_offsets else selected
    return {
        "offset": start,
        "end": end,
        "text": selected,
        "total": len(body),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "complete": end == len(body),
        "invocation_ordinal": ordinal,
        "result_ordinal": ordinal + 1,
        "call_id": "call-" + str(ordinal),
    }


@pytest.mark.parametrize("byte_offsets", [False, True])
def test_full_pages_require_every_character_and_original_hash(byte_offsets):
    text = "original 中文 continuation"
    length = len(text.encode()) if byte_offsets else len(text)
    pages = [
        page(text, 0, 9, byte_offsets=byte_offsets),
        page(text, 9, length, ordinal=3, byte_offsets=byte_offsets),
    ]
    result = evidence.complete_pages(pages, byte_offsets=byte_offsets)
    assert result["source_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert result["page_count"] == 2
    assert evidence.complete_pages(pages[:1], byte_offsets=byte_offsets) is None
    assert evidence.complete_pages([pages[0], pages[0]], byte_offsets=byte_offsets) is None


@pytest.mark.parametrize(
    "tamper", ["gap", "overlap", "hash", "text", "length", "batch", "complete"]
)
def test_partial_forged_overlapping_or_same_turn_pages_do_not_pass(tamper):
    text = "original complete body"
    pages = [page(text, 0, 8), page(text, 8, len(text), ordinal=3)]
    if tamper == "gap":
        pages[1] = page(text, 9, len(text), ordinal=3)
    elif tamper == "overlap":
        pages[1] = page(text, 7, len(text), ordinal=3)
    elif tamper == "hash":
        pages[1]["sha256"] = "0" * 64
    elif tamper == "text":
        pages[1]["text"] = "x" + pages[1]["text"][1:]
    elif tamper == "length":
        pages[1]["total"] += 1
    elif tamper == "batch":
        pages[1]["invocation_ordinal"] = pages[0]["invocation_ordinal"]
    else:
        pages[1]["complete"] = False
    assert evidence.complete_pages(pages, byte_offsets=False) is None


def test_relation_chain_requires_returned_refs_and_later_turns():
    objects = {
        "message:m": {"relations": ["project:p"], "first_invocation": 1, "last_result": 2},
        "project:p": {"relations": ["quest:q"], "first_invocation": 3, "last_result": 4},
        "quest:q": {"relations": [], "first_invocation": 5, "last_result": 6},
    }
    assert evidence.relation_chain(objects, "message:m") == ["message:m", "project:p", "quest:q"]
    objects["project:p"]["first_invocation"] = 1
    assert not evidence.relation_chain(objects, "message:m")
    objects["project:p"]["first_invocation"] = 3
    objects["project:p"]["relations"] = []
    assert not evidence.relation_chain(objects, "message:m")


@pytest.fixture
def read_trigger_pages(delegated):
    store, quest_id, _mandate, graph, teams, service = delegated
    assert service.prepare(quest_id)

    class OfflineLiveProtocol(ProtocolModel):
        execution_mode = "live"

    model = OfflineLiveProtocol([])
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals").fetchone()
    run = manager.start(
        quest_id, plan["id"], "task", team_digest=team["proposal_digest"], member_index=0
    )
    triggers = TriggerService(store)
    triggers.register_source("continuity-fixture", kind="message")
    trigger = triggers.receive("continuity-fixture", "1", "task", {"text": "中文 " * 250})
    triggers.subscribe(trigger["thread_id"], run["id"])
    model.responses.extend(
        [
            tool(
                "read_trigger", {"trigger_id": trigger["id"], "offset_chars": 0, "max_chars": 500}
            ),
            tool(
                "read_trigger",
                {"trigger_id": trigger["id"], "offset_chars": 500, "max_chars": 2048},
                2,
            ),
            response(delivery()),
        ]
    )
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    deliveries = [
        {
            "level": "L3",
            "phase": "after_restart",
            "trigger_id": trigger["id"],
            "run_ids": [run["id"]],
        }
    ]
    return store, run, trigger, deliveries


def test_actual_core_proofs_and_transcripts_allow_only_complete_same_run_pages(read_trigger_pages):
    store, run, _trigger, deliveries = read_trigger_pages
    result = evidence.observe(store.path, store.workspace.root, {"L3": run["quest_id"]}, deliveries)
    assert result["complete_thread_consumption"]
    assert result["thread_consumption"][0]["complete_reads"][0]["page_count"] == 2
    assert not result["typed_multihop_recall"]
    with store.connection() as c:
        frontier = c.execute("SELECT MAX(rowid) FROM evidence").fetchone()[0]
    assert not evidence.observe(
        store.path,
        store.workspace.root,
        {"L3": run["quest_id"]},
        deliveries,
        after_evidence_rowid=frontier,
    )["complete_thread_consumption"]
    rebound = copy.deepcopy(deliveries)
    rebound[0]["run_ids"] = ["another-run"]
    assert not evidence.observe(store.path, store.workspace.root, {"L3": run["quest_id"]}, rebound)[
        "complete_thread_consumption"
    ]


@pytest.mark.parametrize("tamper", ["proof", "partial", "transcript", "unconsumed", "payload"])
def test_stored_success_label_cannot_replace_source_proof_or_full_consumption(
    read_trigger_pages, tamper
):
    store, run, trigger, deliveries = read_trigger_pages
    with store.unit_of_work() as work:
        c = work.connection
        if tamper == "proof":
            c.execute("UPDATE evidence SET trusted=0 WHERE kind='worker_core_source'")
        elif tamper == "partial":
            c.execute("DELETE FROM worker_tool_calls WHERE model_call_id='tool-2'")
        elif tamper == "transcript":
            c.execute("DELETE FROM worker_messages WHERE role='tool'")
        elif tamper == "unconsumed":
            c.execute(
                "DELETE FROM worker_messages WHERE role='assistant' AND COALESCE(json_array_length(json_extract(message_json,'$.tool_calls')),0)=0"
            )
        else:
            c.execute(
                "UPDATE superagent_triggers SET payload_json='{}' WHERE id=?", (trigger["id"],)
            )
    assert not evidence.observe(
        store.path, store.workspace.root, {"L3": run["quest_id"]}, deliveries
    )["complete_thread_consumption"]


def test_real_scripted_worker_traverses_only_returned_relations_and_recovers_full_hashes(delegated):
    store, quest_id, _mandate, graph, teams, service = delegated
    project_root = store.workspace.root / "project"
    project_root.mkdir()
    project = WorkspaceDiscovery(store).associate(quest_id, project_root)
    with store.unit_of_work() as work:
        source = work.connection.execute(
            "SELECT source_message_id FROM delegation_mandates"
        ).fetchone()[0]
        work.connection.execute(
            "UPDATE messages SET metadata=json_set(metadata,'$.project_id',?) WHERE id=?",
            (project["id"], source),
        )
    assert service.prepare(quest_id)

    class OfflineLiveProtocol(ProtocolModel):
        execution_mode = "live"

    model = OfflineLiveProtocol([])
    manager = WorkerManager(
        store, ProviderPool({"mock-provider": model}), ToolEngine(store), team_authority=teams
    )
    plan = graph.get(quest_id)
    with store.connection() as c:
        team = c.execute("SELECT * FROM team_proposals").fetchone()
    run = manager.start(
        quest_id, plan["id"], "task", team_digest=team["proposal_digest"], member_index=0
    )
    hops = []

    async def generate(request):
        model.requests.append(request)
        with store.connection() as c:
            previous = c.execute(
                "SELECT result_json FROM worker_tool_calls WHERE run_id=? AND state='succeeded' ORDER BY rowid DESC LIMIT 1",
                (run["id"],),
            ).fetchone()
        if previous is None:
            arguments = {"reference": "message:" + source}
        else:
            result = json.loads(previous[0])
            if not result["complete"]:
                arguments = {
                    "reference": result["ref"],
                    "offset": result["next_offset"],
                    "expected_hash": result["source_hash"],
                }
            else:
                hops.append(result["ref"])
                if len(hops) == 3:
                    return response(delivery())
                prefix = "project:" if len(hops) == 1 else "quest:"
                reference = next(
                    item["ref"] for item in result["relations"] if item["ref"].startswith(prefix)
                )
                arguments = {"reference": reference}
        return tool("recall_object", arguments, len(model.requests))

    model.generate = generate
    assert asyncio.run(manager.run(run["id"]))["state"] == "review_pending"
    result = evidence.observe(store.path, store.workspace.root, {"L3": quest_id}, [])
    assert result["typed_multihop_recall"]
    assert result["complete_relationship_chains"][0]["references"] == hops
    assert len(hops) == 3
    assert not result["worker_archive_source_recovery_assessed"]
    published = json.dumps(result)
    assert "Explain the inputs" not in published and str(project_root) not in published
