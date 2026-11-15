"""Seed isolated native UI inspection with actual files/receipts and Mock model protocol.

Run from backend/ with PYTHONPATH=src:
python tests/seed_ui_artifact_history.py --workspace <new-empty-folder>
The JSON output contains only paths and public IDs; it never prints a Core secret.
"""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from test_artifact_diff_ui import history

from forge_core.domain import ConversationMessage


def seed(workspace=None):
    root = (
        Path(workspace).resolve()
        if workspace
        else Path(tempfile.mkdtemp(prefix="forge-ui-history-"))
    )
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use a new or empty isolated workspace")
    root.mkdir(parents=True, exist_ok=True)
    store, worker, receipts = history(
        root,
        (
            "# FORGE history fixture\n\nStatus: initial draft\n",
            "# FORGE history fixture\n\nStatus: revised draft\n\nVerified immutable receipt history.\n",
        ),
        path="work/history.md",
    )
    original = ConversationMessage(
        role="user", content="生成一份可核对历史版本的文件。此目录仅用于离线原生 UI 验收。"
    )
    assistant = ConversationMessage(
        role="assistant",
        content="已生成并修订测试文件。模型协议是 Mock；文件、SQLite 和工具回执是真实的。",
        metadata={"execution_mode": "mock", "source": "router_notice"},
    )
    store.add_message(original)
    store.add_message(assistant)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET title=?,metadata=? WHERE id=?",
            (
                "离线原生历史预览验收",
                json.dumps(
                    {
                        "original_request_message_id": original.id,
                        "original_request_sha256": hashlib.sha256(
                            original.content.encode("utf-8")
                        ).hexdigest(),
                    }
                ),
                worker["quest_id"],
            ),
        )
    return {
        "fixture": "Mock model protocol; real SQLite, files and governed ToolEngine receipts",
        "workspace": str(root),
        "db_path": str(store.path),
        "workspace_id": store.workspace.id,
        "quest_id": worker["quest_id"],
        "run_id": worker["id"],
        "original_request_message_id": original.id,
        "path": "work/history.md",
        "receipts": receipts,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="New or empty absolute test workspace directory")
    arguments = parser.parse_args()
    print(json.dumps(seed(arguments.workspace), ensure_ascii=False))
