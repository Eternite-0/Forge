"""Fenced, exactly-accounted model handoffs for hidden Worker histories."""

import asyncio
import hashlib
import json

from .context_service import (
    ContextService,
    _hash,
    _json,
    _worker_batch_boundaries,
    _worker_handoff_input,
)
from .domain import DomainError, ForgeEvent, new_id
from .model_compaction import HANDOFF_PROMPT, verified_compaction_usage
from .providers import ProviderError, ProviderGateway
from .runtime import ModelCallContext, ModelRequest
from .token_policy import token_budgets_enforced
from .worker_envelope import capacity_policy, cycle_allowance
from .worker_model_usage import refund_unsent_worker_model_call, reserve_worker_model_call


def _handoff_response_diagnostics(response, summary_bytes, provider_id, model_id):
    """Fixed transport facts only; never store text or exception messages."""
    text = response.text
    encoded = None
    if isinstance(text, str):
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            pass
    reasons = []
    if response.tool_calls:
        reasons.append("unexpected_tool_calls")
    if not isinstance(response.finish_reason, str) or response.finish_reason not in {
        "stop",
        "end_turn",
        "stop_sequence",
    }:
        reasons.append("incomplete_finish")
    if not isinstance(text, str):
        reasons.append("response_not_text")
    elif not text.strip():
        reasons.append("empty_text")
    if encoded is not None and len(encoded) > summary_bytes:
        reasons.append("summary_byte_limit")
    if response.provider_id != provider_id:
        reasons.append("provider_binding_mismatch")
    if response.model_id != model_id:
        reasons.append("model_binding_mismatch")
    finish = response.finish_reason
    return {
        "response_sha256": hashlib.sha256(encoded).hexdigest() if encoded is not None else None,
        "response_utf8_bytes": len(encoded) if encoded is not None else None,
        "summary_limit_utf8_bytes": summary_bytes,
        "finish_class": finish
        if isinstance(finish, str)
        and finish in {"stop", "end_turn", "stop_sequence", "tool_calls", "tool_use"}
        else ("missing" if finish is None else "other"),
        "tool_call_count": len(response.tool_calls)
        if isinstance(response.tool_calls, list)
        else None,
        "provider_binding_matches": response.provider_id == provider_id,
        "model_binding_matches": response.model_id == model_id,
    }, reasons


class WorkerModelCompactor:
    """One whole-exchange batch; the normal loop rechecks every limit afterward."""

    def __init__(self, manager, provider, run_id, token, *, context_limit, output_limit):
        if not isinstance(provider, ProviderGateway) or provider.config is None:
            raise DomainError("Worker compaction requires its original configured Gateway")
        self.manager, self.provider = manager, provider
        self.run_id, self.token = run_id, token
        self.service = ContextService(manager.store)
        with manager.store.unit_of_work() as work:
            c = work.connection
            row = manager._fenced(c, run_id, token)
            if (
                provider.config.id != row["provider_id"]
                or provider.execution_mode != row["execution_mode"]
            ):
                raise DomainError("Worker compaction Provider binding changed")
            self.context_limit = min(context_limit, row["model_context_window"])
            self.output_limit = min(
                output_limit, row["model_max_output_tokens"], max(1, self.context_limit // 8)
            )
            self.model_id, self.provider_id = row["model_id"], row["provider_id"]

    def _fence(self, connection):
        return self.manager._fenced(connection, self.run_id, self.token)

    def _record_handoff_rejection(
        self, prepared, selected, request, response, digest, summary_bytes, *, stage
    ):
        metadata, reasons = _handoff_response_diagnostics(
            response, summary_bytes, self.provider_id, self.model_id
        )
        self.manager.store.append_event(
            ForgeEvent(
                event_type="worker.compaction_handoff_rejected",
                quest_id=prepared["snapshot"]["run"]["quest_id"],
                worker_id=prepared["snapshot"]["run"]["worker_id"],
                payload={
                    "provenance": "worker_compaction_rejection_v1",
                    "stage": stage,
                    "run_id": self.run_id,
                    "generation": prepared["snapshot"]["run"]["generation"],
                    "call_id": request.budget_context.call_id,
                    "input_digest": digest,
                    "frontier_sha256": _hash(prepared["snapshot"]),
                    "source_count": len(selected),
                    "reason_codes": ["source_bound_handoff_validation"]
                    if stage == "checkpoint"
                    else reasons,
                    "usage_state": "gateway_and_local_settled",
                    "execution_mode": prepared["snapshot"]["run"]["execution_mode"],
                    **metadata,
                },
            )
        )

    def _checkpoint_fence(self, connection, request, usage):
        row = self._fence(connection)
        local = connection.execute(
            "SELECT * FROM worker_model_reservations WHERE call_id=?",
            (request.budget_context.call_id,),
        ).fetchone()
        if (
            not verified_compaction_usage(connection, self.provider, request, usage)
            or local is None
            or local["state"] != "settled"
            or local["run_id"] != self.run_id
            or local["generation"] != row["generation"]
            or local["lease_token"] != self.token
            or json.loads(local["context_json"]).get("purpose") != "context_compaction"
            or local["input_tokens"] != usage["input_tokens"]
            or local["output_tokens"] != usage["output_tokens"]
        ):
            raise DomainError("Worker compaction settlement changed before checkpoint")
        return row

    def _request(self, prepared, selected, call_id, input_budget):
        packet = _worker_handoff_input(prepared, selected)
        summary_bytes = min(
            self.output_limit * 8, self.context_limit // 4, input_budget // 4, 65536
        )
        row = prepared["snapshot"]["run"]
        request = ModelRequest(
            provider_id=self.provider_id,
            model_id=self.model_id,
            messages=[
                {
                    "role": "system",
                    "content": HANDOFF_PROMPT.replace("Router", "Worker")
                    + f"The complete summary must fit in {summary_bytes} UTF-8 bytes.",
                },
                {"role": "user", "content": _json(packet)},
            ],
            # Conservative sizing only; Token count does not guarantee byte size.
            # The original UTF-8 summary limit remains mandatory after response.
            max_output_tokens=min(self.output_limit, max(1, summary_bytes // 4)),
            context_limit=self.context_limit,
            max_retries=0,
            timeout_seconds=max(
                0.001,
                min(
                    self.provider.config.timeout_seconds,
                    row["deadline"] - self.manager._now(),
                    row["lease_until"] - self.manager._now(),
                ),
            ),
            budget_context=ModelCallContext(
                call_id=call_id,
                purpose="context_compaction",
                workspace_id=self.manager.store.workspace.id,
                quest_id=row["quest_id"],
                worker_run_id=self.run_id,
            ),
        )
        return request, _hash(packet), summary_bytes

    def _reserve(self, prepared, request):
        native_input = (
            len(json.dumps(self.provider._payload(request, False), ensure_ascii=False).encode())
            + 64
        )
        reserve = native_input + request.max_output_tokens
        with self.manager.store.unit_of_work() as work:
            c = work.connection
            row = self._fence(c)
            if row["steps"] + 1 >= row["step_limit"]:
                raise DomainError("Worker compaction must retain an execution step")
            if c.execute(
                "SELECT 1 FROM worker_model_reservations WHERE run_id=? AND state='reserved'",
                (self.run_id,),
            ).fetchone():
                raise DomainError("Worker has an unresolved model call")
            if c.execute(
                "SELECT 1 FROM model_budget_attempts WHERE worker_run_id=? "
                "AND purpose='context_compaction' AND state!='settled'",
                (self.run_id,),
            ).fetchone():
                raise DomainError("Worker has an unresolved compaction Gateway attempt")
            policy = capacity_policy(prepared["task"])
            cycle = cycle_allowance(c, row, policy) if policy else None
            held = cycle["held_after_current_request"] if cycle else 0
            if (
                token_budgets_enforced()
                and row["token_spent"] + reserve + held > row["token_limit"]
            ):
                raise DomainError("Worker compaction token allocation exhausted")
            if (
                cycle
                and cycle["correction_started"]
                and (
                    native_input > policy["correction_input_tokens"]
                    or request.max_output_tokens > policy["correction_output_tokens"]
                )
            ):
                raise DomainError("Worker compaction exceeds approved correction request")
            cost, ledgers = None, []
            if row["cost_limit_microunits"] is not None:
                price = json.loads(row["price_bounds_json"])
                cost = (
                    native_input * price["input_microunits_per_million"]
                    + request.max_output_tokens * price["output_microunits_per_million"]
                    + 999999
                ) // 1000000
                ledgers = c.execute(
                    "SELECT * FROM worker_cost_ledger WHERE currency=? AND "
                    "((scope='quest' AND scope_id=?) OR (scope='global' AND scope_id='global'))",
                    (row["currency"], row["quest_id"]),
                ).fetchall()
                if (
                    row["cost_spent_microunits"] + cost > row["cost_limit_microunits"]
                    or not ledgers
                    or any(
                        ledger["reserved_microunits"] + cost > ledger["limit_microunits"]
                        for ledger in ledgers
                    )
                ):
                    raise DomainError("Worker compaction cost allocation exhausted")
                c.execute(
                    "UPDATE worker_runs SET cost_spent_microunits=cost_spent_microunits+? WHERE id=?",
                    (cost, self.run_id),
                )
                for ledger in ledgers:
                    c.execute(
                        "UPDATE worker_cost_ledger SET reserved_microunits=reserved_microunits+? "
                        "WHERE scope=? AND scope_id=? AND currency=?",
                        (cost, ledger["scope"], ledger["scope_id"], ledger["currency"]),
                    )
            c.execute(
                "UPDATE worker_runs SET token_spent=token_spent+?,steps=steps+1 WHERE id=?",
                (reserve, self.run_id),
            )
            reserve_worker_model_call(
                c,
                row,
                request.budget_context.call_id,
                reserve,
                request.max_output_tokens,
                cost,
                ledgers,
                purpose="context_compaction",
            )

    async def compact(self, *, input_budget, source_readback_available):
        prepared = self.service._worker_context(
            self.run_id,
            input_budget=input_budget,
            source_readback_available=source_readback_available,
            _prepare=True,
            _fence=self._fence,
        )
        if not prepared["selected"]:
            return {"messages": prepared["messages"], "source_count": 0}
        boundaries = _worker_batch_boundaries(prepared["selected"])
        call_id = new_id("model_call")
        with self.manager.store.unit_of_work() as work:
            connection = work.connection
            row = self._fence(connection)
            policy = capacity_policy(prepared["task"])
            cycle = cycle_allowance(connection, row, policy) if policy else None
            held = cycle["held_after_current_request"] if cycle else 0
            request_allowance = (
                max(0, row["token_limit"] - row["token_spent"] - held)
                if token_budgets_enforced()
                else None
            )
        allocation_limited = False
        low, high = 0, len(boundaries)
        while low < high:
            middle = (low + high + 1) // 2
            request, _digest, _summary_bytes = self._request(
                prepared, prepared["selected"][: boundaries[middle - 1]], call_id, input_budget
            )
            try:
                payload = self.provider._payload(request, False)
            except ProviderError as exc:
                if exc.code != "context_exceeded":
                    raise DomainError("Invalid Worker compaction request") from None
                high = middle - 1
            else:
                native_input = len(json.dumps(payload, ensure_ascii=False).encode()) + 64
                if (
                    request_allowance is not None
                    and native_input + request.max_output_tokens > request_allowance
                ):
                    allocation_limited = True
                    high = middle - 1
                else:
                    low = middle
        if not low:
            if allocation_limited:
                raise DomainError("Worker compaction token allocation exhausted")
            raise DomainError("Complete earliest Worker exchange does not fit model context")
        selected = prepared["selected"][: boundaries[low - 1]]
        request, digest, summary_bytes = self._request(prepared, selected, call_id, input_budget)
        self._reserve(prepared, request)
        try:
            response = await asyncio.wait_for(
                self.provider.generate(request), timeout=request.timeout_seconds
            )
        except (ProviderError, TimeoutError) as exc:
            if getattr(exc, "usage", None) is not None:
                self.manager._settle_model_usage(
                    self.run_id, self.token, exc.usage, call_id=call_id
                )
            elif getattr(request, "_budget_post_started", None) is False:
                with self.manager.store.unit_of_work() as work:
                    refund_unsent_worker_model_call(work.connection, call_id, self.token)
            raise DomainError(
                "Worker compaction Provider failed; original context retained"
            ) from None
        with self.manager.store.connection() as connection:
            exact_gateway_usage = verified_compaction_usage(
                connection, self.provider, request, response.usage
            )
        # Settle only from the immutable Gateway proof. Unlike generic mock
        # Worker adapters, a Gateway summary never accepts response-only usage.
        if not exact_gateway_usage:
            raise DomainError("Worker compaction requires exact settled Gateway usage")
        settled = self.manager._settle_model_usage(
            self.run_id, self.token, response.usage, call_id=call_id
        )
        if not settled:
            raise DomainError("Worker compaction has no settled current-owner response")
        if (
            response.tool_calls
            or response.finish_reason not in {"stop", "end_turn", "stop_sequence"}
            or not isinstance(response.text, str)
            or not response.text.strip()
            or len(response.text.encode("utf-8")) > summary_bytes
            or response.provider_id != self.provider_id
            or response.model_id != self.model_id
        ):
            self._record_handoff_rejection(
                prepared, selected, request, response, digest, summary_bytes, stage="response"
            )
            raise DomainError("Invalid Worker compaction handoff")
        try:
            self.service._worker_context(
                self.run_id,
                input_budget=input_budget,
                source_readback_available=source_readback_available,
                _fence=lambda connection: self._checkpoint_fence(
                    connection, request, response.usage
                ),
                _expected=prepared["snapshot"],
                _handoff={"text": response.text, "input_digest": digest},
                _max_sources=len(selected),
            )
        except DomainError as exc:
            if str(exc) == "Invalid source-bound Worker model handoff":
                self._record_handoff_rejection(
                    prepared, selected, request, response, digest, summary_bytes, stage="checkpoint"
                )
            raise
        return {"source_count": len(selected), "source_ids": [row["ordinal"] for row in selected]}
