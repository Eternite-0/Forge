"""Offline workspace backup and fenced restore, using Core's shared lock."""

import argparse
import json
import sys

from forge_core.workspace_backup import (
    BackupError,
    backup_workspace,
    restore_workspace,
    review_restore,
)
from forge_core.workspace_lock import WorkspaceBusy


def main():
    parser = argparse.ArgumentParser(description="FORGE offline workspace maintenance")
    parser.add_argument("operation", choices=("backup", "restore", "review-restore"))
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--path")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--rollback-directory")
    parser.add_argument("--reason")
    parser.add_argument("--desktop-gated", action="store_true")
    arguments = parser.parse_args()
    if arguments.desktop_gated and sys.stdin.buffer.read(4) != b"GO1\n":
        print(json.dumps({"ok": False, "code": "DESKTOP_GATE_CLOSED"}))
        raise SystemExit(5)
    try:
        if arguments.operation == "review-restore":
            result = review_restore(
                arguments.workspace_root,
                expected_manifest_sha256=arguments.manifest_sha256,
                expected_rollback_directory=arguments.rollback_directory,
                reason=arguments.reason,
            )
        else:
            if not arguments.path:
                raise BackupError("Backup path required")
            result = (backup_workspace if arguments.operation == "backup" else restore_workspace)(
                arguments.workspace_root, arguments.path
            )
    except WorkspaceBusy:
        print(json.dumps({"ok": False, "code": "WORKSPACE_BUSY"}))
        raise SystemExit(2) from None
    except (BackupError, ValueError):
        print(json.dumps({"ok": False, "code": "INVALID_BACKUP_OR_WORKSPACE"}))
        raise SystemExit(3) from None
    except OSError:
        print(json.dumps({"ok": False, "code": "MAINTENANCE_IO_ERROR"}))
        raise SystemExit(4) from None
    print(json.dumps({"ok": True, "operation": arguments.operation, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
