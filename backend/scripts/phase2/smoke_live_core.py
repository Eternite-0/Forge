"""Minimal Phase 2 live smoke: boot Core, register the local model, send one message."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_core import LiveCore, provider_env_from_key, register_model

WS = Path(__file__).resolve().parents[3] / ".local" / "phase2" / "ws-smoke"


def main():
    key = os.environ.get("FORGE_PROVIDER_KEY_LIVE_TEST")
    if not key:
        raise SystemExit("provider key env var missing")
    core = LiveCore(
        WS, provider_env=provider_env_from_key(key), token_limit=2_000_000, concurrency=4
    )
    out = {}
    try:
        core.start()
        out["health"] = core.call("GET", "/health")["body"]
        out["model"] = register_model(core)
        message = core.call(
            "POST",
            "/api/conversation/message",
            {"content": "你好，用一句话介绍你能做什么。", "request_id": "smoke-1"},
            timeout=300,
        )
        out["message"] = message
        out["quests"] = core.call("GET", "/api/quests")["body"]
        out["budget"] = core.call("GET", "/api/budget")["body"]
    finally:
        core.stop()
        out["log_tail"] = core.tail_log(3000)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:8000])


if __name__ == "__main__":
    main()
