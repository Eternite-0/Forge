"""Real capacity-aware fixtures for legacy Planner transport/lifecycle tests.

These are explicit Mock model/operator contracts; no production gate is patched.
"""

from forge_core.domain import ModelProfile, ProtocolSupport
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway


class NativePlannerMock(ProviderGateway):
    """Native serializer with an explicitly overridden offline generate method."""

    def __init__(self):
        super().__init__(
            ProviderConfig(
                id="planner-fixture",
                kind="openai_compatible",
                base_url="https://example.invalid/v1",
                credential_ref="FORGE_PROVIDER_KEY_TEST",
                supports_tools=True,
                supports_json_schema=True,
                max_retries=0,
            ),
            credential_resolver=lambda _ref: "offline-fixture",
        )
        self.execution_mode = "mock"

    async def generate(self, request):
        raise AssertionError("Lifecycle fixture must supply its offline response behavior")


def configure_planner_capacity(main, provider=None):
    main.store.save_model(
        ModelProfile(
            id="planner-capacity-fixture",
            provider="planner-fixture",
            model_id="mock",
            display_name="Explicit Mock Planner fixture",
            context_window=65536,
            max_output_tokens=4096,
            tool_calling_support=ProtocolSupport(supported=True, source="test_fixture"),
            structured_output_support=ProtocolSupport(supported=True, source="test_fixture"),
        )
    )
    main.router.provider_id = "planner-fixture"
    main.router.context_limit = 65536
    if provider is not None:
        provider.config = ProviderConfig(
            id="planner-fixture",
            kind="openai_compatible",
            base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST",
            supports_tools=True,
            supports_json_schema=True,
            max_retries=0,
        )
        main.provider = provider
        main.router.provider = provider
        pool = ProviderPool({"planner-fixture": provider})
        main.quest_graph.capacity_providers = pool
        main.team_service.capacity_providers = pool


def capacity_graph(key, objective):
    return {
        "nodes": [
            {
                "key": key,
                "objective": objective,
                "success_criteria": ["delivery_review"],
                "budget_tokens": 150000,
                "required_capabilities": [],
                "allowed_tools": [],
                "expected_artifact": None,
                "max_attempts": 1,
                "required_input_paths": [],
                "capacity_bounds": {
                    "max_source_receipt_bytes": 2048,
                    "max_artifact_bytes": 3072,
                    "max_delivery_bytes": 1536,
                },
            }
        ],
        "edges": [],
        "max_total_tokens": 180000,
        "coordination_reserve_tokens": 18000,
        "max_cost_microunits": None,
        "max_duration_seconds": None,
        "cost_currency": None,
    }


def semantic_capacity_graph(key, objective):
    """The same offline semantic contract in the autonomous production wire."""
    graph = capacity_graph(key, objective)
    for node in graph["nodes"]:
        node.pop("budget_tokens")
        node["required_predecessor_deliveries"] = []
        node["success_criteria"] = [
            {"kind": criterion, "artifact_path": None} for criterion in node["success_criteria"]
        ]
        node["capacity_bounds"].pop("max_source_receipt_bytes")
    semantic = {
        "allocation_mode": "core_measured",
        "nodes": dataflow_fixture(
            {"allocation_mode": "core_measured", "nodes": graph["nodes"], "edges": graph["edges"]}
        )["nodes"],
    }
    for node in semantic["nodes"]:
        bounds = node.pop("capacity_bounds")
        node["output_capacity"] = {"profile": "custom", **bounds}
        node["action_evidence"] = {"profile": "declared_sources_only_0"}
        node["hypothesis_read_paths"] = []
    return semantic


def dataflow_fixture(graph):
    """Encode an explicit Mock V20 selection as V21; never repair malformed choices."""
    import copy

    result = {"allocation_mode": graph["allocation_mode"], "nodes": []}
    by_key = {node["key"]: node for node in graph["nodes"]}
    for original in graph["nodes"]:
        node = copy.deepcopy(original)
        inputs = node.pop("required_input_paths")
        deliveries = node.pop("required_predecessor_deliveries")
        node["verified_input_paths"] = list(inputs)
        node["predecessors"] = []
        remaining_deliveries = list(deliveries)
        for edge in graph["edges"]:
            if edge["target"] != node["key"]:
                continue
            producer = by_key[edge["source"]]
            artifact = producer.get("expected_artifact")
            selected_artifact = artifact is not None and artifact in inputs
            if selected_artifact:
                node["verified_input_paths"].remove(artifact)
            selected_deliveries = [key for key in deliveries if key == producer["key"]]
            for key in selected_deliveries:
                remaining_deliveries.remove(key)
            node["predecessors"].append(
                {
                    "node_key": producer["key"],
                    "condition": edge["condition"],
                    "read_artifact": selected_artifact,
                    "delivery_sources": selected_deliveries,
                }
            )
        if remaining_deliveries:
            raise ValueError("Fixture must explicitly select each delivery's dependency")
        criteria = []
        for criterion in node["success_criteria"]:
            if (
                criterion["artifact_path"] is not None
                and criterion["artifact_path"] != node["expected_artifact"]
            ):
                raise ValueError("Fixture cannot repair a mismatched verifier artifact")
            criteria.append({"kind": criterion["kind"]})
        node["success_criteria"] = criteria
        result["nodes"].append(node)
    return result
