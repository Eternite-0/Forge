import asyncio
import hashlib
import json

import pytest
from test_tool_engine import mock_run as mock_run  # noqa: PLC0414 - shared pytest fixture
from test_tool_engine import reserve

from forge_core import gpu_tools
from forge_core.domain import DomainError
from forge_core.tool_domain import CONTRACTS
from forge_core.tool_engine import ToolEngine


def _result(stdout, exit_code=0):
    return {
        "argv": ["host-nvidia-smi"],
        "cwd": "private-worker",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": "",
        "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(b"").hexdigest(),
    }


def test_gpu_probe_is_fixed_read_only_and_retains_actual_output(monkeypatch):
    monkeypatch.setattr(gpu_tools, "_binary", lambda: "host-nvidia-smi")
    called = []
    process = _result("0, NVIDIA Example, 8192, 4096, 7\n")

    def runner(argv, cwd, env):
        called.append((argv, cwd, env))
        return process

    result = gpu_tools.probe_gpu(cwd="private-worker", env={"TEMP": "private-tmp"}, runner=runner)
    assert called == [
        (
            [
                "host-nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            "private-worker",
            {"TEMP": "private-tmp"},
        )
    ]
    assert result["status"] == "available"
    assert json.loads(result["devices_json"])[0]["memory_total_mib"] == 8192
    assert json.loads(result["probe_json"]) == process
    contract = next(contract for contract in CONTRACTS if contract.name == "gpu_status")
    assert contract.risk == "R0" and contract.input_schema["properties"] == {}
    ToolEngine._validate_output(contract, result)


def test_gpu_missing_tool_and_no_device_are_honest_unsupported(monkeypatch):
    monkeypatch.setattr(gpu_tools, "_binary", lambda: None)
    result = gpu_tools.probe_gpu(
        cwd="unused", env={}, runner=lambda *_: pytest.fail("must not run")
    )
    assert result["status"] == "unsupported" and json.loads(result["probe_json"]) == {
        "executed": False
    }
    monkeypatch.setattr(gpu_tools, "_binary", lambda: "host-nvidia-smi")
    no_device = gpu_tools.probe_gpu(cwd="unused", env={}, runner=lambda *_: _result("", 9))
    assert no_device["status"] == "unsupported" and no_device["devices_json"] == "[]"


@pytest.mark.parametrize(
    "stdout", ["bad", "0, Test, nan, 0, 0", "0, Test, 100, 200, 0", "0, Test, 100, 0, 101"]
)
def test_gpu_invalid_metrics_are_unavailable_without_fabricating_support(monkeypatch, stdout):
    monkeypatch.setattr(gpu_tools, "_binary", lambda: "host-nvidia-smi")
    result = gpu_tools.probe_gpu(cwd="unused", env={}, runner=lambda *_: _result(stdout))
    assert result["status"] == "unavailable" and result["devices_json"] == "[]"
    assert json.loads(result["probe_json"])["stdout"] == stdout


def test_real_host_gpu_diagnostic_is_bounded_and_truthful(tmp_path):
    result = gpu_tools.probe_gpu(
        cwd=str(tmp_path), env=ToolEngine._environment(), runner=ToolEngine._run_process
    )
    assert result["status"] in {"available", "unsupported", "unavailable"}
    devices = json.loads(result["devices_json"])
    if result["status"] == "available":
        assert devices and devices[0]["vendor"] == "nvidia"
        process = json.loads(result["probe_json"])
        assert process["exit_code"] == 0
        assert hashlib.sha256(process["stdout"].encode()).hexdigest() == process["stdout_sha256"]
    else:
        assert devices == [] and result["reason"]


def test_real_gpu_tool_engine_requires_approved_scope_and_commits_exact_receipt(mock_run):
    store, quest, worker, plan = mock_run
    engine = ToolEngine(store)
    reserve(store, "gpu-unapproved", "gpu_status", {})
    arguments = {
        "call_id": "gpu-unapproved",
        "quest_id": quest.id,
        "worker_id": worker.id,
        "run_id": "run",
        "lease_token": "lease",
        "tool_name": "gpu_status",
        "parameters": {},
    }
    with pytest.raises(DomainError, match="scope"):
        engine.prepare(**arguments)
    with store.unit_of_work() as work:
        connection = work.connection
        graph = json.loads(
            connection.execute(
                "SELECT graph_json FROM quest_plans WHERE id=?", (plan["id"],)
            ).fetchone()[0]
        )
        graph["nodes"][0]["allowed_tools"].append("gpu_status")
        graph_json = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        plan_digest = hashlib.sha256(graph_json.encode()).hexdigest()
        body = json.loads(
            connection.execute(
                "SELECT body_json FROM team_proposals WHERE id='team-id'"
            ).fetchone()[0]
        )
        body["plan_digest"] = plan_digest
        body["members"][0]["tool_scope"].append("gpu_status")
        body_json = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        team_digest = hashlib.sha256(body_json.encode()).hexdigest()
        tools_json = json.dumps(body["members"][0]["tool_scope"])
        connection.execute(
            "UPDATE quest_plans SET graph_json=?,digest=? WHERE id=?",
            (graph_json, plan_digest, plan["id"]),
        )
        connection.execute(
            "UPDATE task_nodes SET allowed_tools=? WHERE plan_id=?", (tools_json, plan["id"])
        )
        connection.execute(
            "UPDATE team_proposals SET body_json=?,plan_digest=?,proposal_digest=? WHERE id='team-id'",
            (body_json, plan_digest, team_digest),
        )
        connection.execute(
            "UPDATE worker_runs SET allowed_tools_json=?,plan_digest=?,team_digest=? WHERE id='run'",
            (tools_json, plan_digest, team_digest),
        )
    reserve(store, "gpu-approved", "gpu_status", {})
    arguments["call_id"] = "gpu-approved"
    prepared = engine.prepare(**arguments)
    assert prepared["risk"] == "R0" and prepared["resource_scope"] == "gpu:local"
    assert prepared["approval_id"] is None
    with pytest.raises(DomainError, match="parameters"):
        engine.prepare(**{**arguments, "parameters": {"command": "arbitrary"}})
    result = asyncio.run(engine.execute(**arguments))
    assert result["state"] == "succeeded"
    assert result["output"]["status"] in {"available", "unsupported", "unavailable"}
    with store.connection() as connection:
        receipt = connection.execute(
            "SELECT * FROM action_receipts WHERE id=?", (result["receipt_id"],)
        ).fetchone()
        assert (
            hashlib.sha256(receipt["result_json"].encode()).hexdigest() == receipt["result_digest"]
        )
        assert json.loads(receipt["result_json"]) == result["output"]
        assert receipt["intent_id"] == prepared["id"]
        assert connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert asyncio.run(engine.execute(**arguments))["receipt_id"] == result["receipt_id"]
