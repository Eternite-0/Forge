"""Import-only Thread-during-inference probe; importing never calls a Provider.

The owner harness explicitly awaits ``run_thread(store, gateway)`` after its
authorization, source freeze and ledger/secret checks. It must exclusively own
this gateway until this coroutine returns, register one enabled model for its
provider, and persist the returned report. This module neither constructs a
gateway nor reads credentials, discovers models, retries, resumes previous work,
changes limits, or drives the Router. Each invocation creates a NEW condition.

Real execution uses production delegation, tools, SQLite proofs and independent
review. Offline scripted gateways can check orchestration, never live semantics.
The injected requirement is generated source DATA, never a fabricated response.
"""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from forge_core.domain import ConversationMessage, Quest
from forge_core.evidence import _verify_worker_context_call
from forge_core.independent_review import (
    IndependentReviewService,
    _hash,
    _packet,
    review_pass_matches,
)
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.review_envelope import restore_review_text
from forge_core.superagent_delegation import SuperagentDelegation, issue_mandate
from forge_core.superagent_events import TriggerService
from forge_core.team_composition import TeamCompositionService
from forge_core.tool_engine import ToolEngine
from forge_core.worker_continuations import _result_ordinal, pending_continuations
from forge_core.worker_runtime import WorkerManager


class ProbeRefused(RuntimeError):
    """Fixed local diagnostic code; never wrap raw remote exceptions."""


def _sha(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _authority(store, manager, run):
    current = manager.get(run["id"])
    limits = {
        key: current[key]
        for key in (
            "step_limit",
            "tool_limit",
            "token_limit",
            "output_limit",
            "deadline",
            "allowed_tools_json",
            "cost_limit_microunits",
            "plan_digest",
            "team_digest",
        )
    }
    with store.connection() as c:
        grants = [
            dict(row)
            for row in c.execute(
                "SELECT * FROM task_grants WHERE quest_id=? ORDER BY rowid", (run["quest_id"],)
            )
        ]
    return {"limits": limits, "grants": grants}


def _request_packet(messages):
    """Restore the actual lossless reviewer wire; do not build a smaller packet."""
    projected = json.loads(messages[-1]["content"])
    if "reference_key" in projected and "packet" in projected:
        texts = [item["content"].split("\n", 1)[1] for item in messages[1:-1]]
        return restore_review_text(projected, texts)
    if "review_representation" not in projected:
        return projected
    for receipt in projected["receipts"]:
        reference = receipt.get("output", {}).get("content")
        if isinstance(reference, dict) and "review_source_ref" in reference:
            prefix = f"Complete untrusted source {reference['review_source_ref']}:\n"
            receipt["output"]["content"] = next(
                item["content"][len(prefix) :]
                for item in messages
                if item["content"].startswith(prefix)
            )
    constraints = projected["user_constraints"]
    shared = constraints.pop("shared_fields", {})
    constraints["items"] = [{**shared, **item} for item in constraints["items"]]
    for item in constraints["items"]:
        reference = item.pop("statement_ref", None)
        if reference is not None:
            key, start, end = reference
            item["statement"] = projected[key]["content"][start:end]
    del projected["review_representation"]
    return projected


def _full_read_proof(store, run, trigger):
    """Require contiguous, hash-bound actual tool results, not inbox consumption."""
    with store.connection() as c:
        c.execute("BEGIN")
        source = c.execute(
            "SELECT payload_json FROM superagent_triggers WHERE id=?", (trigger["id"],)
        ).fetchone()[0]
        calls = c.execute(
            "SELECT * FROM worker_tool_calls WHERE run_id=? AND tool_name='read_trigger' "
            "AND json_extract(arguments_json,'$.trigger_id')=? ORDER BY rowid",
            (run["id"], trigger["id"]),
        ).fetchall()
        pages = []
        for call in calls:
            if call["state"] != "succeeded" or not _verify_worker_context_call(
                store, c, run["id"], call
            ):
                raise ProbeRefused("invalid_thread_page_proof")
            ordinal = _result_ordinal(c, run["id"], call, 2**63 - 1)
            if ordinal is None:
                raise ProbeRefused("invalid_thread_page_transcript")
            output = json.loads(call["result_json"])
            arguments = json.loads(call["arguments_json"])
            start = output["offset_chars"]
            end = output["next_offset_chars"]
            end = output["total_chars"] if end is None else end
            if (
                output["payload_sha256"] != trigger["payload_sha256"]
                or output["thread_id"] != trigger["thread_id"]
                or output["total_chars"] != len(source)
                or arguments.get("offset_chars", 0) != start
                or not 0 <= start < end <= len(source)
                or output["text"] != source[start:end]
            ):
                raise ProbeRefused("thread_page_source_mismatch")
            proofs = [
                row[0]
                for row in c.execute(
                    "SELECT id FROM evidence WHERE kind='worker_core_source' AND trusted=1 "
                    "AND json_extract(payload,'$.run_id')=? AND json_extract(payload,'$.call_id')=?",
                    (run["id"], call["call_id"]),
                )
            ]
            if len(proofs) != 1:
                raise ProbeRefused("thread_page_evidence_missing")
            pages.append(
                {
                    "call_id": call["call_id"],
                    "model_call_id": call["model_call_id"],
                    "evidence_id": proofs[0],
                    "result_ordinal": ordinal,
                    "offset_chars": start,
                    "end_chars": end,
                    "text_sha256": _sha(output["text"]),
                }
            )
        cursor = 0
        for page in sorted(pages, key=lambda item: item["offset_chars"]):
            if page["offset_chars"] > cursor:
                break
            cursor = max(cursor, page["end_chars"])
        complete = (
            bool(pages) and cursor == len(source) and _sha(source) == trigger["payload_sha256"]
        )
        pending = pending_continuations(store, c, run["id"], before_ordinal=2**63 - 1)
    return {
        "trigger_id": trigger["id"],
        "thread_id": trigger["thread_id"],
        "payload_sha256": trigger["payload_sha256"],
        "total_chars": len(source),
        "page_count": len(pages),
        "complete": complete,
        "pages": pages,
        "pending": pending,
    }


async def run_thread(store, gateway) -> dict:
    """Run one NEW bounded condition. Caller owns live authorization and ledger.

    Returns JSON-safe observations, fixed failure codes and explicit Mock/live
    attribution. No exception body, Provider response, credential or arbitrary
    artifact content is published. Existing failures remain durable, not retried.
    """
    report = {
        "scenario": "thread_during_execution",
        "status": "failed",
        "passed": False,
        "execution_mode": getattr(gateway, "execution_mode", None),
        "scope": "single_worker_thread_artifact_review_not_full_capability_acceptance",
        "worker_rounds": [],
        "review_rounds": [],
        "checks": {},
    }
    original_generate, had_override = None, False
    try:
        config = gateway.config
        if gateway.execution_mode not in {"live", "mock"} or config.max_retries != 0:
            raise ProbeRefused("explicit_gateway_mode_and_zero_retries_required")
        ledger = getattr(gateway, "_budget_ledger", None)
        if gateway.execution_mode == "live":
            from forge_core.providers import ProviderGateway

            if not isinstance(gateway, ProviderGateway):
                raise ProbeRefused("native_live_gateway_required")
            if ledger is None:
                raise ProbeRefused("existing_live_budget_ledger_required")
            if type(ledger.call_limit) is not int or ledger.call_limit < 1:
                raise ProbeRefused("existing_live_ledger_call_limit_required")
        profiles = [
            profile
            for profile in store.list_models()
            if profile.enabled and not profile.quarantine_reason
        ]
        if len(profiles) != 1 or profiles[0].provider != config.id:
            raise ProbeRefused("one_registered_enabled_gateway_model_required")
        profile = profiles[0]
        if not profile.context_window or not profile.max_output_tokens:
            raise ProbeRefused("registered_model_capacity_required")
        store.ensure_router()
        nonce = uuid4().hex
        quest = Quest(title="Thread continuation artifact probe", goal="pending")
        relative = f"work/{quest.id}/thread-result.json"
        path = Path(store.workspace.root) / relative
        base = "base-" + nonce
        goal = (
            f"Create the local JSON artifact {relative} with base={base!r} and notes=[]. "
            "The notes array must also include every compatible note from delivered Thread updates. "
            "Read every delivered Trigger fully using read_trigger (max_chars=4096; follow "
            "next_offset_chars). The final source suffix can add requirements. Apply them to "
            "the actual JSON, not just your summary. If you already wrote it, use governed "
            "file_revise with the current receipt/hash. Do not silently omit an addition. "
            "Keep the task local, concise, and within existing tools/budgets. Deliver the "
            "complete eight-field Worker JSON with the artifact path and evidence references."
        )
        quest.goal = goal
        source = ConversationMessage(role="user", content=goal)
        quest.metadata = {
            "task_kind": "long_research",
            "original_request_message_id": source.id,
            "original_request_sha256": _sha(goal),
        }
        with store.unit_of_work() as work:
            work.add_message(source)
            work.save_quest(quest)
            issue_mandate(work, quest.id, source.id)
        graph, teams = QuestGraphService(store), TeamCompositionService(store)
        if not SuperagentDelegation(store, graph, teams).prepare(quest.id):
            raise ProbeRefused("production_delegation_not_prepared")
        plan = graph.get(quest.id)
        with store.connection() as c:
            team = c.execute(
                "SELECT * FROM team_proposals WHERE quest_id=? AND status='approved'",
                (quest.id,),
            ).fetchone()
        manager = WorkerManager(
            store,
            ProviderPool({config.id: gateway}),
            ToolEngine(store),
            team_authority=teams,
        )
        run = manager.start(
            quest.id,
            plan["id"],
            "task",
            team_digest=team["proposal_digest"],
            member_index=0,
        )
        report.update(
            quest_id=quest.id,
            run_id=run["id"],
            artifact_path=relative,
            provider_id=run["provider_id"],
            model_id=run["model_id"],
        )
        authority = _authority(store, manager, run)
        report["original_authority"] = authority
        events = TriggerService(store)
        source_id = "thread-probe-" + nonce
        events.register_source(source_id, kind="message")
        opening = events.receive(
            source_id,
            "opening",
            nonce,
            {
                "instruction": "Thread for this local artifact task; further updates refine its notes",
            },
        )
        events.subscribe(opening["thread_id"], run["id"])
        addition, review_packets = [], []
        call_limit = run["step_limit"] + 2
        attempted_calls = 0
        required_note = "late-note-" + uuid4().hex + "-保留完整后缀😊"
        payload = {
            "a_context": "Thread source context; preserve full pagination. " * 92,
            "z_required_note": required_note,
            "zz_instruction": "Append z_required_note exactly to the artifact notes array",
        }
        had_override = "generate" in vars(gateway)
        original_generate = gateway.generate

        async def observed_generate(request):
            nonlocal attempted_calls
            context = request.budget_context
            if context is None or context.worker_run_id != run["id"]:
                raise ProbeRefused("exclusive_gateway_use_required")
            if context.purpose not in {"worker", "independent_reviewer"}:
                raise ProbeRefused("unexpected_model_call_purpose")
            if attempted_calls >= call_limit:
                raise ProbeRefused("probe_generate_call_limit_reached_no_replay")
            attempted_calls += 1
            report["generate_attempts"] = attempted_calls
            if context.purpose == "worker":
                with store.connection() as c:
                    before = c.execute(
                        "SELECT COALESCE(MAX(ordinal),-1)+1 FROM worker_messages WHERE run_id=?",
                        (run["id"],),
                    ).fetchone()[0]
                    pending = pending_continuations(store, c, run["id"], before_ordinal=before)
                feedback = [
                    message["content"]
                    for message in request.messages
                    if message.get("content", "").startswith("Core delivery boundary:")
                ]
                record = {
                    "round": len(report["worker_rounds"]) + 1,
                    "call_id": context.call_id,
                    "before_ordinal": before,
                    "pending": pending,
                    "feedback": feedback[-1] if feedback else None,
                    "started_monotonic": time.monotonic(),
                }
                report["worker_rounds"].append(record)
                if not addition:
                    # Enter the REAL generate coroutine, yield at its first await,
                    # then commit a durable event while that inference is pending.
                    inflight = asyncio.create_task(original_generate(request))
                    try:
                        await asyncio.sleep(0)
                        if inflight.done():
                            await inflight
                            raise ProbeRefused("no_inflight_inference_window_no_replay")
                        injected = events.receive(source_id, "during-inference", nonce, payload)
                        addition.append(injected)
                        report["injection"] = {
                            "trigger_id": injected["id"],
                            "thread_id": injected["thread_id"],
                            "payload_sha256": injected["payload_sha256"],
                            "total_chars": injected["total_chars"],
                            "during_call_id": context.call_id,
                            "request_had_addition": False,
                            "inflight_at_commit": not inflight.done(),
                            "committed_monotonic": time.monotonic(),
                        }
                    except asyncio.CancelledError:
                        inflight.cancel()
                        await asyncio.gather(inflight, return_exceptions=True)
                        raise
                    except Exception:
                        # Never abandon/replay a remote outcome on local injection failure.
                        await inflight
                        raise
                    reply = await inflight
                else:
                    reply = await original_generate(request)
                record["settled_monotonic"] = time.monotonic()
                record["selected_tools"] = [call["name"] for call in reply.tool_calls]
                record["is_delivery_attempt"] = not bool(reply.tool_calls)
                return reply
            if context.purpose == "independent_reviewer":
                packet = _request_packet(request.messages)
                review_packets.append(packet)
                report["review_rounds"].append(
                    {
                        "call_id": context.call_id,
                        "packet_sha256": _hash(packet),
                        "fresh_context": all(
                            message["role"] != "tool" and not message.get("tool_calls")
                            for message in request.messages
                        ),
                    }
                )
            return await original_generate(request)

        gateway.generate = observed_generate
        reviewer = IndependentReviewService(manager)
        report["execution_budget"] = {
            "step_limit": run["step_limit"],
            "tool_limit": run["tool_limit"],
            "token_limit": run["token_limit"],
            "output_limit": run["output_limit"],
            "deadline": run["deadline"],
            "review_round_limit": reviewer.max_rounds,
            "probe_generate_call_limit": call_limit,
            "existing_ledger_call_limit": getattr(ledger, "call_limit", None),
            "existing_ledger_token_limit": getattr(ledger, "token_limit", None),
            "gateway_max_retries": config.max_retries,
            "governor": "existing_gateway_ledger_no_reset_or_topup",
        }
        # Only production NEEDS_REVISION admission can requeue this same Worker;
        # this loop does not reset budgets or retry a blocked/unknown outcome.
        for _ in range(reviewer.max_rounds):
            current = await manager.run(run["id"])
            report["worker_state"] = current["state"]
            if current["state"] != "review_pending":
                raise ProbeRefused("worker_not_ready_for_review")
            verdict = await reviewer.review(run["id"])
            report["review"] = (
                {
                    key: verdict.get(key)
                    for key in (
                        "verdict",
                        "execution_mode",
                        "review_context",
                        "packet_sha256",
                        "call_id",
                        "result_event_id",
                        "revision_applied",
                        "error_code",
                    )
                }
                if verdict
                else None
            )
            if (
                not verdict
                or verdict["verdict"] != "NEEDS_REVISION"
                or not verdict.get("revision_applied")
            ):
                break
        if not addition:
            raise ProbeRefused("thread_was_not_injected")
        proof = _full_read_proof(store, run, addition[0])
        report["full_read_proof"] = proof
        with store.connection() as c:
            c.execute("BEGIN")
            row = c.execute("SELECT * FROM worker_runs WHERE id=?", (run["id"],)).fetchone()
            packet = _packet(store, c, row)
            live_pass = review_pass_matches(store, c, row, "delivery_review")
        artifact = next((item for item in packet["artifacts"] if item["path"] == str(path)), None)
        data = path.read_bytes() if path.is_file() else b""
        actual = json.loads(data) if data else None
        report["artifact"] = {
            "sha256": _sha(data),
            "bytes": len(data),
            "receipt_id": artifact["receipt_id"] if artifact else None,
            "required_note_sha256": _sha(required_note),
        }
        later = report["worker_rounds"][1:]
        first_later = later[0] if later else {}
        expected = next(
            (
                item
                for item in first_later.get("pending", [])
                if item["trigger_id"] == addition[0]["id"]
            ),
            None,
        )
        feedback = first_later.get("feedback") or ""
        feedback_pending = json.loads(feedback[feedback.index('{"omitted":') :]) if feedback else {}
        context = [item for item in packet["receipts"] if item.get("context_only")]
        context_pages = [
            item
            for item in context
            if item["tool_name"] == "read_trigger"
            and item["arguments"]["trigger_id"] == addition[0]["id"]
        ]
        # Legal rereads/overlapping pages must not fabricate extra source bytes.
        context_chunks, cursor = [], 0
        for item in sorted(context_pages, key=lambda item: item["output"]["offset_chars"]):
            start, text = item["output"]["offset_chars"], item["output"]["text"]
            if start > cursor:
                break
            context_chunks.append(text[max(0, cursor - start) :])
            cursor = max(cursor, start + len(text))
        context_text = "".join(context_chunks)
        checks = {
            "injected_during_inference": report["injection"]["inflight_at_commit"],
            "outside_preview_and_first_page": len(payload["a_context"]) > 4096,
            "next_round_pending_before_response": bool(
                expected
                and expected["next_offset_chars"] == 0
                and expected in feedback_pending.get("pending", [])
            ),
            "next_round_not_final_delivery_attempt": bool(first_later.get("selected_tools")),
            "full_paginated_source_proved": proof["complete"] and proof["page_count"] >= 2,
            "no_remaining_read_debt": not proof["pending"],
            "subsequent_model_turn_after_full_read": bool(proof["pages"])
            and any(
                item["before_ordinal"] > max(page["result_ordinal"] for page in proof["pages"])
                for item in later
            ),
            "actual_artifact_applies_addition": isinstance(actual, dict)
            and actual.get("base") == base
            and isinstance(actual.get("notes"), list)
            and required_note in actual["notes"],
            "artifact_bound_to_real_action_receipt": bool(
                artifact
                and artifact["sha256"] == _sha(data)
                and any(
                    item.get("id") == artifact["receipt_id"]
                    and not item.get("context_only")
                    and item["tool_name"] in {"file_write", "file_revise"}
                    and item.get("execution_mode") == gateway.execution_mode
                    for item in packet["receipts"]
                )
            ),
            "review_packet_includes_complete_thread": _sha(context_text)
            == addition[0]["payload_sha256"],
            "context_not_action_evidence": bool(context)
            and all("execution_mode" not in item for item in context),
            "independent_packet_not_reduced": bool(review_packets)
            and _hash(review_packets[-1]) == _hash(packet),
            "independent_review_fresh_context": bool(report["review_rounds"])
            and all(item["fresh_context"] for item in report["review_rounds"]),
            "review_pass_bound_to_packet": bool(
                report.get("review")
                and report["review"]["verdict"] == "PASS"
                and report["review"]["packet_sha256"] == _hash(packet)
            ),
            "original_limits_and_permissions_preserved": _authority(store, manager, run)
            == authority,
        }
        report["checks"] = checks
        report["live_review_pass_matches"] = live_pass
        valid = all(checks.values())
        report["passed"] = valid and gateway.execution_mode == "live" and live_pass
        report["offline_checks_passed"] = valid and gateway.execution_mode == "mock"
        if report["passed"]:
            # A review event alone leaves review_pending ownership. Finish via
            # the production fail-closed verifier, never a harness SQL update.
            from forge_core.evidence import NodeVerifier

            verified = NodeVerifier(store).verify(quest.id, run["node_key"])
            report["completion"] = {
                "quest_status": verified["status"],
                "worker_state": manager.get(run["id"])["state"],
                "via": "production_node_verifier",
            }
            if (
                verified["status"] != "completed"
                or report["completion"]["worker_state"] != "succeeded"
            ):
                raise ProbeRefused("verified_thread_did_not_close")
        report["status"] = (
            "passed"
            if report["passed"]
            else ("offline_passed" if report["offline_checks_passed"] else "failed")
        )
        if not valid:
            report["failure_code"] = "thread_acceptance_checks_failed"
    except ProbeRefused as exc:
        report["passed"] = False
        report["status"] = "failed"
        report["failure_code"] = exc.args[0]
    except Exception as exc:  # noqa: BLE001 -- redact arbitrary Provider exception bodies
        report["passed"] = False
        report["status"] = "failed"
        report["failure_code"] = "thread_probe_execution_failed"
        report["exception_type"] = type(exc).__name__
    finally:
        if original_generate is not None:
            if had_override:
                gateway.generate = original_generate
            else:
                del gateway.generate
    return report
