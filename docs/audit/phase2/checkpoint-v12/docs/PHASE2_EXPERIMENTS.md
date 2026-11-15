# Phase 2 — Experiments and Reproduction

Updated: 2026-11-15 (Asia/Shanghai). Real Provider protocols, local/Mock execution,
synthetic algorithm measurements and pending acceptance are reported separately.
Failures and earlier diagnostics remain preserved; a later success does not erase them.

## Recorded runs

| Record | Mode | Result | Scope |
| --- | --- | --- | --- |
| `20261114T233125Z-d07064` | Live protocol, proxy default | 11/11 cases, 26.712 seconds | `reasoning_effort` omitted; Gateway → local proxy → self-reported model; no complex task result |
| [`20261115T005858Z-7ae2c8`](audit/phase2/provider-probe-reasoning-none.json) | Live protocol, explicit `reasoning_effort=none` | 10/11 cases, 20.210 seconds | Exact-text minimal dialogue check failed and response hash retained; request parameter does not prove upstream internal reasoning behavior |
| `20261114T235101Z-L1-3318e2` | Live L1 diagnostic | Failed, 146.716 seconds, `composer_did_not_propose_team` | Provider timeout; two unknown attempts retain reservations; no completed artifact claim |
| `benchmark_c51c77ee97c8` / [offline v2](audit/phase2/benchmarks/offline-intelligence-v2.json) | Offline synthetic plus actual local tools | Complete, 9.48 seconds, zero Provider calls | Six fixed team/context scenarios, governance matrix, source retrieval |
| [Retrieval v2](audit/phase2/retrieval-evaluation-v2.json) | Synthetic, persisted/reopened Core | 9/9 source-ID/hash cases, 1.369 seconds | Source identity and provenance; no domain correctness measurement |
| L1 `20261115T012115Z-L1-2a34ad` | Live, strict acceptance | Passed, 103.621 seconds | 19 model / 6 tool calls; 87,788 input / 10,429 output tokens; unknown attempts 0; NEEDS_REVISION → PASS, one review revision and one self-repair; no archive or restart |
| L2 `20261115T012115Z-L2-8e64f5` | Live, strict acceptance | Failed, 96.416 seconds | 19 model / 6 tool calls; 92,916 input / 10,436 output tokens; unknown attempts 0; intermediate read-only research was asked for a downstream artifact, then context limit blocked review; no restart |
| L3 `20261115T012340Z-L3-17c603` | Live, strict acceptance | Failed, 447.913 seconds | Valid seven-node plan, five Composer notices / ten settled Composer calls, no team or Worker; unavailable model responses prevent identifying the exact missing contract field |
| `benchmark_79b287013615` / [fixed v8 rerun](audit/phase2/benchmark-final-v8.json) | Offline synthetic / local tools | Complete, 17.598702 seconds, zero Provider calls | Unchanged fixed dataset, retained prior reruns; no live/generalization result |
| `benchmark_609a0d95052e` / [fixed v4 rerun](audit/phase2/benchmark-final-v4.json) | Offline synthetic / local tools | Complete, 6.3642 seconds, zero Provider calls | Same fixed dataset and scoped mechanism metrics; historical v1/v2 retained |
| L2 `20261115T013328Z-L2-fed968` | Live, strict acceptance | Failed, 80.711 seconds | 16 model/six tool calls; 54,597/9,558 input/output, unknown zero; raw/native 68,294/68,550 above 65,536; declared concurrency three above cap two; no review or restart |
| L3 `20261115T013328Z-L3-4e61d3` | Live, strict acceptance | Failed, 151.834 seconds | 21 model/six tool calls; 91,162/22,775 input/output, unknown zero; legal seven-role/concurrency-two team; raw/native 67,009/68,224 and only 31,011 cumulative tokens remain; no review or restart |
| L1 `20261115T014735Z-L1-b48614` | Live, strict acceptance | Failed, 122.096 seconds | 15 model/four tool calls; 67,356/11,709 tokens, unknown zero; two NEEDS_REVISION verdicts after self-repair exhaust original max_repairs |
| L2 `20261115T014735Z-L2-cd59cb` | Live, strict acceptance | Failed, 84.678 seconds | 14 model/five tool calls; 48,105/8,295 tokens, unknown zero; native review input 60,425 exceeds cumulative remainder 59,857; no review/restart |
| L3 `20261115T014735Z-L3-4ce51e` | Live, strict acceptance | Failed, 165.77 seconds | 27 model/seven tool calls; 123,506/22,569 tokens, unknown zero; raw/native 65,637/66,278 and only 32,907 cumulative tokens remain; no review/restart |
| L1 `20261115T015415Z-L1-d9dedd` | Live, strict acceptance | Failed, 98.792 seconds | 16 model/four tool calls; 57,113/7,877 tokens, unknown zero; 2048 output truncation exhausted repair |
| L2 `20261115T015415Z-L2-b0e9f0` | Live, strict acceptance | Failed, 93.078 seconds | 19 model/six tool calls; 78,714/11,191 tokens, unknown zero; only two nodes violate specialist count, 14,141-byte artifact/context limit |
| L3 `20261115T015415Z-L3-2732f0` | Live, strict acceptance | Failed, 99.483 seconds | 24 model/nine tool calls; 127,932/11,268 tokens, unknown zero; actual NEEDS_REVISION then max_steps=10 exhaustion, revision incomplete |
| L1 `20261115T020604Z-L1-dceb58` | Live, strict acceptance | Failed, 136.466 seconds | 23 model/14 tool calls, charged 128,890/14,443 input/output tokens, unknown one Router notice; NEEDS_REVISION then tool budget, two actual Worker archives, no Core restart |
| L2 `20261115T020604Z-L2-7f6370` | Live, strict acceptance | Failed, 99.597 seconds | 25 model/15 tool calls, charged 109,862/9,567 tokens, unknown one Router notice; NEEDS_REVISION then tool budget, four actual Worker archives, no successor/restart |
| L3 `20261115T020604Z-L3-30acf0` | Live, strict acceptance | Failed, 103.132 seconds | 18 model/six tool calls, 84,067/12,506 tokens, unknown zero; required file artifact with read-only node tools, actual file absent, no review/successor/restart |
| L2 `20261115T021801Z-L2-b11011` | Live, strict acceptance | Failed, 80.141 seconds | 17 model/six tool calls, 65,607/9,348 charged input/output, unknown zero; 15,235-byte artifact, 68,207-byte raw packet, no review/successor/restart |
| L3 `20261115T021801Z-L3-290b7f` | Live, strict acceptance | Failed, 97.584 seconds | 18 model/six tool calls, 71,908/14,522 charged input/output, one unresolved Router notice; 10,788-byte artifact, 68,967-byte raw packet, no review/successor/restart |
| `benchmark_c4387a6e26ac` / [fixed v9 rerun](audit/phase2/benchmark-final-v9.json) | Offline synthetic / local tools | Complete, 5.9372256 seconds, zero Provider calls | Unchanged fixed dataset and scoped mechanism metrics |
| L1 `20261115T061513Z-L1-c94230` | Live, strict acceptance | Failed, 59.801 seconds | 11 model/five tool calls, 32,806/3,291 input/output, unknown zero; plan ID used as receipt ID, blocked before review |
| L2 `20261115T061513Z-L2-62b044` | Live, strict acceptance | Failed, 80.284 seconds | 17 model/nine tool calls, 75,382/6,207 tokens, unknown zero; actual research PASS, mid-execution restart/archive/readback and resumed architect; successor hit 8192 receipt cap |
| L3 `20261115T061513Z-L3-8fdef4` | Live, strict acceptance | Failed, 120.099 seconds | 25 model/eight tool calls, 134,215/15,831 tokens, final unknown zero; actual NEEDS_REVISION/revision, 70,657-byte new packet and insufficient capacity-repair budget, Core BLOCKED |
| `benchmark_4f7e4f5325ba` / [fixed v10 rerun](audit/phase2/benchmark-final-v10.json) | Offline synthetic / local tools | Complete, 7.5434305 seconds, zero Provider calls | Unchanged fixed dataset and scoped mechanism metrics |
| L1 `20261115T062445Z-L1-e6498c` | Live, strict acceptance | Passed, 111.975 seconds | 19 model/five tool calls, 102,134/12,157 input/output, unknown zero; two real NEEDS_REVISION → revision → PASS; no archive/restart |
| L2 `20261115T062445Z-L2-b7e197` | Live, strict acceptance | Failed, 177.094 seconds | 37 model/16 tool calls, 199,677/25,402 tokens, unknown zero; research/analysis revision → PASS, report exists, preserved mid-Core restart/Router readback and successor continuation; final Worker context_budget |
| L3 `20261115T062445Z-L3-ba6bb7` | Live, strict acceptance | Failed, 95.121 seconds | 17 model/six tool calls, 73,147/13,283 tokens, unknown zero; raw/native 66,309/66,394, old serial-capacity precheck falsely refused revision; no independent review/restart |
| `benchmark_f23819557132` / [fixed v11 rerun](audit/phase2/benchmark-final-v11.json) | Offline synthetic / local tools | Complete, 4.3837569 seconds, zero Provider calls | Unchanged fixed dataset and scoped mechanism metrics |
| L1 `20261115T063556Z-L1-1e03cf` | Live, strict acceptance | Passed, 102.934 seconds | 16 model/four tool calls, 66,279/8,829 input/output, unknown zero; real review revision → PASS, disjoint review one/self zero, no archive/restart |
| L2 `20261115T063556Z-L2-a3ad74` | Live, strict acceptance | Failed, 109.832 seconds | 23 model/nine tool calls, 104,306/10,744 tokens, unknown zero; source-analysis PASS, preserved restart/Router readback, draft artifact; 37,061 remaining cannot admit complete review |
| L3 `20261115T063556Z-L3-14d6f5` | Live, strict acceptance | Failed, 115.371 seconds | 21 model/eight tool calls, 105,632/15,238 tokens, unknown zero; real revision enlarged artifact/delivery, raw/native 68,149/68,412; corrected capacity guard blocks at 40,238 remainder |
| `benchmark_37ee6599ac54` / [fixed v12 rerun](audit/phase2/benchmark-final-v12.json) | Offline synthetic / local tools | Complete, 4.6835418 seconds, zero Provider calls | Same fixed mechanism dataset and limitations |
| L2 `20261115T064204Z-L2-3f89c4` | Live, strict acceptance; same v12 source | Failed, 115.488 seconds | 29 model/nine tool calls, 154,973/15,583 tokens, unknown zero; two review revisions, raw/native 67,630/67,645, 27,358 remaining below fixed review floor; no restart |
| L3 `20261115T064204Z-L3-1288e8` | Live, strict acceptance; same v12 source | Failed, 111.105 seconds | 21 model/nine tool calls, 104,171/14,090 tokens, unknown zero; research PASS, actual restart/Router readback and math continuation, then 40,038 remaining <39,871 input+1,024 verdict |
| Final publication | Allowlisted historical summaries | 50 records in snapshot-v6 | All failed/unknown records retained; aggregate unknown count and total cost remain unknown |

The historical v8 driver records report retry totals L1 17, L2 two and L3 one.
Their then-current `worker_self_repairs` was derived from `repairs`, which also
includes review revisions, so the categories and totals can overlap. They report
one in that repair counter in every run, one review revision in L1/L2 and fifteen
background-notice retries in L1. Provider HTTP retries are zero in all three.
These categories stay separate from the unresolved Router-notice reservations.

The protocol record is stored at
`.local/phase2/provider/20261114T233125Z-d07064/report.json`. Provider: `wb-local`;
requested model: `deepseek-v4.1-flash`; route: local workbuddy-api/wb-in via
`http://127.0.0.1:8788/v1`. Upstream identity remains
`unverified_proxy_reported_identity`. Eleven discovered IDs included the target.
Token admission ceiling 300,000, concurrency two, context operator ceiling 65,536
and output probe ceiling 1,024 are configured bounds, not service maxima or usage.
Actual maximum context/output, rate limit and cost are unknown.

The protocol ledger contains 11 settled attempts reporting 5,444 input and 637 output
tokens. These are usage measurements separate from the reservation ceiling. The L1
diagnostic retains its original `run.json` under `.local/phase2/runs/`; failed runs
remain part of the denominator when final live results are summarized.

The 11 cases cover minimal dialogue, three Router-schema responses, three tool-call
responses, bounded-context recall, two simultaneous requests and streaming. A
returned tool-call structure is protocol evidence; it is not by itself evidence that
the requested tool action executed. Proxy response, returned model response and
the explicit case check remain separate observations.

The separate explicit-`none` probe is retained at
`.local/phase2/provider/20261115T005858Z-7ae2c8/report.json`. Its three Router-schema,
three tool-call, bounded recall, two concurrent-call and streaming checks passed;
the failed minimal dialogue stays in the 11-case denominator. Both probes have
unverified proxy-reported upstream identity. They are distinct configurations and
neither result is a complex task acceptance or proof of internal reasoning behavior.

All three retained 01:21/01:23 live runs used source snapshot
`c5e1674f38345a24a26aaf37473ebad97ed67f41fe445ef6f14d7a3c375c9134`,
explicit reasoning none, three review rounds, a 150-second Worker lease, a
120-second Provider timeout and zero HTTP retries. Costs remain unknown. L2's
final settled ledger supersedes its early boundary label about unsettled calls.
Neither its missing final report nor a restart is inferred from intermediate work.
The L3 null/default-schema code contradiction is separately reproduced and repaired;
without saved model responses it cannot identify the exact bad role/field in that run.

## Fixed offline benchmark

The later [v4 rerun](audit/phase2/benchmark-final-v4.json) completed in 6.3642 seconds
with zero Provider calls using the same dataset hash below. It retains the same
48 hard-contract passes, 12/12 source-recalled obligations, governance 0/11 false
allows and 0/4 false denials, and 9/9 retrieval source checks. These remain fixed,
exploratory mechanism results with the limitations below.

Dataset SHA-256: `99cdfeeae6df2635f03c768ec7290230452e31e15ae65bee74d885aaf1ef8cec`.
The data are fixed, deterministic and exploratory. A separate hard-constraint scorer
checks proposals; no model call contributes an answer. Earlier
[v1](audit/phase2/benchmarks/offline-intelligence-v1.json) is retained as a diagnostic.

### Capability-aware team composition

Six difficulty/domain scenarios compare eight variants under the same synthetic
model profiles, capability targets and resource limits. All 48 proposals passed
the independent contract checks. These metrics describe selected model identities,
not task quality, real token usage or cost.

| Variant | Scenarios | Contract pass rate | Specialist-target fraction |
| --- | --- | --- | --- |
| Single model | 6 | 1.00 | 0.125 |
| Static team | 6 | 1.00 | 0.00 |
| Greedy selection | 6 | 1.00 | 1.00 |
| Capability coverage | 6 | 1.00 | 1.00 |
| Complementarity | 6 | 1.00 | 1.00 |
| Dynamic recomposition | 6 | 1.00 | 0.75 |
| No coordination penalty | 6 | 1.00 | 1.00 |
| No confidence weighting | 6 | 1.00 | 1.00 |

No-confidence and no-coordination did not change this proxy on these cases; these
results do not establish their benefit. Completed-node/history preservation is
covered by separate service regression tests. Real selection quality, latency,
coordination burden and adaptive task success require live comparison.

### Task-centric context compression

Six scenarios each contain two original obligations. Every variant receives a
6,144 serialized UTF-8 byte input ceiling. Actual ContextService archives are
created, the database is reopened, and source IDs/hashes are independently checked.

| Variant | Admitted cases | Obligations retained | Obligations with checked sources |
| --- | --- | --- | --- |
| Full context | 0/6 | 0/12 admitted | 0/12 admitted |
| Recency truncation | 6/6 | 0/12 | 0/12 |
| Conventional summary | 6/6 | 6/12 | 0/12 |
| Task-centric | 6/6 | 6/12 | 6/12 |
| Task-centric + source recall | 6/6 | 12/12 | 12/12 |

Full context exceeds the shared input ceiling and is refused; the original text
still contains its obligations. Conventional summary is a fixed extractive baseline,
not a generated model summary. Byte admission is not actual provider token savings.
This result measures retained source obligations, not downstream model compliance.

### Evidence-driven governance

The 15-case matrix uses the actual ToolEngine and local file tools with a clearly
labelled Mock Worker fixture. Three execution requests produced two unique receipts,
including receipt replay. Eleven expected denials and four expected allows matched;
false-allow rate 0/11 and false-deny rate 0/4. Cases cover missing/revoked grants,
parameter substitution, protected/outside paths, exact R2 approvals and a forged
receipt rejected by NodeVerifier/CompletionGate. R2 cases stop at intent preparation;
no external requests execute.

The model-claim-only counterfactual permits 11/11 expected denials; removing binding
permits 4/11; removing revocation rechecks permits 2/11. These ablations simulate
omitted checks and never authorize an action. Actual receipt verification and fixed
independent verifier/evidence gate paths are exercised. Independent LLM Reviewer
quality and a four-way same-budget live governance comparison remain unmeasured.

### Source retrieval

Dataset: `phase2-retrieval-v1`;
SHA-256: `6d4dd52492b4bb9e55db3dcbff5f5bd49ddfa260665504356097eeede92a533b`.
Nine fixed cases check bilingual budget/retention/deadline terms, language, file path,
decision, Quest identity, Quest-associated constraint and time exclusion. All expected
source identities and independently recomputed original-row hashes match after
storage reopen. Limited bilingual aliases extend lexical retrieval; learned semantic
or embedding recall, held-out generalization and task truth are not measured.

## Local regression records

| Test group | Result | Record |
| --- | --- | --- |
| Router/Planner/constraints/context/memory | 53 passed, 18.36 seconds | [Combined JUnit](audit/phase2/sprint-bf-integrated-tests.xml) |
| Live driver offline checks | 5 passed, 0.57 seconds | [Driver JUnit](audit/phase2/driver-tests.xml) |
| Team/registry/recomposition/benchmark provenance | 42 passed, 14.61 seconds | [Sprint C/I](audit/phase2/sprint-ci.md) |
| Worker/review/Gateway/Dispatcher/tools/receipts/fixed verifiers | 128 passed, 46.34 seconds | [D/E/G report](audit/phase2/sprint-deg.md), [JUnit](audit/phase2/worker-review-final-tests.xml) |
| Owned artifact revision, parser feedback and bounded Reviewer repair | 153 passed, 40.63 seconds | [D/E/G report](audit/phase2/sprint-deg.md), [JUnit](audit/phase2/worker-revision-final-tests.xml) |
| Worker lease/deadline/fencing and timeout recovery | 90 passed, 32.30 seconds | [JUnit](audit/phase2/worker-lease-recovery-tests.xml) |
| Committed revision verdict/requeue restart recovery | 28 passed, 13.95 seconds | [JUnit](audit/phase2/worker-review-restart-tests.xml) |
| Fixed-suite semantic failure → governed revision → re-verification | 119 passed, 44.14 seconds, including 21 new cases | [Sprint C/I](audit/phase2/sprint-ci.md); actual local numeric subprocesses with scripted model fixtures, no Provider calls |
| Persisted semantic-feedback crash recovery | 74 passed, 34.65 seconds; semantic test file now has 30 cases | [JUnit](audit/phase2/semantic-recovery-20261115T090801.xml); idempotent recovery after reopen without model review, with current source/authority/budget guards |
| Lossless Reviewer input and exact review-round limits | 118 passed, 36.18 seconds, including 15 new cases | [JUnit](audit/phase2/review-context-20261115T091955.xml); original packet/hash unchanged, full source/constraint reconstruction and local receipt bindings |
| Provider configuration/timeout and Router/Planner source binding | 42 passed, 4.56 seconds | [Focused report](audit/phase2/provider-source-regressions.md), [JUnit](audit/phase2/provider-source-tests.xml) |
| Cumulative allowance and existing team contracts | 40 passed, 3.61 seconds | [Report](audit/phase2/cumulative-budget-and-quest-identity.md), [JUnit](audit/phase2/cumulative-budget-tests.xml) |
| Quest identity, context/memory, Router/Planner and offline probe | 67 passed, 32.53 seconds | [Report](audit/phase2/cumulative-budget-and-quest-identity.md), [JUnit](audit/phase2/quest-identity-probe-tests.xml) |
| Composer explicit Core schema selection | 45 passed, 5.89 seconds, including 21 new cases | [JUnit](audit/phase2/composer-contract-20261115T093043.xml); explicit wire null only, with strict dynamic contracts and role IDs |
| Review node scope and adaptive verdict capacity | 123 passed, 31.80 seconds | [Report](audit/phase2/review-scope-cap.md), [JUnit](audit/phase2/review-scope-cap-tests.xml); current-node requirements and exact native Gateway accounting |
| Full-suite v4 diagnostic | 781 passed, 6 failed, 1 warning, 290.98 seconds | [JUnit](audit/phase2/frozen-tests-v4.xml); failed legacy M4 criteria fixtures retained |
| Legacy Planner fixture correction | 21 passed, 12.89 seconds | [JUnit](audit/phase2/planner-legacy-fixture-regressions.xml); fixture criteria only, original assertions retained |
| Full backend suite v5 | 820 passed, 1 warning, 219.96 seconds | [JUnit](audit/phase2/frozen-tests-v5.xml), [console log](audit/phase2/frozen-tests-v5.log); predates v6 |
| Composer operator cap | 69 passed, 5.70 seconds | [JUnit](audit/phase2/composer-concurrency-20261115T094900.xml); new/replacement proposals and approval; local/Mock |
| Worker report guidance | 99 passed, 28.05 seconds | [JUnit](audit/phase2/worker-report-guidance-tests.xml); complete requirements retained; local/Mock |
| Full backend suite v6 | 848 passed, 1 failed, 1 warning, 283.77 seconds | [JUnit](audit/phase2/frozen-tests-v6.xml), [console log](audit/phase2/frozen-tests-v6.log); Worker/Gateway concurrency conflation; original actual-lifespan regression retained |
| Full backend suite v7 | 849 passed, 1 warning, 216.87 seconds | [JUnit](audit/phase2/frozen-tests-v7.xml), [log](audit/phase2/frozen-tests-v7.log); predates v8 |
| Native delivery contract | 185 passed, 47.49 seconds | [JUnit](audit/phase2/native-delivery-contract-20261115T151500.xml); strict explicit-null contract and compatibility; local/Mock |
| Latest delivery/non-finite test file | 36 passed, 5.97 seconds | [JUnit](audit/phase2/native-delivery-nonfinite-20261115T152200.xml); overlapping local/Mock group |
| Reviewer lossless-reference group | 124 passed, 60.43 seconds | [JUnit](audit/phase2/reviewer-lossless-v8-tests.xml), [offline diagnostic](audit/phase2/reviewer-lossless-v8-diagnostic.json); exact 524-byte saving, unchanged packet/hash/ceilings |
| Full backend suite v8 | 886 passed, 1 warning, 283.46 seconds | [JUnit](audit/phase2/frozen-tests-v8.xml), [log](audit/phase2/frozen-tests-v8.log); predates v9 |
| Full backend suite v9 | 895 passed, 1 warning, 237.83 seconds | [JUnit](audit/phase2/frozen-tests-v9.xml), [log](audit/phase2/frozen-tests-v9.log), 157-file manifest 9a33b199…; precedes v10 |
| Full backend suite v10 | 915 passed, 1 warning, 279.54 seconds | [JUnit](audit/phase2/frozen-tests-v10.xml), [log](audit/phase2/frozen-tests-v10.log), 158-file manifest 814d0dcc…; precedes v11 |
| Full backend suite v11 | 941 passed, 1 warning, 199.39 seconds | [JUnit](audit/phase2/frozen-tests-v11.xml), [log](audit/phase2/frozen-tests-v11.log), 160-file manifest eec443f0…; precedes v12 |
| Frozen full backend suite v12 | 955 passed, 1 warning, 251.43 seconds | [JUnit](audit/phase2/frozen-tests-v12.xml), [log](audit/phase2/frozen-tests-v12.log), 162-file manifest ccc5efe6… |
| This round's machine-readable consolidation | V12 frozen measured partial state | `docs/audit/phase2/verification.json` records open gates, not Phase 2 completion |

Groups overlap; their counts do not form an aggregate full-suite total. Tests using
Mock providers are never counted as live task executions. Historical Phase 1 test
counts and demonstrations remain in their original reports.

The v4 full suite is a failed integration attempt, not a green final result.
Its six Mock Planner fixtures supplied Done/Ready instead of executable criteria;
only fixture criteria changed to delivery_review. The 21-case pass preserves the
original assertions. The subsequent v5 full suite passed; v12 frozen testing passed as recorded below; complex-task acceptance remains incomplete. The newer
[v5 source manifest](audit/phase2/source-manifest-v5.json) records 153 files and hash
`a1f4646cb9acc810f3d35e8f9609a99ed553efd1ce4c2b3a0906d98c0333ed93`;
Ruff, 150-file Python formatting and diff checks passed. V5 full pytest passed
820 tests with one warning in 219.96 seconds; it predates v6 changes.

Two later retained runs share code snapshot
`03ebc502773bc4fb67444efe1834000e52256441ad422739e17dc487d8d86f13`.
L2 `20261115T013328Z-L2-fed968` failed in 80.711 seconds: 16 model/six tool calls,
54,597/9,558 input/output tokens, zero unknown attempts. Raw packet/native input
reservation were 68,294/68,550, above 65,536; declared concurrency three also
exceeded scenario cap two. L3 `20261115T013328Z-L3-4e61d3` failed in 151.834
seconds: 21 model/six tool calls, 91,162/22,775 tokens, zero unknown attempts.
Composer eventually produced a legal seven-role/concurrency-two team. Its
15,776-byte research artifact yielded raw/native sizes 67,009/68,224; Worker use
38,989/70,000 left 31,011 insufficient for full review. Neither run reached
`review.started` or a Core restart. No Reviewer verdict or complex success is claimed.

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

The v6 live snapshot
`474d4e11edc773ade91419fc8668ec18b8a365937faa06d8f6e177bf36581c13`
retains three further failures, all with zero final unknown attempts. L1
`20261115T014735Z-L1-b48614` failed in 122.096 seconds, 15 model/four tool calls,
67,356/11,709 input/output tokens: two NEEDS_REVISION verdicts after earlier
self-repair exhausted the original Worker `max_repairs`, blocking further revision.
L2 `20261115T014735Z-L2-cd59cb` failed in 84.678 seconds, 14 model/five tool calls,
48,105/8,295 tokens. Its 14,060-byte read-only delivery was outside v6's JSON-artifact
guidance. Raw/native input were 60,371/60,425; Worker use 18,143/78,000 left 59,857,
568 short even for review input alone. L3 `20261115T014735Z-L3-4ce51e` failed in
165.77 seconds, 27 model/seven tool calls, 123,506/22,569 tokens: the 11,651-byte
artifact produced raw/native sizes 65,637/66,278, while use 57,093/90,000 left 32,907.
L2/L3 reached neither `review.started` nor a restart. These are retained failures;
v7 guidance does not retroactively repair them or reset any budget.

The [v6 full suite](audit/phase2/frozen-tests-v6.xml) / [console log](audit/phase2/frozen-tests-v6.log)
recorded 848 passed, one failed, one warning in 283.77 seconds.
`test_actual_lifespan_schedules_parallel_dag_verifies_and_relays` in
`test_m5_lifespan_execution.py` failed because Core conflated Gateway model-call
concurrency one with Worker concurrency two, incorrectly reducing the team operator
cap to one. V7 restores Worker concurrency two independently of Gateway admission;
the original regression test remains unchanged. V7 full/live results are recorded below; v8 outcomes are recorded below; v9 results are recorded below; V12 frozen checks are recorded; the v12 evidence record consolidates this round's partial state.

The [v7 full suite](audit/phase2/frozen-tests-v7.xml) / [console log](audit/phase2/frozen-tests-v7.log)
passed 849 tests with one warning in 216.87 seconds. The
[155-file manifest](audit/phase2/source-manifest-v7.json) is
`58339132008a97956237ec38c2eb19eb7062f46b4ef2f5f946567ad52be26627`;
Ruff and formatting for 152 Python files passed. This snapshot precedes v8
strict-delivery/scope/repair changes and cannot validate them.

The v7 live snapshot
`dabcf69804a9f43b30ba10902ec6e781cdc27077bf374ac9f51927593b710de1`
retains three failures, all with zero final unknown attempts:
L1 `20261115T015415Z-L1-d9dedd`, 98.792 seconds, 16 model/four tool calls,
57,113/7,877 input/output tokens, exhausted repair after output at the 2,048 cap
truncated. L2 `20261115T015415Z-L2-b0e9f0`, 93.078 seconds, 19 model/six tool calls,
78,714/11,191 tokens, planned only two nodes despite the required three–four
specialist Workers; its 14,141-byte artifact also hit review context limits.
L3 `20261115T015415Z-L3-2732f0`, 99.483 seconds, 24 model/nine tool calls,
127,932/11,268 tokens, actually received independent NEEDS_REVISION, then exhausted
its ten-step limit before revision could finish. None passed strict acceptance;
complex compaction/restart continuity remains unestablished.

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

The [native delivery contract group](audit/phase2/native-delivery-contract-20261115T151500.xml)
passed 185 tests / 47.49 seconds. The latest
[delivery/non-finite test file](audit/phase2/native-delivery-nonfinite-20261115T152200.xml)
passed 36 tests / 5.97 seconds; this is overlapping local/Mock coverage, not a full
suite or live success. V8–v12 full/live outcomes are recorded; this round's final state remains partial; see the v12 evidence record.

The [v8 full suite](audit/phase2/frozen-tests-v8.xml) / [console log](audit/phase2/frozen-tests-v8.log)
passed 886 tests with one warning in 283.46 seconds. Its
[156-file manifest](audit/phase2/source-manifest-v8.json) is
`92bb6067ff7bc1dadfa6aa9b5994089969603b53b3980a9a5601d0a140e6b9e3`;
Ruff, formatting for 153 Python files and pip dependency checks passed.
This snapshot predates v9 generated-plan/batch-budget guidance changes.

The [v8 lossless-reference diagnostic](audit/phase2/reviewer-lossless-v8-diagnostic.json)
reconstructs the unchanged 64,323-byte retained L2 packet, original packet hash and PASS binding.
Numeric source labels remove only representation-added duplicate hash/length fields;
every original receipt field and complete source block remains exactly recoverable.
Native input reservation falls 64,870 → 64,346, saving 524 bytes of reservation for
this fixture; output 1,190 brings total reservation to the unchanged 65,536 ceiling.
The raw packet cap remains 65,536 bytes. The
[124-case regression](audit/phase2/reviewer-lossless-v8-tests.xml) passed in 60.43
seconds. This is an offline construction, zero Provider requests, with no historical
state change or new live verdict; no measured model-token/cost saving follows.

The v8 live runs share code snapshot
`8480f4f9c4f88a28d3d41047e5b1dbf90f424f9cc7892d0302c13758433c40ff`.
L1 `20261115T020604Z-L1-dceb58` failed in 136.466 seconds, 23 model/14 tool calls,
charged input/output 128,890/14,443 tokens. L2 `20261115T020604Z-L2-7f6370` failed
in 99.597 seconds, 25 model/15 tool calls, charged 109,862/9,567 tokens. Each record
retains one unresolved Router-notice attempt at stop; its reservation is respectively
10,555+4,096 and 10,290+4,096, not actual charged usage. Total actual usage and cost
remain unknown. Both Workers received real NEEDS_REVISION, then repeatedly reread
files and hit the original tool budget without revised delivery. L1 has two actual
Worker archives (17/6 source messages); L2 has four (9/6/6/4). These observations
establish Worker archiving, without a completed task or stopped-Core Router
compaction/mid-execution restart. L3 `20261115T020604Z-L3-30acf0` failed in 103.132
seconds, 18 model/six tool calls, 84,067/12,506 tokens, unknown zero. Its approved
research node required a file artifact while allowing only read tools; the actual
file was absent and packet construction failed before Reviewer/context admission.
No successor ran in L2/L3. The
[independent continuity/live audit](audit/phase2/continuity-and-live-diagnosis-v8.md)
does not infer a repeated-read cause from archives or claim a predecessor handoff
failure/success where no successor executed. All three strict acceptances failed.

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





The [retained v9 hint regression](audit/phase2/budget-feedback-intermediate-failure-v9.md)
records six legacy context failures after unconditional budget hints; interrupted
larger groups are not counted. Hints now require an effective per-request allowance
of at least 8192. The unchanged legacy file passed 48 cases / 15.81 seconds and
Planner/publication passed 36 / 3.64 seconds; the corrected combined
[66-case JUnit](audit/phase2/worker-budget-feedback-v9-fixed-tests.xml) has no failure.
These overlapping local/Mock groups are separate from the full suite and live runs.


## Reproduction and live acceptance contract

From `backend`, using the existing virtual environment, write to a new output path:

```powershell
.\.venv\Scripts\python.exe scripts/phase2/intelligence_benchmark.py --output 'D:\path\to\new-benchmark.json'
.\.venv\Scripts\python.exe scripts/phase2/retrieval_evaluation.py --output 'D:\path\to\new-retrieval.json'
.\.venv\Scripts\python.exe -m pytest tests/test_constraints_phase2.py tests/test_context_m6.py tests/test_memory_m6.py tests/test_router_intelligence_phase2.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_phase2_driver.py -q
```

Scripts reject overwriting historical outputs. No new production dependency or
environment variable is introduced by the offline benchmark. Live scripts require a
protected process credential reference and finite budget; credentials must not appear
in arguments, output records or source. `verify_provider.py` accepts `--credential-env`;
`live_e2e.py` reads the process-only `FORGE_PROVIDER_KEY_LIVE_TEST` variable. Supply its
value through a protected process input method. Do not place a value in shell history.

Process-only `FORGE_PROVIDER_TIMEOUT_SECONDS` defaults to 30 seconds and accepts a
finite value greater than 0 through 600. `FORGE_PROVIDER_MAX_RETRIES` defaults to 1
and accepts integers 0–3. `FORGE_WORKER_LEASE_SECONDS` defaults to 60 and accepts
finite values greater than 0 through 600. The current live harness selects a
120-second Provider timeout, a 150-second Worker lease and zero additional retries
after the retained Composer timeout diagnostic. Valid owners renew only between
settled steps, within the task deadline and existing authority. Every actual
attempt still requires Gateway admission; an unknown remote result stays reserved.

`FORGE_REVIEW_MAX_ROUNDS` defaults to 2 and accepts integers 1–4. The current
acceptance harness explicitly selects 3 within its scenario cap of 3. Historical
runs keep their original round limits, approvals and outcomes; this setting does
not refresh cumulative budgets or retroactively turn failed reviews into passes.

An example live scenario invocation, after configuring that protected process variable:

```powershell
.\.venv\Scripts\python.exe scripts/phase2/live_e2e.py --level L1 --scenario --restart-after-plan --token-limit 300000 --concurrency 2
```

`acceptance_scenarios.py` provides L1 actual registry/observation analysis inputs,
L2 bounded Router/Worker/Composer architecture inputs and L3 scheduler research/code/
experiment inputs. Descriptors include hashes and goals without seeded plans,
teams, approval, receipts or a hidden implementation. New artifacts are scoped to
`work/`. A plan-boundary restart alone cannot prove in-flight tool recovery.

These are requested scenario goals, not claims of completed runs. The current
Worker toolset permits file creation, syntax checks and bounded numeric AST
experiments; it supplies no unrestricted Python/test runner, arbitrary shell or
general code patch operation. `file_write` remains create-only. Explicitly approved
`file_revise` replaces only the current Worker's own latest artifact, with a checked
receipt chain, unchanged hash and retained immutable prior content. Read-only
acceptance follows that chain before comparing current bytes to the latest receipt;
old hashes remain historical evidence. Review/rework observations must state the
exact paths, authority and fixed execution mechanism used, and cannot be generalized
to arbitrary repository repair.

The retained L2 review-context failure also has an offline representation diagnostic:
the original 56,755-byte packet is unchanged, while complete untrusted source blocks,
shared constraint fields and exact original-message character spans remove repeated
JSON escaping/metadata. The old conservative reserve of 73,065 becomes a 64,622
review-envelope estimate; actual local `ProviderGateway._payload` construction
reserves 64,245 under the unchanged 65,536 context ceiling. All three original
source texts remain present, with no source/review-criterion truncation. This is a
local construction for that exact fixture, not a network call, a successful live
review or proof that a larger packet fits. Exceeded bounds still fail closed;
`_packet` and PASS hash binding retain their original values. [Sprint C/I](audit/phase2/sprint-ci.md)
records the reproduction and 118-test related group.

Receipt-bound invalid JSON triggers labelled Core-parser revision feedback; no LLM
call or PASS is inferred. Reviewer output is capped by 8192, the role output limit
and the registered model output limit. Only settled truncation with trustworthy
exact usage can request a further compact verdict within the same cumulative
budgets and review rounds. These paths have local/Mock evidence above; successful
live autonomous repair must still appear in the final run's actual history.

The fixed numeric-suite repair path is separately labelled
`deterministic_semantic_validation` / `core_fixed_numeric_suite`. Its failed report,
latest result, source/receipt and plan/Worker identities are checked before emitting
at most ten failed-case diagnostics. Same-Worker requeue requires explicit revision
authority, current grant, remaining original budgets and settled prior actions and
model attempts. No synthetic feedback or model claim substitutes for rerunning the
real suite. Persisted feedback can recover once after a crash before callback,
including semantic-only nodes, without a model-review call. Recovery requires an
originally available revision, current result/semantic/source/receipt bindings,
current scopes/grants and remaining original budgets with no unsettled actions or
Gateway attempts. Changed or stale inputs, revoked authority, exhausted budgets,
callback errors and fake success remain fail closed. This bounded recovery is not
replay permission for an unknown external action.

Each final live record must retain Run ID, Provider/model, execution mode, model/tool
calls, charged/unknown token usage, duration, retries, review verdicts/revisions,
artifact hashes, final status and failure diagnostics. Price stays unknown when no
trusted price is configured. Report success denominator, evidence completeness,
compaction source readback and observed restart boundary explicitly. The retained
L1 passes and L2/L3 failures above include the final v12 same-source samples;
the complex-task goal remains unmet. Run directories and proposed tasks are not acceptance evidence.
The driver's `runtime_settings` and Publisher whitelist retain only reasoning
effort, review-round limit, Worker lease seconds, Provider timeout seconds, retry limit
and effective model token/concurrency limits.
Missing values in older records remain null, with no retrospective inference or
approval/configuration change.
