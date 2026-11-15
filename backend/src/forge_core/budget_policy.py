"""Explicit serial capacity sharing without increasing any durable budget."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

from .domain import DomainError
from .token_policy import token_budgets_enforced

SERIAL_CAPACITY_VERSION = "serial-capacity-v1"


def validate_serial_policy(graph):
    policy = graph.get("serial_capacity_policy")
    if policy is None:
        return None
    if (
        not isinstance(policy, dict)
        or set(policy) != {"version", "shared_reserve_tokens"}
        or policy["version"] != SERIAL_CAPACITY_VERSION
        or type(policy["shared_reserve_tokens"]) is not int
        or not 1 <= policy["shared_reserve_tokens"] <= 1000000
        or graph.get("max_cost_microunits") is not None
        or graph.get("cost_currency") is not None
    ):
        raise DomainError("Serial capacity requires an explicit bounded token-only reserve")
    nodes = graph["nodes"]
    keys = {node["key"] for node in nodes}
    edges = graph["edges"]
    incoming, outgoing = dict.fromkeys(keys, 0), dict.fromkeys(keys, 0)
    for edge in edges:
        if (
            edge["condition"] != "success"
            or edge["source"] not in keys
            or edge["target"] not in keys
        ):
            raise DomainError("Serial capacity requires one strict success chain")
        incoming[edge["target"]] += 1
        outgoing[edge["source"]] += 1
    if (
        len(keys) != len(nodes)
        or len(nodes) < 2
        or len(edges) != len(nodes) - 1
        or max(incoming.values(), default=0) > 1
        or max(outgoing.values(), default=0) > 1
        or sum(value == 0 for value in incoming.values()) != 1
        or sum(value == 0 for value in outgoing.values()) != 1
    ):
        raise DomainError("Serial capacity requires one strict success chain")
    cursor = next(key for key, count in incoming.items() if count == 0)
    seen = set()
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        cursor = next((edge["target"] for edge in edges if edge["source"] == cursor), None)
    if seen != keys or cursor is not None:
        raise DomainError("Serial capacity requires a connected acyclic success chain")
    reserve = graph.get("coordination_reserve_tokens")
    if type(reserve) is not int or (
        token_budgets_enforced()
        and (
            reserve < (graph["max_total_tokens"] + 9) // 10
            or sum(node["budget_tokens"] for node in nodes)
            + policy["shared_reserve_tokens"]
            + reserve
            > graph["max_total_tokens"]
        )
    ):
        raise DomainError(
            "Serial baselines, one shared reserve and coordination exceed original cap"
        )
    return dict(policy)


def node_execution_limit(graph, node):
    policy = validate_serial_policy(graph)
    return node["budget_tokens"] + (policy["shared_reserve_tokens"] if policy else 0)


def serial_start_allowance(store, connection, quest, plan, node, member, ledger=None):
    """Read-only admission from settled usage; caller retains its execution fence.

    The returned ceiling is a subset of the approved per-member maximum. It is
    not a new allocation, charge, refund, predicted usage or permission to run.
    """
    from .capacity_preflight import coordination_budget_snapshot
    from .model_call_budget import ModelCallBudgetLedger

    if not connection.in_transaction:
        raise DomainError("Serial admission requires a caller-owned execution transaction")

    plan = dict(plan)
    graph = json.loads(plan["graph_json"]) if "graph_json" in plan else plan
    policy = validate_serial_policy(graph)
    if policy is None:
        raise DomainError("Serial Worker requires its approved serial capacity policy")
    key = member["task_node_key"]
    contracts = [item for item in graph["nodes"] if item["key"] == key]
    if len(contracts) != 1:
        raise DomainError("Serial Worker task contract unavailable")
    contract = contracts[0]
    if (
        not isinstance(ledger, ModelCallBudgetLedger)
        or os.path.normcase(str(Path(ledger.path).resolve()))
        != os.path.normcase(str(Path(store.path).resolve()))
        or ledger.cost_limit is not None
        or (
            token_budgets_enforced()
            and min(
                ledger.token_limit,
                ledger.scope_limits.get(("quest", quest["id"]), (ledger.token_limit, 1))[0],
            )
            > graph["max_total_tokens"]
        )
    ):
        raise DomainError("Serial Worker requires the original durable Gateway budget ledger")
    maximum = node_execution_limit(graph, contract)
    if (
        member.get("serial_capacity_policy") != policy
        or member.get("execution_token_limit") != maximum
        or member["estimated_tokens"] != contract["budget_tokens"]
    ):
        raise DomainError("Serial member capacity differs from its approved plan")
    if connection.execute(
        "SELECT 1 FROM worker_runs WHERE quest_id=? AND state IN ('queued','running','paused','blocked','review_pending') LIMIT 1",
        (quest["id"],),
    ).fetchone():
        raise DomainError("Serial capacity cannot overlap an active Worker or review")
    model_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_budget_attempts'"
    ).fetchone()
    if (
        model_table
        and connection.execute(
            "SELECT 1 FROM model_budget_attempts WHERE quest_id=? AND "
            "(state!='settled' OR input_charged IS NULL OR output_charged IS NULL) LIMIT 1",
            (quest["id"],),
        ).fetchone()
    ):
        raise DomainError("Serial capacity requires every prior model attempt fully settled")
    states = {
        row["node_key"]: row["status"]
        for row in connection.execute(
            "SELECT node_key,status FROM task_nodes WHERE plan_id=?", (plan["id"],)
        )
    }
    pending = {item["key"] for item in graph["nodes"] if states.get(item["key"]) != "succeeded"}
    if key not in pending:
        raise DomainError("Serial Worker node already completed")
    if any(
        edge["target"] == key and states.get(edge["source"]) != "succeeded"
        for edge in graph["edges"]
    ):
        raise DomainError("Serial Worker success predecessor is not completed")
    from .evidence import CompletionGate

    for edge in graph["edges"]:
        if edge["target"] == key:
            predecessor = connection.execute(
                "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?",
                (plan["id"], edge["source"]),
            ).fetchone()
            CompletionGate(store)._check_live_node(
                SimpleNamespace(connection=connection), quest, plan, predecessor
            )
    future = sum(item["budget_tokens"] for item in graph["nodes"] if item["key"] in pending - {key})
    budget = (
        coordination_budget_snapshot(connection, store, quest["id"], graph, ledger)
        if token_budgets_enforced()
        else None
    )
    available = (
        max(0, budget["max_worker_allocations_now"] - future) if budget is not None else None
    )
    observation = graph.get("capacity_contract", {}).get("nodes", {}).get(key, {})
    minimum = observation.get("minimum_node_tokens")
    selected_minimum = member.get("capacity_preflight", {}).get("minimum_node_tokens")
    if type(selected_minimum) is not int or selected_minimum < 1:
        raise DomainError("Serial selected member has no complete capacity measurement")
    minimum = max(minimum, selected_minimum) if type(minimum) is int else None
    if (
        type(minimum) is not int
        or minimum < 1
        or (available is not None and available < max(minimum, contract["budget_tokens"]))
    ):
        raise DomainError(
            "Serial remaining budget cannot fund current full cycle and future baselines"
        )
    return {
        "version": SERIAL_CAPACITY_VERSION,
        "execution_token_limit": min(maximum, available) if available is not None else maximum,
        "approved_execution_token_limit": maximum,
        "baseline_tokens": contract["budget_tokens"],
        "shared_reserve_tokens": policy["shared_reserve_tokens"],
        "minimum_current_cycle": minimum,
        "future_baseline_tokens": future,
        "budget_snapshot": budget,
        "allocation_policy": "Charge each approved baseline and this shared reserve once; no refund of spent, allocated or unresolved amounts.",
    }
