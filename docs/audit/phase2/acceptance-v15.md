# Phase 2 continued acceptance — v15

Date: 2026-11-15 (Asia/Shanghai). Integration owner: root.
Branch: `codex/phase1-continuous`. **Backend checks passed; all three new strict
live runs failed. Phase 2 remains incomplete.** Desktop is being edited in another
window and was not changed, tested or packaged by this acceptance round. No commit,
push or merge was performed.

## Frozen backend result

The [167-file manifest](source-manifest-v15.json) has canonical SHA-256
`802b455f8e3014173ff4a62ecd41c8579af76d44782c6f7943d37e5dbd1f7c46`.
All three live runs use executable snapshot
`8c353fd5f5b3aa5b27996989ef9e1d62dc601f65a1af6ffc40815e8a2e692047`.
Backend source was frozen throughout the complete suite and subsequent live runs.

| Check | Result | Evidence |
| --- | --- | --- |
| Complete backend suite | 1,031 passed; zero failed, errored or skipped; one existing warning; 227.13 seconds | [JUnit](frozen-tests-v15.xml), [console](frozen-tests-v15.log) |
| Ruff | Passed | [Log](frozen-ruff-v15.log) |
| Formatting | All 164 Python files formatted | [Log](frozen-format-v15.log) |
| Installed dependency consistency | Passed | [Log](frozen-pip-v15.log) |
| Whitespace validation | Passed | [Log](frozen-diff-v15.log) |
| Fixed offline mechanism benchmark | Completed in 4.8381962 seconds; zero Provider calls | [JSON](benchmark-final-v15.json) |
| Historical evidence and tests | 355/355 Phase 1 evidence files byte-identical; 364/364 original test identities retained; 15 AST changes disclosed | [Audit](preservation-v15.json) |
| Independent evidence check | Frozen inventory, hashes, full JUnit, static logs, benchmark and preservation verified | [Report](acceptance-independent-v15.md) |

The warning is the existing Starlette/httpx TestClient deprecation. JUnit's own
suite time is 225.037 seconds; the quoted 227.13 seconds is the pytest console
measurement. Local/Mock cases exercise accounting, immutable reservation guards,
migrations/rollback/backup, lease fencing, schema and review repair, memory,
recovery, governance, native tools, lifecycle and startup. They do not replace live
task-quality evidence. The offline benchmark remains a fixed synthetic comparison.

## Retained failures and corrections

The unchanged v12 baseline rerun passed 955 tests in 236.32 seconds. V13 retained
976 passed / two failed / one warning in 208.55 seconds. Its optional-config
compatibility error was corrected; its intermittent Windows startup I/O failure
was diagnosed without claiming a source fix. V14 retained 950 passed / 71 failed /
one warning in 203.47 seconds. Seventy failures exposed an overly strict new
synchronous Provider accounting contract; v15 restores the original fenced
current-owner behavior. The remaining failure was a legacy backup schema fixture
missing the new table in its cleanup. That fixture construction was corrected
without relaxing its assertions. All original failed XML/logs remain available.

V13 adds an advisory, read-only review-capacity preview within existing Worker
request reservations. It does not create a delivery or PASS and does not change
the original packet cap or budget. V14/v15 add schema-12 immutable local model
reservations and exact-once known-usage reconciliation. Late/restart settlement
requires one precisely matching settled Gateway attempt; current-owner synchronous
fallback requires the original fence and no Gateway attempt. Unknown outcomes
retain reservations. Historical calls are not backfilled or refunded.

The [historical diagnosis](restart-known-usage-v14-diagnosis.md) identifies a genuine
15,429-token over-reservation after pause. The [startup diagnosis](startup-io-v14-diagnosis.md)
retains intermittent SQLite I/O/HTTP-reset failures. Original startup tests pass
in the v15 full suite, but the intermittent problem has no demonstrated source fix.
The explicitly approved artifact envelope and Planner feasibility contract in the
[capacity design](capacity-design-v13.md) are proposals, not implemented features.

## Real Provider acceptance

These are native FORGE Gateway calls through the enabled local WorkBuddy WB_IN
credential and requested model `deepseek-v4.1-flash`. No credential is recorded.
Proxy-reported model identity is not independently verified. Original scenario
budgets, tools, evidence gates, 65,536-byte review cap and review-round limits apply.
No failed run was retried merely to obtain a passing sample.

| Level / run | Strict result | Seconds | Model / tool calls | Charged input / output | Final unknown attempts |
| --- | --- | --- | --- | --- | --- |
| L1 `20261115T073729Z-L1-1be482` | Failed | 40.200 | 10 / 4 | 31,734 / 1,880 | 0 |
| L2 `20261115T073745Z-L2-00761c` | Failed | 65.392 | 14 / 5 | 45,280 / 6,670 | 0 |
| L3 `20261115T073825Z-L3-29cb09` | Failed | 103.285 | 22 / 7 | 118,967 / 14,383 | 0 |

L1 read the supplied sources, then unnecessarily requested human confirmation
that inline delivery was acceptable for an explicitly read-only task. No delivery
or independent review followed. Its actual executed team also declared concurrency
two above the scenario cap one, although it had only one Worker. The strict
concurrency failure is valid; the later unexecuted replacement did not cause it.

L2 produced a research delivery, then the first independent review was blocked
before dispatch. Its complete raw packet was 49,832 bytes, under the raw cap, but
the actual native review request needed 49,424 input plus at least 1,024 verdict
tokens against only 40,961 remaining in the original node allocation. This is
cumulative review-budget exhaustion, surfaced by the generic
`review_contract_or_budget_failed` event. No independent-review model call,
successor execution, final report or mid-execution restart occurred.

L3 created a real research artifact, received a settled independent
NEEDS_REVISION, revised its own artifact and submitted another delivery. The second
packet was 65,025 raw bytes; native input was 65,215, which plus the minimum 1,024
verdict exceeded the 65,536 context ceiling. Its original Worker allocation also
had only 47,914 tokens left. Core emitted `review_capacity_unrepairable` / BLOCKED.
The revision did not obtain PASS. No research node succeeded, so mathematical,
coding, experiment and final semantic acceptance were not reached. The planned
mid-execution restart and context-compaction checks remain unverified in this run.

L1/L2 driver failure labels mentioning unsettled attempts describe the stop-time
snapshot, including coordination calls. The harness waited for settlement before
final inspection; all three final ledgers have zero unknown attempts. Worker-local
settlement is audited separately in [live usage diagnosis](live-usage-v15-audit.md).
L2 and L3 each contain one actual Worker archive. Neither contains a
`worker_read_context_source` call; all three contain zero Router archives and only
one Core start. These partial archive events do not prove source-readback or
restart continuity.
No new live pause/restart occurred, so these runs do not independently demonstrate
the new late-settlement path; that path has local regression and migration coverage.

## Publication and open gates

[Snapshot-v7](live-runs/snapshot-v7/index.json) contains all 53 finished driver
records and preserves the previous 50 outcomes. Older snapshots are unchanged.
The original v12 verification and nine documents remain byte-identical under
[checkpoint-v12](checkpoint-v12/index.json). [Verification v15](verification-v15.json)
records this source and its actual results; `verification.json` is its current copy.

Current-source accepted levels: **none**. Earlier L1 successes do not certify v15.
The three new runs account for 195,981 input and 22,933 output tokens. Historical
unknown attempts are at least nine, with six early runs missing usage statistics;
the true historical aggregate and total cost remain unknown. Current zero-unknown
ledgers do not settle historical remote outcomes.

Remaining work includes enforcing source-bound user/task limits at admission,
preventing unnecessary help requests, fitting complete review and revision within
original task budgets, and proving an entire complex task with actual artifacts,
independent checks, repair/revision, source readback, mid-execution restart and
continuation. General running-DAG replanning, learned semantic retrieval, arbitrary
repository execution and equal-budget live quality comparisons also remain open.
These are uncompleted requirements, not claims supported by the passing unit suite.
