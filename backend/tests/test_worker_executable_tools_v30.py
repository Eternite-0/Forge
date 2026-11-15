"""Present only executable observations without changing approved tool authority."""

import asyncio
import json

from test_core_source_prefetch_v23 import entries, prepared

from forge_core import worker_prompts
from forge_core.quest_graph import QuestGraphService
from forge_core.tool_domain import CONTRACTS
from forge_core.worker_prefetch import POLICY, source_message


def test_complete_zero_action_prefetch_uses_same_tool_view_at_runtime_and_preflight(
    tmp_path, monkeypatch
):
    store, manager, model, worker = prepared(tmp_path, monkeypatch)
    result = asyncio.run(manager.run(worker["id"]))
    assert result["state"] == "review_pending" and result["repairs"] == 0
    assert len(model.requests) == 1
    assert "file_read" not in {item["name"] for item in model.requests[0].tools}
    assert result["tool_spent"] == 1 and len(entries(store, worker)) == 1
    measured_tools = []
    original = worker_prompts.worker_request_reservation

    def capture(messages, tools, model_id, output_limit):
        measured_tools.extend(item["name"] for item in tools)
        return original(messages, tools, model_id, output_limit)

    monkeypatch.setattr(worker_prompts, "worker_request_reservation", capture)
    graph = QuestGraphService(store).get(worker["quest_id"])
    with store.connection() as connection:
        connection.execute("BEGIN")
        team = manager.team_authority.get_approved(
            worker["quest_id"], 1, worker["team_digest"], connection=connection
        )
        measured = worker_prompts.measure_initial_worker_request(
            store,
            connection,
            store.get_quest(worker["quest_id"]),
            graph,
            graph["nodes"][0],
            team["members"][0],
            extra_messages=[source_message(entries(store, worker))],
        )
        approved = connection.execute(
            "SELECT allowed_tools_json FROM worker_runs WHERE id=?", (worker["id"],)
        ).fetchone()[0]
    assert json.loads(approved) == ["file_read"]
    assert "file_read" not in measured_tools
    assert set(measured_tools) == {"worker_read_context_source", "worker_request_help"}
    assert measured["input_reservation"] > 0


def test_tool_view_retains_actions_readback_help_and_all_positive_allowance_tools():
    definitions = [
        {"name": contract.name} for contract in CONTRACTS
    ] + worker_prompts.supplemental_worker_tools(source_readback=True, help_allowed=True)
    capsule = {"input_acquisition_policy": POLICY, "action_evidence_allowance_bytes": 0}
    presented = worker_prompts.executable_worker_tools(
        definitions, capsule, declared_sources_complete=True
    )
    names = {item["name"] for item in presented}
    assert not names.intersection(
        {
            "file_read",
            "directory_list",
            "worker_read_action_receipt",
            "worker_read_completed_delivery",
        }
    )
    assert {
        "file_write",
        "file_revise",
        "python_test",
        "numeric_ast",
        "worker_read_context_source",
        "worker_request_help",
    } <= names
    for contract, complete in (
        ({**capsule, "action_evidence_allowance_bytes": 1}, True),
        (capsule, False),
        ({"action_evidence_allowance_bytes": 0}, True),
    ):
        assert (
            worker_prompts.executable_worker_tools(
                definitions, contract, declared_sources_complete=complete
            )
            == definitions
        )
