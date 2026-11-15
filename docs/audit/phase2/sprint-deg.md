# Sprint D / E / G — Worker recovery, independent review and communication

Date: 2026-11-15, Asia/Shanghai. This subreport records local engineering
verification. No Provider network call was performed by this implementation
subtask. Root integration owns the live experiments and final acceptance.

## Audit and preserved implementation

Phase 1 already supplied native model/tool loops, hidden Worker contexts,
approved model/role contracts, durable leases, cumulative allocations, governed
tools, intent/receipt evidence, upstream receipt reads and independent fixed
JSON/numeric verifiers. Those mechanisms are reused. Existing uncommitted Phase
2 delivery-schema and repair-counter changes were preserved and corrected.

The initial repair implementation could leave a failed call `running` and repeat
it before asking the model to repair. Invalid-schema/help responses also left
their pessimistic local reservation charged despite known usage. Expanding the
fallback output schema for old schema-less contracts could exhaust a small
context before tests reached the model, causing a waiting concurrency fixture to
hang. These defects were reproduced or identified in source and fixed.

## Worker execution and recovery

New roles retain the eight-field delivery schema. Explicit `delivery_review`
and `json_review:@delivery` criteria require all eight fields and a finite
confidence in [0,1]. Historical schema-less contracts keep their original narrow
summary schema; their permissions/context reservation are not silently expanded.
Result events record which delivery fields were actually supplied.

Known model responses settle actual token/cost usage before output validation.
Schema failures receive bounded structured feedback. Repair has a cumulative
counter (default two; hard configurable range zero through four), and also
consumes the original step/token/tool/time budgets. Resource, task-contract,
provider, authorization, dependency, code and tool failures remain distinguishable.

A Provider `truncated` response can enter that same bounded repair loop only
with valid known usage. Live provenance additionally requires the exact current
call/provider/model/Worker usage to match a settled Gateway attempt. Its actual
charge is retained before requesting a compact complete delivery. Partial
content and tool calls are discarded without creating tool intents or receipts;
the rejection event records usage and zero executed calls. Timeout, transport
errors and unsettled live outcomes retain their original reservations and do
not enter truncation repair. Concise delivery instructions keep findings brief
and cite exact evidence/path references instead of duplicating source excerpts.

An active Worker now renews its current lease between completed model/tool
steps, after rechecking its token, generation, approved plan/team and running
Quest. Renewal cannot revive an expired/stale claim and is capped by the
original task deadline. Each individual call remains bounded by the current
lease (normally 60 seconds) and task deadline; there is no in-flight heartbeat
or increased task allocation. A sent timeout keeps the unknown Gateway attempt
and local reservation, with no replay. Failure events record only fixed
exception-type labels; an outer built-in `TimeoutError` is classified as
`timeout` without writing exception text or arbitrary class names.

The retained live L1 workspace `ws-20261115T001324Z-L1-70d6a4` was inspected
read-only: creation at 00:14:28.284 UTC and blocking at 00:15:28.353 UTC match
the original 60-second lease. Its fourth attempt began at 00:15:05.349 UTC,
remains reserved with no usage, and no truncation repair ran. A read-only
SQLite backup of that database supplied the original Worker messages, tools,
model and output limit to the real Gateway with a delayed HTTP MockTransport;
the outer timeout reproduced a built-in `TimeoutError` and reserved attempt.
This diagnostic made zero network calls and did not alter the live workspace.

`file_write` retains create-only semantics. The new, separately approved R1
`file_revise` requires `path`, `prior_receipt_id`, `expected_sha256` and complete
new `content`. Its source must be this same Worker's latest successful produced
version, backed by an intact `file_write` / `file_revise` receipt chain. A shared
read-only helper validates every version's run/worker/plan/mode, successful
call/intent/receipt, parameter/result/content hashes and unchanged path. Earlier
full bytes remain in immutable intent parameters; immutable receipts link the
versions. The tool rechecks active generation, plan/team, workspace grant,
current hash and file identity under the Core write lock immediately before
atomic replacement. Unknown replacement results retain uncertain state and
block replay. User files and predecessor artifacts cannot establish ownership.
Existing contracts gain no tool authority. Only an explicitly approved
`file_revise` scope receives bounded Core artifact receipt references in model
context; these references consume the same context/token budget.

Failed fixed R0 diagnostics and local reads may inform a new model-selected
call only after validating their genuine failed receipt. Failed local
`file_read` / `directory_list` operations now produce a real failure receipt for
bounded OS/encoding/policy errors; stale authorization still produces uncertain
state. A Core pre-execution R0 denial can be repaired only after proving there
is no action intent for the call, persisting the rejection/parameter digest and
the actual diagnostic tool reply. This produces no fictional success receipt.
External or uncertain effects retain the original fail-closed behavior.

The failed call remains in history, and is never replayed by the repair loop.
`settled_read_failure` validates the provenance both for restart and for the
completion gate. Batched tool replies stay contiguous; repair instructions are
inside the failing tool feedback rather than inserted between pending replies.

## Independent review and revision

`IndependentReviewService` is an explicitly approved validation stage of the
same node contract. It uses that node's approved Provider/model through the
native Gateway. It is **independent context with the same model**, not evidence
of model diversity or a separate specialist model. Supported criteria are:

- `delivery_review` / `json_review:@delivery`: eight-field delivery plus at least
  one successful authorized tool receipt; no file write is required.
- `json_review:<workspace path>`: actual produced JSON artifact, matching live
  receipt/hash and deterministic JSON verification before model judgment.

The review packet contains the original user message ID/hash where recorded,
the latest goal-update message where recorded, source-linked constraints,
original task/node contract, final delivery and validated receipts/artifacts.
It does not contain the Worker conversation or private reasoning. Packet size
is bounded at 64 KiB and oversized input blocks review rather than truncating
evidence silently. Registered structured-output support enables native JSON
schema requests; unsupported Providers use prompt formatting and strict local
validation.

Review returns only `PASS`, `NEEDS_REVISION`, `REJECT` or `BLOCKED`, findings,
explicit revision goals and bounded confidence. A PASS is rechecked against the
current source packet and a settled Gateway attempt. Mock review cannot prove
live completion. Deterministic artifact/receipt checks remain necessary; a model
review is a domain-quality assessment, not a mathematical correctness proof.

`NEEDS_REVISION` resumes only the same `review_pending` Worker after validating
the current result/review event. It preserves the original approved model,
tools, completed sibling nodes, cumulative budgets and historical receipts.
The old delivery remains recorded. There is no reopening of completed nodes,
no permission expansion and no budget reset. A committed revision verdict is replayed idempotently after restart if Core stopped before requeue. The service defaults to two review
rounds, with a hard upper bound of four.

The review stage charges the same Worker token/step/currency counters and the
Gateway's existing global/quest/team/worker admission ledger. Approval budgets
must include review and possible revision. Capacity pressure proved unsent
refunds its reservation, queues with bounded backoff and consumes no semantic
review round. Started claims have durable leases. After restart, a settled or
rejected attempt, or a trusted expired remote isolation bound, permits bounded
recovery. Unknown remote outcomes keep their reservation and emit one explicit
recovery-blocked event; they are never blindly retried. The Gateway atomically
fences stale/refunded reviewer claims before HTTP admission. Defensive late
response accounting cannot subtract an already refunded reservation, and stale
results cannot replace a current review.

Malformed produced JSON now yields bounded `NEEDS_REVISION` feedback labelled
`deterministic_artifact_validation` from the Core JSON parser. It creates no
fictional Provider attempt or PASS; a corrected artifact still needs independent
model review. This consumes the same repair and review-round limits. A normally
settled truncated model review reconciles actual usage before requesting a new
compact verdict in fresh context, retaining the same source packet and counting
the next round. The output ceiling is at most 8192 and still respects the
approved output/model allowance. Unknown outcomes never enter that repair path.
Oversized packets or model-context overflow produce controlled input-limit
diagnostics, without silently dropping evidence. Reviewer calls also respect
the configured Provider timeout. A 60-second review claim can remain safely
reserved during a longer authorized call: expiry alone cannot release or refund
an admitted unknown remote attempt.

## Durable communication

`AgentMessage` projects persisted Core events into all ten requested protocol
types: assignment, accepted, completed, help, review, revision, dependency block,
model unavailable, budget warning and agent failure. Existing events/outbox/inbox
remain the transport, with Router coordination; no free-text group chat or
Worker-to-Worker mutation channel was introduced.

Workers emit accepted and routine progress facts. Dispatcher validates native
message identity against the durable Worker assignment. Malformed native
envelopes follow the existing bounded retry/dead-letter path. Legacy events
without native run/plan/node identity are explicitly labelled
`legacy_nonexecution`. Planner delivery also accepts `quest.proposal_updated`.

## Local verification

Commands below were run from `backend` with `.venv/Scripts/python.exe`:

- Worker/runtime/tool/receipt/fixed-verifier group: **95 passed in 30.82 s**.
- Phase 2 recovery/protocol plus M4 integration/adversarial group:
  **58 passed, one existing Starlette/httpx deprecation warning in 21.17 s**;
  XML: `worker-review-protocol-tests.xml`. This run caught a subsequent format
  check requirement, which was fixed. The XML records tests, not a static pass.
- Updated Phase 2 recovery/reviewer/Gateway guard plus ToolEngine group:
  **49 passed in 22.73 s**; XML: `worker-review-recovery-tests.xml`.
- Combined updated Worker, review lifecycle, Gateway guard, Dispatcher,
  ToolEngine, upstream receipt and fixed-verifier regression group:
  **128 passed in 46.34 s**; XML: `worker-review-final-tests.xml`.
- Settled truncation, bounded retries, unknown-timeout/unsettled-live rejection,
  Worker/tool integration and reviewer Gateway guards: **81 passed in 48.91 s**;
  XML: `worker-truncation-recovery-tests.xml`. An initial run had one small-budget
  archive-context regression; shortening the Worker prompt fixed it, and the
  six archive cases passed before this complete rerun. Truncation responses use
  HTTP MockTransport; the unsettled-live case is an explicitly labelled stub.
- Dispatcher, workspace backup/restore and migration regressions: **65 passed,
  one existing Starlette/httpx deprecation warning in 18.25 s**; XML:
  `dispatcher-backup-migration-tests.xml`. Dispatcher fixtures now drain their
  finite backlog with a 32-tick bound instead of assuming three 50 ms slices
  finish every delivery under load. The v7 snapshot fixture removes the Phase 2
  repair column and constraint tables before invoking the unchanged migration
  path; restored schema version is checked against `SCHEMA_VERSION`.
- Worker lease renewal, fixed exception labels, actual Gateway timeout,
  truncation and reviewer integration regressions: **90 passed in 32.30 s**;
  XML: `worker-lease-recovery-tests.xml`. The new cases cover a 110-second
  simulated multi-step task under successive 60-second leases, deadline caps,
  expired claims, competing owners, pause and changed-plan rejection. Real local
  file receipts remain present; MockTransport timeout preserves an unknown
  reservation. Ruff check/format passed for the three changed Python files.
- Owned artifact revision, parser feedback, bounded Reviewer truncation repair,
  Worker/ToolEngine and publish-evidence integration: **153 passed in 40.63 s**;
  XML: `worker-revision-final-tests.xml`. Dedicated revision/configuration cases
  include immutable version chains, rejected old contracts, user-file and stale
  hash protection, unknown replacement results, and grant/pause/content races.
  After adding the explicit raw-JSON/no-Markdown initial instruction, the six
  archive-budget cases plus the full bad-JSON/revision/review path passed again:
  **7 passed in 2.89 s**. Ruff check and format were rerun on all seven changed
  Python source/test files after the final prompt wording change and passed.
- Optional Provider reasoning-effort configuration and existing Provider protocol
  regressions: **83 passed in 3.26 s**; XML:
  `provider-reasoning-config-tests.xml`. `FORGE_PROVIDER_REASONING_EFFORT` is
  optional and accepts only `none`, `minimal`, `low`, `medium`, `high`, `xhigh`
  or `max` for `openai_compatible` / `deepseek`; ProviderPool JSON uses the same
  validation. Real Gateway generate/stream methods with HTTP MockTransport
  verified the explicit field and unchanged default payload. The local proxy
  source declares effort support for the chosen model; these offline tests
  establish correct forwarding and accounting, not independent proof that the
  upstream model disabled reasoning. No proxy implementation was modified and
  no Provider network call was made by this subtask.
- Independent auditor's additional review lifecycle tests are retained in
  `test_phase2_integration_security.py`; they cover unsent capacity, settled and
  isolated restart recovery, unknown remote outcomes, and stale late responses.
- Four initial Gateway claim guard tests passed in 1.88 s; the later group adds
  cancellation/pause/stale-plan guard tests.
- Provider diagnostics persist only Core-controlled error codes; raw Provider
  exception messages are not written to failure/review events. Allowlisted
  `budget_exhausted` can distinguish admission failure from a sent timeout.

The tests use explicitly labelled scripted Mock models or HTTP MockTransport,
real SQLite transactions, real governed local reads/writes and real numeric
subprocesses. Synthetic ledger fixtures exercise failure ordering; they are not
live Provider results. Earlier failures were diagnosed in tool output rather
than relabelled as passes. Final combined-suite and source-integrity results
belong in the root verification record.

## Remaining limits

Review is same-model context independence and can share model biases. The fixed
execution toolset still does not implement arbitrary shell, unrestricted Python
or a general-purpose code patch tool. The explicit revision tool only replaces
the current Worker's own proven artifact; it does not edit arbitrary existing
user code or predecessor files. Contracts lacking that explicit tool still
require an approved fresh-path strategy or a reviewed plan/team change. Worker
code correction is bounded tool/contract repair;
this report does not claim unconstrained autonomous software development.

Unknown remote execution without a trusted isolation bound remains an explicit
manual/resource blocker. Recovery after arbitrary database corruption, unlimited
history, or unlimited team expansion is outside the verified scope. Real
multi-role task correctness and real model rework/restart acceptance require the
root live experiments; none is inferred from these offline results.

A final focused pass after the revision-commit/requeue recovery fix reported **28 passed in 13.95 s** (worker-review-restart-tests.xml). Ruff check and format were rerun on the changed review and test files.
