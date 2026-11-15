"""Core-owned single-response policy; not a model-declared allocation."""

PLANNED_WORKER_OUTPUT_TOKENS = 2048


def planned_worker_output_tokens(node):
    """Size new measured contracts without changing already approved resources.

    A complete multi-source delivery needs more response space than a short
    handoff. The ordinary native request/review checks still admit each tier.
    Artifact and delivery bodies occur in separate Worker responses.
    """
    bounds = node.get("capacity_bounds") or {}
    complete_output_bytes = max(
        bounds.get("max_artifact_bytes", 1), bounds.get("max_delivery_bytes", 1)
    )
    if complete_output_bytes <= 2048:
        return PLANNED_WORKER_OUTPUT_TOKENS
    return 4096 if complete_output_bytes <= 6144 else 8192

# The objective itself remains complete in responsibility and the Worker task.
# A reference avoids copying a 2000-character objective into a 1000-character
# input-contract item during internal resource measurement.
INTERNAL_OBJECTIVE_INPUT_REFERENCE = (
    "Approved current node objective and source-bound original request"
)
