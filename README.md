# FORGE v4 — General-purpose Super-agent

The desktop opens to one persistent conversation, with a Codex-inspired
navigation sidebar, app menus and a compact composer. The conversation shows
user messages, assistant replies, usable results and required approvals. Background
work stays behind one execution-details navigation entry, opening its own screen within the app; individual agents open in that same screen
with responsibility and status, while logs and verification metadata remain
optional. Task management is available from the conversation's overflow menu.
The previous [frontend acceptance report](desktop/CODEX-REFACTOR-REPORT.md)
describes the historical Codex-style layout; see the [API-to-UI map](desktop/API-UI-COVERAGE.md)
and [desktop instructions](desktop/README.md) for existing capabilities.

FORGE is a self-built, general-purpose DeepSeek-style Super-agent desktop
application: one persistent Router conversation coordinates hidden, isolated
Workers and continuously receives events.

The [Grok Bot-inspired runtime and desktop rebuild](docs/GROKBOT_REBUILD.md)
documents the new session, replay, supervision, and delivery boundaries. It
describes what was verified offline separately from live and native acceptance.
The [conversation subagents design](docs/CONVERSATION_SUBAGENTS.md) explains
how delegated work is inspected without replacing the main conversation.

## Continuity review — 2026-11-16

The `codex/phase1-continuous` review against `0bef3316` preserves the persistent
Router / hidden Workers / Trigger / pass-through / Chain of Recall architecture;
it does not change the frontend. Project matching now uses parsed path boundaries
and the most specific nested project. Unread Thread requirements enter the next
Worker model turn, and verified full Thread pages reach independent artifact
review as context, never as execution evidence. Router archives expose typed
original-message references for actual paginated recall after restart.

The original **17 failed / 2932 passed / 12 skipped** regression and its failure
classification are retained in [the continuity audit](docs/audit/continuity-review-20261116/REPORT.md).
The [final continuity report](docs/audit/continuity-review-20261116/FINAL_REPORT.md)
separates frozen-source regression, new bounded real observations, historical
failures and remaining limitations. Mock orchestration is not live acceptance;
these minimal conditions do not replace full capability acceptance.

Current source commit `61ec4f8570d72e22aac7ca8d30d05473151150ee` has a new frozen
full offline result of **3015 passed / 0 failed / 12 skipped**. It additionally
defers inbox notices until the admitted assistant/tool batch settles, preserving
next-turn visibility and the original gates and budgets. That ordering fix has
**not** been live-tested. The new real conditions ran at
`9cb619f0b8fa0ad9d93ac9fe6ceaafc4dd358669` and **did not pass**: project creation/switching succeeded, but the
post-compaction restart Recall child failed; a separate Thread condition showed
the addition in the next request, then received a Provider rejection before
artifact/review acceptance. Campaign accounting is now **105 attempts /
1163999 charged-or-reserved tokens**, including **one unknown-usage rejection
with its reservation retained**. Do not run the fixed-99-opening example below
as a fresh campaign or resume either failed condition. Read the final report
and reconcile the retained ledgers and authorization before any new live work.

The optional one-shot probe uses existing dependencies. Run from the repository
root with the backend environment. Only `run` contacts the Provider; `preflight`
is offline and does not create the workspace, and `audit-ledger` is read-only.
Supply the secret through an environment variable (not a CLI flag, committed
file or captured shell command):

```sh
export FORGE_PROVIDER_CREDENTIAL_REF=FORGE_PROVIDER_KEY_CONTINUITY
# Set FORGE_PROVIDER_KEY_CONTINUITY securely in your local environment.
backend/.venv/bin/python backend/scripts/continuity_minimal_probe.py preflight \
  --workspace /absolute/new/isolated-workspace --with-thread
# LIVE: requires current authorization and reconciled remaining campaign budget.
backend/.venv/bin/python backend/scripts/continuity_minimal_probe.py run \
  --workspace /absolute/new/isolated-workspace --with-thread
backend/.venv/bin/python backend/scripts/continuity_minimal_probe.py audit-ledger \
  --workspace /absolute/new/isolated-workspace
```

The driver pins the historical **99 attempts / 1089785 charged-or-reserved
tokens**, allocates at most **18 new attempts / 220000 tokens**, uses a shared
durable pre-HTTP ledger and zero retries, and starts fresh OS processes for
seeding and recall. It refuses existing workspaces, source drift, unknown usage
and replay. It never authorizes another campaign: after any real run, reconcile
its durable ledger before considering a new allocation. The fixed historical
opening is not a mechanism for resetting or duplicating the remaining budget.

### Historical capability sprint status (before this review)

The current backend capability sprint adds project-focused context, bounded
specialist dependencies and expansion, real CPython unittest execution, model
microbenchmarks and progress-based stall detection. Read the
[capability report](docs/SUPERAGENT_CAPABILITY_SPRINT_REPORT.md) and
[experiment record](docs/SUPERAGENT_EXPERIMENTS.md) for current acceptance and
limitations. No frontend redesign or external Agent execution backend is added.
Current full capability acceptance has **not passed**. The latest closed joint
condition completed actual multi-specialist L2 research and L3 development,
including real failed tests, code correction, successful exact-manifest receipts,
seven independent holdouts and reviewed delivery to the original Router.
All 33 calls settled, using 376561 tokens, with no unsettled tool operation.
Complete Thread consumption and actual post-restart relationship Recall failed;
those original failures remain recorded. The completion stage totals 99 settled
attempts and 1089785 charged-or-reserved tokens.

The prior production correction gated final delivery on actual complete
source-bound Thread reads or an explicit proved handback. It preserves charged
drafts and original budgets, and grants no new permissions. This correction has
targeted offline verification at that historical checkpoint, without a new real
Provider run or full-backend regression. That prior full suite's 2886 passed, 13 failed and
3 skipped remain attributed to its earlier frozen source.

The user requested a combined GitHub delivery of the backend and desktop work
on `codex/phase1-continuous`. Real calls were deferred at that checkpoint. Read the
[GitHub continuation guide](docs/GITHUB_CONTINUATION.md) for a fresh checkout,
current checks and remaining acceptance, and the
[backend handoff](docs/SUPERAGENT_NEXT_ENV_HANDOFF.md) for original budget
boundaries. The user's explicit DeepSeek **100M** context declaration takes
precedence while conflicting discovery metadata remains attributed. Model
capacity, request affordability and cumulative spending are separate. Exact
reports, test counts and source hashes are indexed in
[final verification](docs/audit/superagent/final-verification.json).

`python_unittest` executes approved Python with the current OS account. It uses
an exact SHA-256 manifest, an R2 approval, a private working directory, a minimal
environment and a ten-second process-tree limit; this is not an OS sandbox.
Changed source requires another exact approval. The existing `python_test`
restricted-function tool remains available separately.

Authenticated model-intelligence APIs provide identity discovery
(`POST /api/model-intelligence/discover`), existing-record performance/health
(`GET /api/model-intelligence/{profile_id}`), and bounded text microprobes
(`POST /api/model-intelligence/benchmark`, with `profile_id` and optional
`dimensions`). Discovery does not establish capability. The six fixed probes
record source, sample size, time, usage and confidence; vision remains unknown.
These endpoints use the configured Provider and existing Registry/benchmark
records. They require no additional dependencies or environment variables.

An optional trusted `FORGE_MODEL_CALL_LIMIT` sets a positive total HTTP-attempt
ceiling for the existing workspace ledger. Settled, rejected, reserved and
unknown attempts all count; restarting Core does not refund attempts. This cap
is independent of model context capacity and cumulative Token/cost ceilings.

## Engineering status — 2026-11-16

The backend architecture now follows DeepSeek Super-agent's
persistent Router and independent Workers. The default Core delegates ordinary
conversational tasks automatically, filters environmental events, sends Thread
continuations directly to subscribed Workers, follows typed Chain of Recall
references, and supervises durable execution. Plans, Quests and approval records
are internal infrastructure. The frontend is unchanged in this correction.

Read the [architecture audit and refactor plan](docs/SUPERAGENT_REFACTOR_PLAN.md),
[product architecture](docs/PRODUCT_ARCHITECTURE.md) and
[implementation and validation report](docs/SUPERAGENT_IMPLEMENTATION_REPORT.md)
for the exact implemented behavior, tests, real Provider observations and limits.
Historical Phase 2 evidence remains available below; it does not describe the
new autonomous dispatch path.

Frozen-source real Provider acceptance passed both the
[conversational/restart scenario](docs/audit/superagent/20261115T193658Z-d72209.json)
and the [event/adaptive-team scenario](docs/audit/superagent/20261115T193938Z-cold-697d50.json),
with 22/22 and 17/17 calls settled respectively. These bounded observations
verify the new backend flow; exact tool limits and regression debt are recorded
in the implementation report and do not establish arbitrary-task correctness.
The frozen backend regression recorded **2312 passed, 14 failed, 2 skipped**;
the fourteen failures match those reproduced on the base source and tests.
See the [acceptance index](docs/audit/superagent/acceptance-index.json) for
report hashes, final evidence and preserved diagnostic history.

See the [current status](docs/PHASE2_STATUS.md), [Phase 2 report](docs/PHASE2_AGENT_INTELLIGENCE_REPORT.md),
[experiments](docs/PHASE2_EXPERIMENTS.md) and [V22 historical boundary](docs/audit/phase2/acceptance-independent-v22-boundary.md).
Historical failures and original evidence remain preserved. The real algorithm
microstudy scored 25/90 task successes; it does not establish general policy gains.
Frontend work is being handled separately; packaging is outside this sprint.

Phase 1 Milestones 1–6 provide the persistence, governance, Registry, DAG, native Workers, tool/evidence and context/budget foundation. Historical M6 verification passed 372 tests; one bounded live continuation succeeded after five retained diagnostic failures. The [M7 backend hardening round](docs/PHASE1_MILESTONE7_BACKEND_HARDENING.md) passed 488 local tests. Desktop Alpha, final native integration and packaging are deferred; these historical backend results do not establish a desktop release.

See [current status](docs/STATUS.md), the [Phase 0 audit report](docs/PHASE0_AUDIT.md), and the report's dependency-ordered development plan. The release references `codex/MASTER_PROMPT.md`, but that file is missing from the supplied archive.

**The user has one persistent conversation.** A persistent Router Agent owns that conversation and coordinates multiple long-running Quests and hidden Worker Sessions. Workers have isolated execution contexts, but they are never presented as separate user conversations.

## Product invariants

1. One persistent user-facing conversation per workspace.
2. One persistent Router identity and resumable state per workspace.
3. Multiple durable Quests can run behind that conversation.
4. Worker Sessions are private execution contexts, not user-facing chats.
5. Worker events are selectively relayed into the single conversation timeline.
6. Model Provider, Model Profile, Role Template, Agent Instance, Quest and Session are separate domain entities.
7. Model Registry uses provider metadata, FORGE benchmark results, online performance and explicit user overrides.
8. Team composition combines reusable role templates with bounded dynamic role synthesis.
9. FORGE owns its Agent Loop, Router, Worker Runtime, Tool Engine, Memory, Governance and Evidence.
10. DeepSeek Super-agent defines the overall architecture; Codex and Claude Code inform Harness design. None is an execution backend.

## What this release contains

- Typed Python domain layer and repository interfaces.
- SQLite persistence with schema versioning and transaction boundaries.
- Persistent `main` conversation and Router identity.
- Quest lifecycle state machine and hidden Worker Session records.
- Durable event log with sequence cursor.
- Model Registry and explainable capability-fit team composition baseline.
- Provider gateway and explicit unconfigured provider; Mock protocol tests are labelled.
- FastAPI API with input validation and consistent error handling.
- React single-conversation UI with background Quest panel.
- Automated tests for persistence, lifecycle, router classification and team composition.
- Architecture, threat model, ADRs, detailed roadmap and Codex execution prompts.

## Important maturity statement

This is an engineering implementation with bounded context recovery, source-bound
recall and evidence-gated execution. A configured Provider is required; Mock
tests and real Provider observations are identified separately. Autonomous
internal plan/team contracts use an auditable host mandate tied to the original
request or admitted Trigger. Manual approvals retain authenticated user authority,
and risky tool actions still require their own authorization. A mandate or
proposal does not prove execution. Worker directories are private execution
contexts, not an OS sandbox. General complex-task correctness remains subject to
recorded acceptance.

## Backend setup

The model-driven Router requires explicit provider configuration. Set the process environment
variables `FORGE_PROVIDER_ID`, `FORGE_PROVIDER_KIND` (for example `openai_compatible`),
`FORGE_PROVIDER_BASE_URL`, `FORGE_PROVIDER_CREDENTIAL_REF` (for example
`FORGE_PROVIDER_KEY_EXAMPLE`), and `FORGE_ROUTER_MODEL_ID`.
`FORGE_ROUTER_CONTEXT_LIMIT` optionally caps the registered model context window.
When omitted, Router uses the Model Registry window. Current local DeepSeek
Provider metadata reports 1000000 input tokens and 128000 output tokens. This capacity is separate from
cumulative task spending limits and is not an independently measured maximum. Put the actual
credential only in the process environment variable named by the credential reference. Set
`FORGE_PROVIDER_SUPPORTS_TOOLS=true` and `FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA=true` only when
the provider supports them. Without configuration the Core starts but Router message calls fail
explicitly; no keyword or mock provider is selected automatically. The optional `request_id`
in a message request allows safe retry: reuse it only for the same input.
For live calls, register an enabled model with the same provider and model ID and explicitly set
both `tool_calling_support` and `structured_output_support` to supported. Unknown capabilities
are rejected. Values submitted through the model API are recorded as user overrides.

Provider timing, Worker ownership and review/output limits use the process environment:

| Variable | Meaning |
| --- | --- |
| `FORGE_ENFORCE_TOKEN_BUDGETS` | Defaults to `false`: cumulative workspace, Quest, team and Worker token estimates do not stop execution. Usage is still recorded and unsettled calls remain unresolved. Set `true` to opt into cumulative limits and configure `FORGE_MODEL_TOKEN_LIMIT`. Model context/output limits, concurrency, tool permissions, deadlines and review counts still apply. |
| `FORGE_PROVIDER_TIMEOUT_SECONDS` | Per-attempt timeout in seconds; finite and greater than `0`, at most `600`; defaults to `30`. A timeout does not prove the upstream operation stopped. |
| `FORGE_PROVIDER_MAX_RETRIES` | Additional Gateway retries, integer `0`–`3`; defaults to `1`. Each HTTP attempt requires its own budget admission. |
| `FORGE_WORKER_LEASE_SECONDS` | Worker ownership lease, finite and greater than `0`, at most `600`; defaults to `60`. Valid current owners renew between steps, capped by the task deadline. The local call wait is bounded by the current lease; an upstream operation may remain unresolved after that wait ends. |
| `FORGE_REVIEW_MAX_ROUNDS` | Maximum review rounds; integer `1`–`4`, defaults to `2`. The live acceptance harness explicitly selects `3`, matching its scenario cap. This does not reset budgets or change historical runs. |
| `FORGE_PROVIDER_REASONING_EFFORT` | Optional explicit OpenAI-compatible/DeepSeek request setting: `none`, `minimal`, `low`, `medium`, `high`, `xhigh` or `max`. Unset sends no setting. Configure only for a model that advertises support; Anthropic configuration rejects it. |
| `FORGE_ROUTER_OUTPUT_RESERVE` | Router response-token ceiling and reserved output allowance; integer `1`–`8192`, defaults to `2048`. `FORGE_ROUTER_CONTEXT_LIMIT` must be greater than this value. A registered model's known output limit must also support it. |
| `FORGE_ROUTER_CONTEXT_LIMIT` | Operator-supplied Router context ceiling, required when setting a Router model. Core startup requires an integer greater than `2048` and greater than the configured output reserve. |

The Phase 2 live acceptance harness uses a 120-second timeout, a 150-second Worker lease and zero additional
Gateway retries, with a Router output reserve of `4096` and `FORGE_REVIEW_MAX_ROUNDS=3`
within the scenario's three-round cap. Router/Planner schema repair remains a separately bounded stage;
neither setting authorizes replay of an unresolved external action.

The current WorkBuddy acceptance harness explicitly requests `reasoning_effort=none`,
which the local proxy implementation advertises for the requested model. This is
an operator setting, not independent proof of the upstream model's internal behavior.
Earlier diagnostic runs used the proxy default and retain their separate results.

The Router reads a bounded snapshot and calls the provider outside the database write
transaction. A successful decision and its messages commit together; provider errors, invalid
decisions, or budget failures leave no messages or Quest changes for that turn. The caller can
retry with the same request ID. The context budget uses a conservative UTF-8 byte estimate,
reserves output and request overhead, and also respects a known registry context window. Tool
access is limited to read-only Core queries. Worker execution separately requires approved plan/team contracts and governed tool permissions.

Python 3.11+ recommended.

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-lock.txt
python -m pip install --no-deps --no-build-isolation -e .
pytest
python -m forge_api
```

The lock file is verified on Windows with Python 3.13; other platforms and Python versions have not been verified. The editable installation requires build tooling already available. API documentation endpoints are disabled.

### Core authentication and development bootstrap

Core requires a fresh cryptographically random 32-byte process secret; `.env` is explicitly rejected. For manual browser development, supply it as 64 hex characters in `FORGE_CORE_SECRET` using a protected input method and enter the same secret into the UI's password field. Never place it in command history, source files, URLs, logs, or persistent browser storage. In the native desktop integration, the parent supplies the secret through a private pipe and the UI receives the derived session bearer in memory. Restarting Core invalidates old bearer tokens. Native integration is still undergoing final verification.

The unauthenticated public loopback `/health` returns only liveness and a random per-process session challenge; an authenticated request also receives Router identity and background status. A trusted client computes the lowercase hex HMAC-SHA256 over the UTF-8 challenge using the decoded 32-byte secret, and sends `Authorization: Bearer <digest>` on API requests. Requests without an Origin are allowed only with authentication and a loopback peer and Host; browser requests must also use an explicitly allowed Origin. These boundaries protect against unauthorized webpages and other untrusted clients; they do not protect secrets from an attacker who can read the same OS user's process memory. Keep the development server and any allowed origin trusted.

The authenticated user API supports `POST /api/approvals`, `GET /api/approvals?quest_id=...`, `GET /api/approvals/{id}`, and POSTs to `/{id}/decision` or `/{id}/revoke`. Workspace grants use `GET`/`POST /api/quests/{id}/grants` and `POST /api/quests/{id}/grants/revoke`. Decisions and grants record the authenticated local user, not a caller-supplied actor. The bearer represents that user; it must never be exposed to a model or registered as an Agent tool. These routes do not execute actions or accept trusted evidence. The M7 desktop approval and scope controls are under integration testing.

### Workspace and Settings

`python -m forge_api` is the shared Core launcher. From the repository root on Windows, `./scripts/run-backend.ps1` sets up the virtual environment and uses that launcher without shell activation. Pass `-WorkspaceRoot 'D:\YourWorkspace'` to the script, or `python -m forge_api --workspace-root 'D:\YourWorkspace'` directly. Workspace roots must be absolute paths.

The CLI launcher supervises a separate Core process and restarts unexpected exits
with bounded backoff (at most five restarts in ten minutes; five minutes of stable
operation resets that count). Normal shutdown, invalid configuration and a restore
fence stop recovery. A separate supervisor lock prevents duplicate launchers, and
the Core retains its existing Workspace lock. The bootstrap secret reaches the
child through a private pipe; each replacement rotates its session challenge and
invalidates old bearer tokens. On Windows a Job Object drains the Core's descendant
processes before recovery and kills them if the supervisor dies. The native
`--desktop-host` protocol is unchanged; automatic native desktop process restart
is not yet integrated.

The default Workspace is independent of the checkout, installation and shell directory:

| OS | Default Workspace root |
| --- | --- |
| Windows | `%LOCALAPPDATA%/FORGE/workspaces/default` |
| macOS | `~/Library/Application Support/FORGE/workspaces/default` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/forge/workspaces/default` |

The database defaults to `<WorkspaceRoot>/data/forge.sqlite3`. Settings are loaded as follows:

1. Choose the root using `--workspace-root`, then the process variable `FORGE_WORKSPACE_ROOT`, then the OS default (in that order). A root in `.env` is rejected because the root determines which file to read.
2. Load `<WorkspaceRoot>/.env` if present. `FORGE_ENV_FILE` in the process environment can select another file; relative file names resolve from the root. An explicitly selected missing file is an error. The shell directory's `.env` is ignored. Copy `backend/.env.example` to the selected Workspace root.
3. Process variables override file values. Supported settings are `FORGE_WORKSPACE_ID`, `FORGE_DB_PATH`, `FORGE_HOST` and `FORGE_PORT`; empty values and invalid ports fail startup. `.env` does not expand variables or modify the process environment.
4. Resolve relative database paths from the root and normalize them to absolute paths. The default Workspace ID is a hash of the normalized root; an explicit stable ID supports moving a Workspace. A database already bound to another ID is rejected.

The CLI and desktop Core bootstrap use the same Workspace settings and a new process secret. Direct `uvicorn forge_api.main:app` requires the same secret and loopback binding; prefer the shared launcher.

After a workspace restore, a restore fence blocks writes until an offline review succeeds. Close the desktop and Core, inspect the restored workspace and rollback directory, then use the packaged Python executable with the exact manifest hash and rollback directory shown by the application (replace every placeholder):

```powershell
& 'C:\path\to\packaged\python.exe' -I -m forge_api.maintenance review-restore --workspace-root 'C:\path\to\workspace' --manifest-sha256 '<64-lowercase-hex>' --rollback-directory '.forge-previous-<32-hex>' --reason '<10+ character review reason>'
```

The review validates the source hash, rollback directory name, reason, historical receipts and budget state; unresolved effects or reservations cause it to fail closed. Acknowledging an unknown native operation is a separate action and does not clear the restore fence or establish that the operation succeeded.

Legacy databases are **not discovered or moved automatically**. If an earlier version used `backend/data/forge.sqlite3` or another CWD-relative location, stop its Core process, back up the database with SQLite's backup API (or copy the database and WAL files together while stopped), and set `FORGE_DB_PATH` to its absolute path. The first successful open migrates it in place to the current schema without changing the Router ID or existing records. Migration failure rolls back the entire pending chain; unknown future versions and ambiguous version metadata are rejected. Downgrades are unsupported.

Events and pending Outbox rows commit in the same transaction as their associated business changes. Durable per-consumer delivery uses leases, fencing, bounded retries, Inbox deduplication and dead-letter/replay records. Historical events migrate as pending. External tool effects require their own durable intents and receipts; event delivery alone does not make external effects exactly once.

### Autonomous event and Worker execution

The default Router creates bounded internal execution contracts automatically.
It may choose one to four independent Worker assignments and preset roles; the
Registry/Composer selects eligible models. Detailed execution stays inside each
Worker. User and event source references remain attached to the host mandate.
The existing manual contract APIs remain compatibility and inspection tools.

Authenticated host integrations can register sources with
`POST /api/triggers/sources` and submit events with `POST /api/triggers`.
Persistent schedules use `POST /api/triggers/schedules`; local file watches use
`POST /api/triggers/file-watches`. These are Core integration endpoints and are
not exposed as bearer-bearing model tools. Source policy filters events before
inference. Subscribed Thread events go directly to Worker Inboxes, while cold
events receive bounded Router filtering. `GET /api/recall?reference=<typed-ref>`,
`GET /api/projects/discovered` and `GET /api/runtime/health` expose bounded
inspection. Project associations do not grant tool access.

Schedules and file watches currently require those authenticated host APIs;
natural-language schedule/watch creation is not a model tool. External ingress
accepts events from a trusted local adapter, without adding a public webhook
server or built-in messaging connectors.

Autonomous Workers use fixed local file/Python/Git/GPU tools. Terminal inspection
is a fixed version probe, Git is status-only, and arbitrary shell execution is
unavailable. The separately authorized browser tool performs allowlisted HTTPS
GET without interactive rendering; MCP supports preconfigured HTTPS JSON-RPC
calls. Browser and MCP are outside the default autonomous tool allowlist. GPU
support is read-only diagnostics. See the implementation report for exact scope
and the tools exercised by real Provider validation.

The following execution details describe the shared governed Harness used by
both autonomous and manually approved contracts.

`python_test` runs bounded pure Python function tests against a governed workspace
file using an isolated subprocess and an AST interpreter. Its approved parameters
are `path`, `function` and `suite.cases` (1–32 input/expected cases). It supports
bounded loops, helpers and recursion; imports, attributes, external calls, file or
network access, and arbitrary shell/pytest execution are unavailable. A failed
case records the actual and expected values in a failed immutable receipt. An
approved Worker can use `file_revise` on its own artifact, rerun the tests and
submit the new evidence within its original repair and resource limits. No new
dependency or environment variable is required.

Workers consume an approved Quest DAG and a separately approved team. Tool scopes, provider/model identity and token/tool/time/concurrency budgets are bound to those contracts. File writes additionally require a workspace grant; HTTPS/MCP actions require exact action approval and an operator allowlist. `FORGE_TOOL_NETWORK_HOSTS_JSON` is a JSON host list (at most 64); `FORGE_MCP_SERVERS_JSON` maps server names to `{"url":"https://example.invalid/mcp","tools":["example_tool"]}` (at most 16 servers), with the HTTPS host included in the allowlist. Defaults are empty and deny network access. These values are operator configuration, never model output. `jsonschema` validates Worker result contracts; it does not judge domain correctness. Help/reassignment produces a pending team proposal for authenticated approval. An uncertain action cannot be silently retried or discarded during replacement. Unsupported or failed fixed JSON criteria require manual review; infrastructure verification retries are bounded to three attempts.

When cumulative limits are explicitly enabled, a member's
`input_tokens + output_tokens` defines its Worker/review allowance across model
turns. With the default disabled policy, these allocations are estimates and
usage remains recorded. Each request still fits the model context and its
approved single-response `output_tokens` ceiling. Compaction does not reset usage.

V7 guidance covers new intermediate JSON artifacts and delivery_review handoffs with downstream consumers
toward about 4 KiB (at most 6 KiB) only when all assigned requirements fit. Complete code, final reports
and required detailed schemas remain full even when larger. This is soft writing
guidance: no stored artifact is truncated, no hard report-size gate is added, and
original packet/PASS/source/raw/context/evidence requirements remain unchanged.
Planner guidance recommends about two model contexts of cumulative allocation for
a source-heavy entry node, when the task ceiling permits, and at least ten percent
coordination reserve within the original total. V8 removes the fixed intermediate-output
preference and preserves enough per-call output for complete schema-valid tool
arguments and delivery. Shared source citation IDs avoid repeating full hashes while
preserving exact references.
New/replacement team proposals and approval enforce a trusted operator concurrency
cap; existing approved teams and historical evidence retain their contracts. Core
supplies Composer and WorkerManager the Worker concurrency cap `2`, independently
of the Gateway model-call concurrency cap. Both resource limits remain enforced.
The live harness takes the smaller CLI/scenario total-token and concurrency limits
and records the effective values. V8–v12 full/live outcomes are recorded; this round's final state remains partial; see the v12 evidence record.

For new native TeamWire proposals, only explicit `output_schema_json=null`
selects a deep copy of `STRICT_DELIVERY_OUTPUT_SCHEMA`: the model must supply
all eight delivery fields and confidence within 0–1, with no Core-inserted defaults.
The contract is checked under any node criterion through JSON Schema and bounded
repair. Legacy/default direct RoleSpec contracts and explicit older schemas retain
their original meaning; historical approvals/results are unchanged. Non-finite
JSON values are rejected through the existing bounded repair path. Strict structure
does not establish semantic correctness or replace review/evidence gates.

V8 removes the intermediate output_tokens=2048 preference: per-call output must
fit complete schema-valid tool arguments and delivery. Worker instructions cover
the current node's approved objective, role and criteria; downstream nodes retain
their own deliverables. Planner guidance uses one node only when the entire task
is simple read-only analysis and preserves explicitly requested specialist/count
requirements. Composer guidance preserves unique role IDs and correct specialties,
and prefers max_steps=16 only when the user's ceiling permits. Native Core configures
a shared self-repair/review-revision maximum of four; existing counters, original
steps/tokens/tools/cost/time and review-round limits still bind every attempt.
Worker concurrency two remains independent of Gateway model-call concurrency.

V9 rejects a generated node with a concrete artifact verifier unless that same
node already permits `file_write`; feedback uses the original bounded Planner
schema-repair path and inserts no tool authority. Worker requests with an effective per-request allowance ≥8192 show remaining
model steps, tool calls and cumulative tokens, and point to integrity-bound archived
source_refs/current artifact receipts. This guides reuse of evidence without
assuming the repeated-read cause is proved. A tool batch exceeding remaining calls
is rejected before any tool executes and may use the existing bounded repair path;
the settled model attempt stays charged. Original tool/step/token/cost/time and
repair/round limits remain unchanged. Publisher adds numeric retry totals/breakdown
and allowlisted review reason/context fields; prompt/reply/log/artifact bodies stay
excluded, and missing historical fields remain unknown/null. V9 full/live
outcomes are recorded below; V12 frozen results are recorded; whole-task complex continuity remains incomplete.

The [v9 full suite](docs/audit/phase2/frozen-tests-v9.xml) / [log](docs/audit/phase2/frozen-tests-v9.log)
passed 895 tests with one warning in 237.83 seconds. Its [157-file manifest](docs/audit/phase2/source-manifest-v9.json)
is `9a33b199404b777428ff0f0e1a1c1da356ad93b23b4395565fc0d211b3d9203a`;
Ruff, formatting for 154 Python files and pip checks passed. This predates v10.
[Offline benchmark v9](docs/audit/phase2/benchmark-final-v9.json), `benchmark_c4387a6e26ac`,
completed in 5.9372256 seconds with zero Provider calls on the same fixed dataset.

The v9 live runs share snapshot
`624f0ac7cb1873163e2e0a5b1849642bb363d553aedab4331096a0286b134db8`.
L2 `20261115T021801Z-L2-b11011` failed in 80.141 seconds: 17 model/six tool
calls, charged input/output 65,607/9,348 tokens, unknown zero.
L3 `20261115T021801Z-L3-290b7f` failed in 97.584 seconds: 18 model/six tool
calls, charged 71,908/14,522 tokens, one unresolved Router-notice reservation
of 9,985+4,096; total actual usage and cost remain unknown.
Actual receipt-bound intermediate artifacts were 15,235/10,788 bytes respectively.
Read-only reconstruction produced raw packets of 68,207/68,967 bytes, exceeding
the unchanged 65,536 cap. Both retained `review_packet_too_large`, zero
`review.started`, the first Worker at `review_pending`, no successor and no restart.

All 39 finished driver records are safely published in
[snapshot-v2](docs/audit/phase2/live-runs/snapshot-v2/index.json), with original run records and
diagnostic-snapshot-v1 preserved. Publication makes no Provider call and does not
promote failed or unaccepted tasks. Historical retry totals can overlap: the old
`worker_runs.repairs` includes review revisions. V10 counts actual self-repair
events separately and marks future records `retry_accounting=disjoint_event_counts_v2`;
[27 focused regressions](docs/audit/phase2/retry-accounting-v10-fixed.xml) pass, while original
run counters remain unchanged. V10 full/live outcomes are recorded below; v12 frozen results are recorded; the v12 evidence record consolidates this round's partial state.
Core capacity feedback is not independent model review or PASS.

V10 adds labelled Core capacity diagnostics (`deterministic_review_capacity` /
`core_review_capacity`), never a model verdict or PASS. Original packet/source/hash
and 65,536 raw/context caps remain. Same-Worker revision requires approved
`file_revise`, current receipts/grants, remaining original repair/step/tool/token/
cost/time/round limits and no unsettled actions/model attempts. Arithmetic floors
only check whether a complete revision plus real independent review might fit;
they do not remove required evidence or prove compression. Inability to fit is BLOCKED.
The [corrected capacity group](docs/audit/phase2/reviewer-capacity-v10-fixed-tests.xml) passed
143 cases (JUnit 45.843 seconds); the [first 143-case attempt](docs/audit/phase2/reviewer-capacity-v10-tests.xml)
retains one failure. These are overlapping local/Mock tests.
The driver waits read-only for settlement for at most 30 seconds before stopping;
it never resends, cancels or settles unknown remote calls itself.
[Settlement regressions](docs/audit/phase2/driver-settlement-v10.xml) passed 27 cases
(JUnit 4.263 seconds). V10 records use disjoint event-count retry accounting;
historical counters and outcomes remain unchanged.

The [v10 full suite](docs/audit/phase2/frozen-tests-v10.xml) / [log](docs/audit/phase2/frozen-tests-v10.log)
passed 915 tests, one warning, in 279.54 seconds. The [158-file manifest](docs/audit/phase2/source-manifest-v10.json)
is `814d0dcc6496f65d01ededad38a1e45a8d0f9848739467f7c36b985f3c318415`;
Ruff, formatting for 149 Python files, pip and diff checks passed. This precedes v11.
[Benchmark v10](docs/audit/phase2/benchmark-final-v10.json), `benchmark_4f7e4f5325ba`,
completed in 7.5434305 seconds with zero Provider calls on the unchanged fixed dataset.
The [read-only preservation audit](docs/audit/phase2/preservation-v10.json) retains 355 byte-identical
Phase 1 evidence files, all 364 original test identities and nine unchanged reports;
349 test ASTs are identical and 15 changed, with listed assertion replacements.
This is preservation evidence, not an equivalence or live-success claim.

All three v10 live runs share runtime snapshot
`f822b94b68dfa6e682f71fc1930d30054aa47bf41dfc27ef75a984e66f432c40`
and failed strict acceptance. Final unknown attempts are zero in all three; cost
remains unknown. L1 `20261115T061513Z-L1-c94230`: 59.801 seconds, 11 model/five
tool calls, charged 32,806/3,291 input/output tokens. It used a plan ID as an
upstream receipt ID and blocked at `context_source_rejected`, before review.
L2 `20261115T061513Z-L2-62b044`: 80.284 seconds, 17 model/nine tool calls,
75,382/6,207 tokens. Actual independent same-model research review returned PASS
and the source Worker succeeded. With work remaining, Core paused at a settled
boundary, stopped, created a Router archive with two source messages and verified
their readback, then restarted in the same workspace with the same Router ID,
completed node, receipt hashes, settled attempts and cumulative Worker counters.
Resume returned 200. The architect had already started one step before the pause;
it continued after restart, then blocked when reading an 11,211-byte direct
predecessor receipt above the original 8192 readback cap. This demonstrates this
bounded continuity segment, while the final task and strict acceptance failed.
L3 `20261115T061513Z-L3-8fdef4`: 120.099 seconds, 25 model/eight tool calls,
134,215/15,831 tokens. Real independent NEEDS_REVISION led to same-Worker revision;
the new raw packet was 70,657 bytes, native input reservation 70,947. Worker use
98,513/160,000 left 61,487, insufficient for the guarded capacity revision plus
review floor, so Core issued `review_capacity_unrepairable` / BLOCKED. No Core
restart occurred. Its early unsettled-call stop label remains in history; the
final read-only wait observed settlement and the final ledger has unknown zero.
Retry totals under disjoint accounting are 0/0/2, with L3 one review revision and
one background-notice retry, zero Worker self-repairs and zero HTTP retries.

[Snapshot-v3](docs/audit/phase2/live-runs/snapshot-v3/index.json) publishes 42 finished driver
records and preserves snapshot-v2/v1 plus original run records. Publication made
no Provider request and does not promote failed tasks. Historically at v10, v11 was in progress:
bounded correction after a rejected local receipt read and byte/availability hints
have [79 focused passing cases](docs/audit/phase2/receipt-read-repair-v11-tests.xml);
Planner operator-budget enforcement is implemented in v11. V10 L3's model-generated
node budgets summed to 920,000 above its 630,000 scenario ceiling, while actual
Gateway admission always retained that hard ceiling.
V11 full/live outcomes are recorded below; final v12 verification and whole-task
complex continuity acceptance remain pending.

V11 bounds repair of a rejected local `worker_read_action_receipt` before any
external effect: the rejected call stays charged, Core records no-effect proof,
and a fresh call must satisfy original predecessor/scope/receipt bounds.
Upstream references expose result byte count/readback availability and guide
approved artifact file reads instead of oversized receipts. No new tools or
authority are granted. The [79-case focused group](docs/audit/phase2/receipt-read-repair-v11-tests.xml)
passed (JUnit 19.062 seconds); [legacy compatibility](docs/audit/phase2/receipt-repair-legacy-v11.xml)
passed 35 (JUnit 9.704 seconds). Generated Planner proposals now receive and
enforce the trusted operator total-token ceiling before persistence; an over-cap
reply uses the original one repair and repeated violations stop without a plan.
Default/manual/historical contracts remain unchanged. [Budget tests](docs/audit/phase2/planner-operator-budget-v11.xml)
passed 16 (JUnit 1.203 seconds); [compatibility tests](docs/audit/phase2/planner-operator-compat-v11.xml)
passed 51 (JUnit 17.397 seconds). These focused groups overlap the full suite.

The [v11 full suite](docs/audit/phase2/frozen-tests-v11.xml) / [log](docs/audit/phase2/frozen-tests-v11.log)
passed 941 tests, one warning, in 199.39 seconds. Its [160-file manifest](docs/audit/phase2/source-manifest-v11.json)
is `eec443f0aad4687e336a8b9ca3629218ebce4417b8096dabb8eaa44845dc7822`;
Ruff, formatting for 157 Python files, pip and diff passed. This predates v12.
[Benchmark v11](docs/audit/phase2/benchmark-final-v11.json), `benchmark_f23819557132`,
completed in 4.3837569 seconds with zero Provider calls on the unchanged fixed dataset.
[Preservation v11](docs/audit/phase2/preservation-v11.json) still records 355 unchanged Phase 1
evidence files and all 364 original test identities, with the previously disclosed
349 unchanged ASTs/15 modified functions; historical evidence is not rewritten.

The v11 live snapshot is
`dc73aec124a2fc9637e568940a4adfa29b407092d6bcad31ffb11c37839ac520`.
L1 `20261115T062445Z-L1-e6498c` passed strict acceptance in 111.975 seconds:
19 model/five tool calls, charged 102,134/12,157 input/output tokens, unknown zero.
Two real independent same-model NEEDS_REVISION verdicts each led to Worker revision,
then PASS and completed Quest. Disjoint retry accounting records two review
revisions, zero self-repairs/HTTP/background-notice retries. No archive or Core
restart occurred in this L1 run; the earlier L1 pass remains historical evidence.

L2 `20261115T062445Z-L2-b7e197` failed strict acceptance in 177.094 seconds:
37 model/16 tool calls, 199,677/25,402 input/output, final unknown zero.
Research and analysis each received real NEEDS_REVISION, revised and passed;
`work/l2-research.json` and `work/l2-report.json` exist and match latest receipts.
The settled mid-execution Core restart retained the Router ID, completed node,
receipts, settled calls and cumulative counters. Its stopped-Core Router archive
contains one original source with verified readback; resume returned 200.
The analysis successor had one step before pause and continued after restart,
then the final read-only review Worker started. After two steps and six tool reads
it blocked at `context_budget`: its first whole-tail archive could not be built
because that request had no predeclared archive-readback tool. The final report's
existence and intermediate PASS verdicts do not establish final task acceptance.
Disjoint retries are two review revisions plus one background notice; zero
self-repair/HTTP retries. Its early unsettled stop label remains, while final
read-only settlement observed unknown zero.

L3 `20261115T062445Z-L3-ba6bb7` failed in 95.121 seconds: 17 model/six tool
calls, 73,147/13,283 input/output, final unknown zero. Core produced deterministic
`review_capacity_unrepairable` / BLOCKED before independent model review.
Raw packet/native input were 66,309/66,394; Worker use 39,752/110,000 left 70,248.
The old capacity precheck incorrectly summed serial reservations to 76,891;
the maximum per-request lower bound was 49,827. This was an overly conservative
precheck, not an actual Gateway cumulative-budget exhaustion. The raw packet was
still oversized and would require actual governed revision and a new complete
independent review. No such success or Core restart occurred. One background
notice retry is recorded, zero self-repair/review-revision/HTTP retries.

[Snapshot-v4](docs/audit/phase2/live-runs/snapshot-v4/index.json) publishes all 45 finished driver
records while preserving v3/v2/v1 and original run records. V12 is correcting the
serial-reservation precheck; [145 focused cases](docs/audit/phase2/reviewer-serial-capacity-v12-tests.xml)
pass (JUnit 74.570 seconds) with actual Gateway admission over offline MockTransport,
not a live Provider result. First whole-tail archiving must already include the
readback tool in that request and its exact reservation; [87 legacy cases](docs/audit/phase2/first-archive-legacy-v12.xml)
pass (JUnit 65.280 seconds). V12 new-path/full/live results are recorded below.
Neither change expands original authority, budgets or context limits.

V12 corrects the serial-capacity precheck to compare the maximum individual
reservation floor with the remaining original allowance; each subsequent complete
request still requires actual Gateway admission and charges settled usage.
This corrects the conservative v11 rejection without increasing any budget.
First whole-tail Worker archiving now declares source readback in that same
request when steps >0 and the effective per-request allowance is at least 8192,
including the tool's full bytes in original admission. It never enables source
omission without recoverability. The [12-case corrected first-archive group](docs/audit/phase2/first-archive-v12-fixed-tests.xml)
passed (JUnit 1.848 seconds); the [initial 12-case attempt](docs/audit/phase2/first-archive-v12-tests.xml)
retains one failure. The earlier 87 legacy/145 serial-capacity groups are overlapping
local/Mock evidence. No first whole-tail fallback or readback was triggered in
these three new live runs, so that new mechanism remains locally verified only.

The [v12 frozen full suite](docs/audit/phase2/frozen-tests-v12.xml) / [log](docs/audit/phase2/frozen-tests-v12.log)
passed 955 tests, one warning, in 251.43 seconds. The [162-file manifest](docs/audit/phase2/source-manifest-v12.json)
is `ccc5efe69b4493dd77093b4a62f6f6851f09c63ca1f2806298f2e59e80d7f8d7`;
Ruff, formatting for 159 Python files, pip and diff checks passed.
[Benchmark v12](docs/audit/phase2/benchmark-final-v12.json), `benchmark_37ee6599ac54`,
completed in 4.6835418 seconds with zero Provider calls on the same fixed synthetic
dataset. [Preservation v12](docs/audit/phase2/preservation-v12.json) retains 355 byte-identical
Phase 1 evidence files and all 364 original test identities, with 349 unchanged
ASTs and the previously disclosed 15 modifications.

All three v12 live runs share runtime snapshot
`de228407cdb822110db71de4c2ffda7a0a2c351ea02284b4a7e60fcd17d369d4`;
final unknown attempts are zero, cost remains unknown.
L1 `20261115T063556Z-L1-1e03cf` passed strict acceptance in 102.934 seconds:
16 model/four tool calls, charged input/output 66,279/8,829 tokens. Real independent
NEEDS_REVISION led to revision then PASS/completed. Disjoint retry total one is
one review revision; self-repair/HTTP/background-notice retries are zero.
No archive or Core restart occurred in L1.

L2 `20261115T063556Z-L2-a3ad74` failed in 109.832 seconds: 23 model/nine tool
calls, 104,306/10,744 input/output. Source-analysis received real independent
delivery-review PASS and succeeded. A settled mid-execution restart preserved
Router identity, workspace, completed node, receipts, settled calls and budget
counters, with one-source Router archive, verified readback and resume 200.
Draft-report already had one step, 18,234 tokens and zero tools before pause;
those counters survived restart, then it continued to five steps/five tools and
produced receipt-bound `work/l2-draft-report.json`. Its Worker use was
52,939/90,000, leaving 37,061. Read-only reconstruction produces the complete
54,421-byte raw review packet: receipts 23,997, artifacts 12,699, delivery 4,038,
constraints 7,998 bytes, plus original contract/source/identity fields.
The original review allowance was insufficient; `review.blocked` retained
`review_contract_or_budget_failed`, with no new model-review call for that draft.
Disjoint retries are zero. The report and restart segment do not establish final
content acceptance or complex-task completion.

L3 `20261115T063556Z-L3-14d6f5` failed in 115.371 seconds: 21 model/eight tool
calls, 105,632/15,238 input/output. Real independent NEEDS_REVISION led to
`file_revise`; the revised artifact/delivery grew. The complete packet was
68,149 bytes and native input reservation 68,412. Worker use 79,762/120,000 left
40,238; the fixed native input floor 48,956 + revision receipt allowance 1,125 +
minimum verdict 1,024 = 51,105 already exceeds that remainder. The corrected
serial guard therefore retained `review_capacity_unrepairable` / Core BLOCKED.
Tools were two directory lists, four source/manifest file reads, one file write
and one file revision; it did not reread its own artifact. Ordinary Worker
archives existed, but first whole-tail/source-readback was not exercised and
no L3 Core restart occurred. Disjoint retry total one is the real review revision;
self-repair/HTTP/background-notice retries are zero.

The [read-only Reviewer-claim diagnostic](docs/audit/phase2/reviewer-claim-diagnostic-v12.json)
checks an incorrect revision goal from this L3 review: `ast.parse(mode="exec")`
accepts the top-level Return node, and the actual bounded interpreter evaluates
`return 0` to result 0 in two steps. Python bytecode compilation rules are not
that interpreter's contract. This diagnostic made zero Provider calls and changes
no run outcome. Fresh-context same-model review can issue incorrect revision goals;
fixed native numeric semantic gates independently verify applicable artifacts.
A review verdict alone does not guarantee domain correctness.

[Snapshot-v5](docs/audit/phase2/live-runs/snapshot-v5/index.json) publishes all 48 finished driver
records; v4/v3/v2/v1 and original failures remain. The backend source is frozen at
v12 for this round; two additional bounded L2/L3 samples on the same source are now
recorded separately below and do not replace failures. Phase 2 remains incomplete:
complex L2/L3 acceptance, general replanning, learned semantic recall, independent
model diversity and other listed limits remain open.
`docs/audit/phase2/verification.json` consolidates this round's measured
partial status; it is an evidence record, not a completion certificate.

The two final bounded repeats used the identical v12 runtime snapshot, original
scenario limits and no source changes. L2 `20261115T064204Z-L2-3f89c4` failed
strict acceptance in 115.488 seconds: 29 model/nine tool calls, charged
154,973/15,583 input/output tokens, unknown zero. Research had two real independent
NEEDS_REVISION verdicts and revisions; Worker use 122,642/150,000 left 27,358.
Its third complete packet was raw/native 67,630/67,645; fixed native floor
58,051 + receipt allowance 1,124 + verdict 1,024 = 60,199 exceeded the remainder.
Core capacity BLOCKED; no restart occurred. Disjoint retry total two is two
review revisions, with zero self-repair/HTTP/background-notice retries.

L3 `20261115T064204Z-L3-1288e8` failed strict acceptance in 111.105 seconds:
21 model/nine tool calls, 104,171/14,090 input/output, unknown zero and zero retries.
Research received real independent PASS/succeeded at five steps/five tools,
49,821/120,000 tokens. This L3 then demonstrated its first actual settled
mid-execution Core restart: one-source Router archive/readback, same Router ID
and workspace, retained completed node/receipts/settled calls/counters, resume 200.
Math had already started at one step, 18,462 tokens and zero tools before pause;
those counters survived restart, and it continued to five steps/four tools,
49,962/90,000 tokens, producing receipt-bound `work/l3-model.json`.
Its remaining allowance was 40,038. Read-only reconstruction verifies a valid
complete raw packet of 40,350 bytes, including receipts 10,983, artifacts 10,821,
delivery 5,148 and constraints 7,552. Actual native payload construction reserves
39,871 input + minimum verdict 1,024 = 40,895, above the remainder.
`_review_reservation` rejects with `Independent review budget exhausted`;
the historical event stays `review_contract_or_budget_failed`. No math-model
review call ran. This is budget admission failure, not a JSON parse failure.
The restart/readback and successor continuation segment is real; the full complex
Quest still did not complete.

[Snapshot-v6](docs/audit/phase2/live-runs/snapshot-v6/index.json) publishes 50 driver records and
retains snapshot-v5/v4/v3/v2/v1 plus every original run. Across this historical set,
nine unresolved attempts are a known lower bound and six earlier records lack
usage statistics; aggregate unknown-attempt count and true total usage/cost remain
unknown. Reported input/output sums cover only available measurements. Latest
v10/v11/v12 per-run unknown-zero observations do not settle older unknown attempts.
The six missing-statistic records are the five early L1 runs at
21:52:36, 21:56:14, 22:00:18, 22:06:34, 22:08:38 UTC on 2026-11-14 and
`20261114T233731Z-L1-c53b2d`.

The next proposed work is a Core preview of the complete evidence packet and
native review reservation before large artifact production/revision, plus a new
explicit artifact/revision budget contract that reserves room for complete review
and bounded rework within the original total. These are recommendations, not
implemented mechanisms or retrospective grants. Preview must retain full immutable
sources, actual receipts, raw/context limits, original approvals and unknown-outcome
guards; lowering evidence requirements or merely increasing budgets is not acceptance.
The v12 backend source and these documents are frozen for this round.
Phase 2's core complex-task goal remains unmet. The machine-readable
`docs/audit/phase2/verification.json` records this round's measured partial state
and open gates; it is not a Phase 2 completion certificate.






The fixed Worker tools support syntax checking with `python_compile`, bounded
numeric execution with `numeric_ast` and the `python_version` diagnostic through
`shell_inspect`. Arbitrary shell commands, unrestricted Python execution and a
general code patch tool are unavailable. `file_write` only creates new files.
The separately approved R1 `file_revise` tool atomically replaces only the current
Worker's own latest successful artifact. It requires `path`, `prior_receipt_id`,
`expected_sha256` and complete new `content`, an intact same-Worker receipt chain,
the current unchanged hash and a valid workspace grant. Earlier bytes remain in
immutable intent/receipt history. Existing create-only contracts gain no permission;
user files, predecessor artifacts, stale versions and uncertain outcomes cannot be
revised through this tool. All revision remains subject to the original criteria.
Team replacement keeps the current DAG; automatic replanning of a running DAG
remains incomplete.

Independent review uses the approved model in a fresh context and evaluates the
current node's objective, criteria and approved role. The full original request
remains available, and a node assigned a final deliverable retains all original
requirements. Output fits 8192, role/model caps, remaining model context and the
original cumulative token allowance. Adaptive capacity keeps a floor of
`min(1024, original output cap)`; insufficient capacity or original cost authority
fails closed. Native Gateway input reservation accounts for the actual serialized
payload, including schema and reasoning settings. Receipt-bound invalid
JSON produces explicitly labelled Core-parser revision feedback; it is not an LLM
review or PASS. A settled truncated review may request a compact verdict within
the remaining rounds, steps, tokens and deadline after charging actual usage.
Core review defaults to two rounds; `FORGE_REVIEW_MAX_ROUNDS` accepts only integers
one through four. The live harness selects three within its three-round scenario
cap. Prior runs retain their original round limit and outcome. No revision or
retry resets cumulative budgets or replays an unknown remote outcome.

Large review requests can use a lossless input envelope: full source text stays in
untrusted source blocks, repeated constraint metadata is shared and exact statements
reference their original character spans. The original packet and PASS hash binding
remain unchanged. No source text or review requirement is dropped, and requests
that still exceed the packet/model/budget limits remain blocked. The
[118-test local/Mock group](docs/audit/phase2/review-context-20261115T091955.xml)
includes exact reconstruction and review-round configuration checks.

A genuine fixed numeric-suite failure can also return labelled Core feedback
(`deterministic_semantic_validation`) to the same Worker. Requeue requires explicit
`file_revise` authority, a current grant, remaining repair/step/token/tool/cost/time
budgets and no unresolved actions or model attempts. Revised delivery must pass
the real suite again. This feedback is not LLM review or PASS. Persisted feedback
can recover idempotently after a crash before its callback, including semantic-only
nodes, after rechecking the latest result, true suite/source/receipt bindings,
current authority and remaining original budgets. Recovery issues no model review
request and cannot replay unknown actions. Callback errors and fake success remain
blocked. The [local/Mock recovery group](docs/audit/phase2/semantic-recovery-20261115T090801.xml)
passed 74 tests; it does not establish live task correctness.

Worker lease renewal occurs between settled steps after checking the current
token/generation, approvals and running Quest. It is capped by the task deadline
and cannot revive expired ownership, a paused/cancelled assignment or a changed
plan. Individual calls retain their current lease/deadline bound.

An approved dependent Worker may read a bounded receipt from a successful direct
predecessor only when its plan node, approved role and effective Worker tool
scope all permit `worker_read_action_receipt`. Its initial context contains at
most 32 upstream references and an omission indicator; a reference is not proof
until the read checks the current plan and receipt provenance. Returned content
remains untrusted and retains the source execution mode (`live` or `mock`); a
mock receipt is not promoted to live evidence. Completion rechecks the recorded
tool result. Temporary capacity pressure queues work without treating it as a
semantic failure retry. Lease generations fence ownership; the separate bounded
failure-retry count is recorded by retry events, and authorization retries do
not consume that failure limit.

Model calls through the Gateway share a durable admission ledger, including Router, planner, team composer, benchmark and Worker calls. Configure these trusted process environment variables before enabling a provider:

| Variable | Meaning |
| --- | --- |
| `FORGE_MODEL_TOKEN_LIMIT` | Total token reservation ceiling; defaults to `0`, which rejects model calls until explicitly configured. |
| `FORGE_MODEL_CONCURRENCY_LIMIT` | Concurrent admitted attempts; defaults to `1`. |
| `FORGE_MODEL_REMOTE_MAX_SECONDS` | Optional trusted upper bound on remote execution duration. Without one, an attempt with unknown outcome retains its concurrency slot; a client timeout alone cannot establish that remote execution stopped. |
| `FORGE_MODEL_SCOPE_LIMITS_JSON` | Optional array of `{"scope":"quest","scope_id":"<quest-id>","token_limit":10000,"concurrency_limit":1}` entries; scope may be `workspace`, `quest`, or `worker`. Approved task contracts also impose their own limits. |
| `FORGE_PRICE_BOUNDS_JSON` | Optional operator-provided provider/model price ceilings. Unknown prices remain unknown; token-only admission applies when no cost ceiling is configured. |
| `FORGE_MODEL_COST_LIMIT_MICROUNITS`, `FORGE_MODEL_COST_CURRENCY` | Optional global currency ceiling and three-letter currency; both require trusted price bounds and reconciled historical cost. |
| `FORGE_MODEL_HISTORICAL_COST_MICROUNITS` | Operator-reconciled pre-cutover global cost in the configured currency. Unknown historical cost is never assumed to be zero. |
| `FORGE_MODEL_SCOPE_COST_RECONCILIATIONS_JSON` | Optional array of `{"scope":"quest","scope_id":"<quest-id>","currency":"USD","cost_microunits":0}` entries for operator-reconciled historical scope cost. |

An unknown or interrupted remote outcome remains reserved until a trusted remote execution bound and isolation period permit recovery. Without a trusted bound the slot remains reserved; there is currently no operator interface for reconciling an individual unknown attempt. Do not infer that a timeout cancelled the remote request. Current real-provider demonstrations and reproducible research scripts are described in the [M5 report](docs/PHASE1_MILESTONE5_REPORT.md).

### Backend checks

```powershell
# From backend, after installing .[dev]
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts --config pyproject.toml
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m pip check
```

`python-dotenv` is an explicit runtime dependency for Workspace configuration. No model provider or external Agent execution backend is required for these checks.

## Desktop UI setup

Node 22.18+ is required for the desktop test runner's native TypeScript stripping;
the current Windows checks use Node 24.19.0.

```bash
cd desktop
npm install
npm test
npm run build
npm run dev
```

The React/Vite UI can run in browser mode for development. The Tauri shell and Core bootstrap are being integrated; a final native integration and packaging pass is required before calling it a desktop release.

The desktop file workspace opens on demand beside the single conversation. Native
read-only commands list the current workspace, preview UTF-8 source/Markdown,
PDFs and images, and inspect scoped Git changes without modifying the index.
Browser development uses explicitly selected local files or a directory. Git must
be available on PATH for native changes/diffs; no new application dependency was
added. Text is limited to 2 MiB and PDF/images to 24 MiB. See
[desktop/README.md](desktop/README.md) for command contracts and verification
limits, and [desktop/UI-TODO.md](desktop/UI-TODO.md) for the remaining acceptance.

## API quick smoke test

```bash
curl http://127.0.0.1:8765/health
# Authenticated requests require the per-session bearer described above.
```

## Start Codex

### Entire session checkpoints

This repository uses Entire CLI 0.13.4 with the ref-based checkpoint backend
and telemetry disabled. On Windows, install the stable CLI using the [official
PowerShell installation instructions](https://docs.entire.com/installation),
then restart the terminal if the installer updates PATH. The non-Scoop default
installation location is `%USERPROFILE%\.local\bin`; a custom installation can
use any suitable directory on PATH. Verify with `entire version`.

Enable Entire in the repository with `entire enable` and install the Codex agent
hooks as directed by the [official agent setup](https://docs.entire.com/agents/setup).
Codex must support hooks and any applicable hook-trust approval must be granted
before hooks run. Enabling without history import begins tracking new sessions;
it does not import previous conversations. With this repository's ref backend
and automatic push policy, checkpoint refs are pushed with Git pushes. The
shared settings retain Betterleaks' default scanner and add a generic 32-hex
pattern plus email and phone redaction. Redaction has limits: never put provider
credentials or other secrets into chat; use the configured credential store or
protected environment instead.

The locally verified Codex 0.139.0 supports six of the seven hooks generated by
Entire 0.13.4, and those six have been trusted. Its `SessionEnd` event is not
supported by this Codex version, so Entire may still report one hook needing
approval. The official seven-hook template is retained; after upgrading to a
Codex version that supports this event, review hook trust again via `/hooks`.
This limitation does not prevent the six supported hooks or Git hooks from
working.

Open this folder as the project root and first read:

1. `docs/PRODUCT_ARCHITECTURE.md`
2. `docs/DOMAIN_AND_LIFECYCLE.md`
3. `docs/ROUTER_AND_WORKER_PROTOCOL.md`
4. `docs/MODEL_REGISTRY_AND_TEAM_COMPOSITION.md`
5. `docs/CONTEXT_MEMORY_GOVERNANCE.md`
6. `docs/ROADMAP.md`
7. `codex/MASTER_PROMPT.md`

Follow the user's continuous development authorization and the acceptance gates in `docs/STATUS.md`. The referenced `codex/MASTER_PROMPT.md` remains missing from the supplied release.

## Native desktop build (after Rust toolchain installation)

```bash
cd desktop
npm install
npm run tauri dev
```

The Tauri Rust shell and v2 capability manifest are included. Preliminary native startup has been observed on Windows; final target-OS integration and packaging checks remain pending.
