"""Bounded real-Provider acceptance through a fresh Core's authenticated API.

Supply the Provider credential in the process environment; this driver never
reads operator credential files. Published JSON contains allowlisted assertions,
usage and hashes, never raw prompts/replies, logs, secrets or database contents.
Offline tests are separate and are never reported as live success here.
"""

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "phase2"))

import live_core

REPO = Path(__file__).resolve().parents[2]
NOTICE_TYPES = (
    "worker.blocked",
    "worker.failed",
    "worker.result",
    "worker.help_requested",
    "approval.requested",
    "worker.command.cancel",
    "worker.progress",
    "quest.status_changed",
)


class ProbeFailure(RuntimeError):
    pass


def source_digest():
    result = hashlib.sha256()
    files = sorted((REPO / "backend" / "src").rglob("*.py")) + [Path(__file__)]
    for path in files:
        result.update(str(path.relative_to(REPO)).replace("\\", "/").encode())
        result.update(path.read_bytes())
    return result.hexdigest()


def snapshot(path):
    with closing(
        sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True, timeout=5)
    ) as connection:
        connection.row_factory = sqlite3.Row
        rows = lambda sql: [dict(row) for row in connection.execute(sql)]
        messages = rows("SELECT id,role,content,metadata FROM messages ORDER BY rowid")
        return {
            "router": rows("SELECT router_session_id FROM router_state")[0]["router_session_id"],
            "quests": rows("SELECT id,status FROM quests ORDER BY rowid"),
            "workers": rows(
                "SELECT id,quest_id,worker_id,state,execution_mode FROM worker_runs ORDER BY rowid"
            ),
            "hidden": rows("SELECT hidden FROM workers"),
            "request_quests": rows(
                "SELECT q.id,json_extract(m.metadata,'$.request_id') AS request_id "
                "FROM quests q JOIN messages m "
                "ON m.id=json_extract(q.metadata,'$.original_request_message_id')"
            ),
            "subscriptions": rows("SELECT run_id,active FROM superagent_subscriptions"),
            "core_reads": rows(
                "SELECT run_id,tool_name,state FROM worker_tool_calls "
                "WHERE tool_name IN ('read_trigger','recall_object')"
            ),
            "calls": rows(
                "SELECT purpose,state,created_at,input_charged,output_charged "
                "FROM model_budget_attempts ORDER BY created_at"
            ),
            "receipts": rows(
                "SELECT i.run_id,i.tool_name,i.resource_scope,r.execution_mode,r.result_json FROM action_receipts r "
                "JOIN action_intents i ON i.id=r.intent_id WHERE i.state='succeeded'"
            ),
            "events": rows(
                "SELECT id,event_type,quest_id,payload,created_at FROM events ORDER BY seq"
            ),
            "router_notices": [
                dict(row)
                for row in connection.execute(
                    "SELECT e.id,i.processed_at,i.notice_dead_at FROM events e "
                    "LEFT JOIN event_inbox i ON i.event_id=e.id AND i.consumer_id='router' "
                    "WHERE e.event_type IN (" + ",".join("?" for _ in NOTICE_TYPES) + ")",
                    NOTICE_TYPES,
                )
            ],
            "messages": messages,
            "message_digest": hashlib.sha256(
                json.dumps(messages, sort_keys=True).encode()
            ).hexdigest(),
        }


def call(core, method, route, payload=None):
    result = core.call(method, route, payload, timeout=180)
    if result["status"] != 200:
        raise ProbeFailure(f"api_status_{result['status']}")
    return result["body"]


def notices_for_tasks(state):
    events = {event["id"]: event for event in state["events"]}
    result = set()
    for message in state["messages"]:
        metadata = json.loads(message["metadata"])
        event = events.get(metadata.get("event_id"))
        if (
            metadata.get("source") == "router_notice"
            and event
            and (
                event["event_type"] in {"worker.result", "quest.completed", "task.verified"}
                or (
                    event["event_type"] == "quest.status_changed"
                    and json.loads(event["payload"]).get("to") == "completed"
                )
            )
        ):
            result.add(event["quest_id"])
    return result


def guard_bounds(state, args):
    if len(state["calls"]) >= args.max_calls:
        raise ProbeFailure("model_call_ceiling_reached")
    if any(call["state"] in {"uncertain", "unknown"} for call in state["calls"]):
        raise ProbeFailure("unknown_provider_outcome")
    if any(
        call["state"] != "settled" and time.time() - call["created_at"] > 150
        for call in state["calls"]
    ):
        raise ProbeFailure("unsettled_provider_timeout")


def quiescent_since(state, ready, previous, *, now=None):
    """Require drained Router deliveries and an unchanged settled snapshot for 2s."""
    if any(item["notice_dead_at"] is not None for item in state["router_notices"]):
        raise ProbeFailure("router_notice_dead_lettered")
    if (
        not ready
        or any(item["state"] != "settled" for item in state["calls"])
        or any(item["processed_at"] is None for item in state["router_notices"])
    ):
        return None, False
    now = time.monotonic() if now is None else now
    signature = (len(state["calls"]), len(state["events"]), state["message_digest"])
    since = previous[1] if previous is not None and previous[0] == signature else now
    return (signature, since), now - since >= 2


def running_intervals(state):
    """Observed ownership intervals, not continuous compute or CPU utilization.

    A repeated claim without a durable closing fact cannot erase the earlier
    ownership observation. Provider-capacity waits may occur inside an interval.
    """
    active, intervals = {}, []
    closes = {
        "worker.result",
        "worker.blocked",
        "worker.failed",
        "worker.paused",
        "worker.cancelled",
    }
    for event in state["events"]:
        run_id = json.loads(event["payload"]).get("run_id")
        if not run_id:
            continue
        if event["event_type"] == "worker.accepted":
            active.setdefault(run_id, event["created_at"])
        elif event["event_type"] in closes and run_id in active:
            intervals.append(
                {
                    "run_id": run_id,
                    "started_at": active.pop(run_id),
                    "ended_at": event["created_at"],
                }
            )
    boundaries = sorted(
        (stamp, change)
        for item in intervals
        for stamp, change in ((item["started_at"], 1), (item["ended_at"], -1))
    )
    current, maximum = 0, 0
    for _, change in boundaries:
        current += change
        maximum = max(maximum, current)
    return intervals, maximum


def final_accounting(report, state):
    settled = all(item["state"] == "settled" for item in state["calls"])
    drained = all(
        item["processed_at"] is not None and item["notice_dead_at"] is None
        for item in state["router_notices"]
    )
    report["checks"]["final_provider_calls_settled"] = settled
    report["checks"]["final_router_deliveries_drained"] = drained
    if not settled or not drained:
        report["status"] = "failed"
        report.setdefault("failure_code", "final_provider_or_router_work_unsettled")


def verified_artifact(state, workspace, quest_id, expected_marker):
    run_ids = {worker["id"] for worker in state["workers"] if worker["quest_id"] == quest_id}
    for receipt in state["receipts"]:
        if receipt["run_id"] not in run_ids or receipt["tool_name"] not in {
            "file_write",
            "file_revise",
        }:
            continue
        path = Path(receipt["resource_scope"]).resolve()
        if not path.is_relative_to(workspace.resolve()) or not path.is_file():
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if (
            expected_marker.encode() not in data
            or json.loads(receipt["result_json"]).get("sha256") != digest
        ):
            continue
        if any(
            read["run_id"] in run_ids
            and read["tool_name"] == "file_read"
            and read["resource_scope"] == receipt["resource_scope"]
            and json.loads(read["result_json"]).get("sha256") == digest
            for read in state["receipts"]
        ):
            return True
    return False


def run(args):
    credential = os.environ.get(args.credential_env)
    if not credential:
        raise ProbeFailure("provider_credential_environment_missing")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    workspace = REPO / ".local" / "superagent" / run_id
    workspace.mkdir(parents=True)
    inputs = workspace / "inputs"
    inputs.mkdir()
    (inputs / "alpha.txt").write_text(
        "Alpha input: 19 + 23 = 42. Marker ALPHA_42.\n", encoding="utf-8"
    )
    (inputs / "beta.txt").write_text("Beta input: 7 * 8 = 56. Marker BETA_56.\n", encoding="utf-8")
    provider_env = live_core.provider_env_from_key(credential)
    provider_env.update(
        {
            "FORGE_PROVIDER_BASE_URL": args.base_url,
            "FORGE_PROVIDER_CREDENTIAL_REF": args.credential_env,
            args.credential_env: credential,
            "FORGE_PROVIDER_TIMEOUT_SECONDS": "120",
            "FORGE_PROVIDER_MAX_RETRIES": "0",
        }
    )
    live_core.MODEL_ID = args.model
    profile = deepcopy(live_core.MODEL_PROFILE)
    profile.update(id=args.model, model_id=args.model)
    core = live_core.LiveCore(
        workspace, provider_env=provider_env, token_limit=args.token_limit, concurrency=2
    )
    database = workspace / "data" / "forge.sqlite3"
    report = {
        "run_id": run_id,
        "execution_mode": "live",
        "mock_results": None,
        "model_id": args.model,
        "provider_id": live_core.PROVIDER_ID,
        "upstream_identity": "unverified_proxy_reported_identity",
        "capability_source": "existing_exploratory_operator_priors_not_benchmark",
        "source_sha256": source_digest(),
        "max_calls": args.max_calls,
        "token_limit": args.token_limit,
        "checks": {},
        "status": "failed",
    }
    task_ids = []
    followup_markers = {}
    passed = set()
    max_running = 0
    began = time.monotonic()
    state = None
    executor = ThreadPoolExecutor(max_workers=2)
    submitted, resolved, task_by_label = {}, set(), {}
    quiet = None
    try:
        core.start(timeout=60)
        call(core, "POST", "/api/models", profile)
        initial_router = snapshot(database)["router"]
        for label, marker in (("alpha", "ALPHA_42"), ("beta", "BETA_56")):
            submitted[label] = executor.submit(
                call,
                core,
                "POST",
                "/api/conversation/message",
                {
                    "content": (
                        f"请自主在后台完成一个独立的小文件任务（{label}），与其他任务并行推进。"
                        f"必须使用 file_read 读取 inputs/{label}.txt，核对算术与标记 {marker}，"
                        "在本任务获准的工作目录内写入一个简短 report.txt，并重新读取验证文件。"
                        "仅使用一个执行 Worker；不需要额外研究、DAG分解或多个角色。"
                        "如收到同线程后续事件，读取完整引用并在最终总结包含其中verification_marker。"
                        "完成后在当前唯一对话向我汇报，不要让我操作任务或审批内部计划。"
                    ),
                    "request_id": f"superagent-{run_id}-{label}",
                },
            )
        while time.monotonic() - began < args.timeout:
            if core.process.poll() is not None:
                raise ProbeFailure("isolated_core_exited")
            state = snapshot(database)
            guard_bounds(state, args)
            for label, future in submitted.items():
                request_id = f"superagent-{run_id}-{label}"
                durable = [
                    item["id"]
                    for item in state["request_quests"]
                    if item["request_id"] == request_id
                ]
                if len(durable) > 1:
                    raise ProbeFailure("duplicate_task_for_request")
                if durable:
                    task_by_label[label] = durable[0]
                if future.done() and label not in resolved:
                    response = future.result()
                    quest_id = response.get("quest_id")
                    if (
                        response.get("intent") != "create_quest"
                        or not quest_id
                        or (label in task_by_label and task_by_label[label] != quest_id)
                    ):
                        raise ProbeFailure("independent_task_not_created")
                    task_by_label[label] = quest_id
                    resolved.add(label)
            task_ids = [
                task_by_label[label] for label in ("alpha", "beta") if label in task_by_label
            ]
            review_failure = next(
                (event for event in state["events"] if event["event_type"] == "review.blocked"),
                None,
            )
            if review_failure is not None:
                payload = json.loads(review_failure["payload"])
                report["first_failure"] = {
                    "event_type": "review.blocked",
                    "reason_code": payload.get("reason_code"),
                    "run_id": payload.get("run_id"),
                }
                raise ProbeFailure("independent_review_blocked")
            running = sum(worker["state"] == "running" for worker in state["workers"])
            max_running = max(max_running, running)
            for quest_id in task_ids:
                active = [
                    worker
                    for worker in state["workers"]
                    if worker["quest_id"] == quest_id
                    and worker["state"] in {"queued", "running", "paused", "blocked"}
                ]
                subscribed = {item["run_id"] for item in state["subscriptions"] if item["active"]}
                if any(worker["id"] in subscribed for worker in active) and quest_id not in passed:
                    marker = f"FOLLOWUP_{len(passed) + 1}"
                    before = sum(call["purpose"] == "router_trigger" for call in state["calls"])
                    trigger = call(
                        core,
                        "POST",
                        "/api/triggers",
                        {
                            "source_id": "conversation",
                            "external_id": f"followup-{quest_id}",
                            "thread_key": quest_id,
                            "actor": "local_user",
                            "addressed": True,
                            "payload": {
                                "instruction": "Scope unchanged. Read full continuation with read_trigger; include verification_marker in final summary.",
                                "padding": "untrusted continuation data " * 80,
                                "verification_marker": marker,
                            },
                        },
                    )
                    after = snapshot(database)
                    report["checks"][f"pass_through_{len(passed) + 1}"] = (
                        trigger.get("status") == "passed_through"
                        and bool(trigger.get("deliveries"))
                        and sum(item["purpose"] == "router_trigger" for item in after["calls"])
                        == before
                    )
                    followup_markers[quest_id] = marker
                    passed.add(quest_id)
            terminal = {quest["id"] for quest in state["quests"] if quest["status"] == "completed"}
            quiet, ready = quiescent_since(
                state,
                (
                    len(resolved) == 2
                    and len(set(task_ids)) == 2
                    and set(task_ids) <= terminal
                    and set(task_ids) <= notices_for_tasks(state)
                ),
                quiet,
            )
            if ready:
                break
            if any(worker["state"] in {"blocked", "failed"} for worker in state["workers"]):
                raise ProbeFailure("worker_blocked_or_failed")
            time.sleep(0.5)
        else:
            raise ProbeFailure("bounded_acceptance_timeout")
        report["checks"]["two_independent_conversation_tasks"] = len(set(task_ids)) == 2
        intervals, actual_parallel = running_intervals(state)
        report["worker_running_intervals"] = intervals
        report["max_running_workers_from_events"] = actual_parallel
        report["checks"]["parallel_workers_observed"] = actual_parallel >= 2
        report["checks"]["hidden_live_workers"] = (
            len(state["workers"]) >= 2
            and all(worker["execution_mode"] == "live" for worker in state["workers"])
            and all(worker["hidden"] == 1 for worker in state["hidden"])
        )
        report["checks"]["real_tool_receipts_for_both_tasks"] = all(
            any(
                receipt["run_id"] == worker["id"] and receipt["execution_mode"] == "live"
                for worker in state["workers"]
                if worker["quest_id"] == quest_id
                for receipt in state["receipts"]
            )
            for quest_id in task_ids
        )
        report["checks"]["router_delivered_both_results"] = set(task_ids) <= notices_for_tasks(
            state
        )
        report["checks"]["verified_file_artifacts_for_both_tasks"] = all(
            verified_artifact(state, workspace, quest_id, marker)
            for quest_id, marker in zip(task_ids, ("ALPHA_42", "BETA_56"), strict=True)
        )
        report["checks"]["input_source_read_for_both_tasks"] = all(
            any(
                receipt["tool_name"] == "file_read"
                and receipt["resource_scope"]
                == str((workspace / "inputs" / f"{label}.txt").resolve())
                and any(
                    worker["id"] == receipt["run_id"] and worker["quest_id"] == quest_id
                    for worker in state["workers"]
                )
                for receipt in state["receipts"]
            )
            for quest_id, label in zip(task_ids, ("alpha", "beta"), strict=True)
        )
        report["checks"]["spill_markers_in_worker_results"] = len(followup_markers) == 2 and all(
            any(
                event["event_type"] == "worker.result"
                and event["quest_id"] == quest_id
                and marker in json.loads(event["payload"]).get("claim", "")
                for event in state["events"]
            )
            for quest_id, marker in followup_markers.items()
        )
        report["checks"]["spilled_trigger_read_by_both_workers"] = all(
            any(
                read["tool_name"] == "read_trigger"
                and read["state"] == "succeeded"
                and any(
                    worker["id"] == read["run_id"] and worker["quest_id"] == quest_id
                    for worker in state["workers"]
                )
                for read in state["core_reads"]
            )
            for quest_id in task_ids
        )
        if any(item["state"] == "reserved" for item in state["calls"]):
            raise ProbeFailure("restart_requires_settled_provider_calls")
        before_workers = {worker["id"] for worker in state["workers"]}
        before_messages = [message["id"] for message in state["messages"]]
        core.stop()
        core = live_core.LiveCore(
            workspace, provider_env=provider_env, token_limit=args.token_limit, concurrency=2
        )
        core.start(timeout=60)
        restarted = snapshot(database)
        report["checks"]["restart_preserved_identity_and_state"] = (
            restarted["router"] == initial_router
            and {worker["id"] for worker in restarted["workers"]} == before_workers
            and [message["id"] for message in restarted["messages"]][: len(before_messages)]
            == before_messages
        )
        recap = call(
            core,
            "POST",
            "/api/conversation/message",
            {
                "content": "请简短回顾刚才 alpha 和 beta 两个后台文件任务的实际文件结果与算术结论，"
                "保留各自来源文件中的原始验证标记。只查询历史，不创建新任务。",
                "request_id": f"superagent-{run_id}-recall",
            },
        )
        recap_text = recap.get("assistant_message", {}).get("content", "")
        report["checks"]["recap_contains_both_actual_source_markers"] = all(
            marker in recap_text for marker in ("ALPHA_42", "BETA_56")
        )
        quiet = None
        while time.monotonic() - began < args.timeout:
            if core.process.poll() is not None:
                raise ProbeFailure("isolated_core_exited")
            state = snapshot(database)
            guard_bounds(state, args)
            quiet, ready = quiescent_since(state, True, quiet)
            if ready:
                break
            time.sleep(0.5)
        else:
            raise ProbeFailure("bounded_acceptance_timeout")
        report["checks"]["same_conversation_after_restart"] = len(state["quests"]) == 2
        report["checks"]["source_snapshot_unchanged"] = source_digest() == report["source_sha256"]
        report["status"] = "passed" if all(report["checks"].values()) else "failed"
    except Exception as exc:  # noqa: BLE001 - never publish Provider payloads or remote exception text.
        report["failure_code"] = str(exc) if isinstance(exc, ProbeFailure) else type(exc).__name__
    finally:
        core.stop()
        executor.shutdown(wait=False, cancel_futures=True)
        if database.exists():
            state = snapshot(database)
            final_accounting(report, state)
            report["model_calls"] = len(state["calls"])
            report["model_calls_by_purpose"] = {
                purpose: sum(item["purpose"] == purpose for item in state["calls"])
                for purpose in sorted({item["purpose"] for item in state["calls"]})
            }
            report["admission_states"] = {
                status: sum(item["state"] == status for item in state["calls"])
                for status in sorted({item["state"] for item in state["calls"]})
            }
            report["charged_tokens"] = sum(
                (item["input_charged"] or 0) + (item["output_charged"] or 0)
                for item in state["calls"]
            )
            report["workers_by_state"] = {
                status: sum(item["state"] == status for item in state["workers"])
                for status in sorted({item["state"] for item in state["workers"]})
            }
            report["message_sha256"] = state["message_digest"]
        report["max_running_workers_observed"] = max_running
        report["seconds"] = round(time.monotonic() - began, 3)
        output = args.output or REPO / "docs" / "audit" / "superagent" / f"{run_id}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "report": str(output),
                    "model_calls": report.get("model_calls"),
                    "failure_code": report.get("failure_code"),
                }
            )
        )
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-env", default="FORGE_PROVIDER_KEY_LIVE_TEST")
    parser.add_argument("--base-url", default="http://127.0.0.1:8788/v1")
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--max-calls", type=int, default=32)
    parser.add_argument("--token-limit", type=int, default=500000)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 4 <= args.max_calls <= 64 or not 1 <= args.timeout <= 1800 or args.token_limit <= 0:
        parser.error("Invalid bounded acceptance limits")
    try:
        return run(args)
    except ProbeFailure as exc:
        print(json.dumps({"status": "not_run", "failure_code": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
