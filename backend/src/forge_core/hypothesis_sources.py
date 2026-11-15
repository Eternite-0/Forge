"""Explicit approved paths for new observations; recalled contents stay unverified."""

from pathlib import PurePosixPath, PureWindowsPath

from .domain import DomainError


def validate_paths(paths):
    if not isinstance(paths, list) or len(paths) > 32:
        raise DomainError("Hypothesis read paths require a bounded explicit list")
    for path in paths:
        if (
            not isinstance(path, str)
            or not 1 <= len(path) <= 2000
            or "\\" in path
            or any(char in ':*?"<>|' for char in path)
            or PureWindowsPath(path).drive
            or PurePosixPath(path).is_absolute()
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(part.endswith((".", " ")) for part in path.split("/"))
            or any(ord(char) < 32 for char in path)
        ):
            raise DomainError("Hypothesis source must be an exact relative workspace path")
    if len(paths) != len(set(paths)):
        raise DomainError("Duplicate hypothesis source path")
    return paths


def validate_graph(graph):
    contract = graph.get("capacity_contract") or {}
    selected = contract.get("hypothesis_read_paths")
    if selected is None:
        return
    pending = {
        node["key"]: node
        for node in graph["nodes"]
        if node["key"] not in set(contract.get("preserved_completed_keys") or [])
    }
    if (
        contract.get("input_acquisition_policy") != "core-declared-source-prefetch-v1"
        or not isinstance(selected, dict)
        or set(selected) != set(pending)
    ):
        raise DomainError("Hypothesis paths require exact approved pending-node bindings")
    allowances = contract.get("action_evidence_allowances") or {}
    inventory = {item["path"] for item in contract.get("input_inventory", [])}
    artifacts = {node.get("expected_artifact") for node in graph["nodes"]}
    for key, paths in selected.items():
        validate_paths(paths)
        node = pending[key]
        if paths and (
            "file_read" not in node["allowed_tools"]
            or type(allowances.get(key)) is not int
            or allowances[key] <= 0
            or set(paths) & (inventory | artifacts | set(node.get("required_input_paths") or []))
        ):
            raise DomainError(
                "Hypothesis reads need their own exact source scope and evidence allowance"
            )


def validate_member(graph, member):
    selected = (graph.get("capacity_contract") or {}).get("hypothesis_read_paths")
    if selected is None:
        if "hypothesis_read_paths" in member:
            raise DomainError("Unapproved member hypothesis source scope")
        return
    if member.get("hypothesis_read_paths") != selected.get(member["task_node_key"]):
        raise DomainError("Member hypothesis paths differ from the approved plan")
    validate_paths(member["hypothesis_read_paths"])


def validate_quest_hypotheses(connection, quest_id, graph):
    selected = (graph.get("capacity_contract") or {}).get("hypothesis_read_paths") or {}
    paths = {path for items in selected.values() for path in items}
    if not paths:
        return
    quest = connection.execute("SELECT goal FROM quests WHERE id=?", (quest_id,)).fetchone()
    if quest is None or any(path not in quest["goal"] for path in paths):
        raise DomainError("Hypothesis paths must be explicitly carried into the current Quest goal")
