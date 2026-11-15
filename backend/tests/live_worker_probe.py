"""Explicit opt-in live model + local real ToolEngine probe in a temporary workspace."""

import asyncio
import json
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from forge_core.domain import ModelProfile, ProtocolSupport, Quest
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.provider_pool import ProviderPool
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_runtime import WorkerManager


async def main():
    secret = os.environ.get("FORGE_PROVIDER_KEY_LIVE_TEST")
    if not secret or not secret.isascii() or "\r" in secret or "\n" in secret:
        raise SystemExit("FORGE_PROVIDER_KEY_LIVE_TEST is required in process environment")
    root = Path(__file__).resolve().parents[2] / ".local" / "phase1" / "milestone5" / "live-probe"
    root.mkdir(parents=True, exist_ok=True)
    report_file = (
        root / f"worker-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}.json"
    )
    model_id = "deepseek-v4.1-flash"
    endpoint = "http://127.0.0.1:8788/v1"
    report = {
        "mode": "live",
        "model": model_id,
        "endpoint": endpoint,
        "status": "incomplete",
        "cost": None,
        "cost_note": "Unknown; token-only budget, not a monetary cap.",
        "model_capabilities": "No professional capability scores inferred.",
        "schema_note": "Final JSON is validated locally; no claim of provider-side schema enforcement.",
    }

    def redact(value):
        if isinstance(value, str):
            return value.replace(secret, "[REDACTED]")
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {str(k).replace(secret, "[REDACTED]"): redact(v) for k, v in value.items()}
        return value

    try:
        async with asyncio.timeout(120):
            async with (
                httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client,
                client.stream(
                    "GET", endpoint + "/models", headers={"Authorization": f"Bearer {secret}"}
                ) as response,
            ):
                report["metadata_http_status"] = response.status_code
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 1024 * 1024:
                        raise ValueError("metadata_too_large")
                if response.status_code != 200:
                    raise ValueError("metadata_unavailable")
            metadata = json.loads(body)
            report["provider_metadata"] = redact(metadata)
            models = [item for item in metadata.get("data", []) if item.get("id") == model_id]
            if len(models) != 1:
                raise ValueError("model_not_discovered")
            info = models[0]
            context = info.get("context_length")
            max_output = info.get("max_output_tokens")
            if (
                type(context) is not int
                or type(max_output) is not int
                or context < 5000
                or max_output < 256
            ):
                raise ValueError("metadata_limits_unavailable")
            config = ProviderConfig(
                id="live-loopback",
                kind="openai_compatible",
                base_url=endpoint,
                credential_ref="FORGE_PROVIDER_KEY_LIVE_TEST",
                allow_insecure_loopback=True,
                supports_tools=True,
                max_retries=0,
                timeout_seconds=20,
            )
            # Tool support was observed in the separate forced-tool live probe; no professional score is inferred.
            report["tool_support_source"] = (
                "operator-reviewed successful forced-tool protocol probe"
            )
            fixed = {"source": "result = x * x + 1", "inputs": {"x": 3}}
            with tempfile.TemporaryDirectory(prefix="forge-live-worker-") as directory:
                workspace = Path(directory)
                store = Store(workspace / "core.sqlite3", workspace=Workspace.from_path(workspace))
                gateway = ProviderGateway(
                    config,
                    budget_ledger=ModelCallBudgetLedger(
                        store.path, token_limit=10000, concurrency_limit=1
                    ),
                )
                quest = Quest(
                    title="Bounded arithmetic probe",
                    goal="Use numeric_ast with source result = x * x + 1 and inputs x=3, then return JSON summary.",
                )
                store.save_quest(quest)
                graph = QuestGraphService(store)
                plan = graph.propose(
                    quest.id,
                    {
                        "nodes": [
                            {
                                "key": "numeric",
                                "objective": quest.goal,
                                "success_criteria": ["numeric result is 10"],
                                "budget_tokens": 5000,
                                "allowed_tools": ["numeric_ast"],
                            }
                        ],
                        "edges": [],
                        "max_total_tokens": 5000,
                    },
                    expected_version=0,
                )
                graph.approve(
                    quest.id,
                    plan["digest"],
                    plan["plan_revision"],
                    expected_version=1,
                    actor="local_user",
                )
                store.save_model(
                    ModelProfile(
                        id="live-probe-profile",
                        provider="live-loopback",
                        model_id=model_id,
                        display_name=model_id,
                        context_window=context,
                        max_output_tokens=max_output,
                        tool_calling_support=ProtocolSupport(
                            supported=True, source="live_forced_tool_probe"
                        ),
                        provenance={
                            "limits": {"source": "provider_metadata", "verified_maximum": False}
                        },
                    )
                )
                teams = TeamCompositionService(store)
                team = teams.propose(
                    quest.id,
                    [
                        {
                            "id": "coding",
                            "task_node_key": "numeric",
                            "tools": ["numeric_ast"],
                            "input_tokens": 4000,
                            "output_tokens": 1000,
                            "max_steps": 2,
                            "max_tool_calls": 1,
                            "max_duration_seconds": 60,
                        }
                    ],
                    {
                        "max_members": 1,
                        "max_depth": 0,
                        "max_concurrency": 1,
                        "max_total_tokens": 5000,
                        "max_total_tool_calls": 1,
                        "allowed_tools": ["numeric_ast"],
                    },
                    strategy="greedy",
                    expected_version=2,
                )
                teams.approve(
                    quest.id,
                    plan["plan_revision"],
                    team["proposal_digest"],
                    expected_version=2,
                    actor="local_user",
                )
                actual = ToolEngine(store)

                class FixedToolScope:
                    def definitions(self, names):
                        return actual.definitions(names)

                    async def execute(self, **kwargs):
                        if kwargs["tool_name"] != "numeric_ast" or kwargs["parameters"] != fixed:
                            raise ValueError("probe_tool_arguments_outside_fixed_scope")
                        return await actual.execute(**kwargs)

                manager = WorkerManager(
                    store,
                    ProviderPool({"live-loopback": gateway}),
                    FixedToolScope(),
                    team_authority=teams,
                    max_agents=1,
                    max_concurrency=1,
                    global_token_limit=5000,
                    global_tool_limit=1,
                )
                run = manager.start(
                    quest.id,
                    plan["id"],
                    "numeric",
                    team_digest=team["proposal_digest"],
                    member_index=0,
                )
                outcome = await manager.run(run["id"])
                report["worker"] = {
                    "state": outcome["state"],
                    "execution_mode": outcome["execution_mode"],
                    "steps": outcome["steps"],
                    "token_spent": outcome["token_spent"],
                    "tool_spent": outcome["tool_spent"],
                    "cost_spent_microunits": outcome["cost_spent_microunits"],
                }
                with store.connection() as connection:
                    receipts = [
                        dict(row)
                        for row in connection.execute(
                            "SELECT execution_mode,result_json FROM action_receipts"
                        )
                    ]
                report["receipts"] = redact(receipts)
                report["quest_status"] = store.get_quest(quest.id)["status"]
                if len(receipts) == 1:
                    receipt = json.loads(receipts[0]["result_json"])
                    inner = json.loads(receipt.get("result_json", "null"))
                    report["numeric_result_valid"] = (
                        receipt.get("exit_code") == 0 and type(inner) is int and inner == 10
                    )
                else:
                    report["numeric_result_valid"] = False
                if (
                    outcome["state"] == "review_pending"
                    and outcome["execution_mode"] == "live"
                    and report["numeric_result_valid"]
                    and receipts[0]["execution_mode"] == "live"
                ):
                    report["status"] = "live_worker_tool_loop_observed"
    except (Exception, TimeoutError) as exc:  # noqa: BLE001 - never emit remote exception or secret
        report["error_type"] = type(exc).__name__
    with report_file.open("x", encoding="utf-8") as output:
        json.dump(redact(report), output, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            redact(
                {
                    "report": str(report_file),
                    "status": report["status"],
                    "worker": report.get("worker"),
                    "numeric_result_valid": report.get("numeric_result_valid"),
                    "error_type": report.get("error_type"),
                }
            ),
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "live_worker_tool_loop_observed" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
