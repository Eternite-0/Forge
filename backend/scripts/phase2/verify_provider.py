"""Bounded live protocol checks through FORGE's Gateway and admission ledger.

Credentials must be supplied in the process environment. Reports contain only
allowlisted metadata, never HTTP bodies, headers or exception text.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderError, ProviderGateway
from forge_core.providers.config import ProviderConfig
from forge_core.router_domain import RouterDecision
from forge_core.runtime import ModelCallContext, ModelRequest
from forge_core.settings import Workspace
from forge_core.store import Store

REPO = Path(__file__).resolve().parents[3]


async def run(args):
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    root = REPO / ".local" / "phase2" / "provider" / run_id
    root.mkdir(parents=True)
    store = Store(root / "probe.sqlite3", workspace=Workspace.from_path(root))
    ledger = ModelCallBudgetLedger(store.path, token_limit=args.token_limit, concurrency_limit=2)
    config = ProviderConfig(
        "wb-local",
        "openai_compatible",
        args.base_url,
        args.credential_env,
        max_retries=0,
        timeout_seconds=60,
        concurrency=2,
        supports_tools=True,
        supports_json_schema=True,
        allow_insecure_loopback=True,
        reasoning_effort=getattr(args, "reasoning_effort", None),
    )
    gateway = ProviderGateway(config, budget_ledger=ledger)
    started = time.monotonic()
    report = {
        "run_id": run_id,
        "execution_mode": "live",
        "provider": config.id,
        "model_id": args.model,
        "base_url": args.base_url,
        "credential_source": "process_environment_reference_only",
        "token_limit": args.token_limit,
        "concurrency_limit": 2,
        "upstream_identity": "unverified_proxy_reported_identity",
        "reasoning_effort": config.reasoning_effort,
        "limits": {
            "context_operator_ceiling": 65536,
            "output_probe_ceiling": 1024,
            "actual_context_maximum": None,
            "actual_output_maximum": None,
            "rate_limit": None,
            "cost": None,
        },
        "cases": [],
    }

    def request(messages, **options):
        return ModelRequest(
            model_id=args.model,
            provider_id=config.id,
            messages=messages,
            max_output_tokens=1024,
            context_limit=65536,
            model_capabilities={"tools": True, "json_schema": True},
            model_capability_sources={
                "tools": "operator_protocol_probe",
                "json_schema": "operator_protocol_probe",
            },
            budget_context=ModelCallContext(
                call_id="probe-" + secrets.token_hex(10),
                purpose="provider_protocol_probe",
                workspace_id=store.workspace.id,
            ),
            **options,
        )

    async def check(name, req, validator):
        began = time.monotonic()
        row = {
            "name": name,
            "proxy_response_received": False,
            "model_response_received": False,
            "correct": False,
        }
        try:
            response = await gateway.generate(req)
            row.update(
                proxy_response_received=True,
                model_response_received=True,
                reported_model_id=response.model_id,
                reported_provider_id=response.provider_id,
                usage=response.usage,
                finish_reason=response.finish_reason,
                response_sha256=hashlib.sha256(response.text.encode()).hexdigest(),
            )
            try:
                row["correct"] = bool(validator(response))
            except (TypeError, ValueError, KeyError):
                row["correct"] = False
        except ProviderError as exc:
            row["error_code"] = exc.code
        except Exception as exc:  # noqa: BLE001 - retain bounded diagnostics only
            row["error_code"] = type(exc).__name__
        row["seconds"] = round(time.monotonic() - began, 3)
        report["cases"].append(row)

    try:
        models = await gateway.discover()
        report["discovery"] = {
            "count": len(models),
            "ids": [m["id"] for m in models],
            "target_available": any(m["id"] == args.model for m in models),
        }
    except ProviderError as exc:
        report["discovery"] = {"error_code": exc.code}

    await check(
        "minimal_dialogue",
        request([{"role": "user", "content": "Return only FORGE_READY."}]),
        lambda r: r.text.strip() == "FORGE_READY",
    )
    schema = RouterDecision.model_json_schema()
    for repeat in range(3):
        await check(
            f"router_schema_{repeat + 1}",
            request(
                [
                    {
                        "role": "system",
                        "content": "You are a Router. No Quests exist. Simple arithmetic is chat. Return only the requested JSON.",
                    },
                    {
                        "role": "user",
                        "content": "What is 19 + 23? Reply with the answer. Use intent chat, null title/goal/quest_id, and candidates [].",
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "router_decision", "strict": True, "schema": schema},
                },
            ),
            lambda r: (
                (d := RouterDecision.model_validate_json(r.text)).intent == "chat"
                and "42" in d.reply
                and not d.candidates
            ),
        )
    tool = {
        "name": "inspect_value",
        "description": "Read an integer for the protocol check",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    }
    for repeat in range(3):
        await check(
            f"tool_call_{repeat + 1}",
            request(
                [
                    {
                        "role": "user",
                        "content": "Call inspect_value with value 37. Do not answer in prose.",
                    }
                ],
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "inspect_value"}},
            ),
            lambda r: (
                len(r.tool_calls) == 1
                and r.tool_calls[0]["name"] == "inspect_value"
                and r.tool_calls[0]["arguments"] == {"value": 37}
            ),
        )
    marker = "FORGE-" + secrets.token_hex(6)
    filler = "A protocol fixture with irrelevant text. " * 180
    await check(
        "bounded_context_recall",
        request(
            [
                {
                    "role": "user",
                    "content": filler
                    + "\nmarker="
                    + marker
                    + "\n"
                    + filler
                    + "\nReturn only the marker value.",
                }
            ]
        ),
        lambda r: r.text.strip() == marker,
    )
    await asyncio.gather(
        *[
            check(
                f"concurrency_{i + 1}",
                request([{"role": "user", "content": "Return only OK."}]),
                lambda r: r.text.strip() == "OK",
            )
            for i in range(2)
        ]
    )
    stream_started = time.monotonic()
    row = {"name": "streaming", "correct": False}
    pieces = []
    try:
        async for event in gateway.stream(
            request([{"role": "user", "content": "Return only 1 2 3 4 5."}])
        ):
            if event.text:
                pieces.append(event.text)
                row.setdefault("ttft_seconds", round(time.monotonic() - stream_started, 3))
            if event.response is not None:
                row["usage"] = event.response.usage
        row.update(correct="1 2 3 4 5" == "".join(pieces).strip(), chunks=len(pieces))
    except ProviderError as exc:
        row["error_code"] = exc.code
    report["cases"].append(row)
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        report["budget_attempts"] = [
            dict(r)
            for r in connection.execute(
                "SELECT purpose,state,input_reserved,output_reserved,input_charged,output_charged FROM model_budget_attempts"
            )
        ]
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    report["finished_at"] = datetime.now(UTC).isoformat()
    out = root / "report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(out),
                "cases": [
                    {k: r.get(k) for k in ("name", "correct", "error_code")}
                    for r in report["cases"]
                ],
            },
            ensure_ascii=False,
        )
    )
    return bool(report["cases"]) and all(case["correct"] for case in report["cases"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8788/v1")
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--credential-env", default="FORGE_PROVIDER_KEY_LIVE_TEST")
    parser.add_argument("--token-limit", type=int, default=300000)
    parser.add_argument(
        "--reasoning-effort", choices=("none", "minimal", "low", "medium", "high", "xhigh", "max")
    )
    args = parser.parse_args()
    if not os.environ.get(args.credential_env):
        parser.error("Credential environment reference is unset")
    raise SystemExit(0 if asyncio.run(run(args)) else 1)


if __name__ == "__main__":
    main()
