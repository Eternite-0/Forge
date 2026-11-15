# Phase 1 Milestone 6 — Context, Recall and Model Budget

Status: local M6 engineering verification passed. The results below are local engineering
evidence; they do not establish general model quality or unrestricted autonomous
operation.

Context archives preserve source references and user instructions, validate
archive integrity on rebuild, and support bounded Worker context with tool
receipt recovery. Memory indexes Core Quest, Workspace, decision, failure and
artifact sources in bounded incremental batches. Source snapshots are checked
against their current records; revocation, expiry, historical provenance and
plan binding remain visible. Model-derived or manually supplied claims remain
hypotheses. Model-call budget admission is being integrated across call sites.
The tested OpenAI-compatible endpoint returned additional decision fields despite
receiving `response_format` with a strict JSON schema. Gateway now also sends the
format schema as a system instruction; strict validation still rejects invalid
outputs, and a provider capability declaration does not guarantee compliance.
The [synthetic study](PHASE1_MILESTONE6_CONTEXT_EXPERIMENT.md) records its
fixed inputs, baselines, ablations, observed byte counts and limitations.

Gateway admission is configured through trusted process environment settings
documented in the README: `FORGE_MODEL_TOKEN_LIMIT` (default 0, denying calls),
`FORGE_MODEL_CONCURRENCY_LIMIT`, `FORGE_MODEL_REMOTE_MAX_SECONDS`,
`FORGE_MODEL_SCOPE_LIMITS_JSON`, `FORGE_PRICE_BOUNDS_JSON`,
`FORGE_MODEL_COST_LIMIT_MICROUNITS`, `FORGE_MODEL_COST_CURRENCY`,
`FORGE_MODEL_HISTORICAL_COST_MICROUNITS`, and
`FORGE_MODEL_SCOPE_COST_RECONCILIATIONS_JSON`. Historical currency amounts
must be reconciled by the operator; unknown amounts are not assumed zero.
Without a trusted remote execution bound, unknown attempts keep their slot and
there is no individual-attempt operator reconciliation interface yet.

## Capacity and remaining boundaries

The Router capsule preserves every distinct user utterance; continued growth can
eventually exceed the configured context budget and fail closed. Unlimited
conversation continuity is not solved. Task context currently permits at most
100 active Quests and reads a Quest's evidence and receipts without pagination.
Memory synchronization is limited to 100 batches of up to 256 source rows per
type during Router admission and explicitly fails when the backlog remains;
recall examines pages of at most 512 candidates and reports omissions. Future
work needs per-Quest constraint association, relevant source re-reading and
evidence pagination without losing user constraints.

General artifact verification without a plan revision is indexed as an unbound
historical observation. It does not prove a current file or plan is correct.
Events without a resolvable plan revision are omitted. Source content is data,
not an instruction or a permission. Real-provider M6 performance and model
quality remain unmeasured; no external model call is implied by the study.

The optional Windows native preflight with Rust 1.90 reached the Tauri build
script and failed because `desktop/src-tauri/icons/icon.ico` is absent. Its log
and exit status are preserved under `.local/phase1/native-preflight/`; this is
not an M7 implementation or a desktop release verification.

The final source snapshot passed **372 tests, with one FastAPI/Starlette
deprecation warning, in 77.78 seconds**. Ruff check passed from both the
repository root and `backend`; Ruff format checked 88 files. `pip check` and the
locked dependency vulnerability scan passed. The preservation audit found no
missing historical test functions and no changes to 361 baseline report,
audit and ADR files. Two historical assertion ASTs changed for the v8 migration
and required model budget context; their prior behavioral checks remain.
Import-only sorting was also needed to make Ruff's first-party handling
consistent across working directories. Exact commands, logs, exit codes, changed
test files and hashes are in the [verification record](audit/phase1/milestone6-verification.json).

## Live context diagnostics

Five bounded real-provider runs were retained under `.local/phase1/milestone6/`.
All five failed the end-to-end continuation check even though the SQLite Router
identity, 60 synthetic constraints and source readback checks passed. The first
made one HTTP attempt and returned a truncated response at the 512-token output
limit; the Python process failed, although its PowerShell wrapper did not
propagate that failure. The second made one HTTP attempt and ended in a
`DomainError`; the specific reason was not preserved and remains unknown.
The third made one successful HTTP attempt, then its second logical generate was
rejected by FORGE's conservative local 30,000-token reservation ceiling, with no
second HTTP attempt. The fourth used a 75,000-token ceiling and made one HTTP
attempt, but the model requested five tools and Core rejected the batch at its
four-tool limit. The fifth made two HTTP attempts and observed the tool limit,
but its final response used fields outside the strict Router decision schema;
Core rejected it. A database check confirmed the model's Quest ID matched the
original target. Across the two attempts, reported input was 11,403 tokens and
output was 2,127 tokens. This exposed the need to include the schema in model-visible
messages for providers that do not reliably enforce response-format parameters.
Fixes were applied and the final suite passed. The failures are not provider outage evidence
and cannot be counted as a successful recovery demonstration. No monetary cost
was available; the calls used token-only limits.

The sixth bounded run, after the tool-limit and schema-message fixes, passed.
One real HTTP attempt returned a strictly validated `continue_quest` decision
matching the original target. The same Router identity, 60 synthetic constraints,
105 cancelled synthetic Quests and source hash/readback checks were preserved.
It reported 5,420 input and 532 output tokens (5,952 total); the 75,000-token
figure was an admission ceiling, not usage. Cost was unavailable. Across all six
runs there were seven actual HTTP attempts; the third run's second logical
generate was rejected locally and did not send another HTTP request. This is one
narrow successful continuation scenario, not proof of general model quality.

## Next milestone dependencies

M7 remains unimplemented and no desktop Alpha success is claimed. The planned
ownership is Native integration (Context owner), backend bootstrap, backup and
bounded semantic harness (Budget owner), and desktop UI plus final evidence
(Memory owner). The proposed packaging pins a CPython embeddable runtime and
hash-locked wheels, with offline NSIS packaging. Native startup must use a
private pipe, dynamically allocated port and EOF shutdown, together with a
Workspace mutex and Windows Job Object. Each Core generation gets a new secret;
the UI receives only its derived bearer. Windows credential-vault testing is
limited to temporary dummy credentials. These are design dependencies, not
implemented or verified capabilities.

Desktop acceptance must cover Chinese IME composition so Enter does not submit
mid-composition; failed network, 401 and 409 sends must retain the draft and
the same request ID for safe retry and expose the actual error. Browser and
native clients keep the bearer only in memory and reconnect on Core generation
changes. Activity shows Worker state without creating additional conversations.
Lists need loading, empty, error and pagination states. `GET /api/conversation`
currently returns the latest 200 messages without a history cursor; it must be
extended with the backend owner before the UI can claim that the entire long
conversation is browsable.
