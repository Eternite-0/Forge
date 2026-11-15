"""Shared Core process entry point for CLI and future desktop bootstrap."""

import argparse
import os
import sys
import threading

import uvicorn

from forge_core.operator_settings import apply_operator_settings
from forge_core.settings import Settings, Workspace, default_workspace_root
from forge_core.workspace_backup import recover_interrupted_restore
from forge_core.workspace_lock import WorkspaceBusy, WorkspaceLock


def _core_child(root):
    """Keep the original Core bootstrap in its own supervised process."""
    from forge_api.desktop_host import read_frame
    from forge_api.supervisor import validate_secret

    try:
        if sys.stdin.isatty():
            raise ValueError("Private bootstrap pipe required")
        frame = read_frame(sys.stdin.buffer)
        if (
            not isinstance(frame, dict)
            or set(frame) != {"version", "type", "secret"}
            or frame["version"] != 1
            or frame["type"] != "bootstrap"
        ):
            raise ValueError("Invalid bootstrap")
        os.environ["FORGE_CORE_SECRET"] = validate_secret(frame["secret"])
        with WorkspaceLock(Workspace.from_path(root).root):
            recover_interrupted_restore(Workspace.from_path(root).root)
            apply_operator_settings(root)
            settings = Settings.load(workspace_root=root)
            os.environ["FORGE_WORKSPACE_ROOT"] = str(settings.workspace.root)
            server = uvicorn.Server(
                uvicorn.Config(
                    "forge_api.main:app",
                    host=settings.host,
                    port=settings.port,
                )
            )

            def control():
                try:
                    command = read_frame(sys.stdin.buffer)
                    if command is not None and command != {"version": 1, "type": "shutdown"}:
                        raise ValueError("Invalid Core control message")
                except (OSError, ValueError, TypeError):
                    pass
                finally:
                    # Parent death closes the private pipe too. Never leave the
                    # Core accepting work after its supervisor has disappeared.
                    server.should_exit = True

            threading.Thread(target=control, name="forge-parent-control", daemon=True).start()
            server.run()
            return 0 if server.started else 2
    except (WorkspaceBusy, ValueError):
        print(
            "FORGE Core cannot start: invalid configuration or workspace already in use.",
            file=sys.stderr,
        )
        return 2
    except Exception:  # noqa: BLE001 - never emit bootstrap or provider configuration
        print("FORGE Core process failed.", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description="Run FORGE Core for one persistent Workspace")
    parser.add_argument("--workspace-root", help="Absolute Workspace root (overrides environment)")
    parser.add_argument(
        "--desktop-host", action="store_true", help="Private framed desktop control channel"
    )
    parser.add_argument("--core-child", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.desktop_host:
        if arguments.workspace_root or arguments.core_child:
            parser.error("Workspace is supplied through the desktop control channel")
        from forge_api.desktop_host import main as desktop_main

        desktop_main()
        return
    root = arguments.workspace_root or os.environ.get(
        "FORGE_WORKSPACE_ROOT", str(default_workspace_root())
    )
    if arguments.core_child:
        raise SystemExit(_core_child(root))
    from forge_api.supervisor import supervise

    try:
        result = supervise(root)
    except ValueError:
        parser.error("Invalid Workspace or Core supervision configuration")
    raise SystemExit(result)


if __name__ == "__main__":
    main()
