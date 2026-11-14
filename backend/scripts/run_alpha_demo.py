"""Explicitly staged FORGE Alpha probe; never executes a paid model itself.

All model calls are made by an already-running, operator-configured Core. The
caller supplies its session bearer through FORGE_ALPHA_CORE_BEARER, never argv.
Approvals are separate commands bound to a reviewed digest and version.
"""

import argparse
import hashlib
import json
import os
import queue
import re
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROLES = frozenset({"research", "math", "architect", "coding", "experiment", "reviewer"})
SOURCE_URL = "https://arxiv.org/abs/2305.16291"
GOAL = (
    "研究一个有界的 Agent 任务调度实验。请组建 Research、Math、Architect、Coding、"
    "Experiment、Reviewer 六个角色，读取官方论文来源 "
    + SOURCE_URL
    + " 并保留来源和哈希；设计并实现两个候选的可用性与成本选择算法，"
    "将数值 AST 候选保存为 work/scheduler.py；Experiment 读取候选并提出实验需求，"
    "Core 用固定 scheduler-v1 十个案例独立验证；Reviewer 必须读取候选，并用受控"
    " worker_read_action_receipt 工具读取直接上游 Coding 的写入回执，不能仅引用普通文件。"
    "请给出 DAG、预算、权限和待批准事项；所有结果、论文与数学结论须区分证据和未验证主张。"
)


class CoreClient:
    def __init__(self, base_url, bearer, *, transport=None):
        parts = urlsplit(base_url)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"127.0.0.1", "::1"}
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or parts.port is None
            or not re.fullmatch(r"[0-9a-f]{64}", bearer or "")
        ):
            raise ValueError("A numeric loopback Core URL and session bearer are required")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=240, write=10, pool=5),
            headers={"Authorization": "Bearer " + bearer},
        )
        self._timed_out = False

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def call(self, method, path, *, payload=None):
        if not path.startswith("/api/") and path != "/health":
            raise ValueError("Unsupported Core path")
        if self._timed_out:
            raise RuntimeError("Client is unavailable after an unknown request outcome")
        result = queue.Queue(maxsize=1)

        def invoke():
            try:
                with self._client.stream(method, path, json=payload) as response:
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 4 * 1024 * 1024:
                            raise ValueError("Core response exceeded bound")
                    if response.status_code >= 400:
                        raise RuntimeError(f"Core returned HTTP {response.status_code}")
                result.put((True, json.loads(data)))
            except Exception as exc:  # noqa: BLE001 - propagate locally, never print request headers
                result.put((False, exc))

        threading.Thread(target=invoke, daemon=True).start()
        try:
            ok, value = result.get(timeout=300)
        except queue.Empty:
            self._timed_out = True
            raise TimeoutError(
                "Core request outcome unknown; retry only with the same request ID"
            ) from None
        if not ok and isinstance(value, (httpx.TimeoutException, httpx.TransportError)):
            self._timed_out = True
            raise TimeoutError(
                "Core request outcome unknown; retry only with the same request ID"
            ) from None
        if not ok:
            raise value
        return value


def _identity(quest_id, digest, revision, expected_version):
    if not re.fullmatch(r"quest_[A-Za-z0-9]+", quest_id or "") or not re.fullmatch(
        r"[0-9a-f]{64}", digest or ""
    ):
        raise ValueError("Quest ID and exact reviewed digest are required")
    if (
        type(revision) is not int
        or revision < 1
        or type(expected_version) is not int
        or expected_version < 0
    ):
        raise ValueError("Exact reviewed revision and version are required")


def approve_plan(client, quest_id, digest, revision, expected_version):
    _identity(quest_id, digest, revision, expected_version)
    return client.call(
        "POST",
        f"/api/quests/{quest_id}/plan/approve",
        payload={"digest": digest, "plan_revision": revision, "expected_version": expected_version},
    )


def approve_team(client, quest_id, digest, revision, expected_version):
    _identity(quest_id, digest, revision, expected_version)
    return client.call(
        "POST",
        f"/api/quests/{quest_id}/teams/approve",
        payload={
            "proposal_digest": digest,
            "plan_revision": revision,
            "expected_version": expected_version,
        },
    )


def assess_evidence(facts):
    """Fail closed; callers must derive facts from Core, never from a model response."""
    missing_roles = sorted(ROLES - set(facts.get("roles", [])))
    return {
        # Untrusted dictionaries cannot constitute an independent audit.
        "status": "incomplete",
        "missing_roles": missing_roles,
        "source_verified": False,
        "semantic_verified": False,
        "relay_verified": False,
        "restart_verified": False,
        "compaction_verified": False,
        "scope": "Bounded engineering demonstration on 10 fixed cases; no claim of scientific validity or general algorithm superiority.",
    }


def _exclusive_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def prepare(client, request_id, request_checkpoint):
    if not re.fullmatch(r"alpha_[0-9a-f]{32}", request_id or ""):
        raise ValueError("Explicit alpha request ID is required")
    settings = client.call("GET", "/api/settings")
    if settings.get("restore_fenced"):
        raise RuntimeError("Workspace is fenced")
    request_checkpoint = Path(request_checkpoint)
    if request_checkpoint.exists():
        saved = json.loads(request_checkpoint.read_text(encoding="utf-8"))
        if (
            saved.get("request_id") != request_id
            or saved.get("workspace_id") != settings["workspace_id"]
            or saved.get("goal_sha256") != hashlib.sha256(GOAL.encode()).hexdigest()
        ):
            raise ValueError("Request checkpoint does not match this run")
    else:
        _exclusive_json(
            request_checkpoint,
            {
                "request_id": request_id,
                "workspace_id": settings["workspace_id"],
                "goal_sha256": hashlib.sha256(GOAL.encode()).hexdigest(),
            },
        )
    result = client.call(
        "POST",
        "/api/conversation/message",
        payload={"content": GOAL, "request_id": request_id},
    )
    quest_id = result.get("quest_id")
    if (
        result.get("intent") != "create_quest"
        or not isinstance(quest_id, str)
        or not re.fullmatch(r"quest_[A-Za-z0-9]+", quest_id)
    ):
        return {
            "status": "incomplete",
            "reason": "router_did_not_return_quest_identity",
            "router_intent": result.get("intent"),
        }
    quest = client.call("GET", f"/api/quests/{quest_id}").get("quest", {})
    if quest.get("id") != quest_id:
        return {"status": "incomplete", "reason": "quest_identity_mismatch"}
    return {
        "status": "awaiting_plan",
        "quest_id": quest_id,
        "workspace_id": settings["workspace_id"],
        "source_url": SOURCE_URL,
        "user_message_id": result["user_message"]["id"],
        "mode": "operator_configured_core",
    }


def review_plan(client, quest_id):
    if not re.fullmatch(r"quest_[A-Za-z0-9]+", quest_id or ""):
        raise ValueError("Invalid Quest ID")
    quest = client.call("GET", f"/api/quests/{quest_id}")["quest"]
    plan = client.call("GET", f"/api/quests/{quest_id}/plan")
    return {
        "stage": "plan_review",
        "quest": quest,
        "plan": plan,
        "operator_action": {
            "quest_id": quest_id,
            "digest": plan["digest"],
            "revision": plan["plan_revision"],
            "expected_version": quest["version"],
        },
        "note": "Review DAG, exact source URL, permissions and budgets before separately approving.",
    }


def review_team(client, quest_id):
    if not re.fullmatch(r"quest_[A-Za-z0-9]+", quest_id or ""):
        raise ValueError("Invalid Quest ID")
    quest = client.call("GET", f"/api/quests/{quest_id}")["quest"]
    teams = client.call("GET", f"/api/quests/{quest_id}/teams")
    proposed = [
        team
        for team in teams
        if team["status"] == "proposed" and team["plan_revision"] == quest["plan_revision"]
    ]
    if len(proposed) != 1:
        return {"status": "incomplete", "reason": "no_unique_current_team"}
    team = proposed[0]
    roles = {str(role.get("id", "")).lower() for role in team["body"].get("roles", [])}
    return {
        "stage": "team_review",
        "team": team,
        "missing_roles": sorted(ROLES - roles),
        "operator_action": {
            "quest_id": quest_id,
            "digest": team["proposal_digest"],
            "revision": team["plan_revision"],
            "expected_version": quest["version"],
        },
        "note": "Model capability overrides are operator declarations, not benchmarks; approval does not grant tools or network access.",
        "model_profiles": client.call("GET", "/api/models"),
    }


def current_status(client, quest_id):
    if not re.fullmatch(r"quest_[A-Za-z0-9]+", quest_id or ""):
        raise ValueError("Invalid Quest ID")
    return {
        "status": "observation_only",
        "quest": client.call("GET", f"/api/quests/{quest_id}"),
        "activities": client.call("GET", f"/api/activity?quest_id={quest_id}&limit=100"),
        "approvals": client.call("GET", f"/api/approvals?quest_id={quest_id}"),
        "budget": client.call("GET", "/api/budget"),
        "note": "Pending R2 source access requires an exact action approval by the local operator; this report does not approve it.",
    }


def _canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _open_database(settings, db_path):
    root = Path(settings["workspace_root"]).resolve()
    db = Path(db_path)
    if not db.is_absolute():
        raise ValueError("Database path must be absolute")
    db = db.resolve(strict=True)
    if not db.is_relative_to(root):
        raise ValueError("Database must be inside the selected Workspace")
    connection = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        binding = connection.execute("SELECT id FROM workspace WHERE singleton=1").fetchone()
        if binding is None or binding["id"] != settings["workspace_id"]:
            raise ValueError("Database Workspace identity mismatch")
    except BaseException:
        connection.close()
        raise
    return connection


def _archive_proof(connection):
    row = connection.execute(
        "SELECT * FROM context_archives WHERE scope='router' AND scope_id='main' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    sources = json.loads(row["source_json"])
    capsule = json.loads(row["capsule_json"])
    if (
        not sources
        or _canonical_hash(sources) != row["source_hash"]
        or _canonical_hash(capsule) != row["capsule_hash"]
    ):
        raise ValueError("Context archive integrity failure")
    for source in sources:
        original = connection.execute(
            "SELECT * FROM messages WHERE conversation_id='main' AND id=?", (source["id"],)
        ).fetchone()
        if original is None or _canonical_hash(dict(original)) != source["hash"]:
            raise ValueError("Context source integrity failure")
    return {
        "archive_id": row["id"],
        "source_count": len(sources),
        "source_hash": row["source_hash"],
        "capsule_hash": row["capsule_hash"],
    }


def checkpoint(client, db_path=None):
    settings = client.call("GET", "/api/settings")
    conversation = client.call("GET", "/api/conversation")
    health = client.call("GET", "/health")
    proof = None
    if db_path is not None:
        with closing(_open_database(settings, db_path)) as connection:
            state = connection.execute(
                "SELECT router_session_id FROM router_state WHERE conversation_id='main'"
            ).fetchone()
            if (
                state is None
                or state["router_session_id"] != conversation["router"]["router_session_id"]
            ):
                raise ValueError("Database and Core Router identities differ")
            proof = _archive_proof(connection)
    return {
        "workspace_id": settings["workspace_id"],
        "router_session_id": conversation["router"]["router_session_id"],
        "session_challenge_sha256": hashlib.sha256(health["session"].encode()).hexdigest(),
        "archive_proof": proof,
        "generation": settings.get("core_generation"),
    }


def _restart_proof(connection, workspace_id, router_id, quest_id, plan_id, before, after, archive):
    first, second = before.get("generation"), after.get("generation")
    if (
        not isinstance(first, str)
        or not isinstance(second, str)
        or not re.fullmatch(r"[0-9a-f]{32}", first)
        or not re.fullmatch(r"[0-9a-f]{32}", second)
        or first == second
        or not archive
        or before.get("workspace_id") != workspace_id
        or after.get("workspace_id") != workspace_id
        or before.get("router_session_id") != router_id
        or after.get("router_session_id") != router_id
        or before.get("archive_proof") != archive
        or not re.fullmatch(r"[0-9a-f]{64}", before.get("session_challenge_sha256", ""))
        or not re.fullmatch(r"[0-9a-f]{64}", after.get("session_challenge_sha256", ""))
        or before.get("session_challenge_sha256") == after.get("session_challenge_sha256")
    ):
        return None
    rows = []
    for generation, checkpoint in ((first, before), (second, after)):
        row = connection.execute(
            "SELECT seq,created_at,payload FROM events WHERE event_type='core.started' AND json_extract(payload,'$.generation')=? ORDER BY seq DESC LIMIT 1",
            (generation,),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        if (
            payload.get("workspace_id") != workspace_id
            or payload.get("router_session_id") != router_id
            or type(payload.get("pid")) is not int
            or payload["pid"] <= 0
            or payload.get("session_challenge_sha256") != checkpoint.get("session_challenge_sha256")
        ):
            return None
        rows.append((row, payload))
    first_row, second_row = rows[0][0], rows[1][0]
    archive_row = connection.execute(
        "SELECT created_at FROM context_archives WHERE id=? AND scope='router' AND scope_id='main'",
        (archive["archive_id"],),
    ).fetchone()
    if not archive_row or not (
        first_row["seq"] < second_row["seq"]
        and first_row["created_at"] <= archive_row["created_at"] < second_row["created_at"]
    ):
        return None
    activity = connection.execute(
        """SELECT e.id,e.payload,w.id AS run_id FROM events e JOIN worker_runs w
        ON w.id=json_extract(e.payload,'$.run_id') AND w.worker_id=e.worker_id
        WHERE e.conversation_id='main' AND e.event_type='worker.result' AND e.quest_id=?
        AND e.seq>? AND w.quest_id=? AND w.plan_id=? AND w.execution_mode='live' AND w.created_at<?
        ORDER BY e.seq LIMIT 1""",
        (quest_id, second_row["seq"], quest_id, plan_id, second_row["created_at"]),
    ).fetchone()
    if activity is None or json.loads(activity["payload"]).get("plan_id") != plan_id:
        return None
    continuations = connection.execute(
        "SELECT * FROM router_turns WHERE conversation_id='main' AND created_at>? ORDER BY created_at LIMIT 100",
        (second_row["created_at"],),
    ).fetchall()
    for continuation in continuations:
        try:
            turn = json.loads(continuation["result_json"])
            if turn.get("quest_id") != quest_id or turn.get("intent") not in {
                "continue_quest",
                "status_query",
            }:
                continue
            user, assistant = turn["user_message"], turn["assistant_message"]
            messages = [
                connection.execute(
                    "SELECT * FROM messages WHERE conversation_id='main' AND id=?", (message["id"],)
                ).fetchone()
                for message in (user, assistant)
            ]
            if any(row is None for row in messages):
                continue
            if (
                messages[0]["role"] != "user"
                or messages[1]["role"] != "assistant"
                or any(
                    row["content"] != data["content"] or row["created_at"] != data["created_at"]
                    for row, data in zip(messages, (user, assistant), strict=True)
                )
                or messages[0]["created_at"] <= second_row["created_at"]
                or hashlib.sha256(messages[0]["content"].encode()).hexdigest()
                != continuation["input_hash"]
                or json.loads(messages[1]["metadata"]).get("execution_mode") != "live"
            ):
                continue
            return {
                "first_generation": first,
                "second_generation": second,
                "continuation_request_id": continuation["request_id"],
                "worker_event_id": activity["id"],
                "worker_run_id": activity["run_id"],
            }
        except (ValueError, TypeError, KeyError):
            continue
    return None


def _quest_origin(connection, quest_id):
    rows = connection.execute(
        "SELECT * FROM router_turns WHERE conversation_id='main' AND input_hash=? ORDER BY created_at DESC LIMIT 100",
        (hashlib.sha256(GOAL.encode()).hexdigest(),),
    ).fetchall()
    for row in rows:
        try:
            result = json.loads(row["result_json"])
            if result.get("intent") != "create_quest" or result.get("quest_id") != quest_id:
                continue
            user, assistant = result["user_message"], result["assistant_message"]
            stored = [
                connection.execute(
                    "SELECT * FROM messages WHERE conversation_id='main' AND id=?", (message["id"],)
                ).fetchone()
                for message in (user, assistant)
            ]
            if any(message is None for message in stored):
                continue
            if (
                stored[0]["role"] == "user"
                and stored[1]["role"] == "assistant"
                and stored[0]["content"] == GOAL
                and all(
                    message["content"] == saved["content"]
                    and message["created_at"] == saved["created_at"]
                    for message, saved in zip(stored, (user, assistant), strict=True)
                )
                and json.loads(stored[1]["metadata"]).get("execution_mode") == "live"
            ):
                return row["request_id"]
        except (ValueError, TypeError, KeyError):
            continue
    return None


def audit(client, db_path, quest_id, before, after):
    """Read a consistent SQLite snapshot; never authorize or modify execution."""
    from types import SimpleNamespace

    from forge_core.domain import DomainError
    from forge_core.evidence import CompletionGate, _verify_worker_context_call
    from forge_core.semantic_evidence import SemanticVerifier
    from forge_core.settings import Workspace

    settings = client.call("GET", "/api/settings")
    result = assess_evidence({})
    result.update(quest_id=quest_id, reasons=[])
    try:
        if settings.get("restore_fenced"):
            raise ValueError("Workspace is restore fenced")
        with closing(_open_database(settings, db_path)) as connection:
            connection.execute("BEGIN")
            store = SimpleNamespace(
                workspace=Workspace.from_path(settings["workspace_root"], settings["workspace_id"])
            )
            quest = connection.execute(
                "SELECT * FROM quests WHERE id=? AND conversation_id='main'", (quest_id,)
            ).fetchone()
            if quest is None:
                raise ValueError("Quest missing from main conversation")
            if not _quest_origin(connection, quest_id):
                raise ValueError("Live Router turn for the Alpha goal is missing")
            plan = connection.execute(
                "SELECT * FROM quest_plans WHERE quest_id=? AND revision=? AND status='approved' AND approved_by='local_user'",
                (quest_id, quest["plan_revision"]),
            ).fetchone()
            if plan is None or quest["status"] != "completed":
                raise ValueError("Completed Quest and approved current plan required")
            nodes = connection.execute(
                "SELECT * FROM task_nodes WHERE plan_id=?", (plan["id"],)
            ).fetchall()
            gate = CompletionGate(store)
            runs = {}
            for node in nodes:
                if node["status"] != "succeeded":
                    raise ValueError("Every task must be independently verified")
                run = gate._check_live_node(
                    SimpleNamespace(connection=connection), quest, plan, node
                )
                role = run["role_id"].lower()
                if role in runs:
                    raise ValueError("Duplicate role")
                runs[role] = run
            result["missing_roles"] = sorted(ROLES - runs.keys())
            if result["missing_roles"]:
                raise ValueError("Six approved live roles required")
            semantic = connection.execute(
                "SELECT * FROM semantic_test_runs WHERE quest_id=? AND worker_run_id=? ORDER BY created_at DESC",
                (quest_id, runs["coding"]["id"]),
            ).fetchall()
            valid_semantic = []
            for row in semantic:
                try:
                    SemanticVerifier(store).validate_evidence(
                        connection,
                        quest_id=quest_id,
                        plan_id=plan["id"],
                        plan_revision=quest["plan_revision"],
                        worker_id=row["worker_id"],
                        worker_run_id=row["worker_run_id"],
                        semantic_run_id=row["id"],
                    )
                    valid_semantic.append(row)
                except DomainError:
                    continue
            if not valid_semantic:
                raise ValueError("Independent semantic result missing")
            result["semantic_verified"] = True
            candidate = valid_semantic[0]
            experiment_reads = connection.execute(
                "SELECT i.resource_scope,r.result_json FROM action_intents i JOIN action_receipts r ON r.intent_id=i.id WHERE i.run_id=? AND i.tool_name='file_read' AND i.state='succeeded' AND i.execution_mode='live' AND r.execution_mode='live'",
                (runs["experiment"]["id"],),
            ).fetchall()
            if not any(
                row["resource_scope"] == candidate["artifact_path"]
                and json.loads(row["result_json"]).get("sha256") == candidate["source_sha256"]
                for row in experiment_reads
            ):
                raise ValueError("Experiment did not read the candidate")
            source_rows = connection.execute(
                """SELECT i.*,r.result_json,r.result_digest FROM action_intents i JOIN action_receipts r ON r.intent_id=i.id WHERE i.run_id=? AND i.tool_name='browser_get' AND i.state='succeeded' AND i.execution_mode='live' AND r.execution_mode='live'""",
                (runs["research"]["id"],),
            ).fetchall()
            source = None
            for row in source_rows:
                params, body = json.loads(row["parameters_json"]), json.loads(row["result_json"])
                approval = connection.execute(
                    "SELECT * FROM approvals WHERE id=?", (row["approval_id"],)
                ).fetchone()
                if (
                    params == {"url": SOURCE_URL}
                    and row["resource_scope"] == SOURCE_URL
                    and row["risk"] == "R2"
                    and approval is not None
                    and approval["status"] == "APPROVED"
                    and approval["decided_by"] == "local_user"
                    and approval["consumed_at"]
                    and approval["parameter_digest"] == row["parameter_digest"]
                    and approval["resource_scope"] == SOURCE_URL
                    and body.get("status_code") == 200
                    and hashlib.sha256(body.get("body", "").encode()).hexdigest()
                    == body.get("sha256")
                    and hashlib.sha256(row["result_json"].encode()).hexdigest()
                    == row["result_digest"]
                ):
                    source = row
                    break
            if source is None:
                raise ValueError("Approved exact source receipt missing")
            result["source_verified"] = True
            reads = connection.execute(
                "SELECT i.resource_scope,r.result_json FROM action_intents i JOIN action_receipts r ON r.intent_id=i.id WHERE i.run_id=? AND i.tool_name='file_read' AND i.state='succeeded' AND i.execution_mode='live' AND r.execution_mode='live'",
                (runs["reviewer"]["id"],),
            ).fetchall()
            if not any(
                row["resource_scope"] == candidate["artifact_path"]
                and json.loads(row["result_json"]).get("sha256") == candidate["source_sha256"]
                for row in reads
            ):
                raise ValueError("Reviewer did not read the verified candidate")
            prompt = connection.execute(
                "SELECT message_json FROM worker_messages WHERE run_id=? AND role='user' ORDER BY ordinal LIMIT 1",
                (runs["reviewer"]["id"],),
            ).fetchone()
            if prompt is None:
                raise ValueError("Reviewer input unavailable")
            refs = json.loads(json.loads(prompt["message_json"])["content"]).get(
                "upstream_receipts", {}
            )
            receipt = connection.execute(
                "SELECT result_digest FROM action_receipts WHERE id=?", (candidate["receipt_id"],)
            ).fetchone()
            if receipt is None:
                raise ValueError("Candidate receipt missing")
            if refs.get("omitted") is not False or not any(
                ref.get("receipt_id") == candidate["receipt_id"]
                and ref.get("producer_run_id") == candidate["worker_run_id"]
                and ref.get("tool_name") == "file_write"
                and ref.get("result_sha256") == receipt["result_digest"]
                and ref.get("execution_mode") == "live"
                for ref in refs.get("items", [])
            ):
                raise ValueError("Reviewer did not receive complete upstream receipt references")
            calls = connection.execute(
                "SELECT * FROM worker_tool_calls WHERE run_id=? AND tool_name='worker_read_action_receipt'",
                (runs["reviewer"]["id"],),
            ).fetchall()
            result["reviewer_receipt_verified"] = any(
                json.loads(call["arguments_json"]) == {"receipt_id": candidate["receipt_id"]}
                and _verify_worker_context_call(store, connection, runs["reviewer"]["id"], call)
                for call in calls
            )
            if not result["reviewer_receipt_verified"]:
                raise ValueError("Trusted Reviewer receipt read missing")
            archive = _archive_proof(connection)
            result["compaction_verified"] = bool(archive and before.get("archive_proof") == archive)
            router = connection.execute(
                "SELECT router_session_id FROM router_state WHERE conversation_id='main'"
            ).fetchone()[0]
            result["restart_checkpoint_observed"] = (
                before.get("workspace_id") == after.get("workspace_id") == settings["workspace_id"]
                and before.get("router_session_id") == after.get("router_session_id") == router
                and bool(re.fullmatch(r"[0-9a-f]{64}", before.get("session_challenge_sha256", "")))
                and bool(re.fullmatch(r"[0-9a-f]{64}", after.get("session_challenge_sha256", "")))
                and before["session_challenge_sha256"] != after["session_challenge_sha256"]
            )
            restart = _restart_proof(
                connection,
                settings["workspace_id"],
                router,
                quest_id,
                plan["id"],
                before,
                after,
                archive,
            )
            result["restart_verified"] = bool(
                restart
                and result["restart_checkpoint_observed"]
                and before.get("archive_proof") == archive
                and after.get("generation") == settings.get("core_generation")
            )
            if result["restart_verified"]:
                result["restart_proof"] = restart
            events = connection.execute(
                "SELECT worker_id,payload FROM events WHERE conversation_id='main' AND quest_id=? AND event_type='worker.result'",
                (quest_id,),
            ).fetchall()
            relayed = {
                (row["worker_id"], json.loads(row["payload"]).get("run_id")) for row in events
            }
            result["relay_verified"] = all(
                (run["worker_id"], run["id"]) in relayed for run in runs.values()
            )
            if not all(
                result[key] for key in ("compaction_verified", "restart_verified", "relay_verified")
            ):
                raise ValueError("Trusted restart evidence or other required provenance missing")
            if not result["reviewer_receipt_verified"]:
                raise ValueError("Trusted Reviewer receipt projection unavailable")
            result["status"] = "verified_bounded_demo"
            result["semantic_run_id"] = candidate["id"]
            result["source_sha256"] = json.loads(source["result_json"])["sha256"]
    except (ValueError, KeyError, TypeError, sqlite3.Error, OSError, DomainError) as exc:
        result["reasons"].append(str(exc))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explicit staged, evidence-gated FORGE Alpha probe"
    )
    parser.add_argument("--base-url", required=True, help="Numeric loopback Core URL")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "checkpoint", "audit"):
        action = sub.add_parser(name)
        action.add_argument("--output", required=True)
        if name == "prepare":
            action.add_argument(
                "--request-id",
                required=True,
                help="Stable alpha_ plus 32 lowercase hex digits; reuse after timeout",
            )
        if name == "checkpoint":
            action.add_argument("--db-path", required=True)
        if name == "audit":
            action.add_argument("--db-path", required=True)
            action.add_argument("--quest-id", required=True)
            action.add_argument("--before", required=True)
            action.add_argument("--after", required=True)
    for name in ("review-plan", "review-team", "status"):
        action = sub.add_parser(name)
        action.add_argument("--quest-id", required=True)
        action.add_argument("--output", required=True)
    for name in ("approve-plan", "approve-team"):
        action = sub.add_parser(name)
        action.add_argument("--quest-id", required=True)
        action.add_argument("--digest", required=True)
        action.add_argument("--revision", required=True, type=int)
        action.add_argument("--expected-version", required=True, type=int)
    args = parser.parse_args(argv)
    if hasattr(args, "output") and Path(args.output).exists():
        raise FileExistsError(
            "Output already exists; choose a new path or inspect the existing result"
        )
    with CoreClient(args.base_url, os.environ.get("FORGE_ALPHA_CORE_BEARER", "")) as client:
        if args.command == "prepare":
            result = prepare(client, args.request_id, str(args.output) + ".request.json")
        elif args.command == "checkpoint":
            result = checkpoint(client, args.db_path)
        elif args.command == "audit":
            result = audit(
                client,
                args.db_path,
                args.quest_id,
                json.loads(Path(args.before).read_text(encoding="utf-8")),
                json.loads(Path(args.after).read_text(encoding="utf-8")),
            )
        elif args.command == "review-plan":
            result = review_plan(client, args.quest_id)
        elif args.command == "review-team":
            result = review_team(client, args.quest_id)
        elif args.command == "status":
            result = current_status(client, args.quest_id)
        elif args.command == "approve-plan":
            result = approve_plan(
                client, args.quest_id, args.digest, args.revision, args.expected_version
            )
        else:
            result = approve_team(
                client, args.quest_id, args.digest, args.revision, args.expected_version
            )
    if hasattr(args, "output"):
        _exclusive_json(args.output, result)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "status": result.get("status", result.get("stage", "recorded")),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps({"status": "submitted"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
