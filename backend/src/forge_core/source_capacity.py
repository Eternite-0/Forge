"""One authoritative complete-source byte measurement for Planner and preflight."""

from dataclasses import dataclass, field

from .domain import DomainError

SOURCE_CAPACITY_VERSION = "core-source-capacity-v1"
SOURCE_METADATA_RESERVE_BYTES = 1024
MAX_SOURCE_RECEIPT_BYTES = 65536


@dataclass(frozen=True)
class SourceReceiptMeasurement:
    """Receipt bodies stay internal; diagnostics expose only byte counts."""

    receipts: list[dict] = field(repr=False)
    known_source_bytes: int
    future_source_bytes_bound: int
    future_contributions: tuple[dict, ...] = ()

    @property
    def required_source_receipt_bytes(self):
        return self.known_source_bytes + self.future_source_bytes_bound

    def diagnostics(self):
        return {
            "known_source_bytes": self.known_source_bytes,
            "future_source_bytes_bound": self.future_source_bytes_bound,
            "required_source_receipt_bytes": self.required_source_receipt_bytes,
        }


def measure_required_sources(store, graph, node, inventory):
    """Keep the existing canonical receipt, future artifact and handoff formula.

    This measures the explicit paths and deliveries; it never adds, removes or
    deduplicates semantic inputs. Normal graph/source authorization validation
    remains mandatory. Imports are local so preflight can share this measurement
    without duplicating its source reader or canonical receipt serializer.
    """
    from .capacity_preflight import _receipt, _size

    inputs = {item["path"]: item for item in inventory}
    receipts, future_bytes, contributions = [], 0, []
    for path in node["required_input_paths"]:
        if path in inputs:
            receipts.append(_receipt(store, inputs[path]))
        else:
            predecessors = [
                other for other in graph["nodes"] if other.get("expected_artifact") == path
            ]
            if len(predecessors) != 1 or not predecessors[0].get("capacity_bounds"):
                raise DomainError("Future input size is unknown; declare a producer artifact bound")
            amount = 2 * predecessors[0]["capacity_bounds"]["max_artifact_bytes"] + 512
            future_bytes += amount
            contributions.append(
                {
                    "kind": "artifact",
                    "producer": predecessors[0]["key"],
                    "path": path,
                    "declared_bound": predecessors[0]["capacity_bounds"]["max_artifact_bytes"],
                    "bytes": amount,
                }
            )
    deliveries = node.get("required_predecessor_deliveries") or []
    if deliveries:
        from .predecessor_delivery import HANDOFF_METADATA_BYTES

        producers = {item["key"]: item for item in graph["nodes"]}
        if any(
            key not in producers or not producers[key].get("capacity_bounds") for key in deliveries
        ):
            raise DomainError("Completed-delivery source requires an explicit producer byte bound")
        future_bytes += 2
        for key in deliveries:
            amount = (
                producers[key]["capacity_bounds"]["max_delivery_bytes"]
                + HANDOFF_METADATA_BYTES
                + 128
            )
            future_bytes += amount
            contributions.append(
                {
                    "kind": "delivery",
                    "producer": key,
                    "declared_bound": producers[key]["capacity_bounds"]["max_delivery_bytes"],
                    "bytes": amount,
                }
            )
    return SourceReceiptMeasurement(receipts, _size(receipts), future_bytes, tuple(contributions))


def required_source_receipt_bytes(store, graph, node, inventory):
    """Return the exact existing policy requirement without extra source margin."""
    return measure_required_sources(store, graph, node, inventory).required_source_receipt_bytes


def bind_source_receipt_bounds(
    store, data, inventory, completed_keys=(), *, action_evidence_allowances=None
):
    """Bind only omitted pending source bounds, after all measurements succeed.

    New semantic proposals specify complete artifact/delivery bounds and source
    identities. Core adds the canonical complete source requirement plus its
    existing 1024-byte metadata policy. Existing source bounds are never replaced;
    completed contracts remain byte-for-byte unchanged. Overflow rejects, without
    clamping, shrinking, changing the DAG or authorizing any execution.
    """
    from .capacity_preflight import CapacityPreflightError, verify_input_inventory

    verify_input_inventory(store, inventory)
    completed = set(completed_keys)
    if action_evidence_allowances is not None:
        pending = {node["key"] for node in data["nodes"]} - completed
        if (
            not isinstance(action_evidence_allowances, dict)
            or set(action_evidence_allowances) != pending
            or any(
                type(value) is not int or not 0 <= value <= 32768
                for value in action_evidence_allowances.values()
            )
        ):
            raise DomainError("V23 requires exact pending-node action evidence allowances")
    bindings, observations = [], {}
    for node in data["nodes"]:
        if node["key"] in completed:
            continue
        bounds = node.get("capacity_bounds")
        if not isinstance(bounds, dict) or set(bounds) != {
            "max_artifact_bytes",
            "max_delivery_bytes",
        }:
            raise DomainError("Derived source capacity requires omitted pending source bounds")
        if any(type(value) is not int or not 1 <= value <= 32768 for value in bounds.values()):
            raise DomainError("Derived source capacity requires complete bounded outputs")
        measurement = measure_required_sources(store, data, node, inventory)
        allowance = 0
        if action_evidence_allowances is not None:
            allowance = action_evidence_allowances.get(node["key"])
            if type(allowance) is not int or not 0 <= allowance <= 32768:
                raise DomainError("V23 requires explicit bounded action evidence allowance")
        bound = (
            measurement.required_source_receipt_bytes + SOURCE_METADATA_RESERVE_BYTES + allowance
        )
        observation = {
            **measurement.diagnostics(),
            "metadata_reserve_bytes": SOURCE_METADATA_RESERVE_BYTES,
            "max_source_receipt_bytes": bound,
            **(
                {"action_evidence_allowance_bytes": allowance}
                if action_evidence_allowances is not None
                else {}
            ),
        }
        if bound > MAX_SOURCE_RECEIPT_BYTES:
            raise CapacityPreflightError(
                "Complete derived source capacity exceeds 65536 bytes: " + node["key"],
                {
                    "node": node["key"],
                    **observation,
                    "source_receipt_limit_bytes": MAX_SOURCE_RECEIPT_BYTES,
                    "source_capacity_version": SOURCE_CAPACITY_VERSION,
                },
            )
        bindings.append((node, {**bounds, "max_source_receipt_bytes": bound}))
        observations[node["key"]] = observation
    # An error at any node leaves every original contract untouched.
    for node, bounds in bindings:
        node["capacity_bounds"] = bounds
    return {
        "version": SOURCE_CAPACITY_VERSION,
        "nodes": observations,
        "preserved_completed_keys": sorted(completed),
    }
