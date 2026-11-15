# Independent v15 acceptance evidence check

The v15 offline packet is internally consistent. All **167 frozen files** match
their declared byte counts and SHA-256 hashes; the manifest's canonical hash is
`802b455f8e3014173ff4a62ecd41c8579af76d44782c6f7943d37e5dbd1f7c46`.
The declared backend source/script/test/config inventory has no omitted or extra
files. The four retained static logs report successful Ruff, formatting of all
164 Python files, dependency and whitespace checks.

The complete JUnit contains **1,031 distinct passing test cases across all 80 test
modules**, with zero failure, error or skipped elements. Its count matches the
terminal log: **1,031 passed / one existing TestClient deprecation warning in
227.13 seconds**. JUnit separately reports a 225.037-second suite duration; both
measurements are preserved. Critical accounting, migration, archive, preview,
acceptance-evaluator and semantic-revision cases appear in the full result.

The benchmark is explicitly offline and reports zero Provider calls. Its fixed
dataset hash matches v12–v14 and recomputes correctly. Row-derived summaries agree:
48/48 valid team contracts, 12/12 source-recall obligations in the complete recall
variant, all 15 governance expectations and 9/9 retrieval cases. Actual model task
success and Provider cost remain unmeasured. Independent rerunning of the static
Git/AST preservation calculation reproduces the v15 audit exactly except its
timestamp: 355 Phase 1 evidence files and nine reports are byte-identical, all 364
original test identities remain, and 15 changed ASTs are explicitly listed.
The corrected manifest label and explicit reproduction argument both point to v15.

No supplied offline gate is missing. The remaining claim boundaries are:

- At the initial audit time, current-source strict live L1/L2/L3 results were pending;
  all three subsequently failed as recorded in the addendum below. They must prove actual
  scenario artifacts, current-packet independent PASS, governance, budgets and
  settled usage; L3 also needs delivered-source numeric/semantic evidence and an
  actual mid-execution restart.
- Strict success does not force a failure, repair or review revision to occur.
  The original complex-task continuity goal still needs actual ordered evidence
  of repair/revision, source-bound compression/readback, restart and continuation.
- The strict Router archive/readback check does not independently prove the
  Worker first whole-tail archive/readback path. A live claim about that path
  needs original Worker events.
- Synthetic mechanism results do not establish the requested equal-budget,
  independently scored real-model quality comparisons. Historical unknown usage
  and cost also remain unknown.

This check read files and Git only, made no Provider requests, opened no runtime
database and reran no tests. Source, desktop and previous evidence remain intact.
The [paired JSON](acceptance-independent-v15.json) records exact artifact hashes,
checks and limits. Phase 2 completion is not established by this offline packet.

Addendum, 2026-11-15 07:43 UTC: the three live attempts have ended and all failed.
Their summaries appear in the [53-record snapshot-v7 index](live-runs/snapshot-v7/index.json).
All three published summary hashes match that index. Their shared live code hash
`8c353fd5f5b3aa5b27996989ef9e1d62dc601f65a1af6ffc40815e8a2e692047`
recomputes from the 73 executable-source entries in the v15 frozen manifest.

| Published run | Actual result |
| --- | --- |
| [L1 20261115T073729Z-L1-1be482](live-runs/snapshot-v7/20261115T073729Z-L1-1be482.json) | Failed in 40.2s; Worker blocked, scenario concurrency check failed, no independent review or restart. |
| [L2 20261115T073745Z-L2-00761c](live-runs/snapshot-v7/20261115T073745Z-L2-00761c.json) | Failed in 65.392s; Worker review pending with `review_contract_or_budget_failed`, no model verdict, final artifact or restart. |
| [L3 20261115T073825Z-L3-29cb09](live-runs/snapshot-v7/20261115T073825Z-L3-29cb09.json) | Failed in 103.285s; live NEEDS_REVISION followed by deterministic `review_capacity_unrepairable` BLOCKED; no final semantic test or restart. |

Each final published summary reports zero unknown and zero unsettled model
attempts, while cost remains null. Earlier driver stop-reason text about
unsettled calls does not override these final settlement counts. The three
attempts are no longer pending; the overall live acceptance gates remain unmet.
This addendum records published facts without changing the original offline
scope, test results or historical evidence.
