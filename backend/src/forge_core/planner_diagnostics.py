"""Bounded observations for one Planner repair; never decide graph validity.

Only node keys, artifact/input paths and edge keys are inspected. Objectives,
source text, model responses and other graph fields are never copied. The normal
schema/capacity validators remain authoritative; this helper never changes data,
executes tools, calls a Provider or repairs a graph.
"""

import json
import re

VERSION = "planner-semantic-diagnostics-v1"
MAX_NODES = 8
MAX_EDGES = 16
MAX_PATHS_PER_NODE = 32
MAX_VERIFIED_PATHS = 32
MAX_INVALID_PATHS = 16
MAX_PATH_CHARS = 240
MAX_IDENTITY_CHARS = 2000
MAX_DIAGNOSTIC_BYTES = 8192
_KEY = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")


def semantic_graph_diagnostics(data, verified_paths=()):
    """Describe unavailable inputs and one cycle within strict output bounds.

    ``data`` is a semantic wire dictionary, before GraphProposal validation;
    ``verified_paths`` contains only already verified inventory path strings.
    Path visibility follows the existing capacity preflight: verified inputs or
    one direct success/always predecessor artifact. A missing success edge is
    supplemental detail, not a new rule excluding a valid ``always`` edge.
    An empty report never proves validity. Truncation is explicitly recorded.
    """
    report = {
        "version": VERSION,
        "invalid_input_paths": [],
        "cycle_witness": [],
        "truncated": False,
        "omitted": {"nodes": 0, "edges": 0, "input_paths": 0, "input_diagnostics": 0},
    }
    if not isinstance(data, dict):
        report["truncated"] = True
        return report

    def label(value):
        if not isinstance(value, str):
            return "<non-string>"
        if len(value) > MAX_PATH_CHARS:
            report["truncated"] = True
            return value[: MAX_PATH_CHARS - 1] + "…"
        return value

    raw_nodes = data.get("nodes")
    raw_edges = data.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        report["truncated"] = True
        return report
    report["omitted"]["nodes"] = max(0, len(raw_nodes) - MAX_NODES)
    report["omitted"]["edges"] = max(0, len(raw_edges) - MAX_EDGES)
    report["truncated"] = bool(report["omitted"]["nodes"] or report["omitted"]["edges"])
    nodes = []
    for node in raw_nodes[:MAX_NODES]:
        if not isinstance(node, dict) or not isinstance(node.get("key"), str):
            report["truncated"] = True
            continue
        key = node["key"]
        if len(key) > 100 or _KEY.fullmatch(key) is None:
            report["truncated"] = True
            continue
        nodes.append(node)
    keys = {node["key"] for node in nodes}

    edges = []
    for edge in raw_edges[:MAX_EDGES]:
        if not isinstance(edge, dict):
            report["truncated"] = True
            continue
        source, target, condition = (edge.get(key) for key in ("source", "target", "condition"))
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or source not in keys
            or target not in keys
            or condition not in ("success", "failure", "always")
        ):
            report["truncated"] = True
            continue
        edges.append({"source": source, "target": target, "condition": condition})

    verified = set()
    if isinstance(verified_paths, (list, tuple)):
        if len(verified_paths) > MAX_VERIFIED_PATHS:
            report["truncated"] = True
        for path in verified_paths[:MAX_VERIFIED_PATHS]:
            if isinstance(path, str) and len(path) <= MAX_IDENTITY_CHARS:
                verified.add(path)
            else:
                report["truncated"] = True
    else:
        report["truncated"] = True

    invalid_count = 0
    for node in nodes:
        paths = node.get("required_input_paths")
        if paths is None:
            continue
        if not isinstance(paths, list):
            report["truncated"] = True
            continue
        report["omitted"]["input_paths"] += max(0, len(paths) - MAX_PATHS_PER_NODE)
        for path in paths[:MAX_PATHS_PER_NODE]:
            valid_identity = isinstance(path, str) and len(path) <= MAX_IDENTITY_CHARS
            if valid_identity and path in verified:
                continue
            producers = [
                other["key"]
                for other in nodes
                if valid_identity and other.get("expected_artifact") == path
            ]
            visible = [
                edge["source"]
                for edge in edges
                if edge["target"] == node["key"]
                and edge["source"] in producers
                and edge["condition"] in ("success", "always")
            ]
            if len(producers) == 1 and visible and producers[0] != node["key"]:
                continue
            invalid_count += 1
            if len(report["invalid_input_paths"]) >= MAX_INVALID_PATHS:
                continue
            report["invalid_input_paths"].append(
                {
                    "node": node["key"],
                    "path": label(path),
                    "self_output": valid_identity and node.get("expected_artifact") == path,
                    "actual_producers": producers,
                    "missing_direct_success_edge": not any(
                        edge["source"] in producers
                        and edge["target"] == node["key"]
                        and edge["condition"] == "success"
                        for edge in edges
                    ),
                }
            )

    # GraphProposal checks cycles across every edge condition. The witness keeps
    # the exact observed edge keys/conditions; it does not invent a repair.
    adjacency = {key: [] for key in keys}
    for edge in edges:
        adjacency[edge["source"]].append(edge)
    states = {}
    stack_nodes, stack_edges = [], []

    def visit(key):
        states[key] = 1
        stack_nodes.append(key)
        for edge in adjacency[key]:
            target = edge["target"]
            if states.get(target) == 1:
                start = stack_nodes.index(target)
                return stack_edges[start:] + [edge]
            if states.get(target, 0) == 0:
                stack_edges.append(edge)
                witness = visit(target)
                if witness:
                    return witness
                stack_edges.pop()
        stack_nodes.pop()
        states[key] = 2
        return []

    for node in nodes:
        if states.get(node["key"], 0) == 0:
            witness = visit(node["key"])
            if witness:
                report["cycle_witness"] = [dict(edge) for edge in witness]
                break

    report["omitted"]["input_diagnostics"] = invalid_count - len(report["invalid_input_paths"])
    report["truncated"] = report["truncated"] or any(report["omitted"].values())
    while len(json.dumps(report, ensure_ascii=False).encode("utf-8")) > MAX_DIAGNOSTIC_BYTES:
        # A cycle has at most eight ASCII-keyed edges, so removing path rows is
        # always sufficient. Preserve the witness while bounding repair input.
        report["invalid_input_paths"].pop()
        report["omitted"]["input_diagnostics"] += 1
        report["truncated"] = True
    return report
