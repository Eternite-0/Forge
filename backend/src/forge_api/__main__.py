"""Shared Core process entry point for CLI and future desktop bootstrap."""

import argparse
import os

import uvicorn

from forge_core.operator_settings import apply_operator_settings
from forge_core.settings import Settings, Workspace, default_workspace_root
from forge_core.workspace_backup import recover_interrupted_restore
from forge_core.workspace_lock import WorkspaceLock


def main():
    parser = argparse.ArgumentParser(description="Run FORGE Core for one persistent Workspace")
    parser.add_argument("--workspace-root", help="Absolute Workspace root (overrides environment)")
    parser.add_argument(
        "--desktop-host", action="store_true", help="Private framed desktop control channel"
    )
    arguments = parser.parse_args()
    if arguments.desktop_host:
        if arguments.workspace_root:
            parser.error("Workspace is supplied through the desktop control channel")
        from forge_api.desktop_host import main as desktop_main

        desktop_main()
        return
    root = arguments.workspace_root or os.environ.get(
        "FORGE_WORKSPACE_ROOT", str(default_workspace_root())
    )
    with WorkspaceLock(Workspace.from_path(root).root):
        recover_interrupted_restore(Workspace.from_path(root).root)
        apply_operator_settings(root)
        try:
            settings = Settings.load(workspace_root=root)
        except ValueError as exc:
            parser.error(str(exc))
        # The API composition root must use the exact Workspace selected by this launcher.
        os.environ["FORGE_WORKSPACE_ROOT"] = str(settings.workspace.root)
        uvicorn.run("forge_api.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
