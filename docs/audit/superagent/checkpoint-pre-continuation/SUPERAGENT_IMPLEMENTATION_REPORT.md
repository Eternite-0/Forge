# FORGE Super-agent implementation report

## Capability Sprint continuation — 2026-11-16

Base: `57a162d31f9b559353fecbde61f95dd72f878abd`, branch
`codex/phase1-continuous`. The original architecture-correction acceptance below
is historical. Current capability acceptance is recorded separately in the
[Sprint report](SUPERAGENT_CAPABILITY_SPRINT_REPORT.md),
[experiments](SUPERAGENT_EXPERIMENTS.md) and
`audit/superagent/final-verification.json`; implementation alone is not task
acceptance. The frontend belongs to a separate conversation and is outside
this change. No GitHub push is performed.

This continuation keeps the persistent Router, independent Worker loop,
Dispatcher, durable event inbox/subscriptions, existing SQLite transactions,
CAS, Outbox, action evidence and permissions. It adds no parallel state system.

| Capability | Current implementation and boundary |
| --- | --- |
| Router continuity | Project-focused task/memory/Recall/source selection, authoritative continuation links, explicit cross-project retrieval, coordination facts, and transactional pause/resume/cancel. Project identity grants no filesystem permission. |
| Router reasoning recovery | Rejected decisions have bounded safe diagnostics; one existing reflection sees the rejected response transiently. Absent archive sources return an unavailable observation within existing tool rounds; indexed source corruption remains fail-closed. |
| Worker autonomy | Existing independent reasoning/tool/checkpoint loop, exact-manifest CPython unittest, consumed-approval test repair, provably unexecuted path/source repair, and ordered individual R2 approvals for multi-call model batches. Unknown operations remain blocked. |
| Expert collaboration | Versioned v2 mandates reuse the existing graph scheduler for single/parallel/sequential/hierarchical assignments. Source-bound help can request bounded expansion or replacement; at most four cumulative Workers and depth one. Legacy mandates keep their prior scope. |
| Model intelligence | Allowlisted Provider declaration metadata, benchmark source/sample/time/confidence, measured online performance and transport-health projection in existing records. Identity discovery is not measurement; vision and unpriced cost remain unknown. |
| Context | Registry-declared model window by default, optional explicit smaller operator cap, and separate cumulative Worker execution allocations/request estimates. Spill/archive originals retain hashes and scoped readback. |
| Recovery | Progress fingerprints distinguish a live process from advancing work. Durable deduplicated help, existing leases/recovery fences and bounded backoff remain; no automatic replay of unknown calls or side effects. |

The proxy currently declares a 1000000-token input window and 128000-token output
maximum for the configured DeepSeek identity. This differs from the operator's
spoken 100M value; Registry tests cover 100M, while real acceptance uses the
actual API declaration. Acceptance output allowances remain 8192 for Workers
and 4096 for Router. Original Provider configuration is not edited. The fixed
task spending ceilings remain distinct from model context capacity.

`python_unittest` uses the current OS account, an exact SHA-256 file manifest,
R2 approval, private cwd, minimal environment and ten-second process-tree limit.
It supports imports/classes/stdlib tests and provides source-bound receipts.
These controls are not an OS sandbox. Model-written tests, a real model Reviewer
and independent host holdouts are different evidence classes. A completed Core
task can still fail independent artifact acceptance.

Final verification must bind successful L3 tests to the delivered candidate
hash, check strict source citations for L1/L2, recheck budgets after draining,
and preserve all failed/unknown experiments. General Terminal execution and
unrestricted package installation are not newly enabled; existing Browser,
Git, MCP and GPU probe tools retain their governed scopes. Long-term arbitrary
task success and a superior team/routing algorithm are not established by
the small experiment set.

Current-sprint outcome is **partial, full acceptance failed**. L1 has an
independently passing real task. L3 generated correct held-out code but failed
the original receipt-report gate; the receipt visibility fix subsequently
passed two targeted real Provider projections, not a task rerun. L2 did not
complete its expert team. The combined run established pass-through, ownership
overlap, same-state restart and bounded Recall, but not correct project links
or a final reviewed completion notice. Subsequent discovery correction has
programmatic coverage only. All original failures remain retained. See the
current Sprint report and final machine-readable evidence for exact boundaries.

Final production-source regression: **2587 passed, 13 historical failures,
3 skipped** in 556.83 seconds, with no new failed cases. All 59 changed Python
files pass Ruff. The full-suite evidence and later eleven-case test-only lint
followup are recorded separately; targeted suite counts overlap. Actual
task/diagnostic calls total 160 (159 settled, one retained unknown outcome),
plus twelve separate model microprobes. These counts and tests do not establish
complete L2/L3 or long-duration autonomous acceptance.

## Historical architecture correction

Date: 2026-11-16. Base: `4b220d8bdbd834bbadda53d0baa7756e9c1bb90f`.
The frontend is unchanged. Research preceded implementation; see the
[architecture difference audit and plan](SUPERAGENT_REFACTOR_PLAN.md),
[DeepSeek source research](SUPERAGENT_DEEPSEEK_RESEARCH.md) and
[Harness audit](SUPERAGENT_HARNESS_AUDIT.md).

Frozen-source verification: **2312 passed, 14 reproduced baseline failures,
2 skipped** in the complete backend regression. Both real Provider scenarios
passed, with **39/39 calls settled** and final Router notifications drained.
The machine-readable [acceptance index](audit/superagent/acceptance-index.json)
binds the reports and regression to their hashes and retains historical failures.
This is acceptance of this backend architecture correction and its bounded
integration scenarios, within the operating limits below.

## Resulting architecture

The normal Core entry point uses the persistent Router as the sole conversational
Agent. A request needing execution receives a source-bound host mandate. Internal
execution contracts reuse the existing graph, team and Registry implementation;
routine task admission does not require a human plan/team approval. The mandate
has its own `router_mandate:<id>` actor and cannot impersonate `local_user`.

| Requirement | Implementation |
| --- | --- |
| Persistent Router | Existing stable `main` conversation/Router identity, bounded context and CAS; autonomous delegation in the real Core lifecycle; user/event Router inference serialized |
| Isolated Workers | Private messages, run identity, leases, per-run working/temp/output paths; fixed process tools use private cwd and temporary environment; Workers report through Router |
| Event-driven triggers | Durable Source/Thread/Trigger ingress, actor/source Fast Path, semantic batched Slow Path; user messages, existing results, authenticated external ingress, persistent timers and file watches |
| Subscription/pass-through | Many-to-many Thread subscriptions, direct Inbox delivery, ingress dedupe, explicit handback and bounded backpressure reconciliation |
| Inline/Spill | Complete original requests and Trigger payloads retained externally; bounded inline content/previews, exact hashes, paginated source reads |
| Chain of Recall | Scoped typed references between conversations, sources, projects, tasks, Workers, runs, plans, events, evidence, memories, receipts and archives |
| Persistent state | Schema 14 migration within the existing strict migration/rollback mechanism; new state shares SQLite transactions and Outbox/CAS boundaries |
| Watchdog | Durable health, bounded loop recovery, expired-lease reconciliation, environment checks and replay-safe Worker recovery; default CLI parent supervisor with process-tree cleanup |
| Generic workspaces | Manifest/Git/plain-directory detection, persistent project/task association and automatic identification from explicit paths in the immutable request |
| Adaptive composition | Router chooses bounded independent assignments/roles; existing Composer chooses eligible models using capability provenance, context/output limits, speed and cost |
| Local tools | Governed file tools, fixed Python/Git probes and read-only GPU diagnostics; existing separately authorized HTTPS browser/MCP tools retained with their original limits |

No duplicate model manager, new frontend, or external agent execution backend was
introduced. Complex execution planning belongs inside Workers. Autonomous
assignments are coarse, independent contracts with no DAG edges.

Implementation entry points:

- [Core lifecycle and authenticated integration API](../backend/src/forge_api/main.py)
  and [persistent Router](../backend/src/forge_core/router.py).
- [Autonomous delegation](../backend/src/forge_core/superagent_delegation.py),
  [semantic event path](../backend/src/forge_core/superagent_loop.py),
  [subscriptions and fast delivery](../backend/src/forge_core/superagent_events.py),
  and [timer/file sources](../backend/src/forge_core/superagent_sources.py).
- [Recall traversal](../backend/src/forge_core/recall_chain.py),
  [Core source proof](../backend/src/forge_core/worker_core_provenance.py),
  [Worker environment](../backend/src/forge_core/worker_environment.py), and
  [project discovery](../backend/src/forge_core/workspace_discovery.py).
- [Watchdog](../backend/src/forge_core/watchdog.py) and
  [CLI process supervision](../backend/src/forge_api/supervisor.py).
- [Conversational acceptance driver](../backend/scripts/verify_superagent.py)
  and [event/adaptive-team acceptance driver](../backend/scripts/verify_superagent_cold_start.py).

## Authority and delivery boundaries

An internal mandate permits bounded task scheduling and task-local artifact
writes. It grants no external-action approval. Explicit revocations stay revoked;
crash recovery does not silently recreate them. Each execution still passes
existing plan/team authority, workspace scope, action intent/receipt, model-call
ledger and completion evidence checks.

Core context reads have their own trusted, source-bound checkpoint proofs. They
are not fabricated action receipts. Review verifies run/Quest/plan/call identity,
arguments, result hashes, transcripts, actual Trigger delivery and source scope.
Handback commits once at the checkpoint; subsequent review is read-only. A new
Inbox event arriving during final inference prevents result publication until
the Worker has consumed the continuation. Accounting for the draft remains
charged, and step/tool budgets are not reset.

Watchdog recovery never replays an unknown Provider call or tool effect. Exited
loops use bounded backoff and regain restart allowance only after observed
stable operation. Heartbeat storage failures cannot terminate the recovery loop.
On Windows, the CLI supervisor drains the old Job Object process tree before
starting a replacement. Native desktop bootstrap remains compatible.

The Watchdog reports stale heartbeats but does not cancel or duplicate a pending
loop solely because it is stale. The parent supervises process exit, not a live
Core whose event loop is completely hung. This is infrastructure recovery, not
an independent model Session that judges semantic progress or goal drift.

## Verification evidence

The initial infrastructure selection passed **163 tests** before production
changes. The first full regression recorded **2179 passed, 17 failed, 2 skipped**
in [initial regression XML](audit/superagent/backend-regression-initial.xml).
Fourteen failures were reproduced individually with the original source and
tests archived from the base commit, outside the working checkout:
[baseline comparison](audit/superagent/baseline-affected.xml), **209 passed,
14 failed**. They concern previous planner/capacity/output expectations and the
old compact-prompt size assertion. They are retained as baseline failures, not
silenced or presented as passing checks.

The other three failures involved the new automatic lifecycle's old test
expectations and a pseudo-v7 migration fixture containing newer tables. Manual
lifecycle compatibility tests now explicitly use that path; migration fixtures
remove later tables before testing genuine strict historical migration.
Production migrations were not weakened with `IF NOT EXISTS`.

Focused context/state regression:
[121 passed, 2 skipped](audit/superagent/context-state-regression.xml).
Windows skips concern symlink creation privileges. Further final test and real
Provider results are recorded below when completed; intermediate selections
overlap and are not added together as a suite total.

A later complete backend run recorded **2251 passed, 14 failed, 2 skipped** in
[regression XML](audit/superagent/backend-regression-final.xml). Its failures are
exactly the fourteen reproduced baseline failures. The final Router response and
late-attachment corrections are additionally checked in focused tests; they were
motivated by the real diagnostics described below.

The subsequent full snapshot recorded **2308 passed, 16 failed, 2 skipped** in
[acceptance regression XML](audit/superagent/backend-regression-acceptance.xml).
It includes the fourteen reproduced baseline failures and two further lifecycle
failures. The planner test observed its counter before the successful heartbeat
published healthy state; its wait now observes the health boundary and preserves
the original assertions. A desktop hard-stop test reported SQLite `disk I/O
error`; it was not reproduced or explained, and neither production behavior nor
that test was weakened. Both lifecycle modules then passed **three consecutive
7-test runs**. The original failing XML remains available; a later successful
run cannot establish the cause of the earlier I/O error.

The final complete regression on frozen code recorded **2312 passed, 14 failed,
2 skipped**, with no test errors, in
[frozen regression XML](audit/superagent/backend-regression-frozen.xml).
Automated XML comparison confirms its failed-case set exactly matches the
fourteen failures reproduced on the original base source and tests. The two
additional lifecycle cases passed in this final full run. The suite still exits
nonzero because historical failures remain; this is not an all-green test claim.
All changed Python files passed Ruff, `git diff --check` passed, frontend and
Tauri changes are absent, and no dependencies were added.

Reproduce the final full regression from the repository root with:

```powershell
& backend\.venv\Scripts\python.exe -m pytest backend\tests -q --junitxml=docs\audit\superagent\backend-regression-frozen.xml
```

The first real Provider diagnostic, `20261115T183233Z-cf6da7`, failed. Two hidden
Workers executed real file tools, two follow-ups passed through without Router
Trigger inference, and all **25 calls / 110935 charged tokens** settled. A late
continuation raced final publication; another Worker successfully read its
Trigger but review did not yet recognize the new Core proof. The
[original redacted diagnosis](audit/superagent/20261115T183233Z-cf6da7-diagnostic.json)
is retained. These failures drove the final-boundary and provenance fixes.

The next conversational diagnostic, `20261115T190232Z-065808`, exhausted its
unchanged 32-call ceiling. One Worker passed independent review; the second user
turn retried its Router snapshot twice while the first task advanced. The driver
waited for both HTTP turns before injecting continuations and missed the first
Worker's execution window. It stopped with two reserved calls; that workspace is
retained and is not replayed. This led to scoped independent-task snapshot checks,
deterministic filtering of routine internal progress, and concurrent probe
submission/observation.

The cold-start diagnostic, `20261115T190431Z-cold-1f2fe2`, completed two independent
Workers and their reviews, verified both files, admitted a timer task, ignored
file noise and traversed Recall. Its report's intermediate checks all passed,
but another notice began between the settled check and shutdown. The final
ledger had one reserved call, so the published `passed` status is superseded by
the accompanying diagnostic and is **not final acceptance**. The probe now gates
final status on the post-stop ledger and waits for settled notification work.
Original reports are preserved; no failed or uncertain call is replayed.

Final responsiveness corrections keep new independent task admission stable
while unrelated Workers change runtime status. Conversation state, messages,
models, constraints, archive head, original task identities, selected recall and
consulted source checks remain fenced; existing-task intents retain the strict
snapshot check. Autonomous routine progress and nonterminal internal status facts
are acknowledged without model calls. Results, blockers, failures, help and
action approvals retain semantic handling.

An event arriving after Worker finalization cannot be attached to a task with
no accepting Worker. Router context exposes recipient availability, and the
transaction rechecks it. Invalid attachment rolls back the entire batch and
leaves the Trigger pending for retry or a new continuation dispatch. This covers
the separate gap between final publication and review completion.

Completed-task status queries now retain a natural Router recap when every
selected task is Core-completed and the current plan has succeeded Worker/node
deliveries. The bounded source summaries and their payload hashes participate
in snapshot validation; the answer carries task/event references. Manual,
unfinished, mixed-state or unsourced cases retain exact Core status facts.
The final Router compatibility, mutation and native-wire selection passed
**154 tests**. Autonomous context projection measures the actual nested JSON,
schema and tool framing, and retains the more conservative Core guard. It spills
only optional evidence, events and old messages, with typed Recall references;
user input, constraints, archive/capsule and completed source summaries remain.
The event cursor does not acknowledge spilled events that were not presented.
Legacy capacity handling and later tool-round budget checks remain intact.

Archive Recall now verifies complete previous-archive dependencies through the
existing ContextService verifier, including original source/index/scope/hashes,
cycles and link hashes. Revoking an original user message, internal Worker
message or previous archive blocks dependent recall, including legacy inherited
sources. The combined new archive, Recall and Core proof selection passed
**43 tests**. The final event, backpressure, proof, supervisor, Watchdog, project
and acceptance-driver selection passed **94 tests, 1 skipped**. These overlapping
selections are not a replacement for the complete regression result.

Real acceptance uses authenticated HTTP against isolated Core processes, the
configured `deepseek-v4.1-flash` local proxy, actual file/readback receipts and
durable model usage. Credentials come from process environment only, and are not
written to source or reports. Each driver is bounded to 32 calls, 500000 tokens
and 900 seconds. The proxy-reported upstream model identity is not independently
verified. Scripted protocol tests never count as real Provider evidence.

A further diagnostic, `20261115T192301Z-67cf20`, completed both tasks and reviews,
read their spilled follow-ups, verified artifacts, delivered Router results and
preserved restart state, with **23/23 settled calls, 115765 charged tokens**.
It still failed on the historical recap. The retained
[diagnosis](audit/superagent/20261115T192301Z-67cf20-diagnostic.json)
reproduces a first-round context budget error without another Provider call or
database write. The new native/Core projection estimates **62095 bytes before,
61320 after**, under the unchanged **61440-byte input ceiling**. It spills one
evidence item and one event while retaining both completed summaries and the
original input. A separate interval-collection bug concealed overlapping Worker
ownership in that original report; the diagnostic records the corrected overlap,
and the final driver retains the first unclosed claim until a closing event.
Ownership intervals can include Provider-capacity waits, not continuous compute.

The intermediate cold-start run
[20261115T192629Z-cold-d0dae9](audit/superagent/20261115T192629Z-cold-d0dae9.json)
passed all 16 checks with **16/16 settled calls, 46089 charged tokens**. It uses
the earlier source snapshot and does not supersede the separate recap failure.

## Frozen-source real acceptance

The final two scenarios use fresh workspaces and the same backend Python plus
conversational-driver snapshot SHA-256,
`388123662a3e14adcba343ee2d62c6eb84679a989a0f90780d7037efb27c277e`.
The cold-event driver separately records its own file hash in its report.
Neither reopens an uncertain prior run. Limits remain **32 calls, 500000 tokens,
900 seconds per scenario**, with final shutdown accounting and Router delivery
drain required for acceptance.

| Scenario | Final evidence | Checks | Settled calls | Charged tokens |
| --- | --- | --- | --- | --- |
| Two conversational tasks, direct follow-ups and restart recap | [20261115T193658Z-d72209](audit/superagent/20261115T193658Z-d72209.json) | 17/17 | 22/22 | 126055 |
| Cold events, timer, file noise and adaptive two-Worker composition | [20261115T193938Z-cold-697d50](audit/superagent/20261115T193938Z-cold-697d50.json) | 16/16 | 17/17 | 51306 |

The conversational scenario observed two distinct overlapping Worker runs,
real file write/readback receipts, independent review, two direct Thread
continuations without Router Trigger inference, complete spilled source reads,
one Router delivering both results, and the same conversation/Router identity
after restart. The historical recap recovered both actual source markers without
including those marker values in the recap request or creating another task.

The cold-event scenario observed the Router selecting two independent
assignments/roles, persisted contracts and model selection, overlapping Workers,
both verified artifacts, timer dispatch, file-noise rejection, a source-bound
mandate, unified Router delivery and typed Recall links into execution. Every
call settled and every required Router notification drained in both scenarios.
These are actual configured Provider calls, not mocks; the proxy reports
`deepseek-v4.1-flash`, and its upstream identity remains unverified.

## Operating limits

- Private Worker directories are not an OS sandbox. Process isolation and tool
  authorization do not isolate an attacker with the same OS account.
- Automatic delegation currently uses a fixed local tool allowlist. Terminal
  inspection is a fixed Python-version probe; Python compilation and pure-function
  AST tests do not provide arbitrary shell, pytest, package installation or
  unrestricted program execution. Git support here is status inspection.
- The retained browser tool is allowlisted HTTPS GET, without rendering or
  interactive browsing. MCP is preconfigured HTTPS JSON-RPC `tools/call`, without
  general stdio/SSE discovery or session management. Both retain independent
  high-risk action authorization and are outside the autonomous default allowlist.
  The final live scenarios exercise file tools, not browser or MCP execution.
- Project discovery establishes identity and Recall continuity. It does not
  expand execution outside the configured workspace or grant project writes.
  Multiple explicit roots retain the neutral storage association.
- Autonomous dispatch allows at most four independent assignments; actual
  concurrency also respects the configured Core and Provider limits.
  Workers plan steps inside their own model loop; autonomous nested-team spawning
  is not implemented. Real acceptance uses one configured model for multiple
  roles and operator-supplied capability priors, not a capability benchmark or
  evidence of optimal selection across different models.
- Host contract allocation bounds are enforced. Cumulative token use is recorded,
  with stopping controlled by `FORGE_ENFORCE_TOKEN_BUDGETS` (default `false`).
  The acceptance drivers explicitly enable their fixed cumulative budgets.
- Timers and file watches are registered by authenticated local host APIs and
  polled by Core. Natural-language creation of schedules/watches is not exposed
  as a Router/Worker tool. External ingress trusts the authenticated local adapter;
  no public webhook server, OAuth/signature adapters or built-in messaging
  connectors are added.
- Automatic whole-Core process restart applies to the default CLI launcher.
  Native `--desktop-host` and direct uvicorn execution do not gain that parent
  supervisor. A running but fully hung process is not automatically restarted.
- Complete Trigger ingress is bounded to 4 MiB; watched files to 1 MiB. Readback
  pages and Recall traversal are bounded, with explicit continuation references.
- GPU support is read-only status through fixed `nvidia-smi` arguments. GPU
  compute scheduling and OS/container security sandboxes are not implemented.
- Tool network access retains operator allowlists and independent action
  authorization. Scheduling a task cannot authorize an external side effect.
- Bounded real scenarios establish observed integration behavior. They do not
  establish arbitrary-task success, indefinite unattended operation or a
  packaged desktop release. The fourteen reproduced historical test failures
  remain visible technical debt.
