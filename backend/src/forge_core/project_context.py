"""Read-only current-workspace identity for the desktop project navigation.

The database has one workspace binding, not a managed multi-project catalog.
Quest metadata remains an optional grouping label and grants no filesystem scope.
"""

from collections.abc import Mapping

from .settings import Workspace


def workspace_project_context(workspace: Workspace) -> dict:
    root = str(workspace.root)
    name = workspace.root.name or root
    return {
        "version": 1,
        "scope": "current_workspace",
        "workspace": {"id": workspace.id, "name": name, "root": root},
        "default_project": {
            "key": f"workspace:{workspace.id}",
            "name": name,
            "root": root,
            "source": "workspace_binding",
        },
        "capabilities": {
            "multiple_workspaces": False,
            "switch_workspace": False,
            "create_project": False,
            "update_project": False,
            "delete_project": False,
            "metadata_groups_are_managed_projects": False,
        },
    }


def project_quest(quest: Mapping, workspace: Workspace) -> dict:
    """Attach authoritative scope without rewriting stored Quest metadata."""
    return {
        **quest,
        "workspace_id": workspace.id,
        "workspace_root": str(workspace.root),
        "project_context": workspace_project_context(workspace),
    }
