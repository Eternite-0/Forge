"""Offline scripted models with real files, fixed checks and native Core reads.

The live fixture tag exercises provenance gates; no real Provider is contacted
and these tests are not live model-quality or acceptance evidence.
"""

import asyncio
import hashlib
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_receipt_read_repair_phase2 import Protocol
from test_review_envelope_phase2 import envelope
from test_worker_intelligence_phase2 import delivery, response, tool

from forge_core.domain import DomainError, ModelProfile, ProtocolSupport, Quest
from forge_core.evidence import CompletionGate, NodeVerifier, _verify_worker_context_call
from forge_core.governance import GovernanceService
from forge_core.independent_review import (
    IndependentReviewService,
    _hash,
    _packet,
    review_pass_matches,
)
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.predecessor_delivery import (
    HANDOFF_METADATA_BYTES,
    MAX_HANDOFF_BYTES,
    TOOL_NAME,
    _success_ancestor,
    canonical,
    read_completed_delivery,
)
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.quest_graph import QuestGraphService
from forge_core.review_envelope import (
    ReviewEnvelopeError,
    review_packet_sizes,
    validate_review_packet,
)
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager
from forge_core.worker_schema import DELIVERY_FIELDS


def prepared(
    tmp_path,
    *,
    summary="Whole original analysis: 中文 and evidence.",
    reader_criteria=None,
    reader_write=True,
    pending_tail=False,
    producer_model_review=False,
    ancestor_bridge=False,
):
    store = Store(tmp_path / "core.sqlite3", workspace=Workspace.from_path(tmp_path))
    (tmp_path / "work").mkdir()
    (tmp_path / "work/input.json").write_text('{"source":"original"}', encoding="utf-8")
    quest = Quest(
        title="Completed delivery fixture", goal="Read the complete verified predecessor."
    )
    store.save_quest(quest)
    graphs = QuestGraphService(store)
    raw = {
        "nodes": [
            {
                "key": "producer",
                "objective": "Read the actual source and produce an analysis.",
                "success_criteria": ["delivery_review"]
                if producer_model_review
                else ["json_valid:work/source.json"],
                "budget_tokens": 60000,
                "allowed_tools": ["file_read", "file_write"],
                "capacity_bounds": {
                    "max_source_receipt_bytes": 4096,
                    "max_artifact_bytes": 4096,
                    "max_delivery_bytes": 2048,
                },
            },
            {
                "key": "reader",
                "objective": "Read all predecessor claims and write a checked summary.",
                "success_criteria": reader_criteria or ["json_valid:work/review.json"],
                "budget_tokens": 60000,
                "allowed_tools": [TOOL_NAME, "file_write"] if reader_write else [TOOL_NAME],
                "required_predecessor_deliveries": ["producer"],
            },
        ],
        "edges": [{"source": "producer", "target": "reader", "condition": "success"}],
        "max_total_tokens": 155000,
    }
    if pending_tail:
        raw["nodes"].append(
            {
                "key": "tail",
                "objective": "Wait for separately approved remaining work.",
                "success_criteria": ["json_valid:work/tail.json"],
                "budget_tokens": 35000,
                "allowed_tools": ["file_write"],
            }
        )
        raw["edges"].append({"source": "reader", "target": "tail", "condition": "success"})
    if ancestor_bridge:
        raw["nodes"].append(
            {
                "key": "bridge",
                "objective": "Produce the intermediate architecture evidence.",
                "success_criteria": ["json_valid:work/bridge.json"],
                "budget_tokens": 35000,
                "allowed_tools": ["file_read", "file_write"],
            }
        )
        raw["edges"].remove({"source": "producer", "target": "reader", "condition": "success"})
        raw["edges"].extend(
            [
                {"source": "producer", "target": "bridge", "condition": "success"},
                {"source": "bridge", "target": "reader", "condition": "success"},
            ]
        )
    plan = graphs.propose(quest.id, raw, expected_version=0)
    graphs.approve(quest.id, plan["digest"], 1, expected_version=1, actor="local_user")
    store.save_model(
        ModelProfile(
            id="protocol",
            provider="protocol",
            model_id="fixture",
            display_name="Offline handoff fixture",
            context_window=65536,
            max_output_tokens=4000,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    teams = TeamCompositionService(store)
    team = propose_team(store, teams, quest, plan, include_producer=True, reader_write=reader_write)
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    expected = delivery(summary)
    expected["findings"] = ["UNTRUSTED_SOURCE_SENTINEL", "Retain every finding."]
    model = Protocol(
        [
            tool("file_read", {"path": "work/input.json"}),
            tool(
                "file_write",
                {"path": "work/source.json", "content": '{"analysis":"evidence"}'},
                ordinal=2,
            ),
            response(expected),
        ]
    )
    manager = WorkerManager(
        store, ProviderPool({"protocol": model}), ToolEngine(store), team_authority=teams
    )
    producer = manager.start(
        quest.id, plan["id"], "producer", team_digest=team["proposal_digest"], member_index=0
    )
    assert asyncio.run(manager.run(producer["id"]))["state"] == "review_pending"
    if producer_model_review:

        def pass_response(_body, _ordinal):
            return {
                "content": canonical(
                    {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
                )
            }

        with offline_loopback_gateway(store, pass_response) as (gateway, _requests):
            manager.providers = ProviderPool({"protocol": gateway})
            assert (
                asyncio.run(IndependentReviewService(manager).review(producer["id"]))["verdict"]
                == "PASS"
            )
        manager.providers = ProviderPool({"protocol": model})
    NodeVerifier(store).verify(quest.id, "producer")
    assert manager.get(producer["id"])["state"] == "succeeded"
    if ancestor_bridge:
        model.responses.extend(
            [
                tool("file_read", {"path": "work/input.json"}),
                tool(
                    "file_write",
                    {"path": "work/bridge.json", "content": '{"architecture":"checked"}'},
                    ordinal=2,
                ),
                response(delivery("Intermediate architecture is checked.")),
            ]
        )
        bridge = manager.start(
            quest.id,
            plan["id"],
            "bridge",
            team_digest=team["proposal_digest"],
            member_index=next(
                index
                for index, member in enumerate(team["members"])
                if member["task_node_key"] == "bridge"
            ),
        )
        assert asyncio.run(manager.run(bridge["id"]))["state"] == "review_pending"
        NodeVerifier(store).verify(quest.id, "bridge")
    reader = manager.start(
        quest.id, plan["id"], "reader", team_digest=team["proposal_digest"], member_index=1
    )
    return store, manager, model, quest, plan, producer, reader, expected


def propose_team(store, teams, quest, plan, *, include_producer, reader_write=True):
    reader_tools = [TOOL_NAME, "file_write"] if reader_write else [TOOL_NAME]
    nodes = (
        [
            ("producer", "coding", ["file_read", "file_write"]),
            ("reader", "reviewer", reader_tools),
        ]
        if include_producer
        else [("reader", "reviewer", reader_tools)]
    )
    if include_producer:
        with store.connection() as connection:
            if connection.execute(
                "SELECT 1 FROM task_nodes WHERE plan_id=? AND node_key='tail'", (plan["id"],)
            ).fetchone():
                nodes.append(("tail", "architect", ["file_write"]))
            if connection.execute(
                "SELECT 1 FROM task_nodes WHERE plan_id=? AND node_key='bridge'", (plan["id"],)
            ).fetchone():
                nodes.append(("bridge", "architect", ["file_read", "file_write"]))
    team = teams.propose(
        quest.id,
        [
            {
                "id": role,
                "task_node_key": key,
                "tools": tools,
                "input_tokens": 32000,
                "output_tokens": 3000,
                "max_steps": 8,
                "max_tool_calls": 6,
                "max_duration_seconds": 120,
            }
            for key, role, tools in nodes
        ],
        {
            "max_members": len(nodes),
            "max_depth": 0,
            "max_concurrency": 1,
            "max_total_tokens": 35000 * len(nodes),
            "max_total_tool_calls": 6 * len(nodes),
            "allowed_tools": sorted({name for _, _, tools in nodes for name in tools}),
        },
        strategy="greedy",
        expected_version=store.get_quest(quest.id)["version"],
    )
    teams.approve(
        quest.id,
        plan["plan_revision"],
        team["proposal_digest"],
        expected_version=store.get_quest(quest.id)["version"],
        actor="local_user",
    )
    return team


def read(store, reader, *, historical=False):
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        before = connection.total_changes
        result = read_completed_delivery(
            store, connection, reader["id"], {"node_key": "producer"}, historical=historical
        )
        assert connection.total_changes == before == 0
        return result


def successor(store, manager, quest, parent, *, new_reader):
    with store.connection() as connection:
        raw = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE id=?", (parent["id"],)
            ).fetchone()[0]
        )
    graphs = QuestGraphService(store)
    proposal = graphs.propose_remaining(
        quest.id, raw, expected_version=store.get_quest(quest.id)["version"]
    )
    graphs.approve(
        quest.id,
        proposal["digest"],
        proposal["plan_revision"],
        expected_version=store.get_quest(quest.id)["version"],
        actor="local_user",
    )
    if not new_reader:
        return proposal, None
    teams = TeamCompositionService(store)
    team = propose_team(store, teams, quest, proposal, include_producer=False)
    manager.team_authority = teams
    GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
    reader = manager.start(
        quest.id, proposal["id"], "reader", team_digest=team["proposal_digest"], member_index=0
    )
    return proposal, reader


def test_full_original_delivery_reads_without_mutation_and_uses_declared_bound(tmp_path):
    store, manager, _, _, _, producer, reader, expected = prepared(tmp_path)
    manager._claim(reader["id"])
    result = read(store, reader)
    assert result["delivery"] == expected and set(result["delivery"]) == set(DELIVERY_FIELDS)
    assert result["producer_run_id"] == producer["id"] and result["truncated"] is False
    assert result["delivery_sha256"] == hashlib.sha256(canonical(expected).encode()).hexdigest()
    assert len(canonical(result).encode()) <= min(MAX_HANDOFF_BYTES, 2048 + HANDOFF_METADATA_BYTES)
    assert result["trust"].startswith("untrusted")


@pytest.mark.parametrize("historical", [False, True])
def test_explicit_success_path_ancestor_retains_the_full_original_delivery(tmp_path, historical):
    store, manager, _, _, plan, producer, reader, expected = prepared(
        tmp_path, ancestor_bridge=True
    )
    manager._claim(reader["id"])
    result = read(store, reader, historical=historical)
    assert result["delivery"] == expected and result["producer_run_id"] == producer["id"]
    assert result["truncated"] is False
    assert len(canonical(result).encode()) <= min(MAX_HANDOFF_BYTES, 2048 + HANDOFF_METADATA_BYTES)
    with store.connection() as connection:
        assert not connection.execute(
            "SELECT 1 FROM task_edges WHERE plan_id=? AND source='producer' AND target='reader'",
            (plan["id"],),
        ).fetchone()


@pytest.mark.parametrize(
    "mutation", ["nonancestor", "failure_path", "unfinished_intermediate", "persisted_edge"]
)
def test_ancestor_handoff_rejects_nonancestor_non_success_or_unfinished_paths(tmp_path, mutation):
    store, manager, _, _, plan, _, reader, _ = prepared(tmp_path, ancestor_bridge=True)
    manager._claim(reader["id"])
    with store.unit_of_work() as work:
        if mutation == "unfinished_intermediate":
            work.connection.execute(
                "UPDATE task_nodes SET status='failed' WHERE plan_id=? AND node_key='bridge'",
                (plan["id"],),
            )
        elif mutation == "persisted_edge":
            work.connection.execute(
                "DELETE FROM task_edges WHERE plan_id=? AND source='producer'", (plan["id"],)
            )
        elif mutation == "failure_path":
            work.connection.execute(
                "UPDATE task_edges SET condition='failure' WHERE plan_id=? AND source='producer'",
                (plan["id"],),
            )
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM quest_plans WHERE id=?", (plan["id"],)).fetchone()
        graph = json.loads(row["graph_json"])
        source = "reader" if mutation == "nonancestor" else "producer"
        target = "producer" if mutation == "nonancestor" else "reader"
        if mutation == "failure_path":
            graph["edges"][0]["condition"] = "failure"
        with pytest.raises(DomainError):
            _success_ancestor(connection, row, graph, source, target)
        assert connection.total_changes == 0


def test_current_reader_modes_do_not_authorize_execution_after_delivery(tmp_path):
    store, manager, _, _, _, _, reader, _ = prepared(tmp_path)
    with pytest.raises(DomainError):
        read(store, reader)
    manager._claim(reader["id"])
    original = read(store, reader)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET state='review_pending' WHERE id=?", (reader["id"],)
        )
        work.connection.execute(
            "UPDATE task_nodes SET status='review_pending' WHERE worker_id=?",
            (reader["worker_id"],),
        )
    assert read(store, reader, historical=True) == original
    with pytest.raises(DomainError):
        read(store, reader)


def test_running_successor_reads_exact_preserved_producer_after_remaining_dag(tmp_path):
    store, manager, _, quest, plan, producer, _, expected = prepared(tmp_path)
    revised, reader = successor(store, manager, quest, plan, new_reader=True)
    manager._claim(reader["id"])
    result = read(store, reader)
    assert revised["plan_revision"] == 2
    assert result["producer_plan_revision"] == 1 and result["producer_run_id"] == producer["id"]
    assert result["delivery"] == expected


def test_real_core_read_tool_message_and_historical_reader_revalidate_after_lineage(tmp_path):
    store, manager, model, quest, plan, _, reader, expected = prepared(tmp_path, pending_tail=True)
    model.responses.extend(
        [
            tool(TOOL_NAME, {"node_key": "producer"}),
            tool(
                "file_write", {"path": "work/review.json", "content": '{"checked":true}'}, ordinal=2
            ),
            response(delivery("Read the complete predecessor.")),
        ]
    )
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    with store.connection() as connection:
        connection.execute("BEGIN")
        call = connection.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND tool_name=?",
            (reader["id"], TOOL_NAME),
        ).fetchone()
        assert _verify_worker_context_call(store, connection, reader["id"], call)
        assert json.loads(call["result_json"])["delivery"] == expected
    NodeVerifier(store).verify(quest.id, "reader")
    assert manager.get(reader["id"])["state"] == "succeeded"
    original = read(store, reader, historical=True)
    successor(store, manager, quest, plan, new_reader=False)
    assert read(store, reader, historical=True) == original
    with pytest.raises(DomainError):
        read(store, reader)


@pytest.mark.parametrize(
    "mutation",
    [
        "artifact",
        "suite_evidence",
        "source_state",
        "source_result",
        "reader_scope",
        "reader_contract",
        "edge",
        "current_approval",
        "lineage_goal",
    ],
)
def test_read_rejects_tampered_authority_lineage_or_original_evidence(tmp_path, mutation):
    store, manager, _, quest, plan, producer, reader, _ = prepared(tmp_path)
    if mutation == "lineage_goal":
        plan, reader = successor(store, manager, quest, plan, new_reader=True)
    manager._claim(reader["id"])
    with store.unit_of_work() as work:
        c = work.connection
        if mutation == "artifact":
            (tmp_path / "work/source.json").write_text('{"changed":true}', encoding="utf-8")
        elif mutation == "suite_evidence":
            c.execute("DELETE FROM evidence WHERE quest_id=? AND kind='test'", (quest.id,))
        elif mutation == "source_state":
            c.execute("UPDATE worker_runs SET state='review_pending' WHERE id=?", (producer["id"],))
        elif mutation == "source_result":
            c.execute(
                "UPDATE events SET payload=json_set(payload,'$.claim','{}') WHERE event_type='worker.result' AND worker_id=?",
                (producer["worker_id"],),
            )
        elif mutation == "reader_scope":
            c.execute("UPDATE worker_runs SET allowed_tools_json='[]' WHERE id=?", (reader["id"],))
        elif mutation == "reader_contract":
            c.execute(
                "UPDATE task_nodes SET objective='changed' WHERE plan_id=? AND node_key='reader'",
                (plan["id"],),
            )
        elif mutation == "edge":
            c.execute("DELETE FROM task_edges WHERE plan_id=?", (plan["id"],))
        elif mutation == "current_approval":
            c.execute("UPDATE quest_plans SET approved_by='model' WHERE id=?", (plan["id"],))
        else:
            c.execute("UPDATE quests SET goal='another task' WHERE id=?", (quest.id,))
    with pytest.raises(DomainError):
        read(store, reader)


def test_whole_delivery_over_bound_is_rejected_without_truncation(tmp_path):
    store, manager, _, _, _, _, reader, _ = prepared(tmp_path, summary="x" * 3000)
    manager._claim(reader["id"])
    with pytest.raises(DomainError, match="approved byte bound"):
        read(store, reader)


def test_predecessor_original_model_pass_is_rechecked_against_unchanged_packet(tmp_path):
    store, manager, _, _, _, producer, reader, _ = prepared(tmp_path, producer_model_review=True)
    manager._claim(reader["id"])
    original = read(store, reader)
    assert original["producer_run_id"] == producer["id"]
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE events SET payload=json_set(payload,'$.packet_sha256',?) WHERE event_type='review.result' AND worker_id=?",
            ("0" * 64, producer["worker_id"]),
        )
    with pytest.raises(DomainError, match="Independent review"):
        read(store, reader)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"node_key": "reader"},
        {"node_key": "missing"},
        {"node_key": "../producer"},
        {"node_key": "producer", "run_id": "other"},
        {"node_key": True},
    ],
)
def test_reader_rejects_unknown_ambiguous_or_non_node_parameters(tmp_path, arguments):
    store, manager, _, _, _, _, reader, _ = prepared(tmp_path)
    manager._claim(reader["id"])
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        with pytest.raises(DomainError):
            read_completed_delivery(store, connection, reader["id"], arguments)
        assert connection.total_changes == 0


def test_reader_requires_caller_owned_transaction(tmp_path):
    store, manager, _, _, _, _, reader, _ = prepared(tmp_path)
    manager._claim(reader["id"])
    with store.connection() as connection:
        assert not connection.in_transaction
        with pytest.raises(DomainError, match="caller-owned transaction"):
            read_completed_delivery(store, connection, reader["id"], {"node_key": "producer"})


@pytest.mark.parametrize(
    "mutation", [None, "tool_result", "tool_message", "assistant_call", "assistant_arguments"]
)
def test_review_packet_preserves_complete_handoff_and_requires_own_fresh_pass(tmp_path, mutation):
    store, manager, model, _, _, _, reader, expected = prepared(
        tmp_path, reader_criteria=["delivery_review"]
    )
    model.responses.extend(
        [
            tool(TOOL_NAME, {"node_key": "producer"}),
            response(delivery("Assess the full untrusted predecessor claims.")),
        ]
    )
    assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
    if mutation:
        with store.unit_of_work() as work:
            if mutation == "tool_result":
                work.connection.execute(
                    "UPDATE worker_tool_calls SET result_json='{}' WHERE run_id=?", (reader["id"],)
                )
            elif mutation == "tool_message":
                work.connection.execute(
                    "UPDATE worker_messages SET message_json='{}' WHERE run_id=? AND role='tool'",
                    (reader["id"],),
                )
            else:
                for item in work.connection.execute(
                    "SELECT ordinal,message_json FROM worker_messages WHERE run_id=? AND role='assistant'",
                    (reader["id"],),
                ).fetchall():
                    message = json.loads(item["message_json"])
                    if message.get("tool_calls"):
                        if mutation == "assistant_call":
                            message["tool_calls"] = []
                        else:
                            message["tool_calls"][0]["function"]["arguments"] = canonical(
                                {"node_key": "reader"}
                            )
                        work.connection.execute(
                            "UPDATE worker_messages SET message_json=? WHERE run_id=? AND ordinal=?",
                            (canonical(message), reader["id"], item["ordinal"]),
                        )
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        run = connection.execute("SELECT * FROM worker_runs WHERE id=?", (reader["id"],)).fetchone()
        if mutation:
            with pytest.raises(DomainError):
                _packet(store, connection, run)
        else:
            packet = _packet(store, connection, run)
            assert packet["receipts"] == []
            assert packet["predecessor_deliveries"][0]["output"]["delivery"] == expected
            assert not review_pass_matches(store, connection, run, "delivery_review")
        assert connection.total_changes == 0


def test_predecessor_source_bytes_count_all_fields_in_review_envelope():
    packet = {
        "receipts": [],
        "artifacts": [],
        "delivery": delivery(),
        "predecessor_deliveries": [
            {"call_id": "read", "output": {"delivery": delivery("x" * 1500)}}
        ],
    }
    sizes = review_packet_sizes(packet)
    assert sizes["source_bytes"] == len(canonical([]).encode()) + len(
        canonical(packet["predecessor_deliveries"]).encode()
    )
    with pytest.raises(ReviewEnvelopeError, match="review_source_bytes_envelope_exceeded"):
        validate_review_packet(packet, envelope(max_source_bytes=1000))


@contextmanager
def offline_loopback_gateway(store, responder):
    """Exercise the real HTTP and ledger path against a local scripted fixture.

    This is an offline protocol fixture, not an actual model or live acceptance.
    No Gateway mode override, manual attempt row or synthetic settlement is used.
    """
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            message = responder(body, len(requests))
            output = canonical(
                {
                    "choices": [
                        {
                            "message": message,
                            "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 25, "completion_tokens": 15},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        gateway = ProviderGateway(
            ProviderConfig(
                id="protocol",
                kind="openai_compatible",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                credential_ref="FORGE_PROVIDER_KEY_TEST",
                allow_insecure_loopback=True,
                supports_tools=True,
                max_retries=0,
            ),
            credential_resolver=lambda _: "offline-fixture",
            budget_ledger=ModelCallBudgetLedger(
                store.path, token_limit=130000, concurrency_limit=1
            ),
        )
        yield gateway, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_readonly_successor_native_http_fresh_pass_and_completion_without_new_receipt(tmp_path):
    store, manager, _, quest, _, _, reader, expected = prepared(
        tmp_path, reader_criteria=["delivery_review"], reader_write=False
    )
    original_files = {
        str(path.relative_to(tmp_path)): path.read_bytes() for path in (tmp_path / "work").iterdir()
    }
    with store.connection() as connection:
        original_receipts = [
            dict(row) for row in connection.execute("SELECT * FROM action_receipts ORDER BY rowid")
        ]
    reviewed = []

    def responder(body, ordinal):
        if ordinal == 1:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "read-completed",
                        "type": "function",
                        "function": {
                            "name": TOOL_NAME,
                            "arguments": canonical({"node_key": "producer"}),
                        },
                    }
                ],
            }
        if ordinal == 2:
            assert TOOL_NAME in {tool["function"]["name"] for tool in body["tools"]}
            return {
                "content": canonical(
                    delivery("Independently assess the complete predecessor findings.")
                )
            }
        assert ordinal == 3 and not body.get("tools")
        reviewed.append(json.loads(body["messages"][-1]["content"]))
        return {
            "content": canonical(
                {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.8}
            )
        }

    with offline_loopback_gateway(store, responder) as (gateway, requests):
        manager.providers = ProviderPool({"protocol": gateway})
        assert asyncio.run(manager.run(reader["id"]))["state"] == "review_pending"
        with store.connection() as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT * FROM worker_runs WHERE id=?", (reader["id"],)
            ).fetchone()
            packet = _packet(store, connection, row)
            assert not review_pass_matches(store, connection, row, "delivery_review")
        result = asyncio.run(IndependentReviewService(manager).review(reader["id"]))
        assert result["verdict"] == "PASS" and result["packet_sha256"] == _hash(packet)
        assert reviewed[0] == packet and packet["receipts"] == []
        assert packet["predecessor_deliveries"][0]["output"]["delivery"] == expected
        assert len(requests) == 3
        assert NodeVerifier(store).verify(quest.id, "reader")["status"] == "completed"
    with store.connection() as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM worker_runs WHERE id=?", (reader["id"],)).fetchone()
        assert review_pass_matches(store, connection, row, "delivery_review")
        work = type("ReadWork", (), {"connection": connection})()
        CompletionGate(store).check(
            work, connection.execute("SELECT * FROM quests WHERE id=?", (quest.id,)).fetchone()
        )
        assert [
            dict(item)
            for item in connection.execute("SELECT * FROM action_receipts ORDER BY rowid")
        ] == original_receipts
        attempts = connection.execute(
            "SELECT purpose,state,input_charged,output_charged FROM model_budget_attempts ORDER BY created_at"
        ).fetchall()
        assert len(attempts) == 3 and all(item["state"] == "settled" for item in attempts)
        assert (
            sum(item["input_charged"] + item["output_charged"] for item in attempts)
            == row["token_spent"]
            == 120
        )
        assert row["steps"] == 3 and row["token_limit"] == 35000
        assert connection.total_changes == 0
    assert {
        str(path.relative_to(tmp_path)): path.read_bytes() for path in (tmp_path / "work").iterdir()
    } == original_files
