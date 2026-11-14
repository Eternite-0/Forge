"""Reproduce the protocol baseline using a deterministic mock, not a real model."""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from forge_core.benchmark import BenchmarkRunner
from forge_core.settings import Workspace
from forge_core.store import Store


class MockGateway:
    execution_mode = "mock"

    async def generate(self, request):
        return SimpleNamespace(text="4", provider_id="mock", model_id=request.model_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, help="Absolute isolated benchmark directory")
    args = parser.parse_args()
    workspace = Workspace.from_path(Path(args.workspace))
    store = Store(workspace.root / "benchmark.sqlite3", workspace=workspace)
    result = asyncio.run(BenchmarkRunner(store).run(MockGateway(), "mock-baseline", mode="mock"))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
