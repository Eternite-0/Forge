# Super-agent experiments

Date: 2026-11-16. Capability Sprint base: `57a162d`.

## Protocol

The previous Super-agent audit and all Phase 2 experiments remain unchanged.
The new experiment set evaluates task outcomes, not model completion or apparent
progress. Fixed materials and independent acceptance checks are held constant
across single, fixed and adaptive team interventions. No algorithm is assumed
optimal. Model routing compares eligible discovered identities only; identity
discovery is not a measured capability score.

Task-centric context is evaluated through project-isolation mutation tests and
the combined real two-project/restart/compaction/Recall scenario. Recovery tests
inject known infrastructure failures and unknown effects separately; successful
recovery requires preserved evidence and no duplicate uncertain operation.

Collect: task success, settled/unknown calls, input/output/charged tokens, cost
when trusted prices exist, elapsed time, tool counts, self-repair/review rework,
recovery success, source citations and evidence completeness. All denominators,
sample sizes, execution modes and source hashes must be recorded. Real model
results, scripted model protocols and programmatic filesystem/runtime checks
are separate evidence classes.

## Current status

The bounded real-task experiment set ended with partial observations and failed
full acceptance. Per-task ceilings remained fixed in the
[Sprint report](SUPERAGENT_CAPABILITY_SPRINT_REPORT.md). L1 has an independently
passing task; L2 did not complete; L3's real code passed holdouts but its reported
receipt binding did not. No comparative advantage is established.

## Preserved first failure and corrected context metadata

The first adaptive run `20261115T210429Z-capability-adaptive-b78221`
failed after real source reads. Both Workers stopped at `context_budget` and L2
did not form the required specialist team. The original report and separate
read-only supplemental accounting are retained: 24 calls settled, no inferred
success. Final diagnostic reads originally raised SQLite OperationalError
without recording its code; later read-only diagnostics succeeded. The cause
of that transient I/O failure remains unknown.

The operator clarified that this DeepSeek Provider advertises **100M** context.
The old 65536 acceptance profile was an obsolete conservative host assumption,
not the provided model capacity. More significantly, the delegation path used
one estimated request's input plus output (73728 tokens) as the cumulative
Worker allocation, then reduced the request window by its remaining amount.
Compaction consumed that allocation further. Subsequent runs use the explicit
Provider-declared context metadata and separate request estimates from
cumulative allocations. This is not a measured upstream maximum. The fixed
500000-token/32-call/900-second per-task and total 160-call/2500000-token
experiment ceilings remain unchanged, including retained failures. No prior
failed run is reopened or reclassified as successful.

The next `/models` metadata inspection returned `context_length=1000000`,
`max_input_tokens=1000000` and `max_output_tokens=128000`. This conflicts with
the spoken 100M value. Real acceptance now uses the actual 1M declaration,
while tests cover arbitrary valid large Registry windows including 100M. The
first run after the clarification retained the explicitly configured 100M value
and failed Router decision validation; it is preserved separately. Provider
discovery now retains allowlisted declaration fields and their provenance,
without overwriting existing operator profiles or interpreting declared limits
as measured capability. Runtime output allowance remains 8192 in acceptance;
the Provider's larger output maximum does not enlarge that spending allowance.

## Retained capability failures before final verification

| Run suffix | Real calls | Outcome and observed limitation |
| --- | ---: | --- |
| `adaptive-b78221` | 24 settled | Source reads occurred; request estimate was incorrectly used as cumulative allocation. Original diagnostic failure and supplemental read-only accounting both remain. |
| `adaptive-e040bc` | 3 settled, 1 reserved with unknown outcome | Router decision rejected; the old concurrent driver stopped another in-flight request. This database is never reopened for execution. |
| `single-730d18` | 10 settled | Worker chose an out-of-scope write path; pre-execution rejection was safe but lacked a repair entry. |
| `adaptive-8806a4` | 10 settled | Router team-contract repair and Worker archive-reference recovery failed. |
| `development-1cf69f` | 20 settled | Real starter tests failed, corrected visible tests passed, Reviewer required revision; two pending R2 calls then exposed a batch-resume defect. The final code passed a separate host holdout, but Core delivery did not finish. |
| `adaptive-35c07f` | 18 settled | Second Router request tried archive readback when no archive existed, returning 409. L1 later completed with Reviewer PASS, but all six citation ranges were inaccurate, so independent acceptance failed. L3 was not submitted. |

These six attempts consumed **86 task calls: 85 settled and one retained reserved
attempt with unknown outcome**, plus 648087 known charged tokens. The separate
twelve-call microprobe is not included. Monetary cost and the unresolved attempt's
actual usage remain unknown. Failures count against the unchanged experiment
ceiling; they are not replaced by successful retries in a reported denominator.

The observed defects received bounded fixes and targeted regression checks.
Router reflection now sees the rejected response transiently; raw rejected text
is not persisted in diagnostic events. A Worker can recover a provably
unexecuted path/source-read error and resume a batch through individual exact
R2 approvals. No unknown operation is replayed. Source packets now include a
derived, hash-attributed numbered presentation; original content, source hashes
and strict citation checking are preserved. This addresses input presentation
and does not retroactively correct an old report.

Final verification additionally rechecks per-task budgets after recap/draining
and binds L3 test claims to current-code receipts. Host artifact checks, actual
Worker execution and model Reviewer verdicts remain separate evidence classes.

## Final bounded observations

| Run | Calls / known Token | Real outcome |
| --- | --- | --- |
| `adaptive-45f4e4` | 14 settled / 149699 | L1 Core/Reviewer/strict citation checker passed. L2 team extra-field then topology validation failed; L3 was not submitted. |
| `fixed-6e2553` | 18 settled / 209245 | Router selected Research → Architect; Research alone ran and blocked after blind review revisions. No final research task success. |
| `continuity-300fa0` | 40 settled / 345739 | Hierarchical Research/Architect → Reviewer selected, two specialists ran; Research passed, Architect blocked, integrator not started. L3 code/Reviewer/7 host tests passed but exact receipt claim gate failed. Full run failed. |
| `receipt-visibility` | 2 settled / 2437 | Real DeepSeek responses extracted exact receipt identity/state/code hashes from the corrected Core projection. A targeted historical-data diagnostic, not a Worker or task rerun. |

Across nine task attempts plus the final targeted diagnostic, **160 calls** were
admitted: 159 settled and the one earlier reserved/unknown attempt. Known charged
usage is **1355207 Token**; unknown usage and currency cost are not imputed. The
separate microprobe remains 12 settled calls / 1414 Token. No additional live
calls are made after the fixed task/diagnostic ceiling is reached.

The combined continuity run had a maximum of three overlapping Worker ownership
intervals and two overlapping tasks; ownership is not a CPU-concurrency measure.
Both Thread follow-ups passed through without an extra `router_trigger` model
call. Host fault injection restarted the same Core state at an R2 wait, compacted
five real Router source messages and traversed typed history. Recall reached its
64-node bound and was incomplete. Both task/project links incorrectly referenced
beta; the later dispatch fix has programmatic coverage only. A pre-review Worker
claim was notified to the original main conversation, but the final reviewed
completion notification was not established.

The original `router_notice_dead_lettered` failure is preserved. An approval
notice dead-lettered after five attempts; the final completion notice had four
failed attempts and no processed timestamp. The forty-call hard admission cap
blocked further calls. This is not evidence of Provider transport failure.

The fixed and dynamic experiments span successive source fixes and n is tiny.
Single failed on an early path-repair defect; fixed failed on a later review
projection defect; dynamic reached parallel specialists but did not finish.
These are engineering diagnostics, not a controlled estimate of team-policy
quality. Full success, latency/cost advantage, causal recovery efficacy and
task-level model-routing superiority remain unmeasured. Vision remains unknown.
Reproducible fixed materials, independent checkers, source hashes and complete
failure denominators are retained to support a later frozen-source comparison.
