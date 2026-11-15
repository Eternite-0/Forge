"""Cumulative node allowances remain finite and subordinate to the approved graph."""

import pytest
from pydantic import ValidationError

from forge_core.graph_domain import GraphProposal


def graph(node_budget, total_budget):
    return {
        "nodes": [
            {
                "key": "analysis",
                "objective": "Analyze with independent review",
                "success_criteria": ["delivery_review"],
                "budget_tokens": node_budget,
            }
        ],
        "edges": [],
        "max_total_tokens": total_budget,
    }


def test_multi_call_node_allowance_can_exceed_a_single_model_context():
    proposal = GraphProposal.model_validate(graph(150000, 180000))
    assert proposal.nodes[0].budget_tokens == 150000


@pytest.mark.parametrize("node_budget,total_budget", [(180001, 180000), (1000001, 1000000)])
def test_cumulative_node_allowance_cannot_exceed_graph_or_hard_ceiling(node_budget, total_budget):
    with pytest.raises(ValidationError):
        GraphProposal.model_validate(graph(node_budget, total_budget))
