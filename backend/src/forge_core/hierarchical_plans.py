"""Bounded, separately approved replacement of one unfinished node by child nodes."""

from .domain import DomainError
from .plan_lineage import digest
from .token_policy import token_budgets_enforced


def expansion_contract(original, proposed, parent_plan, parent_key, completed_keys):
    old = {node["key"]: node for node in original["nodes"]}
    new = {node["key"]: node for node in proposed["nodes"]}
    if parent_key not in old or parent_key in completed_keys or parent_key in new:
        raise DomainError("Expand exactly one unfinished parent by replacing its node")
    if any(new.get(key) != node for key, node in old.items() if key != parent_key):
        raise DomainError("Hierarchical expansion must preserve every other node contract")
    children = sorted(set(new) - set(old))
    if not 2 <= len(children) <= 8:
        raise DomainError("Hierarchical expansion requires two to eight bounded child nodes")
    parent = old[parent_key]
    prior = original.get("hierarchical_expansions", [])
    parent_depth = max(
        (entry["depth"] for entry in prior if parent_key in entry["child_node_keys"]), default=0
    )
    depth = parent_depth + 1
    source_limits = (original.get("execution_constraints") or {}).get("limits", {})
    if (
        depth > min(8, source_limits.get("max_depth", 8))
        or len(new) > min(8, source_limits.get("max_nodes", 8))
        or len(new) > source_limits.get("max_members", 16)
    ):
        raise DomainError("Hierarchical expansion exceeds task depth/node/member limit")
    if (
        token_budgets_enforced()
        and sum(new[key]["budget_tokens"] for key in children) > parent["budget_tokens"]
    ):
        raise DomainError("Child allocations exceed the original parent node budget")
    if any(
        set(new[key].get("allowed_tools", [])) - set(parent.get("allowed_tools", []))
        for key in children
    ):
        raise DomainError("Hierarchical children cannot expand parent tool authority")
    if any(
        max(new[key].get("required_capabilities", {}).get(name, -1) for key in children) < minimum
        for name, minimum in parent.get("required_capabilities", {}).items()
    ):
        raise DomainError("Hierarchical children weaken parent capability requirements")
    if not any(
        set(parent["success_criteria"]).issubset(new[key]["success_criteria"])
        and new[key].get("expected_artifact") == parent.get("expected_artifact")
        for key in children
    ):
        raise DomainError(
            "A child must retain the parent's complete delivery criteria and artifact"
        )
    triples = {(edge["source"], edge["target"], edge["condition"]) for edge in proposed["edges"]}
    internal = [(a, b) for a, b, _ in triples if a in children and b in children]
    roots = set(children) - {b for _, b in internal}
    leaves = set(children) - {a for a, _ in internal}
    for edge in original["edges"]:
        a, b, condition = edge["source"], edge["target"], edge["condition"]
        if b == parent_key:
            if any((a, child, condition) not in triples for child in roots):
                raise DomainError("Expanded child roots must retain all parent prerequisites")
        elif a == parent_key:
            if any((child, b, condition) not in triples for child in leaves):
                raise DomainError("Expanded child leaves must retain all parent successors")
        elif (a, b, condition) not in triples:
            raise DomainError("Expansion cannot remove unrelated dependency edges")
    if len(prior) >= 8:
        raise DomainError("Hierarchical expansion history exceeds its bound")
    return [
        *prior,
        {
            "version": "bounded-hierarchy-v1",
            "parent_node_key": parent_key,
            "parent_plan_id": parent_plan["id"],
            "parent_plan_digest": parent_plan["digest"],
            "parent_contract": parent,
            "parent_contract_sha256": digest(parent),
            "child_node_keys": children,
            "depth": depth,
        },
    ]


def node_depth(graph, node_key):
    return max(
        (
            entry["depth"]
            for entry in graph.get("hierarchical_expansions", [])
            if node_key in entry["child_node_keys"]
        ),
        default=0,
    )
