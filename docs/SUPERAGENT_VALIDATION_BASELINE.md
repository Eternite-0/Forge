# Super-agent validation baseline

Date: 2026-11-16. Audited starting commit: `4b220d8bdbd834bbadda53d0baa7756e9c1bb90f`.
This is a source and local-protocol audit, not acceptance of the proposed architecture.

## Existing infrastructure to retain

| Area | Source evidence | Current behavior and architectural implication |
| --- | --- | --- |
| Model intelligence | `backend/src/forge_core/domain.py::ModelProfile`, `model_intelligence.py::ObservationStore`, `registry.py::ModelRegistry` | Capability measurements have source/confidence/sample metadata. Mathematics, coding, architecture, research, planning, design and related capabilities are represented. Cost, currency, speed, TTFT, latency, reliability and timeouts are represented separately. Discovered identities are provider-qualified; missing measurements remain unknown. Reuse these records instead of adding another model manager. |
| Team selection | `team_composition.py::BoundedTeamComposer`, `RoleSpec`, `TeamWire`, `TeamCompositionService` | Bounded single-model, greedy, coverage and complementarity strategies already consider eligibility, capabilities, confidence, speed, cost and online performance. Dynamic roles require complete contracts. The limitation is the proposal/plan/approval execution entry point, rather than an absence of model-selection machinery. Router orchestration should reuse the composer. |
| Workspace identity | `settings.py::Workspace`, `Settings.load`, `project_context.py::workspace_project_context` | A normalized absolute root determines a stable local identity, independent of GitHub. However, there is one database workspace binding, and the public projection explicitly reports no multiple-workspace/switch/create capabilities. Quest metadata is only a grouping label and grants no scope. General project detection and durable task-to-project association are missing. |
| Persistence | `store.py::Store`, `Repository`, `UnitOfWork`; `migrations.py` | Connection ownership, WAL, foreign keys, atomic units of work, versioned migrations, workspace binding and CAS already exist. Router state, messages, Quests, Workers and events are durable. Preserve these mechanisms and extend the migration chain atomically. |
| Event delivery | `store.py::Repository.append_event`, existing dispatcher schemas and tests | Domain changes and Outbox inserts commit together. Existing consumer ownership, Inbox deduplication and retry machinery can host Super-agent orchestration. External effects still need their own intents and immutable receipts. |
| API entry point | `forge_api/main.py` | `/api/conversation/message` is already the user conversation entry. Planner/composer/worker/verifier background consumers are wired, while plan and team approvals remain separate workflow gates. The orchestration path must make routine internal planning an implementation detail under the user's existing authority without weakening tool grants or uncertain-effect handling. |

`CAPABILITY_NAMES` lists common capability labels, but capability measurements are a dictionary rather than a closed enum. Adding a second registry only to introduce a reasoning label would duplicate existing functionality. A future explicit reasoning measurement must keep provenance and leave unknown values unknown.

## Local baseline checks

The existing `backend/.venv/Scripts/python.exe` is usable; Python is 3.13 and pytest is 9.2.0. No dependency installation or environment mutation was required.

The following checks ran before production changes in this workstream:

```powershell
cd backend
& .venv\Scripts\python.exe -m pytest -q tests/test_model_registry_m3.py tests/test_settings.py tests/test_workspace_scope_m7.py tests/test_phase2_provider_config.py tests/test_composer_request_v28.py tests/test_composer_lifecycle_v28.py
# 93 passed
& .venv\Scripts\python.exe -m pytest -q tests/test_team_composition_m5.py tests/test_team_intelligence_phase2.py tests/test_transactions.py tests/test_migrations.py tests/test_project_context.py
# 70 passed; one existing Starlette/httpx deprecation warning
```

Total: **163 passed**. This selection checks infrastructure relevant to the proposed change; it is not a fresh full-suite result. Historic failures and prior real-provider content-review failures remain separate evidence.

## Real Provider readiness and reusable harness

The current inherited process contains no FORGE Provider configuration. Existing local Phase 2 runners already define an operator-configured localhost proxy and select exactly one enabled, nondeleted `WB_IN` credential from the operator's proxy settings. That same local source was checked without printing or persisting credential values.

Readiness observation: authenticated `GET http://127.0.0.1:8788/v1/models` returned HTTP 200, listing 11 models including `deepseek-v4.1-flash`. This request generated no model output and does not establish model quality, actual model identity, maximum context, cost or future availability. Upstream identity is proxy-reported and has not been independently verified.

Reusable source paths:

- `backend/scripts/phase2/live_core.py`: isolated workspace/Core process, fresh authentication challenge, configured Gateway, registered profile and bounded timeout; credential stays in child-process environment.
- `backend/scripts/phase2/verify_provider.py`: protocol checks through the Gateway and durable model-call ledger, with allowlisted report fields. It exercises roughly eleven generations, so a smaller focused probe is preferable for each backend iteration.
- `backend/scripts/phase2/live_e2e.py`: existing evidence and execution driver; retains its historic plan/team workflow assumptions.
- `backend/tests/live_provider_probe.py` and other `live_*_probe.py`: additional live utilities; their presence does not count as a successful current run.

Do not publish the operator settings file, raw model prompts/replies, process environment, Core bearer, database, or logs as validation evidence. Published reports should contain allowlisted assertions, response hashes, usage/admission states and provenance only.

## Minimum useful acceptance probe

Use a fresh isolated workspace, the real configured Gateway, zero automatic HTTP retries and bounded request/step/concurrency limits. Keep the production workspace untouched.

1. Send a task through the single conversation endpoint. Confirm stable Router identity and automatically created hidden Worker state.
2. Deliver a subsequent event with a task/thread reference. Assert the subscription resolves to that Worker and no additional Router model call occurs for a direct pass-through.
3. Deliver an unrelated event. Assert cheap fast-path rejection when deterministically irrelevant; otherwise invoke the slow-path model decision once and record it.
4. Close and reopen the Store/Core. Assert Router identity, event consumption, Worker state, task association and original source references survive.
5. Exercise one bounded real Worker completion. Assert the result enters the Router's unified conversation and keeps the Gateway ledger settled. Never call a synthetic transport a real Provider run.
6. Simulate a stale Worker/consumer owner independently from the live call. Assert safe recovery resumes only replayable work; an unresolved external effect remains fenced and is never silently replayed.

The probe must report genuine live failure if the Provider becomes unavailable. Protocol readiness alone cannot satisfy the requested real Router/Worker acceptance.

## Independent implementation boundary

A small backend-only extension can detect project roots using local manifest/file evidence, persist task-to-project associations, and retain the existing workspace binding and permission scope. It should recognize ordinary folders and Python/Node/Rust projects as well as Git, require no network, avoid crawling unrelated filesystem roots, and never treat a model-provided path or Quest metadata as a filesystem grant. This can be implemented independently of trigger routing after the overall audit fixes its data contract.

## Implemented validation support

`workspace_discovery.py` now provides `migrate(connection)` and `WorkspaceDiscovery.associate(quest_id, path=None)`, `for_quest` and `list_projects`. An explicit absolute path may identify a project outside the Core storage workspace. Discovery inspects only named marker existence along bounded ancestors; it does not read manifests, execute Git, grant filesystem access or change the original desktop scope projection. Association and its event/Outbox record commit atomically.

`watchdog.py` now provides `migrate(connection)`, throttled component `heartbeat`, `snapshot`, `tick` and `supervise`. Heartbeats normally persist at most once per ten seconds. Pending tasks are never cancelled or duplicated because a heartbeat stalled. Exited loop tasks may restart under persistent bounded attempts and exponential backoff. Expired database-only delivery leases can be released without resetting delivery attempts. Expired Workers requeue only when original Gateway accounting can be reconciled and no unresolved tool, action intent or model reservation remains; unknown effects remain blocked. Requeue increments the generation and leaves existing claim-time authorization checks intact. Invalid or linked execution-environment components also block recovery.

New project/Watchdog tests: **20 passed**. Migration-related checks, including strict creation and rollback at the new migration step: **71 passed**. An earlier broader Worker/runtime selection passed **118 tests** before the final additional environment test. These are overlapping selections, not a combined full-suite total. Old test-only pseudo-downgrade fixtures now remove all newer tables before rerunning earlier migrations; production migrations retain strict schema creation.

`backend/scripts/verify_superagent.py` is the new bounded live acceptance driver. It uses the existing isolated Core subprocess harness, authenticated HTTP endpoints and read-only SQLite evidence. Supply its named credential through the process environment, then run:

```powershell
& backend\.venv\Scripts\python.exe backend\scripts\verify_superagent.py --max-calls 32 --token-limit 500000 --timeout 900
```

The driver creates two independent conversational tasks, observes hidden live Workers, supplies same-thread continuation events through HTTP, checks pass-through without a Router Trigger call, verifies successful file receipts against the actual artifact and a subsequent read, checks spilled continuation markers in Worker results, waits for Router result notices, and restarts only after Provider calls settle. It then checks persistent Router/Worker/message identity and asks for a historical recap in the same conversation. Its published report separates live provenance from offline tests and contains only bounded assertions, counts, source/message hashes and charged usage. The operator credential file location remains outside product and probe code. Current local syntax/lint checks pass; actual live outcomes must be read from the resulting report rather than inferred from the driver implementation.

First real diagnostic run, `20261115T183233Z-cf6da7` (UTC identifier; local date November 16), **failed** after two hidden live Workers executed local tools and produced results. Both continuation events passed through without a Router Trigger model call, but one late event missed its Worker's final response; the other Worker used `read_trigger`, after which independent review rejected the Core read as an invalid ordinary action receipt. The stopped run retained **25 settled Provider calls, 110,935 charged tokens**, original local database/logs, and [redacted diagnosis](audit/superagent/20261115T183233Z-cf6da7-diagnostic.json). No calls remained in flight at stop; the failed case was not automatically rerun. This establishes a concrete implementation boundary, not final Super-agent acceptance. The driver now stops immediately on a durable `review.blocked` event or Core exit.

The diagnosed Core-source failure is now repaired without inventing action receipts or weakening their gates. `worker_core_provenance.py` records a trusted Core proof in the existing evidence table inside the same fenced transaction as successful `read_trigger`, `recall_object` or `handback_trigger` completion and the tool transcript. It binds the actual run, Quest, Worker, plan revision, model-selected call, canonical arguments/result, typed source, immutable source identity/content and exact page range. Review and completion both require this proof plus matching assistant/tool transcripts, then recheck current scope, source revocation/integrity, actual Trigger event/inbox/delivery and handback event/reason. Historical dynamic task/event projections may advance without invalidating a genuine read. Missing historical proofs fail closed and are never reconstructed from model claims. Core read proofs remain context evidence and never satisfy an external action receipt requirement.

Handback now executes only once in its success checkpoint transaction; checkpoint failure rolls back the delivery mutation and its event. Historical source validation explicitly rejects dispatching these tools, and review uses read-only fact checks. The per-owner `trigger.delivery_handed_back` event is distinct from the final-owner `trigger.handed_back` Router wakeup.

Focused regression after this repair: **127 passed, 1 skipped**, covering the 21 new provenance cases, independent reviewer context/Gateway/repair/capacity, prior native Core reads, the Super-agent loop and event routing. Three new cases exercise actual SQLite/tools, a scripted local HTTP server through the real Gateway/ledger, independent review and successful NodeVerifier completion. They are offline protocol tests, not real Provider acceptance. Ruff and `git diff --check` also passed. Two fresh real Provider scenarios are pending a full production-source freeze; the retained failed diagnostic above remains unchanged.

Two subsequent real Provider runs used the same frozen source digest and retained their original reports/workspaces. Neither establishes final acceptance. `20261115T190232Z-065808` stopped at the original 32-call ceiling with 30 settled and 2 reserved calls (204,971 charged tokens). Its first task genuinely passed independent review and verification with Core context reads, but the driver waited for both conversation HTTP requests before beginning Worker observation/followup injection. Concurrent snapshot retries delayed the second Router turn; the first short Worker had already finished before the observation window. See [the timing diagnosis](audit/superagent/20261115T190232Z-065808-diagnostic.json). No unknown call was replayed.

The separately authorized timer cold-start run `20261115T190431Z-cold-1f2fe2` observed two parallel hidden Workers, both completed artifacts, independent reviews, a unified Router result, slow-path timer dispatch/file-noise rejection and typed Recall. Its original report says `passed`, but a final accounting race invalidates that acceptance status: after the all-settled check, another Router notice started before Core shutdown, leaving 18 settled and 1 reserved call (58,442 charged tokens). The [correction diagnosis](audit/superagent/20261115T190431Z-cold-1f2fe2-diagnostic.json) explicitly marks effective acceptance failed and preserves the original report. Reports must gate final shutdown accounting as well as the earlier business snapshot. No automatic rerun or replay occurred.

After the authorized responsiveness, Recall and driver fixes, fresh frozen-source runs produced the following evidence. `20261115T192301Z-67cf20` completed both tasks, verified both artifacts/spilled event reads, delivered both Router results and preserved state after restart, with **23/23 settled calls and 115,765 charged tokens**. It still **failed** on the historical recap: read-only reconstruction reproduces `Router tool context budget exceeded`, because the first serialized round occupies 61,785 bytes against the unchanged 61,440-byte input ceiling. The [diagnosis](audit/superagent/20261115T192301Z-67cf20-diagnostic.json) also corrects a driver interval bug: a second claim of the same run replaced its earlier unclosed claim, hiding actual overlapping ownership. The driver now keeps the first observation until an explicit closing fact, while clearly distinguishing ownership spans from continuous computation. Initial user turns used two Router calls, two semantic calls and zero reflections, with no repeated snapshot cycle.

The fresh timer scenario [20261115T192629Z-cold-d0dae9](audit/superagent/20261115T192629Z-cold-d0dae9.json) **passed all 16 checks**, including final shutdown accounting: **16/16 calls settled, 46,089 charged tokens**, two overlapping hidden Workers, both verified artifacts, persisted independent role assignments, source-bound mandate, timer dispatch/file-noise filtering, Router result and typed Recall. This is real Provider execution through the configured local proxy; upstream model identity remains proxy-reported. It establishes that scenario on its recorded source digest, while the separate recap failure remains open. Further live runs require another explicit source freeze after the narrow wire-budget repair; prior reports and unresolved runs remain untouched.

## Final frozen-source acceptance

The narrow wire-budget correction is now implemented and covered by the actual
Gateway payload regression. It reduces the read-only failing snapshot's native/Core
input estimate from 62095 to 61320 bytes under the same 61440-byte ceiling,
preserving user input and both completed summaries. Optional historical sources
remain available through typed references; unseen events are not acknowledged.
The full Router compatibility/mutation/native-wire selection passed **154 tests**.

Fresh real runs use the identical backend Python plus conversational-driver digest
`388123662a3e14adcba343ee2d62c6eb84679a989a0f90780d7037efb27c277e`:

- [20261115T193658Z-d72209](audit/superagent/20261115T193658Z-d72209.json):
  **17/17 checks passed**, 22/22 calls settled, 126055 charged tokens. Two parallel
  hidden Workers, both direct continuations, verified artifacts and Router
  delivery passed; restart retained identity and the same conversation correctly
  recalled both actual source markers without creating another task.
- [20261115T193938Z-cold-697d50](audit/superagent/20261115T193938Z-cold-697d50.json):
  **16/16 checks passed**, 17/17 calls settled, 51306 charged tokens. Timer/file
  filtering, two independent role contracts and selected models, overlapping
  Workers, both artifacts, Router result and typed execution Recall passed.

Both scenarios require final shutdown accounting and Router notice drain, and
retain the original 32-call/500000-token/900-second ceilings. Neither replays an
uncertain previous workspace. Upstream identity is still proxy-reported, and
these bounded file-based scenarios do not establish unrestricted tool use or
general task quality. See the [implementation report](SUPERAGENT_IMPLEMENTATION_REPORT.md)
for final regression status and operating limits. Earlier diagnostic statements
above describe their recorded snapshots and remain preserved as history.
