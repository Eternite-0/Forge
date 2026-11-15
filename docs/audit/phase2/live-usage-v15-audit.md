# V15 live usage audit

Only the three new V15 finished runs; no historical inference, database mutation, source edit or Provider call. Outputs contain IDs, numeric counters, state/reason codes and hashes only.

All databases were read with mode=ro and query_only read snapshots. No Provider request or database mutation was performed.

- 20261115T073729Z-L1-1be482: local settled=3, refunded=1; final Gateway unsettled=0, unknown=0; accounting checks=pass.
  Worker run_df3cb94812f3: 11513 = 11513 Worker + 0 review tokens; steps 3 = 3 + 0.
- 20261115T073745Z-L2-00761c: local settled=3; final Gateway unsettled=0, unknown=0; accounting checks=pass.
  Worker run_f910b046b994: 11039 = 11039 Worker + 0 review tokens; steps 3 = 3 + 0.
- 20261115T073825Z-L3-29cb09: local settled=8; final Gateway unsettled=0, unknown=0; accounting checks=pass.
  Worker run_a7d82da1f713: 92086 = 75051 Worker + 17035 review tokens; steps 9 = 8 + 1.

L2 pure review reconstruction: raw packet 49,832 bytes remains below the 65,536-byte cap. Schema-capable native input 49,424 plus minimum verdict 1,024 needs 50,448 tokens, while only 40,961 remain (deficit 9,487). Without schema formatting the minimum remains 49,631, also too large.

- The L1/L2 driver label mentions unsettled attempts at its observation point; it is not the terminal Worker reason and does not describe the final Gateway state.
- L1 terminal Worker reason is help_requested. Its executed approved team has max_concurrency=2 versus strict scenario cap=1; the later unexecuted replacement has max_concurrency=1 and does not cause that failed check.
- L2 is review_pending with review.blocked review_contract_or_budget_failed; read-only reconstruction identifies insufficient remaining cumulative review allowance, not a local reservation leak.
- L3 ran one settled independent model review and one applied revision. The subsequent review is a deterministic capacity block, not a second model judgment.
- None of these three runs exercised a mid-execution Core restart, Worker pause/resume late settlement, or a Worker generation handoff while a request was pending. Offline tests cover those paths; these new live records do not newly prove them.
- Monetary cost bounds were not configured; null costs remain unknown and are never reported as zero.
- Accounting checks do not establish autonomous task success or strict acceptance.

L3 final capacity: raw packet 65,025 bytes is below the raw cap. Native input 65,215 plus minimum verdict 1,024 requires 66,239, exceeding the 65,536 context by 703. Fixed input floor 48,355 plus minimum verdict requires 49,379, exceeding the remaining cumulative 47,914 by 1,465. The final deterministic outcome is review_capacity_unrepairable / BLOCKED.

L2 and L3 each have one real Worker archive and zero worker_read_context_source calls. Router archives are zero in all three runs. Archive creation is a local observation; no source-readback or Router-compaction continuity is established.

An independent read-only crosscheck confirmed all 14 settled Worker bindings, the one unsent refund and the reconstructed counters.
