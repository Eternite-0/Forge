# M6 context and recall experiment (synthetic)

Run from `backend`: `.venv\Scripts\python.exe scripts/run_context_study.py`.
The fixed study exercises the production `ContextService` and `MemoryService`
in isolated SQLite workspaces and reopens each database. Six cases cover a
long-log scenario, multiple Quests, an old constraint, conflicting and expired
records, restart, and ambiguous Quest names with instruction-like text. They use
actual Core Quest rows and events, while explicitly synthetic manual memory
sources remain hypotheses of unknown freshness. The critical user instruction
is retained verbatim and ordinary filler uses the assistant role.

For each case, the study compares task-centric output, a recency-only baseline,
full input, and offline `no_refs` and `no_constraints` ablations. Every arm
uses the same canonical JSON encoder and fixed policy, tools and output-reserve
envelope. The ablations alter only experimental copies. The study checks exact
marked constraint retention, references, designated revoked and expired sources,
Quest candidate count, Core Quest source retrieval, conflict flags and recovery
after reopening. Candidate count does not establish that a model will ask for
clarification or obey instruction boundaries.

In the recorded run all six task-centric cases retained the marked constraint
with references, excluded the designated invalid sources, reopened the database,
resolved both Core Quest sources, exposed two candidate Quests and flagged both
conflicting records. The no-reference and no-constraint ablations respectively
lose the measured references and marked constraint. Normalized task-centric
output was 32.59%–33.69% smaller than full input in these cases.

A separate Worker case uses a real local `ToolEngine.file_read` on a synthetic
14 KB file, a real persisted receipt, and Worker tool-call/result messages. Its
model execution mode is mock; no external model call occurs. The production
Worker context preserved the user instruction, receipt ID and result digest,
and the archived tool result was recovered and hash-verified after reopening.
The same canonical envelope measured 15,069 full bytes and 1,362 compacted bytes
(90.96% fewer) for this one case.

A prior study incorrectly represented tool logs as user messages; its script and
output remain at `.local/phase1/milestone6/context-study-v1.py` and
`context-study-v1.json`, and its results are superseded. Current case-level
output is `.local/phase1/milestone6/final2-context-study.json`. Bytes are not provider token
counts. These synthetic cases are smoke evidence, not proof of model quality,
generalization, or improvement on real workloads.
