# Phase 2 — Experiments and Reproduction

Updated 2026-11-15. V22 implementation and acceptance are in progress. Frozen full-suite, strict current
L1/L2/L3 and final evidence inventory are pending. Original failed attempts stay
immutable and are included in the eventual publication index.

## Evidence categories

| Evidence | What it establishes | Limit |
| --- | --- | --- |
| Local/Mock tests | Deterministic contracts, authority, budgets, failure handling and persistence | No real model quality |
| Scripted loopback HTTP | Actual native Gateway, attempt ledger and runtime protocol integration | Local scripted responses, even when the production adapter reports live mode |
| Real Provider protocol | Configured proxy returned supported dialogue/schema/tool/stream responses | Proxy success, upstream identity and correctness are separate claims |
| Real algorithm study | Fixed-case policy observations with independent bounded scoring | Small single-model sample; not autonomous Quest acceptance |
| Strict live Quest | Original scenario checks, actual evidence, continuity, budgets and current-source binding | A failed or partial run remains failed |

Historical reports and failed test attempts are retained under
`docs/audit/phase2/`; no later repair changes their original source identity.

## V16 historical boundary

Full backend suite: 1,304 passed, five failed, one warning, 328.69 seconds. The
retained failures concern Planner wire/schema/lease/API compatibility.
`source-manifest-v16.json` contains 188 files and canonical manifest SHA-256
`e069c337d3d4cb5554181ef79a0f3803700b6e5d7f6c5a117dcb35d17c3f9cad`.

| Strict run | Duration | Known tokens / original cap | Result |
| --- | --- | --- | --- |
| `20261115T082953Z-L1-16b57e` | 193.361 s | 66,428 / 180,000 | Failed before plan approval |
| `20261115T082953Z-L2-a68109` | 312.402 s | 78,872 / 360,000 | Failed before plan approval |
| `20261115T083313Z-L3-673598` | 373.787 s | 90,794 / 630,000 | Failed before plan approval |

Each run retained five Planner processing attempts, ten settled Planner HTTP
calls, one Router call and one semantic recall call. All 36 attempts settled,
with no Worker/review/archive/restart. Detailed rejected drafts were not saved;
`Invalid_graph_proposal` alone cannot identify a schema/capacity/model cause.
See [the immutable boundary](audit/phase2/acceptance-independent-v16-boundary.md)
and [read-only usage audit](audit/phase2/live-usage-v16-audit.md).

## V17 local records

| Record | Result | Boundary |
| --- | --- | --- |
| [Final predecessor-delivery helper](audit/phase2/predecessor-ancestor-v17-second.xml) | 35 passed, 27.19 s | Full source/PASS/lineage/bytes plus actual three-node success-ancestor path and nonancestor/failure/unfinished/tampered path rejection; first failed fixture attempt retained |
| [Review/context/lineage group](audit/phase2/predecessor-review-lineage-v17-regression.xml) | 157 passed, 61.94 s | Overlapping local regressions; counts are not additive |
| [Native serial HTTP fixture](audit/phase2/serial-runtime-v17-final.xml) | 3 passed, 6.081 s | Actual Gateway/ledger with scripted responses; two nodes and Store reopen, cost/tamper failures before HTTP |

The V17 full frozen suite passed 1,405 tests with one warning in 427.75 seconds.
All three strict live runs failed before a Planner model call or Worker at
`Quest_is_too_large_to_plan`. Each retained five failed Planner inbox attempts
and two settled Router/semantic calls; there were no unknown attempts.

| Strict V17 run | Duration | Known tokens / original cap | Result |
| --- | --- | --- | --- |
| `20261115T092655Z-L1-c3a404` | 376.207 s | 4,233 / 180,000 | Entry guard failed |
| `20261115T092655Z-L2-350db9` | 376.101 s | 4,366 / 360,000 | Entry guard failed |
| `20261115T092655Z-L3-bf61b1` | 376.139 s | 4,844 / 630,000 | Entry guard failed |

The archived 201-file source independently matches its original manifest; this
is historical source. See
[the V17 boundary](audit/phase2/acceptance-independent-v17-boundary.md) and
[read-only usage audit](audit/phase2/live-usage-v17-audit.md).

## V18 historical boundary

The V18 full frozen suite passed 1,413 tests with one warning in 408.37 seconds.
All three strict live runs failed before plan approval at
`Invalid_graph_proposal`. Each retained ten rejection events and five Planner
inbox attempts. The complete detailed diagnostics remain persisted.

| Strict V18 run | Duration | Known tokens / original cap | Settled calls | Result |
| --- | --- | --- | --- | --- |
| `20261115T093857Z-L1-598df5` | 383.096 s | 80,013 / 180,000 | 13 | Plan validation failed |
| `20261115T093857Z-L2-bd6e94` | 377.332 s | 87,717 / 360,000 | 12 | Plan validation failed |
| `20261115T093857Z-L3-0b7788` | 378.376 s | 102,389 / 630,000 | 12 | Plan validation failed |

The read-only audit reconciled 270,119 known tokens across all 37 settled calls,
zero unknown attempts and zero database changes. L1 had two Router calls; L2/L3
had one. No Worker, review, context archive or Core restart occurred. The
203-file source ZIP rehashes exactly against the historical V18 manifest. See
[the immutable V18 boundary](audit/phase2/acceptance-independent-v18-boundary.md)
and [usage audit](audit/phase2/live-usage-v18-audit.md).

## V19 historical boundary

The frozen suite passed 1,448 tests with one warning in 321.62 seconds. All
three strict live runs failed before plan approval, retaining one Planner inbox
attempt and two rejection events each.

| Strict V19 run | Duration | Known tokens / original cap | Settled calls | Result |
| --- | --- | --- | --- | --- |
| `20261115T095809Z-L1-92e62d` | 23.587 s | 16,860 / 180,000 | 4 | Plan validation failed |
| `20261115T095809Z-L2-896400` | 31.557 s | 18,893 / 360,000 | 4 | Plan validation failed |
| `20261115T095809Z-L3-545d98` | 38.688 s | 22,476 / 630,000 | 4 | Plan validation failed |

All twelve calls settled at 58,229 known tokens; unknown usage and database
audit changes were zero. No Worker, review, archive or restart occurred. The
207-file archived source matches its original manifest. Five rejections failed
exact criterion validation; the sixth reached source/capacity checks. See
[the V19 boundary](audit/phase2/acceptance-independent-v19-boundary.md) and
[usage audit](audit/phase2/live-usage-v19-audit.md).

## V20 and V21 historical boundaries

V20 passed 1,490 frozen tests / one warning / 312.74 seconds. Its strict runs
all failed: L1 obtained plan approval but no team; L2/L3 stopped at planning.
The 22 calls settled at 108,458 tokens with zero final unknown usage. No Worker
or restart occurred. See [boundary](audit/phase2/acceptance-independent-v20-boundary.md)
and [usage](audit/phase2/live-usage-v20-audit.md).

V21 passed 1,560 frozen tests / one warning / 445.09 seconds. L1 obtained plan
and team approval, performed three real file reads, and blocked before review.
L2/L3 stopped at planning. All three strict runs remained failed.

| Strict V21 run | Duration | Known tokens / original cap | Settled calls |
| --- | --- | --- | --- |
| `20261115T102752Z-L1-6a4082` | 61.496 s | 40,081 / 180,000 | 9 |
| `20261115T102753Z-L2-ba61b0` | 38.141 s | 21,354 / 360,000 | 5 |
| `20261115T102753Z-L3-913aa9` | 41.602 s | 22,092 / 630,000 | 4 |

All eighteen calls settled at 83,527 tokens, with zero final unknown usage.
The original transient-unsettled driver flag stays unchanged; the final drain
snapshot cannot prove earlier settlement timing. See
[boundary](audit/phase2/acceptance-independent-v21-boundary.md),
[usage](audit/phase2/live-usage-v21-audit.md) and
[timing addendum](audit/phase2/live-usage-v21-timing.md). V22 final frozen-suite
and strict records remain pending.

## Fixed semantic retrieval sample

`.local/phase2/semantic-crossquest-v17-fixed-01/` preserves one fixed real call
with zero retries. Two prior Quests and two original user messages were seeded;
their status remained proposed. The Chinese query had no lexical index match to
the English target preference. The call settled at 1,713 input + 113 output =
1,826 tokens, with no unknown usage and unknown monetary cost.

The original `result.json` remains false. Its oracle only captured four manually
seeded candidate IDs, while source sync created two additional literal global
constraints. A selected global target constraint became null in that fixture's
lookup and caused three false oracle checks. Independent read-only inspection
rebuilt all six source records and matched the exact complete candidate snapshot
SHA-256 `713287d72f61f2db2664a222409c7a3c515a97e6c65ddd9e52f346cc66697789`.
All three returned records exactly matched the target source; both original
message rows and hashes were unchanged. All eleven independent checks passed,
with SQLite `total_changes=0`.

See [the separate immutable interpretation](audit/phase2/semantic-crossquest-v17-independent.md).
It neither rewrites the failed raw result nor establishes successful Quest
execution, broad memory quality, source diversity or multi-model capability.

## Historical controlled algorithm study

The retained V16 study has 90 rows, six fixed cases and fifteen policy variants.
It compares six team policies, five context policies and four governance
policies. Independent scoring found 25 task successes and 65 failures; all
stages completed. Its 135 calls used 91,487 input + 19,756 output = 111,243
tokens, with no unknown usage or score/hash/row discrepancies.

Unequal model calls, independently sampled candidates and deterministic failed
check feedback limit causal comparisons. One model and small fixed cases do not
establish dynamic-team superiority, model diversity or general task correctness.
The fixed oracle gate applies only to its stated cases. See
[results](audit/phase2/live-algorithm-study-v16-results.json) and
[independent audit](audit/phase2/live-algorithm-study-v16-independent.md).

## Reproduction and final acceptance

Use the native scripts under `backend/scripts/phase2/`. Provider calls pass
through the existing Gateway and durable model ledger. Credentials come only
from the existing local process configuration and never enter source or
reports. Do not modify the user's Provider service.

Strict scenarios retain 180,000 / 360,000 / 630,000 total tokens, the original
node/member/concurrency/duration/retry caps, full source criteria and the
65,536-byte raw review limit. Serial sharing is an explicit approved resource
policy with concurrency one; it does not demonstrate parallel execution.
Core-measured resource values require the normal plan and team approval.
Unknown/rejected-without-usage attempts keep their reservations.

Audit runtime databases with SQLite URI `mode=ro`, `PRAGMA query_only=ON` and an
explicit read transaction. Do not construct Store. The V17 usage auditor checks
original scenario caps, once-only reserve markers, held baseline allocations,
actual Gateway/local counters, full current review/PASS reconstruction, source
handoffs and artifact hashes. Final independent acceptance separately checks
the frozen inventory, full suite/static records, original evidence/test
preservation, published live snapshots and all fifteen goal rows.

Accepted results require actual current-source strict scenario success, required
source readback/context continuity and a real mid-execution Core process restart
where specified. Store reopen and after-completion restarts are narrower tests.
