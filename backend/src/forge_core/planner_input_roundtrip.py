"""Synthetic native input-acquisition framing, never tool execution evidence."""

from .worker_prompts import canonical


def measurement_call_id(index, *, action=False):
    return (
        ("measurement_action_" if action else "measurement_call_")
        + str(index).zfill(4)
        + "_"
        + "0" * 64
    )


def acquisition_assistant_frame(node):
    """Mirror the runtime's persisted assistant envelope for a bounded read batch.

    These labelled placeholders exist only inside measurement. No call, receipt,
    Worker message or result is created. Additional model exploration remains
    subject to cumulative runtime limits; this funds one declared read batch.
    """
    calls = [
        {"name": "file_read", "arguments": {"path": path}} for path in node["required_input_paths"]
    ] + [
        {"name": "worker_read_completed_delivery", "arguments": {"node_key": key}}
        for key in node.get("required_predecessor_deliveries") or []
    ]
    if not calls:
        return None
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": measurement_call_id(index),
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": canonical(item["arguments"]),
                },
            }
            for index, item in enumerate(calls)
        ],
    }


def acquisition_tool_history(node, receipts, workspace_root):
    """One native reply for each synthetic call; unknown future body stays {}.

    Future complete bodies are funded separately by the original source-growth
    formula. This never supplies those placeholders to runtime or a Provider.
    """
    from pathlib import Path

    known = {str(Path(item["resource_scope"]).resolve()): item for item in receipts}
    results = []
    for index, path in enumerate(node["required_input_paths"]):
        source = known.get(str((Path(workspace_root) / path).resolve()))
        output = {key: source["output"][key] for key in ("content", "sha256")} if source else {}
        results.append(
            {
                "role": "tool",
                "tool_call_id": measurement_call_id(index),
                "content": canonical(output),
            }
        )
    for index, _key in enumerate(
        node.get("required_predecessor_deliveries") or [], start=len(results)
    ):
        results.append(
            {
                "role": "tool",
                "tool_call_id": measurement_call_id(index),
                "content": "{}",
            }
        )
    return results


def prefetch_source_history(node, receipts, workspace_root, *, source_wire_format=None):
    """Measure the exact Core user-data serializer without creating evidence.

    Known file bodies remain complete. Future bodies are explicit unknowns
    funded separately by the unchanged complete-source growth policy. This
    synthetic measurement never reaches runtime, storage or a Provider call.
    """
    from pathlib import Path

    from .source_wire import validate_source_wire_format
    from .worker_prefetch import source_message

    validate_source_wire_format(source_wire_format)
    known = {str(Path(item["resource_scope"]).resolve()): item for item in receipts}
    entries = []
    selected = [("file_read", {"path": path}) for path in node["required_input_paths"]] + [
        ("worker_read_completed_delivery", {"node_key": key})
        for key in node.get("required_predecessor_deliveries") or []
    ]
    for index, (name, arguments) in enumerate(selected):
        source = (
            known.get(str((Path(workspace_root) / arguments["path"]).resolve()))
            if name == "file_read"
            else None
        )
        output = {key: source["output"][key] for key in ("content", "sha256")} if source else {}
        entries.append(
            {
                "ordinal": index,
                "tool_name": name,
                "arguments": arguments,
                "call_id": "core_source_" + "0" * 32,
                "receipt_id": "receipt_" + "0" * 32 if name == "file_read" else None,
                "result_sha256": "0" * 64,
                "output": output,
            }
        )
    return [source_message(entries, source_wire_format=source_wire_format)] if entries else []


def action_completion_stages(node, graph, workspace_root):
    """Frame minimal declared numeric/write completions; no action is performed.

    Empty bodies are explicit unknowns covered by canonical byte growth, never
    candidate code, results, receipts or evidence supplied to a Worker/model.
    Additional actions stay bounded and charged at runtime.
    """
    from pathlib import Path

    selected = []
    contract = graph.get("capacity_contract") or {}
    hypotheses = (contract.get("hypothesis_read_paths") or {}).get(node["key"], [])
    if hypotheses:
        from .hypothesis_sources import validate_paths

        validate_paths(hypotheses)
        allowance = (contract.get("action_evidence_allowances") or {}).get(node["key"])
        if type(allowance) is not int or allowance <= 0:
            from .domain import DomainError

            raise DomainError("Hypothesis read completion requires explicit evidence allowance")
        for path in hypotheses:
            selected.append(
                (
                    "file_read",
                    {"path": path},
                    {"content": "", "sha256": "0" * 64},
                    allowance,
                )
            )
    if "numeric_ast" in node["allowed_tools"]:
        producers = {edge["source"] for edge in graph["edges"] if edge["target"] == node["key"]}
        source_bound = min(
            16000,
            max(
                [
                    node["capacity_bounds"]["max_artifact_bytes"],
                    *[
                        item["capacity_bounds"]["max_artifact_bytes"]
                        for item in graph["nodes"]
                        if item["key"] in producers
                    ],
                ]
            ),
        )
        selected.append(
            (
                "numeric_ast",
                {"source": "", "inputs": {}},
                {
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "source_sha256": "0" * 64,
                    "input_sha256": "0" * 64,
                    "result_json": "",
                    "steps": 1,
                    "subset": "forge_numeric_ast_v1",
                },
                source_bound,
            )
        )
    if "file_write" in node["allowed_tools"] and node.get("expected_artifact"):
        selected.append(
            (
                "file_write",
                {"path": node["expected_artifact"], "content": ""},
                {
                    "path": str(Path(workspace_root) / node["expected_artifact"]),
                    "sha256": "0" * 64,
                    "bytes": node["capacity_bounds"]["max_artifact_bytes"],
                },
                node["capacity_bounds"]["max_artifact_bytes"],
            )
        )
    history, growth, stages = [], 0, []
    for index, (name, parameters, output, body_bound) in enumerate(selected):
        call_id = measurement_call_id(index, action=True)
        history.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": canonical(parameters),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": canonical(output),
                },
            ]
        )
        growth += 2 * body_bound + 1024
        stages.append(
            {
                "after_tool": name,
                "extra_messages": list(history),
                "future_body_and_receipt_growth": growth,
            }
        )
    return stages
