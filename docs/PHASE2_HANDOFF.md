# Phase 2 implementation handoff — 2026-11-16

Continue on `codex/phase1-continuous`. Backend work shares the checkout with a
separate frontend conversation; preserve its changes.

## Current implementation

- Cumulative token ceilings are disabled by default. Usage, unresolved calls,
  model context/output limits, tool authority and approvals remain recorded.
- Router and Worker compaction use model-generated handoffs, complete original
  archives, source integrity checks and durable continuation state. The Codex
  source reference is recorded in [ADR-013](adr/ADR-013-optional-token-limits-and-model-context-handoffs.md).
- V30 Planner repair retains the complete original request and sends the rejected
  proposal's hash, byte length and diagnostics instead of echoing its full body.
  A repair that cannot fit terminates the notice rather than restarting it.
- Worker repair reports the actual remaining approved correction calls and the
  canonical UTF-8 delivery limit, including when the last correction is used.
- Live-driver finalization reads the actual budget policy even when Router,
  Planner or Composer fails early. Settlement waits for queued Router notices
  and a quiet dispatcher interval, preventing shutdown between two notice calls.
- New Planner proposals can explicitly select `inline_6144` for complete
  multi-source analysis. Measured response ceilings scale with declared complete
  artifact/delivery size: 2,048 tokens through 2,048 bytes, 4,096 through 6,144
  bytes and 8,192 above. Planner, capacity preview and Composer share the measured
  value; native context, model output and full review checks still decide
  admission. Existing approvals are not enlarged.
- New Core-measured plans fund two bounded correction calls, including read-only
  analysis. Team remeasurement retains the exact count in any already approved
  envelope.
- When complete declared-source prefetch is verified and the extra action
  allowance is zero, runtime and preflight share the same tool presentation:
  hide unavailable extra observations while retaining execution permissions,
  writes/tests, archive readback and help. Delivery guidance leaves serialization
  headroom without truncating the assigned result.

## Progress boundary

V29's archived suite has 2,079 passes and eight failures. Its L1/L2 Workers
produced 4,320/4,559-byte deliveries against 2,048-byte approved limits; L2 also
exhausted a 2,048-token response. L3 stopped during planning. Those original
failed runs remain unchanged.

This continuation reran the eight failed cases once: all passed. Three Planner
repair regressions and seven finalization/settlement regressions also passed. Existing
V30 Worker records already cover its wider compaction and repair changes, so
they are not being rerun. These local checks do not establish live task success
or full Phase 2 acceptance.

New proposal sizing now addresses the observed mismatch. Necessary checks for
the new measured plan-to-Composer contract and runtime/preflight tool filtering
passed. Two focused current-source L1 runs checked the implementation before and
after the resulting local fixes. No freeze or full-suite cycle was started.

The first current-source L1 run, `20261115T171355Z-L1-14e302`, completed planning
and team approval with the new 6,144-byte delivery and 4,096-token response
contract. It failed after an unnecessary extra-source tool batch used its single
correction and the replacement delivery reached 6,260 bytes. Shutdown also caught
a newly started Router notice, leaving one unresolved reservation. Its failure
record is retained.

The final L1 run, `20261115T172036Z-L1-9189f7`, reached complete structured
deliveries and two fresh independent reviews through the real Provider. It used
15 settled calls, 71,472 input tokens and 5,474 output tokens, with zero unknown
usage. The Worker made five steps, used two repairs and retained three real Core
source reads. Both reviews returned `NEEDS_REVISION`; the final report still
misread registry short-circuit behavior. No PASS, completed Quest or successful
L1 acceptance is claimed. The driver recorded `all_remaining_workers_settled_blocked`
and `quest_status_running`; its full failed checks remain in the local run record.

The settlement wait was subsequently narrowed to the Router's actual handled
event types, so inert inbox events do not force a 30-second wait. The regression
with a queued notice, a later real reservation and an inert event passed.

Remaining work concerns model analysis/revision quality, L2/L3 task completion,
complex-task continuity and wider model selection. The execution improvements
above are implemented; full Phase 2 acceptance remains open. No further live or
full-suite validation is queued by this handoff.

## Run an isolated task

With the authorized local Provider key supplied only through the process
environment variable `FORGE_PROVIDER_KEY_LIVE_TEST`, run from the repository root:

```powershell
backend/.venv/Scripts/python.exe backend/scripts/phase2/live_e2e.py --scenario --level L1 --no-token-budget --concurrency 1
```

This existing driver uses the configured local WorkBuddy endpoint through the
normal Gateway, creates an isolated workspace under `.local/phase2`, records
operator plan/team approvals and retains the actual tools, reviews and outcome.
It does not require an evidence freeze. Use `--prompt '<task>'` without
`--scenario` for a custom task against its seeded `project` directory. No new
dependency or environment variable is introduced.
