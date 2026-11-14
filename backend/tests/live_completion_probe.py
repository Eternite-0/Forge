"""Opt-in real Worker file creation and fixed JSON completion verification.

The unique workspace, SQLite database and artifact remain available for audit.
This verifies only JSON validity, never arbitrary mathematical correctness.
"""

import asyncio
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import httpx

from forge_core.domain import ModelProfile, ProtocolSupport, Quest
from forge_core.evidence import NodeVerifier
from forge_core.governance import GovernanceService
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
    evidence_root = Path(__file__).resolve().parents[2] / ".local/phase1/milestone5/live-probe"
    identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    workspace = evidence_root / ("completion-workspace-" + identity)
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / "work").mkdir()
    artifact = workspace / "work/result.json"
    report_file = evidence_root / ("completion-" + identity + ".json")
    endpoint, model_id = "http://127.0.0.1:8788/v1", "deepseek-v4.1-flash"
    report = {
        "mode": "live",
        "status": "incomplete",
        "workspace": str(workspace),
        "database": str(workspace / "core.sqlite3"),
        "model": model_id,
        "endpoint": endpoint,
        "cost": None,
        "scope": "Create a JSON file and independently verify JSON syntax only; no domain result correctness claim.",
    }
    try:
        async with asyncio.timeout(120):
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream(
                    "GET", endpoint + "/models", headers={"Authorization": "Bearer " + secret}
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1024 * 1024:
                            raise ValueError("metadata_too_large")
                matches = [
                    item for item in json.loads(body).get("data", []) if item.get("id") == model_id
                ]
            if len(matches) != 1:
                raise ValueError("model_not_discovered")
            info = matches[0]
            context, max_output = info.get("context_length"), info.get("max_output_tokens")
            if (
                type(context) is not int
                or type(max_output) is not int
                or context < 8000
                or max_output < 1000
            ):
                raise ValueError("metadata_limits_unavailable")
            store = Store(workspace / "core.sqlite3", workspace=Workspace.from_path(workspace))
            expected_arguments = {"path": "work/result.json", "content": '{"probe":true}'}
            goal = (
                "Call file_write exactly once with these exact JSON arguments: "
                + json.dumps(expected_arguments)
                + ". Then return a JSON object with a summary string. The only success criterion is that the created file is valid JSON."
            )
            quest = Quest(title="Live JSON artifact completion probe", goal=goal)
            store.save_quest(quest)
            graph = QuestGraphService(store)
            criterion = "json_valid:" + str(artifact)
            plan = graph.propose(
                quest.id,
                {
                    "nodes": [
                        {
                            "key": "artifact",
                            "objective": goal,
                            "success_criteria": [criterion],
                            "budget_tokens": 8000,
                            "allowed_tools": ["file_write"],
                        }
                    ],
                    "edges": [],
                    "max_total_tokens": 8000,
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
                    id="live-completion-profile",
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
                        "task_node_key": "artifact",
                        "tools": ["file_write"],
                        "input_tokens": 6500,
                        "output_tokens": 1500,
                        "max_steps": 3,
                        "max_tool_calls": 1,
                        "max_duration_seconds": 80,
                    }
                ],
                {
                    "max_members": 1,
                    "max_depth": 0,
                    "max_concurrency": 1,
                    "max_total_tokens": 8000,
                    "max_total_tool_calls": 1,
                    "allowed_tools": ["file_write"],
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
            GovernanceService(store).grant_workspace(quest.id, "work", "local_user")
            gateway = ProviderGateway(
                ProviderConfig(
                    id="live-loopback",
                    kind="openai_compatible",
                    base_url=endpoint,
                    credential_ref="FORGE_PROVIDER_KEY_LIVE_TEST",
                    allow_insecure_loopback=True,
                    supports_tools=True,
                    max_retries=0,
                    timeout_seconds=25,
                ),
                budget_ledger=ModelCallBudgetLedger(
                    store.path, token_limit=8000, concurrency_limit=1
                ),
            )
            tools = ToolEngine(store)

            class FixedScope:
                def definitions(self, names):
                    return tools.definitions(names)

                async def execute(self, **kwargs):
                    if kwargs["tool_name"] != "file_write" or kwargs["parameters"] != {
                        "path": "work/result.json",
                        "content": '{"probe":true}',
                    }:
                        raise ValueError("probe_tool_arguments_outside_fixed_scope")
                    return await tools.execute(**kwargs)

            manager = WorkerManager(
                store,
                ProviderPool({"live-loopback": gateway}),
                FixedScope(),
                team_authority=teams,
                max_agents=1,
                max_concurrency=1,
                global_token_limit=8000,
                global_tool_limit=1,
            )
            run = manager.start(
                quest.id,
                plan["id"],
                "artifact",
                team_digest=team["proposal_digest"],
                member_index=0,
            )
            outcome = await manager.run(run["id"])
            report.update(
                quest_id=quest.id,
                run_id=run["id"],
                worker_state=outcome["state"],
                execution_mode=outcome["execution_mode"],
                token_spent=outcome["token_spent"],
                tool_spent=outcome["tool_spent"],
            )
            if outcome["state"] != "review_pending" or outcome["execution_mode"] != "live":
                raise ValueError("live_worker_not_reviewable")
            verified = NodeVerifier(store).verify(quest.id, "artifact")
            with store.connection() as connection:
                report["receipts"] = [
                    dict(row) for row in connection.execute("SELECT * FROM action_receipts")
                ]
                report["evidence"] = [
                    dict(row)
                    for row in connection.execute("SELECT * FROM evidence ORDER BY created_at,id")
                ]
                report["independent_runs"] = [
                    dict(row) for row in connection.execute("SELECT * FROM execution_runs")
                ]
                report["task_status"] = connection.execute(
                    "SELECT status FROM task_nodes WHERE plan_id=?", (plan["id"],)
                ).fetchone()[0]
            report["quest_status"] = verified["status"]
            report["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if (
                verified["status"] == "completed"
                and report["task_status"] == "succeeded"
                and len(report["receipts"]) == 1
            ):
                report["status"] = "live_json_artifact_completion_verified"
    except Exception as exc:  # noqa: BLE001 - never emit remote text or credentials
        report["error_type"] = type(exc).__name__
    encoded = json.dumps(report, ensure_ascii=False, indent=2).replace(secret, "[REDACTED]")
    with report_file.open("x", encoding="utf-8") as output:
        output.write(encoded)
    print(
        json.dumps(
            {
                "report": str(report_file),
                "status": report["status"],
                "worker_state": report.get("worker_state"),
                "quest_status": report.get("quest_status"),
                "error_type": report.get("error_type"),
            }
        )
    )
    return 0 if report["status"] == "live_json_artifact_completion_verified" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
