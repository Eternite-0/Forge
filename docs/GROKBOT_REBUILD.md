# FORGE runtime and desktop rebuild

## Status and scope

This is the architecture and acceptance contract for the November 2026 rebuild.
The implementation and offline evidence are recorded below. Unverified matrix
rows are not release claims. No live Provider, paid service, or upstream clone
code is required by the refactor.

Keep FORGE's Python Core, React/TypeScript desktop and Tauri host unless a
measured requirement justifies changing them. Rebuild the end-to-end contracts,
not just the visible shell. A user speaks through one durable conversation;
independent Workers execute background tasks and return inspectable progress,
artifacts and verified outcomes to it. Multiple projects are context, not
fabricated chat histories. Existing data must remain recoverable.

## External references and reuse boundaries

These public references were inspected on 2026-11-17. Their documentation makes
claims about their own implementations; this document does not certify those
implementations or their security. Pin an upstream commit before any code reuse.

| Reference | Useful scope | Reuse boundary |
|---|---|---|
| [nickvasilescu/grokbot-clone](https://github.com/nickvasilescu/grokbot-clone), [pinned architecture](https://github.com/nickvasilescu/grokbot-clone/blob/7ee6a7ed8464311dbb467bb9ee7c76d9286e56be/docs/ARCHITECTURE.md) | Full-stack agent application; serial server-owned turns, revisioned transcript rows, persisted approvals and computer-view relay | [Code license is MIT](https://github.com/nickvasilescu/grokbot-clone/blob/main/LICENSE). Its [third-party notice](https://github.com/nickvasilescu/grokbot-clone/blob/main/THIRD_PARTY_NOTICES.md) explicitly excludes proprietary restored artwork from that grant. No code or artwork copied. |
| [LING71671/open-grokbot](https://github.com/LING71671/open-grokbot), [protocol](https://github.com/LING71671/open-grokbot/blob/main/docs/protocol.md) | Per-agent scheduling, durable acceptance/deduplication, bounded retries, subagent lineage and cancellation | [GPL-3.0-only](https://github.com/LING71671/open-grokbot/blob/main/LICENSE). Architectural reference, not permission to copy GPL code into an incompatible distribution. UI polish is listed as unfinished. |
| [vladvrx/chat-bot-windows](https://github.com/vladvrx/chat-bot-windows) | Reconstruction/provenance comparison only | Its [NOTICE](https://github.com/vladvrx/chat-bot-windows/blob/main/NOTICE.md) grants no upstream source-code license. Do not transplant reconstructed code, binaries or assets. |

The MIT clone's documented approval model permits computer/shell execution
without the connector-write gate. That is **not** a FORGE design target. Its
external VM and connector dependencies also do not justify silently adding paid
services, credentials or account connections to FORGE.

## Source baseline inspected

The following are actual source entry points, not claims of new implementation:

- [Domain/lifecycle](DOMAIN_AND_LIFECYCLE.md): distinguishes the stable
  Conversation, durable Quest, TaskNode and private WorkerSession.
- [Core model contracts](../backend/src/forge_core/runtime.py): model calls carry
  workspace, quest, worker-run and parent-call context.
- [Worker runtime](../backend/src/forge_core/worker_runtime.py): durable hidden
  execution and evidence-bound receipts; a model result is not verification.
- [Event domain](../backend/src/forge_core/event_domain.py) and
  [trigger admission](../backend/src/forge_core/superagent_events.py): sequence
  identifiers, leases and scoped untrusted event payloads.
- [Timeline projection](../backend/src/forge_core/timeline.py): bounded read-only
  presentation of durable facts; it is not a raw model/tool payload dump.
- [API](../backend/src/forge_api/main.py): existing conversation, events,
  approvals and timeline surfaces. [Desktop feed](../desktop/src/hooks/useConversationFeed.ts)
  currently refreshes multiple snapshots and reconciles outgoing request IDs.
- [Supervisor](../backend/src/forge_api/supervisor.py): process ownership and
  restart lifecycle, deliberately not an action-replay owner.
- [Governance](../backend/src/forge_core/governance.py): canonical parameter
  digests, explicit action-risk classification and fail-closed unknown actions.
- [Semantic memory](../backend/src/forge_core/semantic_memory.py): bounded,
  source-referenced selection, explicitly not a source of facts or authority.

## Architecture decisions

### 1. One conversational entrance, independent execution context

The conversation ID remains stable across UI navigation, preview expansion,
reconnection and process restart. User messages have stable request IDs. An
accepted send must be distinguishable from a completed reply and from a failed
transport. Background events never become new user instructions merely because
they appear in the same timeline.

Router conversational context and Worker execution/activity context remain
separate. A Worker has explicit lineage, assignment, bounded context and tool
scope; it is not another visible top-level chat. The user can continue speaking
while a Worker runs. Updated requirements reach the relevant runtime through a
durable inbox/steering contract, not by mutating another run's prompt in place.

### 2. Durable event revisions, with replay and snapshot recovery

Target contract: each public change has a stable entity/event identity,
workspace/generation boundary and monotonic durable cursor or revision. A
delivery retry cannot duplicate the entity; an older update cannot overwrite a
newer one. Persist the fact before advertising it. Keep append-only execution
facts distinct from mutable UI projections.

On reconnect, request changes after the last applied cursor. If retention or a
generation change invalidates that cursor, return an explicit resync condition
and rebuild from a bounded snapshot. Pagination and live updates must merge
without losing older loaded messages or moving the reader's anchor. SSE is a
delivery option, not a substitute for durable storage or replay. A polling
implementation is acceptable if it obeys the same revision contract and reports
its actual latency. Do not describe existing snapshot polling as replayable SSE.

Acceptance of a send is durable and keyed by request identity plus canonical
input digest. Reusing an identity with different input is rejected. Retry only
where deduplication is guaranteed; never replay an uncertain tool side effect.

### 3. Supervised tasks and honest cancellation

Distinguish queued, running, awaiting approval, blocked, review pending and
terminal outcomes. Persist ownership/checkpoints and recover through the same
database, workspace lock and receipt checks. A supervisor restarts the process,
not the business action. Leases and terminal transitions require deterministic
validation. Preserve cumulative budgets across retries/restarts.

Cancellation acknowledges the request separately from confirmed termination.
Late Worker output cannot revive a cancelled task or erase an uncertain outcome.
Failed/unknown results remain visible. A timeout does not prove an external
operation failed and must not authorize an automatic duplicate action.

### 4. Isolation and approval are runtime boundaries

Workspace identity, connection generation and Worker scope must be checked at
every state/IO boundary. Keep private worker transcripts out of unrelated tasks.
Treat project discovery, imported files, recalled content, connector payloads
and model-supplied tool parameters as untrusted data, not permission grants.

Bind approval to authenticated user action, exact parameters/digest, relevant
plan revision, scope and expiry. Changes invalidate stale approval. Persist
pending approval before showing its card; restart must neither lose the request
nor run it twice. Browser, shell and connector routes must not bypass the common
policy gate. UI buttons display decisions; the server enforces them.

### 5. Evidence and memory have different jobs

Receipts retain run/tool/intent linkage, execution mode, outcome and artifact
hash. Mock, live, failed and uncertain outcomes are distinguishable in storage
and UI. Independent review gates completion; an assistant's success sentence,
a screenshot, a remembered claim or an unverified artifact is not sufficient.

Durable memory is source-linked and bounded. Compaction retains references to
original records for paginated recall after restart. A recalled requirement can
inform a task but cannot grant tools or manufacture execution evidence. Keep
corrections and superseded facts identifiable rather than silently overwriting
provenance. Preserve the existing [memory governance](CONTEXT_MEMORY_GOVERNANCE.md).

### 6. Desktop is a projection of real state

Render conversation, activity, approvals, task status and files from Core
contracts. Preserve the single draft, reading anchor and open files across
navigation. Keyboard focus must survive tab deletion, responsive preview
closure and return from task/settings views. No synthetic histories, permissions,
connected services, live-browser tabs or completed-task badges in production.

External browsing/computer control requires a genuine authenticated execution
adapter, capabilities and policy integration. A file preview is not an embedded
browser. Keep Windows native-frame validation separate from browser screenshots.

## Acceptance matrix

Every row is **PENDING**. Suggested existing tests are coverage entry points,
not asserted results; add integration tests where they do not exercise the
described workflow. Run provider-free fixtures first. Real provider acceptance
requires its own authorization, bounded budget and captured provenance.

| Workflow | Required observable result | Coverage entry point / evidence to record |
|---|---|---|
| Send, disconnect, retry, reconnect | Exactly one accepted user message/run for same ID+digest; mismatch rejected; draft not duplicated | Conversation API tests plus new lost-response/restart integration; DB identities and HTTP trace |
| Receive revisions during older-history reading | Updates merge once/in order; invalid cursor forces explicit resync; reader anchor retained | Event/timeline tests and `desktop/tests/ui-windows-transcript-resize.playwright.js`; cursor trace and screenshot |
| User changes direction during background execution | Same user conversation remains responsive; appropriate Worker receives durable update; unrelated Worker context unchanged | `backend/tests/test_serial_runtime_v17.py`, `test_superagent_events.py`; inbox/run linkage |
| Core crashes while task is active | Single replacement owner; recoverable checkpoint; no uncertain side-effect replay or budget reset | `backend/tests/test_core_supervisor.py`, `test_capability_restart_boundary.py`; restart and receipt audit |
| Cancel then receive late result | Cancellation request/termination distinguished; no terminal resurrection or false success | Worker runtime/event dispatcher integration; persisted transition sequence |
| Approval survives restart; parameters change | Pending decision restored; stale digest/revision denied; approved action executes at most once | `backend/tests/test_governance.py` plus API/runtime approval workflow; decision/intent/receipt linkage |
| Forged result or mock success | Completion denied without independent bound evidence; UI labels execution mode accurately | `backend/tests/test_evidence_gate.py`, `test_execution_evidence.py`; rejected cases and verified receipts |
| Compact, restart, recall original requirement | Source record pages recover correctly; recalled text remains context, not authority/evidence | `backend/tests/test_recall_restart_continuity.py`, `test_recall_chain_superagent.py`; source IDs and model request trace |
| Switch connection/workspace with requests in flight | No old-generation messages, files, settings or permissions enter the new context | Desktop stale-response fixtures and backend scope tests; both workspace IDs and rejected response trace |
| Preview/tab/task/settings navigation | Draft and scroll survive; adjacent-tab/fallback focus restored; hidden/inert controls not focused | `desktop/tests/ui-preview.playwright.js`, `ui-preview-tree.playwright.js`, `ui-windows-resize.playwright.js`; 320/390/900/1626/2048 checks |
| Safe settings and real capabilities | Only supported controls; saved values persist and errors do not claim success | Settings API/UI tests; reload and rejected-save traces, secret-redacted |
| Actual end-to-end task | Real user request → bounded provider run → approved tool → artifact → independent review → same-conversation delivery | Separately authorized live test; frozen commit, provider/model identity, cost/usage, receipt hashes and result |

## Evidence ledger and release conditions

For each completed matrix row record: exact commit, environment/platform,
command, timestamp, exit status, test count, evidence paths, mock/live mode and
remaining limitation. Screenshots must be marked synthetic when fixtures supplied
their content. Never store credentials or personal payloads in the report.

- The desktop reads durable main-conversation deltas with an opaque cursor and
  independent backward history. It preserves the draft on resync, coalesces
  independent reads, and does not block chat on tasks, approvals, or settings.
  The workspace context panel describes live Core data, not fabricated accounts.
- Conversation HTTP handling is separated from runtime bootstrap. An
  authenticated lookup recovers an already committed turn by request ID without
  executing the Router. An ambiguous network result is not retried automatically.
- Runtime supervision owns background tasks, bounded backoff, restart tracking
  and cleanup; Watchdog and persisted recovery remain authoritative.
- Worker result admission is a separate fenced transaction stage. It either
  persists pending tool calls, records more context debt, blocks, or queues
  independent review. A delivery claim is not verified completion.
- After successful tool work, a terminal reply rejected only for JSON syntax or
  schema enters durable reply-format correction, not artifact execution. The
  next request retains existing Core read-only source/receipt recall but removes
  action tools; admission independently rejects unexpected action calls before
  staging them. The original contract, source archives and review gates remain
  unchanged. Pause/reopen and untrusted inbox context preserve this restriction;
  an existing authoritative independent-review revision restores ordinary scoped execution.
  This uses existing capsule state, with no migration or provider-specific
  structured-output capability assumption.
- The shipped synchronization uses bounded polling, not SSE or token streaming.
  Conversation deltas replay new committed messages; ongoing activity remains
  a separate snapshot. No migration or workspace reset is required.
- Actual live-provider acceptance: **not established by this document**.
- Native Windows package/frame and installation acceptance: **not established**.
- Embedded external browser and arbitrary computer-control parity: **not established**.
- Upstream clone feature claims/security: **not independently validated**.

Historical regression numbers and failed live campaigns in the root README stay
historical. Do not carry them forward as this rebuild's pass count. Do not resume
an old paid campaign implicitly. Release claims must match the newly recorded
evidence, including failures, skipped platform tests and unsupported features.
