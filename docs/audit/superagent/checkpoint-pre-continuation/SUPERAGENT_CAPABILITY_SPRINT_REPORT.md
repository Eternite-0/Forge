# Super-agent Capability Sprint

Date: 2026-11-16. Base: `57a162d31f9b559353fecbde61f95dd72f878abd`.
Branch: `codex/phase1-continuous`. Status: implementation advanced; full task acceptance **not passed**.
No frontend work, architecture replacement, Git push or external Agent backend.

## Audit before implementation

The starting checkout was clean. README, STATUS, implementation report, source
and the preserved acceptance index were read first. STATUS still led with older
Phase 2 results; the actual baseline is the newer Super-agent implementation:
2312 passing tests, fourteen reproduced historical failures, two Windows skips,
and two bounded real scenarios with 39 settled calls. These historical successes
do not establish the more complex L1/L2/L3 required here.

Existing Worker inference/tools/checkpoints, DAG dependencies, source-verified
predecessor deliveries, partial replanning, model observations, Wilson intervals,
Registry discovery, compaction and governance are reused. Targeted starting
regression passed **104 tests** in `audit/superagent/capability-baseline.xml`.

| Sprint | Actual gap | Minimum change |
| --- | --- | --- |
| A | Router context ignores project links; ambiguity spans projects; lifecycle controls stay outside conversation | Project-focused retrieval/candidates, source-bound project selection, coordination facts and conversational controls; same Router/CAS |
| B | Python tools cannot execute stdlib project tests; failed executable test result has no safe repair proof | Exact-manifest R2 CPython unittest tool, private cwd, process-tree cleanup, consumed approval proof and existing bounded repair loop |
| C | Autonomous contracts forbid dependencies although scheduler already supports them | Bounded coarse expert topology, verified handoffs, role-specific tools, source-bound help and bounded replacement/expansion |
| D | Identity discovery is not capability measurement; some health/outcome projections are absent | Extend existing performance projection and benchmark_runs with small attributed probes; keep unsupported dimensions unknown |
| E | Task-centric project isolation and long-term object retrieval need stronger tests | Project-filtered source retrieval and multi-hop/compaction continuity on existing records, preserving hashes and revocation |
| F | Healthy loop heartbeat does not establish Worker progress | Inspect actual checkpoints/settlements, emit deduplicated help with source references; retain lease/unknown-effect fences |
| G | Earlier live tasks were bounded file markers, not complex content/coding acceptance | New L1/L2/L3 with independent artifact checks and controlled strategy comparisons |

## Validation plan fixed before live execution

Real Provider runs use fresh isolated storage, the already authorized local proxy,
process-environment credentials and the registered Provider context declaration. The initial conservative 65536 profile is superseded by the operator correction and actual 1M API declaration retained in the experiment record. Router output reserve remains 4096. Operator configuration is not edited. Each task keeps the
existing **32-call / 500000-token / 900-second** ceiling; no automatic Gateway
retry is added. A failed or uncertain run is retained and never replayed.

The planned task set is L1 project diagnosis, L2 source-based Agent-framework
research and L3 a small Python feature verified by real tests and independent
held-out cases. L2 also supplies a controlled single-Worker and fixed-team
comparison to the adaptive path: five task runs, at most 160 calls and 2500000
tokens in aggregate. Up to two discovered models may receive one six-dimension
microprobe suite each, separately capped at 16 calls / 100000 tokens. Unknown
prices remain unknown. Small samples support exploratory observations only.

One combined scenario must observe two distinct local project roots, concurrent
Workers, direct Thread follow-up, settled-boundary Core restart, actual context
compaction, typed Recall and delivery to the original Router. A model's claims,
mock fixtures and programmatic checks are recorded separately. Approved Python
code runs with the same OS account; private directories and fixed invocation do
not constitute an OS sandbox or a tamper-proof correctness oracle.

The bounded experiment ceiling includes failed attempts and targeted diagnostic
calls. Final evidence and unresolved limitations below do not predeclare success.

## Implemented changes

Router project focus now filters task candidates, lexical/semantic memory,
recent messages, events, evidence, constraints, archive prose and object/source
readback. Explicit cross-project requests can traverse the shared historical
conversation. Task and project associations remain references, never filesystem
grants. Continuations inherit the target task's authoritative project identity.
Conversation lifecycle controls share the existing Router transaction, CAS and
durable Worker commands; they cannot grant tools or clear unknown effects.

The autonomous v2 mandate uses existing graph/team scheduling for single,
parallel, sequential and hierarchical expert topologies. Completed predecessor
deliveries require the original source/evidence proofs. A single Worker may
request two specialists and an integrator within four cumulative Workers and
depth one. Expansion and replacement preserve spent/unknown budget reservations,
prior outcomes and the original tool/deadline limits. Legacy mandates retain
their original policy and tool scope. Replacement is not counted as expansion.

The CPython tool supports imports, classes and stdlib unittest, with exact-file
authorization, bounded output/time and process-tree cleanup. Known settled test
failures can enter the existing two-repair loop only with matching consumed R2
approval, policy, result protocol and manifest proofs. Timeouts and unknown
external effects remain blocked against replay. Independent held-out tests are
separate host checks; model-written tests are not a trusted correctness oracle.

Model measurements extend existing observations and benchmark_runs rather than
adding another state store. Transport health distinguishes known in-flight work
from unknown attempts. The Watchdog observes transcript/tool/model settlements
and capsule frontiers separately from heartbeats. An unchanged frontier emits a
source-bound, durably deduplicated help event, retaining lease and unknown-effect
fences. A stall observation is not proof of failed task quality or permission to
repeat an external operation.

## Real model microprobes

The retained `20261115T205519Z-capability-model-probes.json` records twelve real
calls, all settled, 1,414 charged tokens and unknown monetary cost. The configured
proxy reported DeepSeek and GLM identities; their upstream identity is unverified.
DeepSeek passed five of six fixed cases; GLM passed three. Each dimension has one
sample, and the exact-response contract also measures format compliance. Research
failed the strict oracle on both; GLM coding and planning responses failed the
JSON contract. Design checks a textual accessibility specification, not image
quality. Vision was unsupported and generated no call. These results do not
establish general capability scores or justify a preferred routing algorithm.

## Observed real-task results

L1 independently passed in `20261115T215924Z-capability-adaptive-45f4e4`:
one real Worker read the actual FORGE modules, wrote its analysis, passed the
model Reviewer and strict source/hash/line/quote checking. The combined run
remained failed because L2 Router admission failed. Earlier L1 attempts and
their inaccurate citation ranges remain failures.

L2 did not complete. A fixed Research → Architect condition admitted the correct
chain but its first Worker blocked after two Reviewer revisions. A subsequent
adaptive condition actually admitted Research and Architect in parallel, with
a dependent Reviewer integrator. Research passed, Architect blocked, and the
integrator never started. The absence of intermediate JSON artifact bodies
from a delivery-only review packet caused blind revisions in the fixed run;
the implementation now projects only source-verified own JSON artifacts into
that review. This fix retains original criteria, scopes and byte limits.

L3 in `20261115T221734Z-capability-continuity-300fa0` produced real code, ran
approved CPython tests, corrected an observed failure, passed the real Reviewer
and all seven independent host holdouts. It still failed full delivery
acceptance: the report cited inferred action names instead of actual test
receipt IDs. The Runtime exposed only the test output, omitting the Core receipt
identity, so the Worker could not cite the ID the checker required. This is a
Harness defect; the checker correctly rejected the report. The subsequent fix
exposes a source-bound Core receipt envelope for Python results and failed-test
feedback. It does not alter old artifacts, receipts or the failed verdict.

The final combined run observed three overlapping Worker ownership intervals
across two tasks, both Thread pass-through paths, a safe Core restart using the
same Router/Worker IDs, compaction of five real Router messages, and typed Recall
references to tasks, Workers, receipts and archives. Recall remained incomplete
at its fixed bounds. These are partial continuity observations. Both tasks were
wrongly linked to the same project, and the final reviewed completion notice
did not reach the Router. An earlier Worker claim did reach the original main
conversation; it must not be reported as the final reviewed completion.

Independent read-only supplements retain these distinctions and source hashes.
The original combined report remains failed. An approval notice exhausted its
bounded processing attempts; the final completion notice remained unprocessed
after four failures. The fixed forty-call admission ceiling blocked further
admissions. No uncertain effects were replayed, and the limit is not a Provider
outage or proof of failed model transport.

## Verification boundary and remaining work

The full `capability-backend-frozen.xml` run recorded **2533 passed, 14 failed,
3 skipped** before the last narrow fixes. Thirteen failures exactly match the
historical baseline; one was an intentional new diagnostic-event contract
conflicting with the older assertion that an invalid Router response leaves no
events. The former historical compact-prompt failure passed after redundant
wording was reduced. The atomicity regression now explicitly verifies unchanged
Router state, no messages/Quests and exactly the safe diagnostic events; it does
not permit scheduling or user-turn side effects. Original failed XMLs are kept.

Later targeted regressions cover the team error hints, receipt visibility,
specialist review, per-task/final accounting and the optional atomic HTTP-attempt
ceiling. See `final-verification.json` for final source hashes and exact suite
counts. Overlapping targeted counts must not be summed as a full suite.

The final full regression on the final production source recorded **2587 passed,
13 failed, 3 skipped**, one existing Starlette deprecation warning, in 556.83
seconds (`capability-backend-final.xml`). All thirteen failures match historical
baseline cases; there are no new failed cases. The diagnostic-event contract
test passes. One already collected project test later received only an import
spacing/unused-variable lint cleanup, and its eleven cases were rerun and passed.
All 59 modified/new Python files pass Ruff; backend/README/docs diff checks pass.
This regression result does not turn the failed live acceptance into a pass.

The final two real Provider probes both recovered exact Python test receipt IDs,
execution states and candidate hashes from the corrected projection. They use
the original historical database read-only and execute no old Worker or test.
Task/diagnostic use totals 160 admitted calls, 159 settled and one old reserved
attempt with unknown outcome; known usage is 1355207 Token. The separate twelve
microprobe calls used 1414 Token. Monetary cost remains unknown. No further real
calls are made beyond the fixed ceiling.

The model window defaults to the Registry declaration. Cumulative allocation,
request affordability, compaction protocol and evidence limits remain separate
checks. `context_budget` was historically a broad external error code, so it
cannot establish that a Provider's declared context window was exhausted.

Unfinished acceptance includes a successful multi-specialist L2, a full L3
delivery after the receipt-envelope fix, correct two-project associations across
the complete live scenario, final reviewed Router notifications and recap, and
a controlled successful single/fixed/dynamic comparison on frozen source.
Dynamic expansion, replacement, failover and stall recovery have targeted
programmatic/scripted coverage; this experiment set does not establish their
live long-term success rates. No superior policy or general capability ranking
is claimed. Tools retain their existing governance; arbitrary shell execution,
OS sandboxing and unrestricted package installation are not introduced.
