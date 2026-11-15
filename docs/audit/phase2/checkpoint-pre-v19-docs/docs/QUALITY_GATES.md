# Quality Gates and Known Limitations

Current acceptance — v15, 2026-11-15: **1,031 backend tests passed**, one
existing TestClient warning, 227.13 seconds. Ruff, formatting of 164 Python files,
dependency and whitespace checks passed on the unchanged 167-file frozen source.
The three current-source strict live runs **all failed**: L1 requested help and
its approved concurrency exceeded the scenario cap; L2 lacked cumulative review budget;
L3 revised after real NEEDS_REVISION, then exhausted review capacity. Neither
complex run reached its mid-execution restart. Phase 2 remains incomplete.
See [this round's evidence](audit/phase2/acceptance-v15.md) and
[machine-readable verification](audit/phase2/verification-v15.json).
The following earlier-round results are historical; the original v12 verification
and documents remain byte-identical under `audit/phase2/checkpoint-v12/`.


Updated: 2026-11-15. Phase 2 Agent Intelligence is in progress; desktop acceptance is deferred.

## Current evidence

- Phase 2 local/Mock focused records: B/F 53 passed in 18.36 seconds, driver 5 passed in 0.57 seconds, C/I 42 passed in 14.61 seconds, D/E/G 128 passed in 46.34 seconds, Provider/source binding 42 passed in 4.56 seconds. Groups overlap and are not a full-suite count. V12 frozen backend checks are recorded below; complex-task acceptance remains incomplete.
- Later focused local/Mock records: own-artifact revision and Reviewer repair 153 passed in 40.63 seconds, Worker lease recovery 90 passed in 32.30 seconds, committed revision/requeue restart recovery 28 passed in 13.95 seconds. [D/E/G evidence](audit/phase2/sprint-deg.md) retains earlier results; these overlapping groups are not live quality measurements.
- Fixed numeric-suite revision group: 119 passed in 44.14 seconds, including 21 new cases, with actual local numeric subprocesses and scripted model fixtures; no Provider call. [C/I evidence](audit/phase2/sprint-ci.md) records the fail-closed authorization, budget, unresolved-outcome and callback checks.
- Later [semantic revision recovery group](audit/phase2/semantic-recovery-20261115T090801.xml): 74 passed in 34.65 seconds; the semantic test file now contains 30 cases. Persisted feedback before callback recovers idempotently after reopen without model review, while current source/authority/budget and unresolved-outcome guards remain enforced. This is local/Mock evidence.
- [Lossless review-input / round-limit group](audit/phase2/review-context-20261115T091955.xml): 118 passed in 36.18 seconds, including 15 new cases. Complete sources and exact constraint spans reconstruct the unchanged original packet/PASS binding. The retained L2 fixture's native Gateway payload reserves 64,245 under the unchanged 65,536 ceiling; local construction made no network call and proves no larger-packet capacity or live acceptance.
- [Composer explicit-default contract group](audit/phase2/composer-contract-20261115T093043.xml): 45 passed in 5.89 seconds, including 21 new cases. Only explicit wire `output_schema_json=null` selects the Core schema; dynamic contracts remain nonempty and strict role IDs/permissions remain unchanged.
- [Current-node review / adaptive output group](audit/phase2/review-scope-cap-tests.xml): 123 passed in 31.80 seconds. The [read-only diagnostic](audit/phase2/review-scope-cap.md) preserves the exact failed L2 second-review packet and original limits; its 59,761 input reservation plus 5,775 output fits 65,536 without a network call or live-success claim.
- Retained live acceptance: L1 `20261115T012115Z-L1-2a34ad` passed strict checks in 103.621 seconds with one review revision and one Worker self-repair, without compaction or restart. L2 `20261115T012115Z-L2-8e64f5` failed in 96.416 seconds at review scope/context; L3 `20261115T012340Z-L3-17c603` failed in 447.913 seconds before team/Worker execution. Exact L3 missing-field attribution is unavailable. Later L2/L3 reruns also failed before review/restart; v6 live reruns failed as recorded below; v7 live failures are recorded below; v8 failures are recorded below; v9 failures are recorded below; v10 outcomes are recorded below; v11 outcomes are recorded below; v12 results are recorded below; final complex-task acceptance remains unmet.
- Two later retained runs share code snapshot `03ebc502773bc4fb67444efe1834000e52256441ad422739e17dc487d8d86f13`. L2 `20261115T013328Z-L2-fed968` failed in 80.711 seconds: 16 model/six tool calls, 54,597/9,558 input/output tokens, zero unknown attempts. Raw packet/native input reservation were 68,294/68,550, above 65,536; declared concurrency three also exceeded scenario cap two. L3 `20261115T013328Z-L3-4e61d3` failed in 151.834 seconds: 21 model/six tool calls, 91,162/22,775 tokens, zero unknown attempts. Composer eventually produced a legal seven-role/concurrency-two team. Its 15,776-byte research artifact yielded raw/native sizes 67,009/68,224; Worker use 38,989/70,000 left 31,011 insufficient for full review. Neither run reached `review.started` or a Core restart. No Reviewer verdict or complex success is claimed.
- [Full-suite v4](audit/phase2/frozen-tests-v4.xml): 781 passed, 6 failed, 1 warning in 290.98 seconds. The six legacy M4 Mock Planner criteria fixtures alone changed to executable `delivery_review`; [21 original-assertion regressions](audit/phase2/planner-legacy-fixture-regressions.xml) passed in 12.89 seconds. The failed full attempt remains retained. The [v5 manifest](audit/phase2/source-manifest-v5.json) contains 153 files; Ruff, formatting for 150 Python files and diff checks passed, and [v5 full pytest](audit/phase2/frozen-tests-v5.xml) / [console log](audit/phase2/frozen-tests-v5.log) recorded 820 passed, one Starlette/httpx deprecation warning in 219.96 seconds. Manifest hash: `a1f4646cb9acc810f3d35e8f9609a99ed553efd1ce4c2b3a0906d98c0333ed93`. V5 predates v6 changes.
- V6 focused local/Mock groups: [Composer operator cap](audit/phase2/composer-concurrency-20261115T094900.xml), 69 passed / 5.70 seconds; [Worker report guidance](audit/phase2/worker-report-guidance-tests.xml), 99 passed / 28.05 seconds. V6 full/live failed as recorded below; v7 results are recorded below; v8 outcomes are recorded below; v9 results are recorded below; V12 frozen checks are recorded; the v12 evidence record consolidates this round's partial state.
- The [v6 full suite](audit/phase2/frozen-tests-v6.xml) / [console log](audit/phase2/frozen-tests-v6.log) recorded 848 passed, one failed, one warning in 283.77 seconds. `test_actual_lifespan_schedules_parallel_dag_verifies_and_relays` in `test_m5_lifespan_execution.py` failed because Core conflated Gateway model-call concurrency one with Worker concurrency two, incorrectly reducing the team operator cap to one. V7 restores Worker concurrency two independently of Gateway admission; the original regression test remains unchanged. V7 full/live results are recorded below; v8 outcomes are recorded below; v9 results are recorded below; V12 frozen checks are recorded; the v12 evidence record consolidates this round's partial state.
- The v6 live snapshot `474d4e11edc773ade91419fc8668ec18b8a365937faa06d8f6e177bf36581c13` retains three further failures, all with zero final unknown attempts. L1 `20261115T014735Z-L1-b48614` failed in 122.096 seconds, 15 model/four tool calls, 67,356/11,709 input/output tokens: two NEEDS_REVISION verdicts after earlier self-repair exhausted the original Worker `max_repairs`, blocking further revision. L2 `20261115T014735Z-L2-cd59cb` failed in 84.678 seconds, 14 model/five tool calls, 48,105/8,295 tokens. Its 14,060-byte read-only delivery was outside v6's JSON-artifact guidance. Raw/native input were 60,371/60,425; Worker use 18,143/78,000 left 59,857, 568 short even for review input alone. L3 `20261115T014735Z-L3-4ce51e` failed in 165.77 seconds, 27 model/seven tool calls, 123,506/22,569 tokens: the 11,651-byte artifact produced raw/native sizes 65,637/66,278, while use 57,093/90,000 left 32,907. L2/L3 reached neither `review.started` nor a restart. These are retained failures; v7 guidance does not retroactively repair them or reset any budget.
- The [v7 full suite](audit/phase2/frozen-tests-v7.xml) / [console log](audit/phase2/frozen-tests-v7.log) passed 849 tests with one warning in 216.87 seconds. The [155-file manifest](audit/phase2/source-manifest-v7.json) is `58339132008a97956237ec38c2eb19eb7062f46b4ef2f5f946567ad52be26627`; Ruff and formatting for 152 Python files passed. This snapshot precedes v8 strict-delivery/scope/repair changes and cannot validate them.
- The v7 live snapshot `dabcf69804a9f43b30ba10902ec6e781cdc27077bf374ac9f51927593b710de1` retains three failures, all with zero final unknown attempts: L1 `20261115T015415Z-L1-d9dedd`, 98.792 seconds, 16 model/four tool calls, 57,113/7,877 input/output tokens, exhausted repair after output at the 2,048 cap truncated. L2 `20261115T015415Z-L2-b0e9f0`, 93.078 seconds, 19 model/six tool calls, 78,714/11,191 tokens, planned only two nodes despite the required three–four specialist Workers; its 14,141-byte artifact also hit review context limits. L3 `20261115T015415Z-L3-2732f0`, 99.483 seconds, 24 model/nine tool calls, 127,932/11,268 tokens, actually received independent NEEDS_REVISION, then exhausted its ten-step limit before revision could finish. None passed strict acceptance; complex compaction/restart continuity remains unestablished.
- The [native delivery contract group](audit/phase2/native-delivery-contract-20261115T151500.xml) passed 185 tests / 47.49 seconds. The latest [delivery/non-finite test file](audit/phase2/native-delivery-nonfinite-20261115T152200.xml) passed 36 tests / 5.97 seconds; this is overlapping local/Mock coverage, not a full suite or live success. V8–v12 full/live outcomes are recorded; this round's final state remains partial; see the v12 evidence record.
- The [v8 full suite](audit/phase2/frozen-tests-v8.xml) / [console log](audit/phase2/frozen-tests-v8.log) passed 886 tests with one warning in 283.46 seconds. Its [156-file manifest](audit/phase2/source-manifest-v8.json) is `92bb6067ff7bc1dadfa6aa9b5994089969603b53b3980a9a5601d0a140e6b9e3`; Ruff, formatting for 153 Python files and pip dependency checks passed. This snapshot predates v9 generated-plan/batch-budget guidance changes.
- The [v8 lossless-reference diagnostic](audit/phase2/reviewer-lossless-v8-diagnostic.json) reconstructs the unchanged 64,323-byte retained L2 packet, original packet hash and PASS binding. Numeric source labels remove only representation-added duplicate hash/length fields; every original receipt field and complete source block remains exactly recoverable. Native input reservation falls 64,870 → 64,346, saving 524 bytes of reservation for this fixture; output 1,190 brings total reservation to the unchanged 65,536 ceiling. The raw packet cap remains 65,536 bytes. The [124-case regression](audit/phase2/reviewer-lossless-v8-tests.xml) passed in 60.43 seconds. This is an offline construction, zero Provider requests, with no historical state change or new live verdict; no measured model-token/cost saving follows.
- The v8 live runs share code snapshot `8480f4f9c4f88a28d3d41047e5b1dbf90f424f9cc7892d0302c13758433c40ff`. L1 `20261115T020604Z-L1-dceb58` failed in 136.466 seconds, 23 model/14 tool calls, charged input/output 128,890/14,443 tokens. L2 `20261115T020604Z-L2-7f6370` failed in 99.597 seconds, 25 model/15 tool calls, charged 109,862/9,567 tokens. Each record retains one unresolved Router-notice attempt at stop; its reservation is respectively 10,555+4,096 and 10,290+4,096, not actual charged usage. Total actual usage and cost remain unknown. Both Workers received real NEEDS_REVISION, then repeatedly reread files and hit the original tool budget without revised delivery. L1 has two actual Worker archives (17/6 source messages); L2 has four (9/6/6/4). These observations establish Worker archiving, without a completed task or stopped-Core Router compaction/mid-execution restart. L3 `20261115T020604Z-L3-30acf0` failed in 103.132 seconds, 18 model/six tool calls, 84,067/12,506 tokens, unknown zero. Its approved research node required a file artifact while allowing only read tools; the actual file was absent and packet construction failed before Reviewer/context admission. No successor ran in L2/L3. The [independent continuity/live audit](audit/phase2/continuity-and-live-diagnosis-v8.md) does not infer a repeated-read cause from archives or claim a predecessor handoff failure/success where no successor executed. All three strict acceptances failed.
- [Offline benchmark v8](audit/phase2/benchmark-final-v8.json), `benchmark_79b287013615`: completed in 17.598702 seconds / zero Provider calls on the unchanged fixed dataset; no live quality or generalization claim.
- Real Gateway protocol probes through local workbuddy-api/wb-in, requested `deepseek-v4.1-flash`: the proxy-default run passed 11/11 small cases in 26.712 seconds; the [explicit `reasoning_effort=none` probe](audit/phase2/provider-probe-reasoning-none.json) passed 10/11 in 20.210 seconds, retaining the failed exact-text minimal dialogue check. The request parameter does not prove internal upstream reasoning behavior. Upstream identity remains proxy-reported and unverified; actual service capacity and price are unknown. Protocol checks do not establish task completion.
- Offline v2: six fixed domains/difficulties, 48 team proposals satisfying hard constraints, 12/12 obligations recovered by compaction plus source recall, governance 0/11 false allows and 0/4 false denials, retrieval 9/9 source-ID/hash checks. These are exploratory synthetic/local mechanism measurements with zero Provider calls.
- [Offline v4 rerun](audit/phase2/benchmark-final-v4.json): 6.3642 seconds, zero Provider calls, unchanged dataset and scoped mechanism metrics. Historical v1/v2 remain; no live quality or generalization gain follows.
- Historical M6 verification passed 372 tests; one bounded live continuation succeeded after five retained failures. M7 backend hardening passed 488 tests. Their reports retain exact scope; they are not current Phase 2 full-suite or packaged-desktop acceptance.

Detailed evidence and reproducible commands are in the [Phase 2 report](PHASE2_AGENT_INTELLIGENCE_REPORT.md),
[experiments](PHASE2_EXPERIMENTS.md) and [execution-chain audit](audit/phase2/execution-chain-audit.md).
The final integration owner records the frozen result in `docs/audit/phase2/verification.json`.

## Required Phase 2 acceptance

1. Preserve one persistent user-facing Router/conversation and private native Workers.
2. Require valid original-request/constraint sources, bounded schemas and explicit authenticated plan/team/replacement approval. Default Review remains enabled.
3. Admit every model attempt through the durable Gateway token/concurrency ledger; retain unknown usage/effects and never infer remote cancellation from a timeout.
4. Enforce tool policy, current grants, exact approvals, durable intents, receipts and execution-mode provenance. Mock records cannot prove live completion.
5. Bind fresh review verdicts to original criteria, actual artifacts/receipts and the latest delivery. `NEEDS_REVISION` must contain actionable goals and stay within cumulative budgets and round limits.
6. Recheck approval history/current bindings, artifact hashes, tool results and applicable fixed semantic verifiers at completion. A model PASS or JSON syntax alone does not establish domain correctness.
7. Record L1/L2/L3 live metrics and independently inspect artifacts. Demonstrate observed failure/repair, review revision, actual archive/readback and Core restart boundaries rather than inferring them from records or directory names.
8. Run checks against the final source snapshot and preserve failed attempts and historical evidence. A complex live task must satisfy the intended continuity condition before Phase 2 is declared complete.
9. Permit artifact replacement only with explicitly approved `file_revise`, the same current Worker's latest successful receipt chain, unchanged file hash and current workspace grant. Recheck the chain and latest bytes at review/completion; retain immutable prior versions. Existing create-only contracts gain no permission.
10. Keep parser/fixed-suite revision feedback distinguishable from LLM review or PASS. Bind semantic failure to the actual suite/report/source/receipt and latest delivery, require explicit revision authority and remaining original budgets with no unresolved actions/calls, and rerun the real suite after repair. Charge settled truncated reviews before bounded retry; never reset cumulative budgets or replay unknown remote outcomes. Lease renewal must retain fencing, current authority and the original task deadline.

## Known limits

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

The [v9 full suite](audit/phase2/frozen-tests-v9.xml) / [log](audit/phase2/frozen-tests-v9.log)
passed 895 tests with one warning in 237.83 seconds. Its [157-file manifest](audit/phase2/source-manifest-v9.json)
is `9a33b199404b777428ff0f0e1a1c1da356ad93b23b4395565fc0d211b3d9203a`;
Ruff, formatting for 154 Python files and pip checks passed. This predates v10.
[Offline benchmark v9](audit/phase2/benchmark-final-v9.json), `benchmark_c4387a6e26ac`,
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
[snapshot-v2](audit/phase2/live-runs/snapshot-v2/index.json), with original run records and
diagnostic-snapshot-v1 preserved. Publication makes no Provider call and does not
promote failed or unaccepted tasks. Historical retry totals can overlap: the old
`worker_runs.repairs` includes review revisions. V10 counts actual self-repair
events separately and marks future records `retry_accounting=disjoint_event_counts_v2`;
[27 focused regressions](audit/phase2/retry-accounting-v10-fixed.xml) pass, while original
run counters remain unchanged. V10 full/live outcomes are recorded below; v12 frozen results are recorded; the v12 evidence record consolidates this round's partial state.
Core capacity feedback is not independent model review or PASS.

V10 adds labelled Core capacity diagnostics (`deterministic_review_capacity` /
`core_review_capacity`), never a model verdict or PASS. Original packet/source/hash
and 65,536 raw/context caps remain. Same-Worker revision requires approved
`file_revise`, current receipts/grants, remaining original repair/step/tool/token/
cost/time/round limits and no unsettled actions/model attempts. Arithmetic floors
only check whether a complete revision plus real independent review might fit;
they do not remove required evidence or prove compression. Inability to fit is BLOCKED.
The [corrected capacity group](audit/phase2/reviewer-capacity-v10-fixed-tests.xml) passed
143 cases (JUnit 45.843 seconds); the [first 143-case attempt](audit/phase2/reviewer-capacity-v10-tests.xml)
retains one failure. These are overlapping local/Mock tests.
The driver waits read-only for settlement for at most 30 seconds before stopping;
it never resends, cancels or settles unknown remote calls itself.
[Settlement regressions](audit/phase2/driver-settlement-v10.xml) passed 27 cases
(JUnit 4.263 seconds). V10 records use disjoint event-count retry accounting;
historical counters and outcomes remain unchanged.

The [v10 full suite](audit/phase2/frozen-tests-v10.xml) / [log](audit/phase2/frozen-tests-v10.log)
passed 915 tests, one warning, in 279.54 seconds. The [158-file manifest](audit/phase2/source-manifest-v10.json)
is `814d0dcc6496f65d01ededad38a1e45a8d0f9848739467f7c36b985f3c318415`;
Ruff, formatting for 149 Python files, pip and diff checks passed. This precedes v11.
[Benchmark v10](audit/phase2/benchmark-final-v10.json), `benchmark_4f7e4f5325ba`,
completed in 7.5434305 seconds with zero Provider calls on the unchanged fixed dataset.
The [read-only preservation audit](audit/phase2/preservation-v10.json) retains 355 byte-identical
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

[Snapshot-v3](audit/phase2/live-runs/snapshot-v3/index.json) publishes 42 finished driver
records and preserves snapshot-v2/v1 plus original run records. Publication made
no Provider request and does not promote failed tasks. Historically at v10, v11 was in progress:
bounded correction after a rejected local receipt read and byte/availability hints
have [79 focused passing cases](audit/phase2/receipt-read-repair-v11-tests.xml);
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
authority are granted. The [79-case focused group](audit/phase2/receipt-read-repair-v11-tests.xml)
passed (JUnit 19.062 seconds); [legacy compatibility](audit/phase2/receipt-repair-legacy-v11.xml)
passed 35 (JUnit 9.704 seconds). Generated Planner proposals now receive and
enforce the trusted operator total-token ceiling before persistence; an over-cap
reply uses the original one repair and repeated violations stop without a plan.
Default/manual/historical contracts remain unchanged. [Budget tests](audit/phase2/planner-operator-budget-v11.xml)
passed 16 (JUnit 1.203 seconds); [compatibility tests](audit/phase2/planner-operator-compat-v11.xml)
passed 51 (JUnit 17.397 seconds). These focused groups overlap the full suite.

The [v11 full suite](audit/phase2/frozen-tests-v11.xml) / [log](audit/phase2/frozen-tests-v11.log)
passed 941 tests, one warning, in 199.39 seconds. Its [160-file manifest](audit/phase2/source-manifest-v11.json)
is `eec443f0aad4687e336a8b9ca3629218ebce4417b8096dabb8eaa44845dc7822`;
Ruff, formatting for 157 Python files, pip and diff passed. This predates v12.
[Benchmark v11](audit/phase2/benchmark-final-v11.json), `benchmark_f23819557132`,
completed in 4.3837569 seconds with zero Provider calls on the unchanged fixed dataset.
[Preservation v11](audit/phase2/preservation-v11.json) still records 355 unchanged Phase 1
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

[Snapshot-v4](audit/phase2/live-runs/snapshot-v4/index.json) publishes all 45 finished driver
records while preserving v3/v2/v1 and original run records. V12 is correcting the
serial-reservation precheck; [145 focused cases](audit/phase2/reviewer-serial-capacity-v12-tests.xml)
pass (JUnit 74.570 seconds) with actual Gateway admission over offline MockTransport,
not a live Provider result. First whole-tail archiving must already include the
readback tool in that request and its exact reservation; [87 legacy cases](audit/phase2/first-archive-legacy-v12.xml)
pass (JUnit 65.280 seconds). V12 new-path/full/live results are recorded below.
Neither change expands original authority, budgets or context limits.

V12 corrects the serial-capacity precheck to compare the maximum individual
reservation floor with the remaining original allowance; each subsequent complete
request still requires actual Gateway admission and charges settled usage.
This corrects the conservative v11 rejection without increasing any budget.
First whole-tail Worker archiving now declares source readback in that same
request when steps >0 and the effective per-request allowance is at least 8192,
including the tool's full bytes in original admission. It never enables source
omission without recoverability. The [12-case corrected first-archive group](audit/phase2/first-archive-v12-fixed-tests.xml)
passed (JUnit 1.848 seconds); the [initial 12-case attempt](audit/phase2/first-archive-v12-tests.xml)
retains one failure. The earlier 87 legacy/145 serial-capacity groups are overlapping
local/Mock evidence. No first whole-tail fallback or readback was triggered in
these three new live runs, so that new mechanism remains locally verified only.

The [v12 frozen full suite](audit/phase2/frozen-tests-v12.xml) / [log](audit/phase2/frozen-tests-v12.log)
passed 955 tests, one warning, in 251.43 seconds. The [162-file manifest](audit/phase2/source-manifest-v12.json)
is `ccc5efe69b4493dd77093b4a62f6f6851f09c63ca1f2806298f2e59e80d7f8d7`;
Ruff, formatting for 159 Python files, pip and diff checks passed.
[Benchmark v12](audit/phase2/benchmark-final-v12.json), `benchmark_37ee6599ac54`,
completed in 4.6835418 seconds with zero Provider calls on the same fixed synthetic
dataset. [Preservation v12](audit/phase2/preservation-v12.json) retains 355 byte-identical
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

The [read-only Reviewer-claim diagnostic](audit/phase2/reviewer-claim-diagnostic-v12.json)
checks an incorrect revision goal from this L3 review: `ast.parse(mode="exec")`
accepts the top-level Return node, and the actual bounded interpreter evaluates
`return 0` to result 0 in two steps. Python bytecode compilation rules are not
that interpreter's contract. This diagnostic made zero Provider calls and changes
no run outcome. Fresh-context same-model review can issue incorrect revision goals;
fixed native numeric semantic gates independently verify applicable artifacts.
A review verdict alone does not guarantee domain correctness.

[Snapshot-v5](audit/phase2/live-runs/snapshot-v5/index.json) publishes all 48 finished driver
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

[Snapshot-v6](audit/phase2/live-runs/snapshot-v6/index.json) publishes 50 driver records and
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






- Router/Planner repair is bounded once; autonomous plans cap at eight nodes/sixteen edges. General remaining-DAG replanning and arbitrary hierarchical Agent expansion are incomplete.
- Goal updates/new DAG proposals accept only proposed/awaiting-approval Quests. Team replacement preserves the current DAG; it does not implement autonomous replanning of a running DAG.
- Cumulative Worker/review token allowance is distinct from the per-request context/output bound; both remain enforced, along with approved node/team limits. Bounded Quest identity recall is separate from ordinary fact/constraint recall; true identity omissions/cursors and multiple candidates still require clarification.
- Dynamic team changes retain completed nodes and require approval. Performance snapshots are bounded, sourced and sample-counted; the current five-observation eligibility threshold does not establish statistical sufficiency or automatically learned general capability.
- Independent review uses the same approved underlying model in a fresh context. It evaluates the current node's objective, criteria and approved role; full original requirements still govern nodes assigned final deliverables. Independent model diversity is not implemented. `FORGE_REVIEW_MAX_ROUNDS` defaults to two and accepts integers one through four; the live harness selects three within its scenario's three-round cap, without changing historical runs. Output fits 8192, role/model caps, remaining context and the original cumulative token allowance, with adaptive floor `min(1024, original output cap)`. Inability to fit that floor or original cost authority fails closed; lower original caps remain valid. Native input reservation uses the actual serialized Gateway schema/reasoning payload and matches admission. Labelled Core JSON-parser revision feedback supplies no LLM review or PASS. Legacy/default delivery compatibility requires only `summary`, while `delivery_review` requires all eight fields.
- A lossless review envelope can remove duplicate escaping/schema/constraint metadata while preserving full source text, exact source spans and the original packet/PASS hash. It changes representation, not context or evidence limits. Oversized packets, model-context overflow and exhausted budgets still block. Published runtime settings use an explicit whitelist including effective model token/concurrency limits; missing legacy values remain null.
- Router/Worker archives have bounded highlights, preserve original-source recovery and signal omissions. Active-Quest snapshots, indexing batches, evidence/receipt pages and model windows remain finite. Long histories can require multiple reads or fail closed.
- Constraint literal extraction and potential-conflict dimensions are bounded heuristics. User/model provenance does not prove domain facts or grant action approval. Confirmation/revocation have Core operations but no new public interface in this sprint.
- Recall uses bounded lexical terms, bilingual aliases and source/Quest/time/type filters. Learned semantic retrieval, general relevance and arbitrary-scale memory are not established.
- Worker execution tools are fixed: `python_compile` checks syntax, `numeric_ast` runs a bounded pure numeric subset and `shell_inspect` permits `python_version`. Arbitrary shell, unrestricted Python, arbitrary test runners and a general code patch tool are absent.
- `file_write` remains exclusive-create. Explicitly approved `file_revise` only replaces the current Worker's own latest produced artifact with receipt-chain/hash checks and immutable version history; user files, predecessor artifacts, stale versions and uncertain outcomes are excluded. General repository patch/test loops remain incomplete, and all revision must preserve the original approved criteria.
- Worker leases renew only between settled steps for a valid current owner, bounded by the task deadline. Expired/stale claims, changed plans, competing owners and paused/cancelled assignments cannot be revived; calls remain capped by the current lease/deadline and unknown outcomes retain reservations.
- Fixed semantic-suite feedback is deterministic Core validation, not independent LLM review. Persisted feedback before callback can recover once after restart, including semantic-only nodes, only after current result/semantic/source/receipt, scope/grant and original-budget checks with no unsettled actions or model attempts. It issues no model-review request; stale sources, revoked authority, exhausted budgets, unknown actions and callback errors remain blocked. This does not establish arbitrary in-flight recovery.
- ACP is a typed projection of durable events with task identities and fenced ownership; exhaustive payload contracts remain partial. Unknown external effects and unresolved model attempts may conservatively block progress until trusted settlement is possible.
- Team/context/governance benchmarks use small deterministic cases, synthetic profiles, fixed extractive summaries and byte budgets. Same-budget real task quality, measured token/cost savings, independent LLM governance quality and statistical generalization remain open.
- SQLite migration/restart tests do not establish production durability under every failure mode. Packaged Windows Alpha, full native lifecycle, credential vault and backup/restore integration remain outside this sprint's completed evidence.

## Historical verification

Phase 1 reports retain their original results and limitations. In particular, the
[M3 report](PHASE1_MILESTONE3_REPORT.md) records 140 local/Mock tests at that stage;
later bounded real-provider observations are in M5/M6 and the Phase 2 protocol probe.
Earlier native/UI checks and dependency audits do not establish final packaging or
cross-platform safety. Never describe a proposed Worker as a running Worker.
