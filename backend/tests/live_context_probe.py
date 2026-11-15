"""Explicitly opted-in, bounded live continuity probe; never run automatically by pytest."""

import asyncio
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from forge_core.context_service import ContextService
from forge_core.domain import (
    ConversationMessage,
    DomainError,
    ModelProfile,
    ProtocolSupport,
    Quest,
    QuestStatus,
    now_iso,
)
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService
from forge_core.settings import Workspace
from forge_core.store import Store

_SAFE_DOMAIN_MESSAGES = {
    "Invalid Router model decision",
    "Router response did not complete",
    "Router response exceeds output budget",
    "Router tool context budget exceeded",
    "Router tool step limit exceeded",
    "Too many Router tool calls",
    "Router requested an unauthorized tool",
    "Invalid context source range",
    "Invalid context source offset",
    "Context source changed concurrently",
    "Router context changed concurrently",
    "Router context budget exceeded",
    "Router context budget exhausted",
    "Router model capability is unknown or unsupported",
    "Router model configuration is ambiguous",
    "Router revision conflict",
    "Quest proposal requires title and goal",
    "Only a proposed Quest goal can be updated",
    "Quest version changed during update",
    "Memory indexing backlog exceeds safe limit",
}


def safe_diagnostics(value, secrets_to_redact):
    if isinstance(value, str):
        for secret in secrets_to_redact:
            value = value.replace(secret, "[REDACTED]")
        return value[:10000]
    if isinstance(value, list):
        return [safe_diagnostics(item, secrets_to_redact) for item in value[:100]]
    if isinstance(value, dict):
        return {
            safe_diagnostics(str(key), secrets_to_redact): safe_diagnostics(item, secrets_to_redact)
            for key, item in list(value.items())[:100]
        }
    return value if value is None or type(value) in (int, float, bool) else "[unsupported]"


class TwoCalls:
    execution_mode = "live"

    def __init__(self, gateway):
        self.gateway = gateway
        self.calls = 0
        self.decision = None
        self.responses = []

    async def generate(self, request):
        if self.calls >= 2:
            raise RuntimeError("probe_call_limit")
        self.calls += 1
        payload = self.gateway._payload(request, False)
        observation = {
            "input_serialized_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            "finish_reason": None,
            "tool_names": [],
        }
        self.responses.append(observation)
        response = await self.gateway.generate(request)
        observation.update(
            finish_reason=response.finish_reason,
            tool_names=[call.get("name") for call in response.tool_calls if isinstance(call, dict)],
        )
        try:
            self.decision = json.loads(response.text)
        except (ValueError, TypeError):
            pass
        return response


async def main():
    if os.environ.get("FORGE_LIVE_CONTEXT_PROBE") != "1":
        raise SystemExit("Set FORGE_LIVE_CONTEXT_PROBE=1 to explicitly opt in.")
    keys = (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
        "FORGE_ROUTER_MODEL_ID",
    )
    if any(not os.environ.get(key) for key in keys):
        raise SystemExit(
            "Provider identity, endpoint, credential reference and Router model must be configured."
        )
    credential_ref = os.environ["FORGE_PROVIDER_CREDENTIAL_REF"]
    if not os.environ.get(credential_ref):
        raise SystemExit(
            "The referenced provider credential is absent from the process environment."
        )
    token_text = os.environ.get("FORGE_CONTEXT_PROBE_TOKEN_LIMIT", "30000")
    if (
        not token_text.isascii()
        or not token_text.isdecimal()
        or len(token_text) > 6
        or not 1 <= int(token_text) <= 100000
    ):
        raise SystemExit("FORGE_CONTEXT_PROBE_TOKEN_LIMIT must be an integer from 1 to 100000.")
    token_limit = int(token_text)
    secret_values = sorted(
        {
            value
            for key, value in os.environ.items()
            if value
            and (
                key == credential_ref
                or (
                    len(value) >= 4
                    and any(word in key.upper() for word in ("KEY", "SECRET", "TOKEN", "PASSWORD"))
                )
            )
        },
        key=len,
        reverse=True,
    )
    root = (
        Path(__file__).resolve().parents[2]
        / ".local"
        / "phase1"
        / "milestone6"
        / (datetime.now(UTC).strftime("context-%Y%m%dT%H%M%SZ-") + secrets.token_hex(4))
    )
    root.mkdir(parents=True, exist_ok=False)
    workspace = Workspace.from_path(root)
    store = Store(root / "probe.sqlite3", workspace=workspace)
    report = {
        "mode": "live",
        "status": "incomplete",
        "cost": None,
        "cost_note": "Unknown; token-only admission, no monetary cap.",
        "attempt_limit": 2,
        "token_limit": token_limit,
    }
    target = Quest(
        title="北辰代理算法实验",
        goal="继续北辰代理算法实验并保留全部约束",
        metadata={"constraints": [f"约束{i:02d}：保留数据" for i in range(60)]},
    )
    store.save_quest(target)
    for i in range(105):
        old = Quest(title=f"旧{i}", goal="无关")
        store.save_quest(old)
        store.transition_quest(
            old.id,
            QuestStatus.CANCELLED.value,
            now_iso(),
            expected_version=0,
            expected_status=QuestStatus.PROPOSED.value,
        )
    first_message = None
    for constraint in target.metadata["constraints"]:
        message = ConversationMessage(role="user", content=constraint)
        first_message = first_message or message
        store.add_message(message)
    for index in range(6):
        store.add_message(
            ConversationMessage(role="assistant", content=f"实验记录 {index}: " + "x" * 4000)
        )
    report["before_context_bytes"] = len(
        json.dumps(ContextService(store).router_snapshot()["messages"], ensure_ascii=False).encode(
            "utf-8"
        )
    )
    ContextService(store).compact_router(keep_recent=0)
    identity = store.ensure_router()["router_session_id"]
    store = Store(root / "probe.sqlite3", workspace=workspace)
    report["router_identity_restored"] = store.ensure_router()["router_session_id"] == identity
    report["fixture_constraints"] = len(target.metadata["constraints"])
    report["fixture_cancelled_quests"] = 105
    rebuilt = ContextService(store).rebuild_router()
    capsule_text = json.dumps(rebuilt["capsule"], ensure_ascii=False)
    report["after_context_bytes"] = len(json.dumps(rebuilt, ensure_ascii=False).encode("utf-8"))
    source = ContextService(store).read_source("router", "main", first_message.id)
    report["source_sha256"] = hashlib.sha256(first_message.content.encode("utf-8")).hexdigest()
    report["source_readback_verified"] = (
        source["content"] == first_message.content
        and hashlib.sha256(source["content"].encode("utf-8")).hexdigest() == report["source_sha256"]
    )
    report["constraints_preserved"] = all(
        item in capsule_text for item in target.metadata["constraints"]
    )
    provider_id, model_id = os.environ["FORGE_PROVIDER_ID"], os.environ["FORGE_ROUTER_MODEL_ID"]
    store.save_model(
        ModelProfile(
            id="context-probe",
            provider=provider_id,
            model_id=model_id,
            display_name=model_id,
            context_window=65536,
            max_output_tokens=2048,
            tool_calling_support=ProtocolSupport(supported=True, source="operator"),
            structured_output_support=ProtocolSupport(supported=True, source="operator"),
        )
    )
    ledger = ModelCallBudgetLedger(store.path, token_limit=token_limit, concurrency_limit=1)
    config = ProviderConfig(
        id=provider_id,
        kind=os.environ["FORGE_PROVIDER_KIND"],
        base_url=os.environ["FORGE_PROVIDER_BASE_URL"],
        credential_ref=credential_ref,
        max_retries=0,
        timeout_seconds=40,
        supports_tools=True,
        supports_json_schema=True,
        allow_insecure_loopback=os.environ.get("FORGE_CONTEXT_PROBE_ALLOW_INSECURE_LOOPBACK")
        == "1",
    )
    provider = TwoCalls(ProviderGateway(config, budget_ledger=ledger))
    router = RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=provider,
        provider_id=provider_id,
        model_id=model_id,
        context_limit=65536,
        output_reserve=2048,
        max_conflicts=0,
    )
    try:
        result = await router.handle_user_message(
            "接着之前的北辰代理算法实验，按原有约束继续。", request_id="live-context-probe"
        )
        matched = (
            isinstance(provider.decision, dict)
            and provider.decision.get("quest_id") == target.id
            and result.get("intent") == "continue_quest"
        )
        report.update(
            intent=result.get("intent"),
            matched_original_quest=matched,
            status="passed"
            if matched
            and report["constraints_preserved"]
            and report["router_identity_restored"]
            and report["source_readback_verified"]
            else "target_not_recovered",
        )
    except Exception as exc:  # noqa: BLE001 - no provider exception text or headers in the report
        report.update(
            status="failed", error_type=type(exc).__name__, error_code=getattr(exc, "code", None)
        )
        if isinstance(exc, DomainError):
            report["domain_error"] = (
                str(exc) if str(exc) in _SAFE_DOMAIN_MESSAGES else "other_domain_error"
            )
            cause = exc.__cause__
            if isinstance(cause, ValidationError):
                report["schema_validation"] = [
                    {"type": error["type"], "location": list(error["loc"])}
                    for error in cause.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                ]
    with store.connection() as connection:
        attempts = connection.execute(
            "SELECT state,input_reserved,output_reserved,input_charged,output_charged,cost_charged,currency FROM model_budget_attempts ORDER BY created_at"
        ).fetchall()
        report["attempts"] = [dict(row) for row in attempts]
    report["calls"] = provider.calls
    if isinstance(provider.decision, dict):
        report["model_decision"] = {
            key: provider.decision.get(key)
            for key in ("intent", "reply", "title", "goal", "quest_id", "candidates")
            if key in provider.decision
        }
    report["responses"] = provider.responses
    report["report_path"] = str(root / "report.json")
    report = safe_diagnostics(report, secret_values)
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
