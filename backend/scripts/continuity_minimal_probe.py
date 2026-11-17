"""One-shot NEW live Router project/archive-restart probe; never resumes a run.

From backend: .venv/bin/python scripts/continuity_minimal_probe.py
  preflight --workspace /NEW/ABS/PATH          # offline, no workspace writes
  run --workspace /NEW/ABS/PATH [--with-thread] # LIVE; do not run during development
  audit-ledger --workspace /EXISTING/PATH     # offline, read-only

Environment only: FORGE_PROVIDER_CREDENTIAL_REF=FORGE_PROVIDER_KEY_<NAME>, with
that variable holding the secret. No dotenv, discovery, HTTP retries or recovery.
--with-thread imports sibling continuity_thread_probe.async run_thread(store,
gateway) in a final independent child; all children share ONE durable ledger.
Default runs Router only, reserving at least 8 calls/80k for that integration.
Router proof is not Worker/Core receipt proof, file isolation, or full acceptance.
"""

import argparse
import asyncio
import copy
import hashlib
import importlib.util
import json
import logging
import os
import re
import secrets
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
HISTORY_PATH = "docs/audit/superagent/capability-completion-handoff-verification.json"
HISTORY_SHA256 = "9ba167532800222a2e129c080b6045731cbee40a598366ff1087e8880f5b1a8c"
MODEL = "global:deepseek-v4.1-flash-sg"
BASE_URL = "http://127.0.0.1:7863/v1"
OPENING_CALLS, OPENING_TOKENS = 99, 1089785
STAGE_CALL_CAP, STAGE_TOKEN_CAP = 160, 2500000
PROTOCOL_NAME = "continuity-minimal-protocol.json"
NEW_CALL_CAP, NEW_TOKEN_CAP = 18, 220000
ROUTER_CALL_CAP, ROUTER_TOKEN_CAP = 10, 140000
PROVIDER_ID = "continuity-minimal"
sys.path.insert(0, str(REPO / "backend/src"))
sys.path.insert(0, str(SCRIPTS))


class Refused(RuntimeError):
    """Fixed local error codes only; no raw exception strings reach output."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown argv, which could include an accidental key.
        print(
            json.dumps(
                {
                    "status": "refused",
                    "failure_code": "invalid_cli_arguments",
                    "provider_requests_by_this_command": 0,
                }
            )
        )
        raise SystemExit(2)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def history(repo=REPO):
    """Pin the inherited denominator AND all four original failed results."""
    data = (repo / HISTORY_PATH).read_bytes()
    if sha(data) != HISTORY_SHA256:
        raise Refused("historical_anchor_changed_manual_reconciliation_required")
    anchor = json.loads(data)
    expected = {
        "attempts": OPENING_CALLS,
        "settled": OPENING_CALLS,
        "charged_or_reserved_tokens": OPENING_TOKENS,
        "attempt_ceiling": STAGE_CALL_CAP,
        "token_ceiling": STAGE_TOKEN_CAP,
    }
    if any(anchor["completion_stage_accounting"].get(k) != v for k, v in expected.items()):
        raise Refused("historical_totals_mismatch")
    retained = []
    for reference in [*anchor["prior_results_retained"], anchor["latest_real_result"]]:
        path = repo / reference["path"]
        data = path.read_bytes()
        if sha(data) != reference["sha256"]:
            raise Refused("historical_result_changed")
        result = json.loads(data)
        retained.append(
            {**reference, "status": result["status"], "accounting": result["accounting"]}
        )
    if (
        sum(r["accounting"]["attempts"] for r in retained) != OPENING_CALLS
        or sum(r["accounting"]["charged_or_reserved_tokens"] for r in retained) != OPENING_TOKENS
    ):
        raise Refused("historical_result_sum_mismatch")
    return {
        **expected,
        "anchor": {"path": HISTORY_PATH, "sha256": HISTORY_SHA256},
        "retained_failed_conditions": retained,
        "previous_closed_stages": {
            "reference": anchor["prior_closed_stage_index"],
            "included_in_this_stage_denominator": False,
            "usage_and_effects": "retain_original_unknowns_no_refund_no_replay",
            "note": "Earlier 155-attempt stage and other historical stages stay separate.",
        },
    }


def source_freeze(repo=REPO):
    """Current dirty bytes, not HEAD alone, bind the proposed protocol."""

    def git(*args):
        result = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
            cwd=repo,
            env={k: os.environ[k] for k in ("PATH", "SYSTEMROOT") if k in os.environ},
            capture_output=True,
            check=True,
            timeout=15,
        )
        return result.stdout

    def manifest():
        files = set()
        for folder in ("backend/src", "backend/scripts", "backend/tests"):
            files.update((repo / folder).rglob("*.py"))
        files.update(
            repo / p
            for p in (
                "AGENTS.md",
                "README.md",
                "backend/pyproject.toml",
                "backend/requirements-lock.txt",
                "docs/SUPERAGENT_NEXT_ENV_HANDOFF.md",
                "docs/SUPERAGENT_EXPERIMENTS.md",
            )
        )
        entries = []
        for path in sorted(files):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(repo):
                raise Refused("source_not_regular_in_repository")
            entries.append(
                {"path": path.relative_to(repo).as_posix(), "sha256": sha(path.read_bytes())}
            )
        return entries

    head = git("rev-parse", "HEAD").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise Refused("commit_identity_invalid")
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    files = manifest()
    if (
        manifest() != files
        or git("rev-parse", "HEAD").decode("ascii").strip() != head
        or git("status", "--porcelain=v1", "-z", "--untracked-files=all") != status
    ):
        raise Refused("source_changed_during_freeze")
    return {
        "commit": head,
        "dirty": bool(status),
        "git_status_sha256": sha(status),
        "files": files,
        "manifest_sha256": sha(encoded(files)),
        "scope": "all_backend_python_and_explicit_protocol_docs_and_dependency_lock",
        "current_source_live_acceptance": "not_run",
    }


def credential_present(environ):
    reference = environ.get("FORGE_PROVIDER_CREDENTIAL_REF", "")
    if not re.fullmatch(r"FORGE_PROVIDER_KEY_[A-Z0-9_]+", reference):
        return False
    # Do not serialize the reference or value, even if the reference is malformed.
    return bool(environ.get(reference, "").strip())


def fresh_workspace(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise Refused("workspace_must_be_new_absolute_path")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise Refused("workspace_symlink_forbidden")
    if path.exists():
        raise Refused("workspace_exists_no_resume_or_overwrite")
    if not path.parent.is_dir():
        raise Refused("workspace_parent_must_exist")
    if path.is_relative_to(REPO) and not path.is_relative_to(REPO / ".local"):
        raise Refused("workspace_must_not_modify_repository_sources")
    return path


def budget(attempts):
    """Every ledger row counts, even failures/rejections/unknowns; never refund.

    Unknown usage retains each unreported component's original reservation,
    matching ModelCallBudgetLedger admission accounting. This is an offline
    observation, NOT a Gateway admission guard or a claim of billed usage.
    """
    seen, records, charged_or_reserved, known_complete = set(), [], 0, 0
    for row in attempts:
        identifier = row["attempt_id"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise Refused("invalid_or_duplicate_attempt")
        seen.add(identifier)
        components = []
        for kind in ("input", "output"):
            reserved, charged = row[kind + "_reserved"], row[kind + "_charged"]
            if (
                type(reserved) is not int
                or reserved < 0
                or (charged is not None and (type(charged) is not int or charged < 0))
            ):
                raise Refused("invalid_attempt_usage")
            components.append(reserved if charged is None else charged)
        usage_known = all(row[k + "_charged"] is not None for k in ("input", "output"))
        state = row["state"]
        if state not in {"settled", "reserved", "rejected", "unknown", "uncertain", "overrun"}:
            raise Refused("unknown_attempt_state")
        charge = sum(components)
        charged_or_reserved += charge
        known_complete += charge if usage_known else 0
        records.append(
            {
                "attempt_id_sha256": sha(identifier.encode()),
                "state": state,
                "call_id_sha256": sha(row["call_id"].encode()),
                "purpose_sha256": sha(row["purpose"].encode()),
                "model_matches_protocol": row["model_id"] == MODEL,
                "input_reserved": row["input_reserved"],
                "output_reserved": row["output_reserved"],
                "input_charged": row["input_charged"],
                "output_charged": row["output_charged"],
                "charged_or_reserved_tokens": charge,
                "usage_known": usage_known,
            }
        )
    total_calls, total_tokens = OPENING_CALLS + len(records), OPENING_TOKENS + charged_or_reserved
    return {
        "opening_attempts": OPENING_CALLS,
        "opening_tokens": OPENING_TOKENS,
        "new_attempts": len(records),
        "new_charged_or_reserved_tokens": charged_or_reserved,
        "new_known_complete_usage_tokens": known_complete,
        "new_unknown_usage_attempts": sum(not r["usage_known"] for r in records),
        "new_unsettled_attempts": sum(
            r["state"] not in {"settled", "rejected", "overrun"} for r in records
        ),
        "attempts": records,
        "stage_attempts": total_calls,
        "stage_charged_or_reserved_tokens": total_tokens,
        "within_caps": (
            len(records) <= NEW_CALL_CAP
            and charged_or_reserved <= NEW_TOKEN_CAP
            and total_calls <= STAGE_CALL_CAP
            and total_tokens <= STAGE_TOKEN_CAP
        ),
        "new_call_cap": NEW_CALL_CAP,
        "new_token_cap": NEW_TOKEN_CAP,
        "stage_call_cap": STAGE_CALL_CAP,
        "stage_token_cap": STAGE_TOKEN_CAP,
        "remaining_stage_attempts": STAGE_CALL_CAP - total_calls,
        "remaining_stage_tokens": STAGE_TOKEN_CAP - total_tokens,
        "maximum_if_new_allocation_fully_used": {
            "attempts": OPENING_CALLS + NEW_CALL_CAP,
            "charged_or_reserved_tokens": OPENING_TOKENS + NEW_TOKEN_CAP,
        },
        "currency_cost": None,
        "admission_enforcement": "live_gateway_durable_ledger",
    }


def protocol(workspace, *, environ=None, with_thread=False):
    workspace = fresh_workspace(workspace)
    if with_thread and not (SCRIPTS / "continuity_thread_probe.py").is_file():
        raise Refused("thread_driver_missing")
    return {
        "schema_version": 2,
        "mode": "new_live_condition",
        "workspace": str(workspace),
        "created_at": datetime.now(UTC).isoformat(),
        "with_thread": with_thread,
        "source": source_freeze(),
        "history": history(),
        "budget": budget([]),
        "credential_present": credential_present(os.environ if environ is None else environ),
        "provider": {"model": MODEL, "base_url": BASE_URL, "max_retries": 0},
        "router_allocation": {"calls": ROUTER_CALL_CAP, "tokens": ROUTER_TOKEN_CAP},
        "thread_reserved": {"calls": 8, "tokens": 80000},
        "original_per_task_caps": {"calls": 32, "tokens": 500000},
        "limitations": [
            "Router evidence is not Worker receipt proof or file-write isolation",
            "Historical decisions/todos are user-specified, not Agent-originated",
            "No full regression or three-scenario acceptance implied",
            "Single campaign owner; other workspaces require reconciliation",
        ],
    }


def write_once(path, value):
    with Path(path).open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())


def load_plan(workspace):
    workspace = Path(workspace)
    if not workspace.is_absolute() or any(p.is_symlink() for p in (workspace, *workspace.parents)):
        raise Refused("workspace_binding_invalid")
    target = workspace / PROTOCOL_NAME
    if target.is_symlink():
        raise Refused("protocol_symlink_forbidden")
    plan = json.loads(target.read_bytes())
    if (
        plan.get("mode") != "new_live_condition"
        or plan.get("workspace") != str(workspace)
        or plan.get("history") != history()
        or plan.get("budget") != budget([])
    ):
        raise Refused("audit_protocol_binding_mismatch")
    return plan


def assert_source(plan):
    if source_freeze() != plan["source"]:
        raise Refused("source_changed_since_preparation")


def open_store(workspace):
    from forge_core.settings import Workspace
    from forge_core.store import Store

    database = Path(workspace) / "data/forge.sqlite3"
    if database.is_symlink() or database.parent.is_symlink():
        raise Refused("database_symlink_forbidden")
    return Store(workspace=Workspace.from_path(workspace))


def campaign_ledger(store, *, router_only=False):
    """One pre-HTTP ledger. Thread widens SAME DB, counting all Router attempts.

    Aggregate limits are stricter than the original per-task 32 calls/500k.
    """
    from forge_core.model_call_budget import ModelCallBudgetLedger

    load_plan(store.workspace.root)
    return ModelCallBudgetLedger(
        store.path,
        call_limit=ROUTER_CALL_CAP if router_only else NEW_CALL_CAP,
        token_limit=ROUTER_TOKEN_CAP if router_only else NEW_TOKEN_CAP,
        concurrency_limit=1,
        enforce_token_budgets=True,
    )


def make_gateway(store, *, router_only=True, transport=None):
    from forge_core.providers import ProviderConfig, ProviderGateway

    if not credential_present(os.environ):
        raise Refused("environment_credential_missing")

    class ObservedGateway(ProviderGateway):
        """Capture boundaries in memory; persist only hashes and metadata."""

        async def generate(self, request):
            assert_source(load_plan(store.workspace.root))
            health = audit_ledger(store.workspace.root)
            # Active Worker ownership is expected in the Thread child. Unknown
            # model usage is not: never infer again after an uncertain attempt.
            if (
                health["budget"]["new_unknown_usage_attempts"]
                or health["budget"]["new_unsettled_attempts"]
                or not health["budget"]["within_caps"]
            ):
                raise Refused("prior_attempt_or_effect_not_clean")
            request.max_retries = 0
            before = copy.deepcopy(request.messages)
            entry = {
                "call_id_sha256": sha(request.budget_context.call_id.encode()),
                "request_sha256": sha(encoded(before)),
                "failure_code": None,
            }
            self.calls.append(entry)
            try:
                result = await super().generate(request)
                self.turns.append((before, copy.deepcopy(result)))
                entry["response_sha256"] = sha(
                    encoded({"text": result.text, "tools": result.tool_calls})
                )
                return result
            except Exception:
                entry["failure_code"] = "gateway_call_failed_no_retry"
                raise
            finally:
                entry["post_started"] = bool(getattr(request, "_budget_post_started", False))
                write_once(store.workspace.root / f"call-{entry['call_id_sha256']}.json", entry)

    gateway = ObservedGateway(
        ProviderConfig(
            id=PROVIDER_ID,
            kind="openai_compatible",
            base_url=BASE_URL,
            credential_ref=os.environ["FORGE_PROVIDER_CREDENTIAL_REF"],
            max_retries=0,
            timeout_seconds=120,
            concurrency=1,
            supports_tools=True,
            supports_json_schema=True,
            allow_insecure_loopback=True,
        ),
        budget_ledger=campaign_ledger(store, router_only=router_only),
        transport=transport,
    )
    gateway.calls, gateway.turns = [], []
    return gateway


def make_router(store, gateway):
    from forge_core.domain import ModelProfile, ProtocolSupport
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    support = ProtocolSupport(supported=True, source="user_override")
    store.save_model(
        ModelProfile(
            id="continuity-probe",
            display_name="Continuity probe",
            provider=PROVIDER_ID,
            model_id=MODEL,
            context_window=100000000,
            max_output_tokens=8192,
            tool_calling_support=support,
            structured_output_support=support,
        )
    )
    return RouterService(
        store,
        TeamComposer(ModelRegistry(store)),
        provider=gateway,
        model_id=MODEL,
        provider_id=PROVIDER_ID,
        context_limit=100000000,
        output_reserve=4096,
        max_conflicts=0,
        max_repairs=0,
        semantic_recall=False,
        autonomous=True,
    )


def compact(store):
    from forge_core.context_service import ContextService, _hash, _router_handoff_input

    if audit_ledger(store.workspace.root)["failure_codes"]:
        raise Refused("compaction_requires_quiescent_settled_boundary")
    service = ContextService(store)
    prepared = service.prepare_router_compaction(keep_recent=0)
    result = service.compact_router(
        keep_recent=0,
        snapshot=prepared["snapshot"],
        model_handoff={
            "text": "Historical details remain in original sources.",
            "input_digest": _hash(
                _router_handoff_input(
                    prepared["snapshot"], prepared["eligible"], prepared["previous"]
                )
            ),
        },
    )
    if result["source_count"] < 1:
        raise Refused("no_actual_history_compacted")
    return {"source_count": result["source_count"], "receipt_sha256": sha(encoded(result))}


def facts(workspace, name):
    # Host oracle values never enter the restart prompt or environment.
    identity = sha((str(workspace) + name).encode())[:24]
    return {"decision": "DECISION_" + identity, "pending": "TODO_" + identity}


async def seed_projects(store, gateway):
    router, records = make_router(store, gateway), []
    first_compaction = None
    for name in ("alpha_project", "beta_project"):
        root = store.workspace.root / name
        root.mkdir(mode=0o700)
        (root / "pyproject.toml").write_text(f"[project]\nname='{name}'\n", encoding="utf-8")
        request = (
            f"Create a distinct task for project `{root}`. Select its presented project_id. "
            "Do not start work. Keep title, goal, reply and other fields generic: "
            "refer to the original request, do not repeat its historical codes. "
            "This is a pending implementation task, not a chat request. "
            + "background " * 200
            + "\nHistorical user decision and unfinished todo: "
            + json.dumps(facts(store.workspace.root, name))
        )
        first = len(gateway.turns)
        result = await router.handle_user_message(request, request_id=secrets.token_hex(16))
        if result["intent"] != "create_quest":
            raise Refused("router_did_not_create_distinct_project_task")
        with store.connection() as connection:
            row = connection.execute(
                "SELECT p.id,p.root,q.metadata FROM task_project_links l "
                "JOIN workspace_projects p ON p.id=l.project_id "
                "JOIN quests q ON q.id=l.quest_id WHERE q.id=?",
                (result["quest_id"],),
            ).fetchone()
        if row is None or Path(row["root"]) != root:
            raise Refused("wrong_project_task_binding")
        decision = json.loads(gateway.turns[-1][1].text)
        if decision.get("project_id") != row["id"] or len(gateway.turns) != first + 1:
            raise Refused("project_selection_not_single_actual_model_decision")
        metadata = json.loads(row["metadata"])
        records.append(
            {
                "project_ref": "project:" + row["id"],
                "quest_ref": "quest:" + result["quest_id"],
                "source_ref": "message:" + metadata["original_request_message_id"],
                "source_sha256": metadata["original_request_sha256"],
            }
        )
        if len(records) == 1:
            first_compaction = compact(store)
    if len({r["project_ref"] for r in records}) != 2:
        raise Refused("projects_not_distinct")
    return {
        "projects": records,
        "first_compaction": first_compaction,
        "router_session_id": store.ensure_router()["router_session_id"],
    }


def checked_reply(reply, expected):
    """Exact per-project oracle; mere presence of all codes is insufficient."""
    try:
        value = json.loads(reply)
    except (TypeError, ValueError):
        raise Refused("recall_reply_not_json_mapping") from None
    if value != expected:
        raise Refused("recall_reply_project_mapping_mismatch")
    return value


def recall_evidence(turns, sources, expected):
    """Actual response -> tool result -> subsequent inference, not host Recall.

    Reuses strict complete-page verification. Relations must be delivered BEFORE
    the next invocation; a relation on a partial page is valid navigation, but
    every object on the resulting chain must ultimately be read in full.
    Router has no Worker source receipts.
    """
    from capability_continuity_evidence import complete_pages

    codes = [v for project in expected.values() for v in project.values()]
    if not turns or any(value in json.dumps(turns[0][0]) for value in codes):
        raise Refused("history_leaked_into_initial_restart_context")
    context = json.loads(turns[0][0][1]["content"])
    root = context["recall"].get("archive_ref")
    grouped, invocations, links = {}, set(), []
    for index, (_messages, response) in enumerate(turns):
        for call in response.tool_calls:
            if call["name"] != "recall_object" or call["id"] in invocations:
                raise Refused("non_recall_or_duplicate_tool_invocation")
            invocations.add(call["id"])
            if index + 1 >= len(turns):
                raise Refused("tool_result_not_consumed_by_model")
            messages = turns[index + 1][0]
            actual = [
                c
                for m in messages
                if m["role"] == "assistant"
                for c in m.get("tool_calls", [])
                if c["id"] == call["id"]
            ]
            delivered = [
                m for m in messages if m["role"] == "tool" and m["tool_call_id"] == call["id"]
            ]
            if (
                len(actual) != 1
                or len(delivered) != 1
                or actual[0]["function"]["name"] != call["name"]
                or json.loads(actual[0]["function"]["arguments"]) != call["arguments"]
            ):
                raise Refused("tool_boundary_mismatch")
            page = json.loads(delivered[0]["content"])
            if page["ref"] != call["arguments"]["reference"]:
                raise Refused("recall_reference_mismatch")
            links.extend(
                (page["ref"], item["relation"], item["ref"], 2 * index + 1)
                for item in page["relations"]
            )
            grouped.setdefault(page["ref"], []).append(
                {
                    "offset": page["offset"],
                    "end": page["next_offset"],
                    "total": page["content_bytes"],
                    "sha256": page["source_hash"],
                    "text": page["content"],
                    "complete": page["complete"],
                    "call_id": call["id"],
                    "invocation_ordinal": 2 * index,
                    "result_ordinal": 2 * index + 1,
                    "relations": [item["ref"] for item in page["relations"]],
                }
            )
    objects = {
        ref: proof
        for ref, pages in grouped.items()
        if (proof := complete_pages(pages, byte_offsets=True))
    }
    chain = next(
        (
            [root, previous, source]
            for parent, kind, previous, delivered in links
            if parent == root
            and kind == "previous_archive"
            and root in objects
            and previous in objects
            and objects[previous]["first_invocation"] > delivered
            for ancestor, relation, source, observed in links
            if ancestor == previous
            and relation == "archived_message"
            and source in sources
            and source in objects
            and objects[source]["first_invocation"] > observed
        ),
        [],
    )
    if not chain or not all(ref in objects for ref in sources) or turns[-1][1].tool_calls:
        raise Refused("incomplete_actual_recall_chain_or_answer")
    for reference, source in sources.items():
        text = "".join(p["text"] for p in sorted(grouped[reference], key=lambda p: p["offset"]))
        record = json.loads(text)
        if sha(record["content"].encode()) != source["sha256"]:
            raise Refused("original_source_hash_mismatch")
        if any(value not in record["content"] for value in source["values"]):
            raise Refused("original_source_does_not_contain_expected_facts")
    raw = json.loads(turns[-1][1].text)
    recovered = checked_reply(raw.get("reply"), expected)
    return {
        "proof_class": "actual_router_gateway_tool_consumption",
        "root": root,
        "two_hop_chain": chain,
        "objects": objects,
        "chain_order": "relation_delivered_before_next_invocation_all_objects_complete",
        "reply_sha256": sha(raw["reply"].encode()),
        "recovered_by_project": recovered,
        "decision_and_pending_recovered": True,
        "initial_context_excluded_facts": True,
    }


async def recall_history(store, gateway):
    before = json.loads((store.workspace.root / "seed-result.json").read_bytes())
    if before["router_session_id"] != store.ensure_router()["router_session_id"]:
        raise Refused("router_identity_changed")
    result = await make_router(store, gateway).handle_user_message(
        "Read-only historical recovery for both prior tasks. Do not create/update tasks. "
        "Use recall_object, starting with recall.archive_ref in your context. Follow "
        "previous_archive and archived_message relations; read COMPLETE original user "
        "messages from both archives (paginate using next_offset and expected_hash if needed). "
        "There are three tool rounds; batch up to four independent reads per round. "
        "Return intent=chat, with reply a JSON object keyed by alpha_project and beta_project; "
        "each value must contain exactly decision and pending with that project's original "
        "historical codes. No markdown or extra keys. Do not infer from summaries.",
        request_id=secrets.token_hex(16),
    )
    if result["intent"] != "chat":
        raise Refused("recall_was_not_read_only_chat")
    expected = {
        name: facts(store.workspace.root, name) for name in ("alpha_project", "beta_project")
    }
    sources = {
        record["source_ref"]: {
            "values": list(expected[name].values()),
            "sha256": record["source_sha256"],
        }
        for name, record in zip(("alpha_project", "beta_project"), before["projects"], strict=True)
    }
    proof = recall_evidence(gateway.turns, sources, expected)
    assistant = result["assistant_message"]
    raw_reply = json.loads(gateway.turns[-1][1].text)["reply"]
    with store.connection() as connection:
        persisted = connection.execute(
            "SELECT content FROM messages WHERE id=?", (assistant["id"],)
        ).fetchone()
    if assistant["content"] != raw_reply or persisted is None or persisted[0] != raw_reply:
        raise Refused("raw_result_persisted_chat_reply_mismatch")
    proof["raw_result_persisted_chat_reply_identical"] = True
    return proof


async def stage(workspace, name):
    plan = load_plan(workspace)
    assert_source(plan)
    write_once(workspace / f"{name}-started.json", {"pid": os.getpid()})
    store = open_store(workspace)
    gateway = make_gateway(store, router_only=name != "thread")
    if name == "seed":
        result = await seed_projects(store, gateway)
    elif name == "recall":
        seed = json.loads((workspace / "seed-result.json").read_bytes())
        if seed["pid"] == os.getpid():
            raise Refused("restart_requires_new_os_process")
        result = await recall_history(store, gateway)
    else:
        if not plan["with_thread"]:
            raise Refused("thread_not_in_run_plan")
        spec = importlib.util.spec_from_file_location(
            "continuity_thread_probe", SCRIPTS / "continuity_thread_probe.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        make_router(store, gateway)  # Register the explicit model for WorkerManager.
        report = await module.run_thread(store, gateway)
        write_once(workspace / "thread-report.json", report)
        result = {
            "status": report.get("status"),
            "passed": report.get("passed") is True,
            "failure_code": report.get("failure_code"),
            "report_sha256": sha(encoded(report)),
        }
        if not result["passed"]:
            write_once(
                workspace / "thread-result.json",
                {**result, "pid": os.getpid(), "calls": gateway.calls},
            )
            raise Refused("thread_acceptance_failed_no_retry")
    result.update(pid=os.getpid(), calls=gateway.calls)
    write_once(workspace / f"{name}-result.json", result)


def child(workspace, name):
    nonce = secrets.token_hex(32)
    write_once(
        workspace / f"{name}-permit.json",
        {"nonce_hash": sha(nonce.encode()), "parent_pid": os.getpid()},
    )
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "_" + name, "--workspace", str(workspace)],
        env=dict(os.environ, FORGE_CONTINUITY_CHILD=nonce),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        code = process.wait(timeout=900)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise Refused("child_timeout_remote_usage_and_effects_may_be_unknown") from None
    if code != 0:
        raise Refused("child_failed_no_retry_inspect_durable_ledger_and_call_receipts")
    result = json.loads((workspace / f"{name}-result.json").read_bytes())
    if result["pid"] != process.pid or result["pid"] == os.getpid():
        raise Refused("child_process_identity_mismatch")
    return result


def run(workspace, *, with_thread=False):
    plan = protocol(workspace, with_thread=with_thread)
    if not plan["credential_present"]:
        raise Refused("environment_credential_missing")
    workspace = Path(plan["workspace"])
    workspace.mkdir(mode=0o700)
    write_once(workspace / PROTOCOL_NAME, plan)
    result = {
        "status": "failed",
        "failure_codes": [],
        "thread": "not_run_reserved",
        "source": plan["source"],
        "history": plan["history"],
    }
    try:
        result["project_switch"] = child(workspace, "seed")
        result["compaction"] = compact(open_store(workspace))
        result["recall"] = child(workspace, "recall")
        if with_thread:
            result["thread"] = child(workspace, "thread")
        result["status"] = "router_probe_passed_not_full_acceptance"
    except Exception as exc:  # noqa: BLE001 -- redact foreign errors; always finalize durable audit.
        result["failure_codes"].append(
            exc.args[0] if isinstance(exc, Refused) else "driver_failed_no_retry"
        )
    finally:
        try:
            result["audit"] = audit_ledger(workspace)
            result["failure_codes"].extend(result["audit"]["failure_codes"])
        except Exception:  # noqa: BLE001 -- preserve explicit unknown effects if final audit fails.
            result["failure_codes"].append("final_audit_unavailable_effects_unknown")
        if result["failure_codes"]:
            result["status"] = "failed"
        write_once(workspace / "result.json", result)
    return result


def audit_ledger(workspace):
    """Audit ONE prepared condition; never migrate DB or report scenario success.

    Reuses existing read-only Sprint metrics. Its independently sampled snapshot
    is labelled separately; accounting/effects below share one SQLite snapshot.
    Even a settled snapshot is not proof that another process cannot start work.
    """
    workspace = Path(workspace)
    if not workspace.is_absolute() or any(p.is_symlink() for p in (workspace, *workspace.parents)):
        raise Refused("audit_workspace_must_be_absolute_without_symlinks")
    plan_path, database = workspace / PROTOCOL_NAME, workspace / "data/forge.sqlite3"
    if plan_path.is_symlink() or database.is_symlink() or database.parent.is_symlink():
        raise Refused("audit_symlink_forbidden")
    plan = load_plan(workspace)
    if not database.is_file():
        raise Refused("audit_database_missing_no_creation")
    sys.path.insert(0, str(SCRIPTS))
    from forge_core.settings import Workspace

    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        identity = Workspace.from_path(workspace).id
        if conn.execute("SELECT id FROM workspace WHERE singleton=1").fetchone()[0] != identity:
            raise Refused("audit_database_workspace_mismatch_no_old_experiments")
        if conn.execute(
            "SELECT 1 FROM model_budget_attempts WHERE workspace_id != ? LIMIT 1", (identity,)
        ).fetchone():
            raise Refused("audit_attempt_workspace_mismatch")
        attempts = [
            dict(row)
            for row in conn.execute(
                "SELECT attempt_id,call_id,purpose,state,model_id,input_reserved,output_reserved,"
                "input_charged,output_charged FROM model_budget_attempts ORDER BY created_at,attempt_id"
            )
        ]
        result = budget(attempts)
        effects = {}
        for name, query in {
            "tool_intents": "SELECT COUNT(*) FROM action_intents WHERE state IN ('prepared','executing','uncertain')",
            "worker_tools": "SELECT COUNT(*) FROM worker_tool_calls WHERE state IN ('pending','running','uncertain')",
            "worker_reservations": "SELECT COUNT(*) FROM worker_model_reservations WHERE state='reserved'",
            "active_workers": "SELECT COUNT(*) FROM worker_runs WHERE state IN ('queued','running','review_pending')",
        }.items():
            effects[name] = conn.execute(query).fetchone()[0]
    current_source = source_freeze()
    source_changed = current_source != plan["source"]
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "status": "observation_only_not_acceptance",
        "provider_requests_by_this_command": 0,
        "prepared_source": plan["source"],
        "current_source": current_source,
        "source_changed_since_preparation": source_changed,
        "budget": result,
        "unresolved_or_active": effects,
        "failure_codes": (["budget_cap_exceeded"] if not result["within_caps"] else [])
        + (
            ["unsettled_or_active_work"]
            if any(effects.values()) or result["new_unsettled_attempts"]
            else []
        )
        + (
            ["model_mismatch"]
            if any(not r["model_matches_protocol"] for r in result["attempts"])
            else []
        )
        + (
            ["provider_usage_overrun"]
            if any(r["state"] == "overrun" for r in result["attempts"])
            else []
        )
        + (["unknown_usage_retained"] if result["new_unknown_usage_attempts"] else [])
        + (["source_changed_since_preparation"] if source_changed else []),
        "gateway_failure_details": "not_recorded_in_attempt_table_no_remote_text_published",
        "limitations": "Budget/effects observation only; actual Recall proof is separate.",
    }


def _main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument(
        "command", choices=("preflight", "run", "audit-ledger", "_seed", "_recall", "_thread")
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--with-thread", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command.startswith("_"):
            name = args.command[1:]
            permit = json.loads((args.workspace / f"{name}-permit.json").read_bytes())
            nonce = os.environ.pop("FORGE_CONTINUITY_CHILD", "")
            if (
                not nonce
                or sha(nonce.encode()) != permit["nonce_hash"]
                or permit["parent_pid"] != os.getppid()
                or (args.workspace / "result.json").exists()
            ):
                raise Refused("no_live_parent_one_shot_permit")
            asyncio.run(stage(args.workspace, name))
            return 0
        if args.command == "run":
            report = run(args.workspace, with_thread=args.with_thread)
        elif args.command == "audit-ledger":
            report = audit_ledger(args.workspace)
        else:
            report = protocol(args.workspace, with_thread=args.with_thread)
    except Refused as exc:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "failure_code": exc.args[0],
                    "provider_requests_by_this_command": 0,
                }
            )
        )
        return 2
    except Exception:  # noqa: BLE001 - never emit paths, credentials or foreign exception text.
        print(
            json.dumps(
                {
                    "status": "refused",
                    "failure_code": "probe_failed_no_retry",
                    "provider_requests_by_this_command": "inspect_durable_ledger",
                }
            )
        )
        return 2
    print(encoded(report).decode("utf-8"), end="")
    return 2 if report.get("failure_codes") else 0


def main(argv=None):
    # Redact this command, without leaking a process-global logging mutation to
    # callers/tests that invoke the offline CLI in their existing Core process.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return _main(argv)
    finally:
        logging.disable(previous)


if __name__ == "__main__":
    raise SystemExit(main())
