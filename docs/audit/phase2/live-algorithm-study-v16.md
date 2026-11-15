# Fixed live algorithm microstudy — v16

The study is a controlled live-model policy ablation through the original FORGE
ProviderGateway and ModelCallBudgetLedger. It is separate from autonomous Quest
acceptance. The controller establishes an explicitly approved read-only source
assignment and reads the actual fixed files through ToolEngine, retaining original
immutable receipts. It does not manufacture `worker.result`, `task.verified`,
independent PASS records or Quest completion.

The versioned dataset has six fixed cases: code-contract evaluation, integer
scheduling and approval-state analysis, each at two difficulties. It runs six team
variants, five context variants and four governance variants: 90 independent
case-variant rows in total. Each row receives the same 16,000-token maximum,
16,384-context ceiling, 512-output ceiling, zero HTTP retries and at most two model
stages. The sum of all independent row caps is 1,440,000 tokens. Summary, review and
coordination calls all consume the same row's original Gateway ledger. A failed
admission retains its failure; the script does not enlarge a budget or rerun rows.

The separate deterministic oracle grades returned integer answers, exact source
identity/hash and valid line ranges, all required constraint labels and the
completion claim. It is never passed to candidates or reviewers. Dynamic
recomposition receives only failed-check names, followed by one bounded correction
call. Governing claims and review verdicts are scored independently; a reviewer
PASS is not ground truth. Source receipt checks verify current actual file bytes.
Duplicate receipts, changed source files and fabricated hashes fail validation.

All team selections call the existing TeamComposerEngine. The available live
inventory contains only `deepseek-v4.1-flash`, so assignment strategies can select
the same model. Their differences here concern call allocation and coordination;
this cannot establish diverse-model selection quality. The source-reader Worker
is controlled by the benchmark fixture, not an autonomous model tool decision.

The context variants use the same early source/constraint message and ten fixed
distractors. Conventional summary is a real model call. Task-centric variants
invoke actual `ContextService.compact_router`, reopen the Store and reconstruct
the capsule. Source recall additionally invokes `read_source` and checks the exact
original content. A source excerpt can lose facts; no answer is inserted to repair
that loss. Governance candidates are independently sampled per variant, rather
than replayed as shared candidates. This sampling choice is disclosed and the
small sample does not support a causal or statistically general conclusion.

Actual observed token use is reported separately from the shared cap. Incomplete
usage produces null totals, with known partial charges and unresolved reservations
retained in separate fields. Unknown price stays null. Configured context/output
ceilings are operator bounds, not measured Provider maxima.

The manifest is written before any live call and records the exact public dataset,
its hash, the hidden oracle hash, both script hashes, row/call limits and declared
limitations. Every row retains its own database, source files, receipts, model
attempts, status and candidate. Existing output paths are rejected. Native headers,
credentials and exception text are not saved.

After the integration owner injects the existing approved credential into its
process environment, run from the repository root with a new output filename:

```powershell
& .\backend\.venv\Scripts\python.exe .\backend\scripts\phase2\live_algorithm_benchmark.py --output .\docs\audit\phase2\live-algorithm-study-v16-results.json
```

The default credential reference is `FORGE_PROVIDER_KEY_LIVE_TEST`; the benchmark
does not load credentials from disk. Offline verification uses MockTransport,
clearly records `execution_mode=mock`, and performs no real Provider calls. See
`live-algorithm-benchmark-v16-final.xml` and the accompanying log for final test
results. These tests establish mechanics and falsification boundaries, not live
task success.
