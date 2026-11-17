"""Offline durable regressions for the live Thread harness; no real HTTP."""

import asyncio
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_superagent_delegation import delegated as delegated  # noqa: PLC0414
from test_worker_intelligence_phase2 import delivery, response, tool

from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderGateway

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/continuity_thread_probe.py"
SPEC = importlib.util.spec_from_file_location("thread_probe_tests", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture(autouse=True)
def no_real_provider(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("real Provider forbidden in offline tests")

    monkeypatch.setattr(ProviderGateway, "generate", forbidden)


class ScriptedThread:
    execution_mode = "mock"

    def __init__(self, store, *, yields=True):
        self.store, self.yields, self.calls = store, yields, 0
        self.config = SimpleNamespace(id="mock-provider", max_retries=0)

    async def generate(self, request):
        self.calls += 1
        if self.yields:
            await asyncio.sleep(0)
        if request.budget_context.purpose == "independent_reviewer":
            packet = probe._request_packet(request.messages)
            assert packet["artifacts"] and any(r.get("context_only") for r in packet["receipts"])
            return response(
                {"verdict": "PASS", "findings": [], "revision_goals": [], "confidence": 0.9}
            )
        run_id = request.budget_context.worker_run_id
        with self.store.connection() as c:
            run = c.execute("SELECT * FROM worker_runs WHERE id=?", (run_id,)).fetchone()
            goal = c.execute("SELECT goal FROM quests WHERE id=?", (run["quest_id"],)).fetchone()[0]
            triggers = c.execute(
                "SELECT t.* FROM superagent_triggers t JOIN superagent_subscriptions s "
                "ON s.thread_id=t.thread_id WHERE s.run_id=? ORDER BY t.rowid",
                (run_id,),
            ).fetchall()
        pages = [
            json.loads(m["content"])
            for m in request.messages
            if m["role"] == "tool" and '"payload_sha256"' in m.get("content", "")
        ]
        for trigger in triggers:
            prior = [p for p in pages if p.get("trigger_id") == trigger["id"]]
            cursor = max((p["offset_chars"] + len(p["text"]) for p in prior), default=0)
            if cursor < len(trigger["payload_json"]):
                # Omit initial offset to cover the legal tool default, follow real results thereafter.
                args = {"trigger_id": trigger["id"], "max_chars": 4096}
                if cursor:
                    args["offset_chars"] = cursor
                return tool("read_trigger", args, ordinal=self.calls)
        path = self.store.workspace.root / f"work/{run['quest_id']}/thread-result.json"
        if not path.exists():
            texts = sorted(
                (p for p in pages if p["trigger_id"] == triggers[-1]["id"]),
                key=lambda p: p["offset_chars"],
            )
            addition = json.loads("".join(p["text"] for p in texts))
            content = {
                "base": re.search(r"base='([^']+)'", goal)[1],
                "notes": [addition["z_required_note"]],
            }
            return tool(
                "file_write",
                {"path": str(path), "content": json.dumps(content)},
                ordinal=self.calls,
            )
        result = delivery("Actual JSON includes the complete Thread addition")
        result["artifacts"] = [str(path)]
        return response(result)


def test_thread_harness_real_sqlite_tools_artifact_and_review_offline(delegated):
    store = delegated[0]
    model = ScriptedThread(store)
    report = asyncio.run(probe.run_thread(store, model))
    assert report["status"] == "offline_passed", report
    assert report["offline_checks_passed"] and not report["passed"]
    assert not report["live_review_pass_matches"]
    assert len(report["checks"]) == 15 and all(report["checks"].values())
    assert model.calls == 6 and report["full_read_proof"]["page_count"] == 2
    assert "generate" not in vars(model)


def test_thread_harness_no_await_window_fails_once_without_replay(delegated):
    model = ScriptedThread(delegated[0], yields=False)
    report = asyncio.run(probe.run_thread(delegated[0], model))
    assert report["status"] == "failed" and model.calls == 1
    assert not report.get("injection") and not report["passed"]
    assert "generate" not in vars(model)


def test_thread_harness_refuses_retry_policy_before_generation(delegated):
    model = ScriptedThread(delegated[0])
    model.config.max_retries = 1
    report = asyncio.run(probe.run_thread(delegated[0], model))
    assert report["failure_code"] == "explicit_gateway_mode_and_zero_retries_required"
    assert model.calls == 0


@pytest.mark.parametrize(
    "ledger_present", [False, True], ids=["missing-ledger", "missing-call-cap"]
)
def test_thread_harness_live_requires_existing_capped_ledger(delegated, ledger_present):
    store = delegated[0]
    ledger = (
        ModelCallBudgetLedger(store.path, token_limit=220000, concurrency_limit=1)
        if ledger_present
        else None
    )
    gateway = ProviderGateway(
        ProviderConfig(
            id="mock-provider",
            kind="openai_compatible",
            base_url="http://127.0.0.1:1/v1",
            credential_ref="FORGE_PROVIDER_KEY_OFFLINE",
            max_retries=0,
            allow_insecure_loopback=True,
        ),
        budget_ledger=ledger,
    )
    report = asyncio.run(probe.run_thread(store, gateway))
    assert report["failure_code"] == (
        "existing_live_ledger_call_limit_required"
        if ledger_present
        else "existing_live_budget_ledger_required"
    )
    assert not report["passed"] and not report["worker_rounds"]
