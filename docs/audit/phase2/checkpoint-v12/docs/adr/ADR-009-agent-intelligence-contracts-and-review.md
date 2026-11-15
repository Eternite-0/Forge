# ADR 009: Source-bound planning, independent review and durable coordination

Status: Phase 2 implementation in progress; local and live acceptance are recorded separately.
Date: 2026-11-15.

## Context

ADRs [007](ADR-007-native-worker-and-execution-evidence.md) and
[008](ADR-008-context-recall-budget.md) establish approved native execution, trusted
receipts, bounded context and Gateway admission. Phase 2 needs stronger planning,
revision and collaboration without replacing those controls or adding user chats.

## Decision

1. Keep the persistent Router as the only user-facing Agent. A separate Planner
   consumes the original request, source hashes, constraints and offered tools to
   propose a bounded DAG. A proposal never grants execution authority; plan and
   team approval continue to require the authenticated local user.
2. Store literal user clauses, pending model extractions and separately confirmed
   clauses with distinct provenance. Bound context highlights and retain original
   source readback paths. Conflicts, omissions, expiry and revocation remain visible;
   extracted instructions never substitute for action approval.
3. Review a delivery in a fresh context containing the original contract, criteria,
   bounded actual artifacts and verified receipts. Do not copy the Worker's private
   conversation. Bind the verdict to its input hash and admitted model attempt.
   Evaluate the current node's objective, success criteria and approved role; a
   read-only intermediate researcher must not be assigned a downstream writer's
   artifact. Keep the original request in full and retain every original requirement
   for a node assigned the corresponding final deliverable. Revision grants no authority.
   `NEEDS_REVISION` requires concrete goals and consumes the existing bounded budget.
   A fresh PASS is necessary for review criteria but remains subject to NodeVerifier
   and CompletionGate checks. The current reviewer uses the approved Worker's model;
   separate context does not establish independent model identity.
   Large inputs may use a lossless wire envelope with full untrusted source blocks,
   receipt/text-hash/byte bindings, shared repeated constraint fields and exact
   original-message character spans. Keep the original `_packet` and PASS hash
   unchanged; omit no source or review criterion and retain all context/budget limits.
4. Project typed Agent messages from the existing durable event/outbox transport.
   Preserve task identity, lease fencing, Inbox deduplication and Router-mediated
   control. Agent messages confer no right to mutate another Worker's assignment.
5. Reuse observed model performance as a sourced, sample-counted input to new team
   proposals. Preserve explicit user scores, approved profile hashes, completed
   nodes and historical contracts. Replacement never resets cumulative budgets.
6. Keep `file_write` create-only. Add separately approved R1 `file_revise` only for
   the current Worker's own latest successfully produced artifact, with exact prior
   receipt/hash, current authority/grant checks and atomic replacement. Retain old
   content in immutable intent/receipt history; review and completion validate the
   bounded same-Worker chain and latest bytes. Do not extend old contracts or grant
   authority over user files, predecessor artifacts or unsettled outcomes.
7. Distinguish Core JSON-parser feedback (`deterministic_artifact_validation`,
   `core_json_parser`) from model review. Parser failures may request concrete
   bounded revision but create no model attempt or PASS. Reviewer output fits
   8192, approved role/model caps, remaining model context and the original cumulative
   token remainder. Adaptive output has floor `min(1024, original output cap)`;
   insufficient capacity or original cost authority fails closed, while lower original
   caps stay valid. Native input reservation uses the actual serialized Gateway
   payload, including schema/reasoning settings, and matches admission. Settled truncation
   charges exact actual usage before any bounded new verdict. Unknown remote
   outcomes retain reservations. Committed revision verdicts requeue idempotently.
8. Renew Worker ownership only between settled steps after current token/generation,
   plan/team and status checks, to `min(task deadline, now + lease_seconds)`. Do not
   revive expired claims or enlarge task allocations. Individual calls remain
   bounded by their current lease/deadline.
9. Treat a node's token allowance as cumulative input/output/review/repair usage,
   separate from each model context window. Align the node schema ceiling with the
   existing finite 1,000,000-token graph ceiling; keep the sum of node allowances
   within the approved graph total and retain all lower scenario, team and global
   limits. This does not change previously approved budgets or grant unlimited use.
10. Bind fixed numeric-suite failure feedback to the latest delivery, exact
    Quest/plan/revision/Worker/run, real semantic run/report, source and receipt.
    Label it `deterministic_semantic_validation` / `core_fixed_numeric_suite`, with
    at most ten failed-case diagnostics and no LLM verdict or PASS. Invoke the same
    Worker's revision handler only with explicit graph/node/Worker/member revision
    scopes, a current grant, remaining original budgets and no unresolved actions
    or model attempts; a new delivery must pass the real suite again.

11. V7 guidance covers new intermediate JSON artifacts and delivery_review handoffs with downstream consumers
    toward about 4 KiB (at most 6 KiB) only when all assigned requirements fit. Complete code, final reports
    and required detailed schemas remain full even when larger. This is soft writing
    guidance: no stored artifact is truncated, no hard report-size gate is added, and
    original packet/PASS/source/raw/context/evidence requirements remain unchanged.
    Planner guidance recommends about two model contexts for cumulative source-heavy
    entry-node usage when the original task total permits, retaining at least ten percent
    coordination reserve. V8 removes the output_tokens=2048 preference and requires
    sufficient per-call output for complete schema-valid tool arguments and delivery.
    Central citation IDs retain exact sources.
    New/replacement team proposals and approval enforce a trusted operator concurrency
    cap; absent optional configuration preserves compatibility. Existing approved teams and historical evidence retain their contracts. Core
    supplies Composer and WorkerManager the Worker cap `2`, independent of Gateway
    model-call concurrency. Each resource retains its own original admission limits.
    The live harness takes the smaller CLI/scenario total-token and concurrency limits
    and records the effective values. V8–v12 full/live outcomes are recorded; this round's final state remains partial; see the v12 evidence record.

12. For new native TeamWire proposals, only explicit `output_schema_json=null`
    selects a deep copy of `STRICT_DELIVERY_OUTPUT_SCHEMA`: the model must supply
    all eight delivery fields and confidence within 0–1, with no Core-inserted defaults.
    The contract is checked under any node criterion through JSON Schema and bounded
    repair. Legacy/default direct RoleSpec contracts and explicit older schemas retain
    their original meaning; historical approvals/results are unchanged. Non-finite
    JSON values are rejected through the existing bounded repair path. Strict structure
    does not establish semantic correctness or replace review/evidence gates.
13. V8 removes the intermediate output_tokens=2048 preference: per-call output must
    fit complete schema-valid tool arguments and delivery. Worker instructions cover
    the current node's approved objective, role and criteria; downstream nodes retain
    their own deliverables. Planner guidance uses one node only when the entire task
    is simple read-only analysis and preserves explicitly requested specialist/count
    requirements. Composer guidance preserves unique role IDs and correct specialties,
    and prefers max_steps=16 only when the user's ceiling permits. Native Core configures
    a shared self-repair/review-revision maximum of four; existing counters, original
    steps/tokens/tools/cost/time and review-round limits still bind every attempt.
    Worker concurrency two remains independent of Gateway model-call concurrency.

14. V9 rejects a generated node with a concrete artifact verifier unless that same
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

The [v9 full suite](../audit/phase2/frozen-tests-v9.xml) / [log](../audit/phase2/frozen-tests-v9.log)
passed 895 tests with one warning in 237.83 seconds. Its [157-file manifest](../audit/phase2/source-manifest-v9.json)
is `9a33b199404b777428ff0f0e1a1c1da356ad93b23b4395565fc0d211b3d9203a`;
Ruff, formatting for 154 Python files and pip checks passed. This predates v10.
[Offline benchmark v9](../audit/phase2/benchmark-final-v9.json), `benchmark_c4387a6e26ac`,
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
[snapshot-v2](../audit/phase2/live-runs/snapshot-v2/index.json), with original run records and
diagnostic-snapshot-v1 preserved. Publication makes no Provider call and does not
promote failed or unaccepted tasks. Historical retry totals can overlap: the old
`worker_runs.repairs` includes review revisions. V10 counts actual self-repair
events separately and marks future records `retry_accounting=disjoint_event_counts_v2`;
[27 focused regressions](../audit/phase2/retry-accounting-v10-fixed.xml) pass, while original
run counters remain unchanged. V10 full/live outcomes are recorded below; v12 frozen results are recorded; the v12 evidence record consolidates this round's partial state.
Core capacity feedback is not independent model review or PASS.

V10 adds labelled Core capacity diagnostics (`deterministic_review_capacity` /
`core_review_capacity`), never a model verdict or PASS. Original packet/source/hash
and 65,536 raw/context caps remain. Same-Worker revision requires approved
`file_revise`, current receipts/grants, remaining original repair/step/tool/token/
cost/time/round limits and no unsettled actions/model attempts. Arithmetic floors
only check whether a complete revision plus real independent review might fit;
they do not remove required evidence or prove compression. Inability to fit is BLOCKED.
The [corrected capacity group](../audit/phase2/reviewer-capacity-v10-fixed-tests.xml) passed
143 cases (JUnit 45.843 seconds); the [first 143-case attempt](../audit/phase2/reviewer-capacity-v10-tests.xml)
retains one failure. These are overlapping local/Mock tests.
The driver waits read-only for settlement for at most 30 seconds before stopping;
it never resends, cancels or settles unknown remote calls itself.
[Settlement regressions](../audit/phase2/driver-settlement-v10.xml) passed 27 cases
(JUnit 4.263 seconds). V10 records use disjoint event-count retry accounting;
historical counters and outcomes remain unchanged.

The [v10 full suite](../audit/phase2/frozen-tests-v10.xml) / [log](../audit/phase2/frozen-tests-v10.log)
passed 915 tests, one warning, in 279.54 seconds. The [158-file manifest](../audit/phase2/source-manifest-v10.json)
is `814d0dcc6496f65d01ededad38a1e45a8d0f9848739467f7c36b985f3c318415`;
Ruff, formatting for 149 Python files, pip and diff checks passed. This precedes v11.
[Benchmark v10](../audit/phase2/benchmark-final-v10.json), `benchmark_4f7e4f5325ba`,
completed in 7.5434305 seconds with zero Provider calls on the unchanged fixed dataset.
The [read-only preservation audit](../audit/phase2/preservation-v10.json) retains 355 byte-identical
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

[Snapshot-v3](../audit/phase2/live-runs/snapshot-v3/index.json) publishes 42 finished driver
records and preserves snapshot-v2/v1 plus original run records. Publication made
no Provider request and does not promote failed tasks. Historically at v10, v11 was in progress:
bounded correction after a rejected local receipt read and byte/availability hints
have [79 focused passing cases](../audit/phase2/receipt-read-repair-v11-tests.xml);
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
authority are granted. The [79-case focused group](../audit/phase2/receipt-read-repair-v11-tests.xml)
passed (JUnit 19.062 seconds); [legacy compatibility](../audit/phase2/receipt-repair-legacy-v11.xml)
passed 35 (JUnit 9.704 seconds). Generated Planner proposals now receive and
enforce the trusted operator total-token ceiling before persistence; an over-cap
reply uses the original one repair and repeated violations stop without a plan.
Default/manual/historical contracts remain unchanged. [Budget tests](../audit/phase2/planner-operator-budget-v11.xml)
passed 16 (JUnit 1.203 seconds); [compatibility tests](../audit/phase2/planner-operator-compat-v11.xml)
passed 51 (JUnit 17.397 seconds). These focused groups overlap the full suite.

The [v11 full suite](../audit/phase2/frozen-tests-v11.xml) / [log](../audit/phase2/frozen-tests-v11.log)
passed 941 tests, one warning, in 199.39 seconds. Its [160-file manifest](../audit/phase2/source-manifest-v11.json)
is `eec443f0aad4687e336a8b9ca3629218ebce4417b8096dabb8eaa44845dc7822`;
Ruff, formatting for 157 Python files, pip and diff passed. This predates v12.
[Benchmark v11](../audit/phase2/benchmark-final-v11.json), `benchmark_f23819557132`,
completed in 4.3837569 seconds with zero Provider calls on the unchanged fixed dataset.
[Preservation v11](../audit/phase2/preservation-v11.json) still records 355 unchanged Phase 1
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

[Snapshot-v4](../audit/phase2/live-runs/snapshot-v4/index.json) publishes all 45 finished driver
records while preserving v3/v2/v1 and original run records. V12 is correcting the
serial-reservation precheck; [145 focused cases](../audit/phase2/reviewer-serial-capacity-v12-tests.xml)
pass (JUnit 74.570 seconds) with actual Gateway admission over offline MockTransport,
not a live Provider result. First whole-tail archiving must already include the
readback tool in that request and its exact reservation; [87 legacy cases](../audit/phase2/first-archive-legacy-v12.xml)
pass (JUnit 65.280 seconds). V12 new-path/full/live results are recorded below.
Neither change expands original authority, budgets or context limits.

V12 corrects the serial-capacity precheck to compare the maximum individual
reservation floor with the remaining original allowance; each subsequent complete
request still requires actual Gateway admission and charges settled usage.
This corrects the conservative v11 rejection without increasing any budget.
First whole-tail Worker archiving now declares source readback in that same
request when steps >0 and the effective per-request allowance is at least 8192,
including the tool's full bytes in original admission. It never enables source
omission without recoverability. The [12-case corrected first-archive group](../audit/phase2/first-archive-v12-fixed-tests.xml)
passed (JUnit 1.848 seconds); the [initial 12-case attempt](../audit/phase2/first-archive-v12-tests.xml)
retains one failure. The earlier 87 legacy/145 serial-capacity groups are overlapping
local/Mock evidence. No first whole-tail fallback or readback was triggered in
these three new live runs, so that new mechanism remains locally verified only.

The [v12 frozen full suite](../audit/phase2/frozen-tests-v12.xml) / [log](../audit/phase2/frozen-tests-v12.log)
passed 955 tests, one warning, in 251.43 seconds. The [162-file manifest](../audit/phase2/source-manifest-v12.json)
is `ccc5efe69b4493dd77093b4a62f6f6851f09c63ca1f2806298f2e59e80d7f8d7`;
Ruff, formatting for 159 Python files, pip and diff checks passed.
[Benchmark v12](../audit/phase2/benchmark-final-v12.json), `benchmark_37ee6599ac54`,
completed in 4.6835418 seconds with zero Provider calls on the same fixed synthetic
dataset. [Preservation v12](../audit/phase2/preservation-v12.json) retains 355 byte-identical
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

The [read-only Reviewer-claim diagnostic](../audit/phase2/reviewer-claim-diagnostic-v12.json)
checks an incorrect revision goal from this L3 review: `ast.parse(mode="exec")`
accepts the top-level Return node, and the actual bounded interpreter evaluates
`return 0` to result 0 in two steps. Python bytecode compilation rules are not
that interpreter's contract. This diagnostic made zero Provider calls and changes
no run outcome. Fresh-context same-model review can issue incorrect revision goals;
fixed native numeric semantic gates independently verify applicable artifacts.
A review verdict alone does not guarantee domain correctness.

[Snapshot-v5](../audit/phase2/live-runs/snapshot-v5/index.json) publishes all 48 finished driver
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

[Snapshot-v6](../audit/phase2/live-runs/snapshot-v6/index.json) publishes 50 driver records and
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






## Consequences and limits

The implementation adds focused services and schema changes rather than a parallel
runtime. Default Review autonomy, policy, exact approvals, receipts and Gateway
reservations remain authoritative. Unknown remote effects stay unresolved until
trusted recovery conditions permit settlement; a client timeout permits no replay.
The [118-case review-input group](../audit/phase2/review-context-20261115T091955.xml)
records local/Mock reconstruction and admission checks. Its L2 fixture's native
Gateway construction reserves 64,245 under the same 65,536 ceiling; this made no
network call and establishes no general larger-packet capacity or live success.

The later [123-case review scope/cap group](../audit/phase2/review-scope-cap-tests.xml)
and [read-only diagnostic](../audit/phase2/review-scope-cap.md) retain the failed L2
second-review packet: 59,601 bytes, unchanged original packet/PASS binding and
65,536-byte raw packet limit. Its local native reservation is 59,761 input plus
5,775 output = 65,536 under the unchanged context ceiling. This offline construction
made no network call, resets no budget and expands no permission.

Later L2/L3 live runs still exceeded raw/context limits before review or restart;
L3 also lacked cumulative review headroom, while L2 declared concurrency three
above scenario cap two. The [v5 full suite](../audit/phase2/frozen-tests-v5.xml)
passed 820 tests / one warning / 219.96 seconds before v6 changes. The
[69-case operator-cap group](../audit/phase2/composer-concurrency-20261115T094900.xml)
and [99-case report-guidance group](../audit/phase2/worker-report-guidance-tests.xml)
are local/Mock evidence. The [v6 full suite](../audit/phase2/frozen-tests-v6.xml)
retained 848 passed, one failed, one warning / 283.77 seconds: the actual parallel
lifespan test exposed the incorrect coupling of Gateway model concurrency one and
Worker concurrency two. V7 restores separate limits without editing the original
test. All three v6 live reruns failed at original repair or review capacity limits;
v7 guidance/limits do not retroactively repair them. The [v7 full suite](../audit/phase2/frozen-tests-v7.xml) passed 849 tests /
one warning / 216.87 seconds, manifest `58339132008a97956237ec38c2eb19eb7062f46b4ef2f5f946567ad52be26627`
(155 files; Ruff/152-file formatting passed). All three v7 live attempts failed:
output truncation/repair exhaustion, missing required specialists/context capacity,
and actual NEEDS_REVISION followed by ten-step exhaustion. The
[185-case contract group](../audit/phase2/native-delivery-contract-20261115T151500.xml)
and [latest 36-case file](../audit/phase2/native-delivery-nonfinite-20261115T152200.xml)
are overlapping local/Mock evidence for later v8 changes. The [v8 full suite](../audit/phase2/frozen-tests-v8.xml) passed 886 /
one warning / 283.46 seconds on manifest `92bb6067ff7bc1dadfa6aa9b5994089969603b53b3980a9a5601d0a140e6b9e3`
(156 files; Ruff/153-file formatting/pip passed). All three v8 live attempts failed:
L1/L2 reviewed then exhausted tools through repeated reads, with actual Worker
archives but no Core restart and one unresolved Router-notice attempt each; L3's
read-only node could not produce its required file. The
[independent audit](../audit/phase2/continuity-and-live-diagnosis-v8.md) separates
implemented continuity from these actual failures. The
[lossless-reference diagnostic](../audit/phase2/reviewer-lossless-v8-diagnostic.json)
and [124-case group](../audit/phase2/reviewer-lossless-v8-tests.xml), 60.43 seconds,
verify an exact 524-byte native reservation saving with original packet/hash/raw
cap preserved, no network call and no historical change. V9 full/live results are recorded above; V12 frozen checks are recorded; the v12 evidence record consolidates this round's partial state.

Fixed-suite feedback rejects duplicate results, callback errors and fake success;
if a callback commits before raising, the exact queued revision is fenced blocked.
A crash after semantic feedback commit but before callback permits idempotent
recovery through `IndependentReviewService.tick()`, including semantic-only nodes,
without a model-review request. Recovery requires `revision_available=true`, the
current delivery and genuine semantic/source/receipt bindings, current tool
scopes/grants, remaining original budgets and no unsettled actions or Gateway
attempts. The Worker revision interface rechecks approved team, cumulative
repair/step/token/deadline limits and one-time event consumption. Stale or changed
sources, revoked grants, exhausted budgets and originally unavailable revisions
remain blocked. The [74-case recovery group](../audit/phase2/semantic-recovery-20261115T090801.xml)
provides local/Mock evidence; this does not authorize unknown-action replay or
establish live task correctness.

Schema repair, review rounds, Agent counts, context packets and replanning are
bounded. General automatic remaining-DAG replanning, arbitrary hierarchical Agent
expansion, exhaustive per-message payload schemas and learned semantic retrieval
remain incomplete. The legacy summary-only delivery contract stays compatible;
`delivery_review` explicitly requires all eight delivery fields. Local/Mock tests
and synthetic benchmarks do not establish complex live-task acceptance. See the
[Phase 2 report](../PHASE2_AGENT_INTELLIGENCE_REPORT.md) for the current evidence.

The current execution boundary remains the fixed Core toolset: bounded numeric
AST, syntax checks and diagnostics, with create-only writes and explicitly scoped
own-artifact revision. General program execution, arbitrary test runners, general
repository patching and running-DAG revision remain incomplete.
