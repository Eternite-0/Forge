"""Offline only: real Router/Store/Gateway, MockTransport, and OS restart."""

import asyncio
import copy
import importlib.util
import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from forge_core.model_call_budget import BudgetExceeded
from forge_core.providers import ProviderGateway
from forge_core.runtime import ModelCallContext

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/continuity_minimal_probe.py"
SPEC = importlib.util.spec_from_file_location("minimal_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
SECRET = "OFFLINE_PLACEHOLDER_NOT_A_REAL_CREDENTIAL"
ENV = {"FORGE_PROVIDER_CREDENTIAL_REF": "FORGE_PROVIDER_KEY_OFFLINE",
       "FORGE_PROVIDER_KEY_OFFLINE": SECRET}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real socket/Provider forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(ProviderGateway, "generate", forbidden)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def frozen(monkeypatch):
    value = {"commit": "a" * 40, "dirty": True, "files": [], "manifest_sha256": "b" * 64}
    monkeypatch.setattr(probe, "source_freeze", lambda: copy.deepcopy(value))
    return value


def prepare(tmp_path):
    root = tmp_path / "fresh"
    plan = probe.protocol(root)
    root.mkdir(mode=0o700)
    probe.write_once(root / probe.PROTOCOL_NAME, plan)
    return root, probe.open_store(root)


def attempt(name="a", inp=10, out=20, state="settled"):
    return {"attempt_id": name, "call_id": name, "purpose": "router", "state": state,
            "model_id": probe.MODEL, "input_reserved": 100, "output_reserved": 200,
            "input_charged": inp, "output_charged": out}


def test_historical_denominator_and_failed_originals():
    history = probe.history()
    assert history["attempts"] == history["settled"] == 99
    assert history["charged_or_reserved_tokens"] == 1089785
    assert [r["accounting"]["attempts"] for r in history["retained_failed_conditions"]] == [21, 20, 25, 33]
    assert all(r["status"] == "failed" for r in history["retained_failed_conditions"])


def test_preflight_never_resets_history_or_runs_provider(tmp_path, frozen):
    result = probe.protocol(tmp_path / "new", environ=ENV)
    assert not (tmp_path / "new").exists()
    assert result["budget"]["stage_attempts"] == 99
    assert result["budget"]["maximum_if_new_allocation_fully_used"] == {
        "attempts": 117, "charged_or_reserved_tokens": 1309785}
    assert result["router_allocation"] == {"calls": 10, "tokens": 140000}
    assert result["thread_reserved"] == {"calls": 8, "tokens": 80000}
    assert result["original_per_task_caps"] == {"calls": 32, "tokens": 500000}
    assert SECRET not in probe.encoded(result).decode()


@pytest.mark.parametrize("reference", ["OFFLINE_KEY", "FORGE_PROVIDER_KEY_bad", SECRET, ""])
def test_credentials_must_use_provider_environment_contract(reference):
    assert not probe.credential_present({"FORGE_PROVIDER_CREDENTIAL_REF": reference, reference: SECRET})


def test_fresh_and_immutable_claim(tmp_path, frozen):
    root, _store = prepare(tmp_path)
    with pytest.raises(probe.Refused, match="exists_no_resume"):
        probe.run(root)
    with pytest.raises(FileExistsError):
        probe.write_once(root / probe.PROTOCOL_NAME, {})
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(probe.Refused):
        probe.fresh_workspace(link / "new")


def test_unknown_components_and_overruns_are_not_zero_or_clamped():
    rows = [attempt(), attempt("unknown", inp=None, out=50, state="reserved"),
            attempt("overrun", inp=200, out=400, state="overrun")]
    budget = probe.budget(rows)
    assert budget["new_charged_or_reserved_tokens"] == 30 + 150 + 600
    assert budget["stage_charged_or_reserved_tokens"] == 1089785 + 780
    assert budget["new_unknown_usage_attempts"] == budget["new_unsettled_attempts"] == 1
    assert not probe.budget([attempt(str(i)) for i in range(19)])["within_caps"]
    assert not probe.budget([attempt(inp=220001)])["within_caps"]


def test_shared_ledger_survives_reopen_and_preserves_thread_reserve(tmp_path, frozen):
    root, store = prepare(tmp_path)
    ledger = probe.campaign_ledger(store, router_only=True)
    for i in range(10):
        identity = ledger.admit(ModelCallContext(str(i), "router", store.workspace.id),
                                probe.PROVIDER_ID, probe.MODEL, input_tokens=100,
                                output_tokens=100, remote_timeout=120)
        ledger.settle(identity, {"input_tokens": 20, "output_tokens": 10})
    reopened = probe.campaign_ledger(probe.open_store(root), router_only=True)
    with pytest.raises(BudgetExceeded):
        reopened.admit(ModelCallContext("eleventh", "router", store.workspace.id),
                       probe.PROVIDER_ID, probe.MODEL, input_tokens=1, output_tokens=1,
                       remote_timeout=120)
    shared = probe.campaign_ledger(probe.open_store(root))
    for i in range(10, 18):
        identity = shared.admit(ModelCallContext(str(i), "router", store.workspace.id),
                                probe.PROVIDER_ID, probe.MODEL, input_tokens=100,
                                output_tokens=100, remote_timeout=120)
        shared.settle(identity, {"input_tokens": 20, "output_tokens": 10})
    with pytest.raises(BudgetExceeded):
        shared.admit(ModelCallContext("nineteenth", "router", store.workspace.id),
                     probe.PROVIDER_ID, probe.MODEL, input_tokens=1, output_tokens=1,
                     remote_timeout=120)
    before = Path(store.path).read_bytes()
    audit = probe.audit_ledger(root)
    assert Path(store.path).read_bytes() == before
    assert audit["budget"]["stage_attempts"] == 117
    assert audit["budget"]["new_charged_or_reserved_tokens"] == 540


def decision(intent="chat", **overrides):
    return {"intent": intent, "reply": "Generic history task", "title": "History task" if intent == "create_quest" else None,
            "goal": "Implement the original request" if intent == "create_quest" else None,
            "quest_id": None, "candidates": [], "task_kind": "long_research", **overrides}


class ScriptedWire:
    """Uses ONLY actual model-visible context and tool results, no fixture refs."""

    def __init__(self):
        self.requests = []

    def __call__(self, request):
        assert str(request.url) == probe.BASE_URL + "/chat/completions"
        packet = json.loads(request.content)
        self.requests.append(packet)
        messages = packet["messages"]
        context = next(json.loads(m["content"]) for m in messages
                       if m.get("content", "").startswith('{"')
                       and '"project_context"' in m["content"]
                       and '"recall"' in m["content"])
        if context["input"].startswith("Create a distinct task"):
            answer = decision("create_quest", project_id=context["project_context"]["focused_project_id"])
            calls = []
        else:
            pages = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}
            references = {context["recall"]["archive_ref"]}
            for page in pages.values():
                references.update(r["ref"] for r in page["relations"]
                                  if r["relation"] in {"previous_archive", "archived_message"})
            calls = []
            for reference in sorted(references):
                prior = sorted([p for p in pages.values() if p["ref"] == reference], key=lambda p: p["offset"])
                if prior and prior[-1]["complete"]:
                    continue
                arguments = {"reference": reference}
                if prior:
                    arguments.update(offset=prior[-1]["next_offset"], expected_hash=prior[-1]["source_hash"])
                calls.append({"id": "read_" + str(len(self.requests)) + "_" + str(len(calls)),
                              "type": "function", "function": {"name": "recall_object", "arguments": json.dumps(arguments)}})
            assert len(calls) <= 4
            answer = None
            if not calls:
                found = {}
                for reference in references:
                    prior = sorted([p for p in pages.values() if p["ref"] == reference], key=lambda p: p["offset"])
                    if not prior:
                        continue
                    record = json.loads("".join(p["content"] for p in prior))
                    text = record.get("content", "")
                    if "Historical user decision and unfinished todo: " in text:
                        name = "alpha_project" if "alpha_project" in text else "beta_project"
                        found[name] = json.loads(text.split("Historical user decision and unfinished todo: ")[1])
                assert set(found) == {"alpha_project", "beta_project"}
                answer = decision(reply=json.dumps(found))
        message = {"role": "assistant", "content": json.dumps(answer) if answer else ""}
        if calls:
            message["tool_calls"] = calls
        return httpx.Response(200, json={"choices": [{"message": message,
            "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100}})


def offline_stage(root, name):
    """Only subprocess test entry. Installs MockTransport BEFORE generating."""
    root = Path(root)
    plan = probe.load_plan(root)
    probe.source_freeze = lambda: plan["source"]
    real_factory = probe.make_gateway
    def factory(store, **kw):
        if name != "thread":
            return real_factory(store, **kw, transport=httpx.MockTransport(ScriptedWire()))
        # Reuse Cicero's durable offline fixture, not a second Thread simulator.
        # Only the transport is mocked; native Gateway admission/settlement runs.
        from test_continuity_thread_probe import ScriptedThread
        model, current = ScriptedThread(store), []

        async def transport(_http_request):
            response = await model.generate(current[0])
            message = {"role": "assistant", "content": response.text}
            if response.tool_calls:
                message["tool_calls"] = [{"id": c["id"], "type": "function", "function": {
                    "name": c["name"], "arguments": json.dumps(c["arguments"])}}
                    for c in response.tool_calls]
            return httpx.Response(200, json={"choices": [{"message": message,
                "finish_reason": response.finish_reason}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100}})

        gateway = real_factory(store, **kw, transport=httpx.MockTransport(transport))
        original = gateway.generate

        async def capture(request):
            current[:] = [request]
            return await original(request)

        gateway.generate = capture
        return gateway

    probe.make_gateway = factory
    async def guarded_stage():
        # Windows initializes the event loop with a local socketpair before
        # entering this coroutine. Block all stage networking, not that private
        # loop bootstrap; MockTransport is still mandatory for provider calls.
        def forbidden(*args, **kwargs):
            raise AssertionError("network forbidden")

        with (
            patch.object(socket.socket, "connect", forbidden),
            patch.object(socket.socket, "connect_ex", forbidden),
            patch.object(socket, "create_connection", forbidden),
        ):
            await probe.stage(root, name)

    asyncio.run(guarded_stage())


def test_offline_stage_allows_loop_bootstrap_but_blocks_network(tmp_path, monkeypatch):
    import _socket

    # Exercise the Python socketpair fallback used by Windows even on Linux.
    # The autouse guard must permit only loop construction in this test; the
    # helper must reinstate the guard before any stage code executes.
    monkeypatch.setattr(socket.socket, "connect", _socket.socket.connect)
    monkeypatch.setattr(socket, "socketpair", socket._fallback_socketpair)
    monkeypatch.setattr(probe, "load_plan", lambda root: {"source": {}})
    seen = []

    async def stage(root, name):
        seen.append((root, name))
        with socket.socket() as connection:
            for operation in (connection.connect, connection.connect_ex):
                with pytest.raises(AssertionError, match="network forbidden"):
                    operation(("127.0.0.1", 9))
        with pytest.raises(AssertionError, match="network forbidden"):
            socket.create_connection(("127.0.0.1", 9))

    monkeypatch.setattr(probe, "stage", stage)
    monkeypatch.setattr(probe, "make_gateway", probe.make_gateway)
    monkeypatch.setattr(probe, "source_freeze", probe.source_freeze)
    offline_stage(tmp_path, "seed")
    assert seen == [(tmp_path, "seed")]


def offline_child(root, name):
    command = [sys.executable, "-c",
               ("import sys; sys.path.insert(0,sys.argv[1]); "
                "from test_continuity_minimal_probe import offline_stage; offline_stage(sys.argv[2],sys.argv[3])"),
               str(Path(__file__).parent), str(root), name]
    process = subprocess.run(command, capture_output=True, check=False, env=os.environ.copy(), timeout=45)
    assert process.returncode == 0, process.stderr.decode()
    return json.loads((root / f"{name}-result.json").read_bytes())


def test_real_router_tools_two_projects_compaction_and_os_restart(tmp_path, frozen, monkeypatch):
    monkeypatch.setattr(probe, "child", offline_child)
    report = probe.run(tmp_path / "fresh")
    assert report["failure_codes"] == []
    assert report["status"] == "router_probe_passed_not_full_acceptance"
    assert report["project_switch"]["pid"] != report["recall"]["pid"]
    assert report["recall"]["decision_and_pending_recovered"]
    assert report["recall"]["raw_result_persisted_chat_reply_identical"]
    assert len(report["recall"]["two_hop_chain"]) == 3
    assert report["audit"]["budget"]["new_attempts"] == 6
    assert report["audit"]["budget"]["stage_attempts"] == 105
    assert report["audit"]["budget"]["stage_charged_or_reserved_tokens"] == 1089785 + 1200
    assert report["thread"] == "not_run_reserved"
    assert not report["audit"]["failure_codes"]
    assert SECRET not in probe.encoded(report).decode()


def test_with_thread_real_components_and_shared_ledger_offline(tmp_path, frozen, monkeypatch):
    monkeypatch.setattr(probe, "child", offline_child)
    report = probe.run(tmp_path / "fresh", with_thread=True)
    # Native MockTransport is explicitly Mock. Even a complete scripted Thread
    # PASS must not satisfy the live-only child gate or finalize a live Worker.
    assert report["status"] == "failed" and "driver_failed_no_retry" in report["failure_codes"]
    thread = json.loads((tmp_path / "fresh/thread-report.json").read_bytes())
    assert thread["status"] == "offline_passed" and not thread["passed"]
    assert thread["offline_checks_passed"] and not thread["live_review_pass_matches"]
    refused = json.loads((tmp_path / "fresh/thread-result.json").read_bytes())
    assert refused["passed"] is False and refused["status"] == "offline_passed"
    assert len(thread["checks"]) == 15 and all(thread["checks"].values())
    assert thread["generate_attempts"] == 6
    assert thread["execution_budget"]["existing_ledger_call_limit"] == 18
    assert thread["execution_budget"]["existing_ledger_token_limit"] == 220000
    assert thread["execution_budget"]["output_limit"] == 8192
    assert report["audit"]["budget"]["new_attempts"] == 12
    assert report["audit"]["budget"]["stage_attempts"] == 111
    assert report["audit"]["budget"]["stage_charged_or_reserved_tokens"] == 1089785 + 2400
    assert len({report[k]["pid"] for k in ("project_switch", "recall")} | {refused["pid"]}) == 3
    assert report["audit"]["unresolved_or_active"]["active_workers"] == 1
    assert "unsettled_or_active_work" in report["failure_codes"]


def test_child_failure_stops_no_restart_or_replay(tmp_path, frozen, monkeypatch):
    calls = []
    def fail(root, name):
        calls.append(name)
        probe.open_store(root)
        raise probe.Refused("injected_child_failure")
    monkeypatch.setattr(probe, "child", fail)
    result = probe.run(tmp_path / "fresh")
    assert result["status"] == "failed"
    assert result["failure_codes"] == ["injected_child_failure"]
    assert calls == ["seed"]
    assert result["audit"]["budget"]["stage_attempts"] == 99


def test_host_answer_or_missing_tools_cannot_pass():
    from forge_core.runtime import ModelResponse
    answer = ModelResponse(text="DECISION_secret TODO_secret", tool_calls=[], usage={})
    with pytest.raises(probe.Refused):
        probe.recall_evidence([([{"role": "system", "content": ""},
            {"role": "user", "content": json.dumps({"recall": {"archive_ref": "archive:1"}})}], answer)],
            {}, {"alpha_project": {"decision": "DECISION_secret", "pending": "TODO_secret"}})


def test_final_reply_rejects_swapped_projects():
    expected = {name: probe.facts(Path("/offline/oracle"), name)
                for name in ("alpha_project", "beta_project")}
    assert probe.checked_reply(json.dumps(expected), expected) == expected
    swapped = dict(zip(expected, reversed(list(expected.values())), strict=True))
    with pytest.raises(probe.Refused, match="recall_reply_project_mapping_mismatch"):
        probe.checked_reply(json.dumps(swapped), expected)


def test_cli_never_echoes_secret_and_internal_stage_needs_parent(tmp_path, capsys):
    previous = logging.root.manager.disable
    with pytest.raises(SystemExit):
        probe.main(["run", "--workspace", str(tmp_path / "fresh"), "--api-key", SECRET])
    assert logging.root.manager.disable == previous
    assert SECRET not in capsys.readouterr().out
    assert probe.main(["_seed", "--workspace", str(tmp_path / "fresh")]) == 2
    assert logging.root.manager.disable == previous
    assert SECRET not in capsys.readouterr().out
