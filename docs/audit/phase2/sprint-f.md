# Sprint F — Source-linked Constraints and Bounded Context

Date: 2026-11-15 (Asia/Shanghai). Integration owner: root agent. Scope: backend context,
memory, source-linked user constraints and offline retrieval evaluation. No Provider or
credential files were read by this subtask; no live model call was made.

## Audit result and changes

The M6 archive stored every distinct user message verbatim in the next capsule. It
preserved originals but increased every subsequent model input indefinitely. The early
Phase 2 Router stored extracted constraints in `memories` as hypotheses; a key derived
from the full statement did not associate different budget statements for conflict
detection, and those rows lacked source-content hashes or confirmation provenance.

The existing SQLite archive, source-integrity checks, transaction boundaries, Gateway,
and Worker tool-pair protection are retained. The Phase 2 v10 migration also installs
`constraint_records`, `constraint_message_index` and an incremental source cursor.

- `ConstraintService.index_message` associates the current original user message with
  the Router-selected Quest. Reindexing is idempotent; changed source content and an
  attempted conflicting reassignment fail closed. Message metadata supplied by a caller
  does not establish Quest association.
- A bounded literal extractor copies at most eight short clauses per user message.
  Its origin is `explicit_user`; model-derived paraphrases remain `pending_model`.
  A separate confirmation operation requires another user message containing both a
  confirmation expression and the exact clause. Confirmations have their own source ID
  and hash. These labels describe instruction provenance, not domain facts or approval.
- Records retain original message IDs, SHA-256, Quest association, dimension, origin,
  optional timezone-aware expiry and revocation state. Budget/deadline/language/retention/
  scope/model dimensions allow **potential** conflicts to remain visible. A changed,
  missing, expired or revoked source is excluded. Conflicting statements are not resolved
  by silently choosing the newest one.
- Router archives retain at most 64 user highlights within 12,288 encoded bytes and 16
  other highlights within 4,096 bytes. Individual user highlights are bounded UTF-8
  excerpts. Initial anchors and recent items are favored; omissions and source-recall
  entry points are recorded. Duplicate messages have at most four source references in
  a highlight. Originals remain in `messages` and source archives.
- Worker archives bound claims and completed-tool highlights, retain the initial task
  contract and live tool-call/result pair, and trim archived highlights to the actual
  input budget before committing. Trusted receipts and original worker messages remain
  in their durable stores. Bounded sourced constraints are included in Worker context;
  archived Router rebuilds refresh constraint records from the current source store.
- Per-Quest evidence and receipt snapshots are capped at 64 entries each and explicitly
  indicate omissions. The independent evidence gate continues to use authoritative
  execution records; a context snapshot is not completion authority.
- Memory indexing processes at most 128 source messages per batch. It stores source
  hashes, terms and bounded clauses rather than another copy of the full corpus.
  Archived original messages are therefore retrievable after their highlights leave
  the capsule. L0 labels current interactions; archived sources use L1 for an associated
  Quest or L2 for unassociated history. Existing Quest, project and failure indexing
  retains the L1/L2/L3 paths.
- Recall supports bounded keyword/source/Quest lookup, a small bilingual alias expansion,
  timezone-aware time filters and source-type filters. The alias dictionary helps with
  cases such as `spending` → `预算`, but this is **lexical retrieval**, not embedding or
  learned semantic retrieval.

## Verification

Commands run from `backend`, using its existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_constraints_phase2.py tests/test_context_m6.py tests/test_memory_m6.py -q --junitxml=../docs/audit/phase2/sprint-f-tests.xml
.\.venv\Scripts\python.exe scripts/phase2/retrieval_evaluation.py --output ../docs/audit/phase2/retrieval-evaluation-v2.json
```

The focused source/context/memory run passed **39 tests in 15.42 seconds** (8 new tests
plus the 31 existing M6 context/memory tests). These are offline deterministic/Mock
tests. They cover pending versus confirmed provenance, exact original-source IDs,
confirmation tampering, conflict visibility, expiry, revocation, Quest isolation,
transaction rollback, duplicate-reference bounds, UTF-8 budgets, and recall/readback of
an omitted message after three compactions and a storage reopen. The raw history in
that continuity test contains 240 messages; every original remains available.

Ruff check and format checks passed for this subtask's seven Python files. The root
agent runs the complete integration checks and records their frozen-source results.

The fixed `phase2-retrieval-v1` evaluation passed **9/9 exploratory synthetic cases**.
Dataset SHA-256:
`6d4dd52492b4bb9e55db3dcbff5f5bd49ddfa260665504356097eeede92a533b`.
The evaluator checks expected source identities and independently recomputed source
hashes after reopening storage. It covers bilingual budget/retention/deadline queries,
language, a file-path reference, a recorded decision, historical Quest identity,
Quest-associated constraints, and a time-exclusion query. The v1 diagnostic output is
preserved; v2 additionally records and validates actual source hashes. Neither output
contains model calls, external tool execution, model quality measurements or live
domain-task success evidence.

`tests/test_router_intelligence_phase2.py` adds separate Mock Router/Planner regressions
for bounded repair, atomic failure, constraint provenance, original-request integrity,
planning contract names, node limits and single-Router recovery. Their final integrated
focused run passed **14/14 in 1.68 seconds**. An initial run identified a missing
autonomous-Planner capability-name check; root integrated the fix and also checks generated
tools against the actual offered catalogue. Known-but-unavailable R2 tools, repeated invalid
plans, missing original requests and overlarge autonomous DAGs are covered.

The final combined B/F focused run passed **53 tests in 18.36 seconds**:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_constraints_phase2.py tests/test_context_m6.py tests/test_memory_m6.py tests/test_router_intelligence_phase2.py -q --junitxml=../docs/audit/phase2/sprint-bf-integrated-tests.xml
```

The complete backend suite remains root's integration responsibility. None of these
53 tests is a live-provider validation.

## Limits

The literal extractor is deliberately small and cannot establish the semantics of every
quoted, conditional or implied user instruction. Pending model extracts require source
review; a literal user clause is not automatically an authenticated action approval.
Potential-conflict dimensions may require user clarification. Confirmation/revocation
are Core service operations; this subtask does not add a public user interface for them.

The fixed retrieval corpus is small and exploratory. It does not measure unconstrained
semantic retrieval, learned relevance, Router task understanding, or model correctness.
Candidate pages, active-Quest snapshots and per-call indexing remain bounded. Long
histories can require multiple indexing batches or source reads; arbitrary-scale memory
and unbounded storage are not claimed. Omitted context never implies that the underlying
record, requirement, receipt or artifact does not exist.

Later scoped `file_revise` support does not rewrite context/source history. Original
messages, source snapshots, old intent content and receipts remain immutable; the
current bytes of the same Worker's own artifact may change only under separately
approved receipt-chain/hash checks. Review and read-only acceptance follow that
version chain before comparing the latest bytes. See the [D/E/G report](sprint-deg.md)
for local/Mock revision, Reviewer repair and lease-recovery evidence; these are
separate from Sprint F's historical focused results above.

## Follow-up: budget-aware settled tool-tail archival

A retained L2 source-analysis Worker blocked with 32,023 cumulative tokens left,
an 8,000-token output reserve and 2,136 bytes of tool definitions. Its actual
message input budget was 21,759 bytes. The old rule pinned the entire last tool
batch: initial messages plus raw tail were already 23,930 bytes, and even the
smallest task capsule/constraint packet reached 29,381 bytes. Removing every
old highlight could not resolve it. This was a real failure under the existing
recent-message policy and approved budget, not a model completion.

The minimal follow-up retains initial contracts and whole protocol exchanges.
Only when an archive already exists (so the current request already offers
`worker_read_context_source`) and the minimum pinned context still cannot fit,
the complete latest assistant call batch plus all results may be archived together.
Every call, intent and receipt must be successful and match the current run,
Worker, plan, revision and execution mode. Pending, uncertain, incomplete and
unreceipted tails remain live. Metadata explicitly records omitted raw results,
source ordinals, mandatory readback and untrusted status; it grants no PASS or
completion evidence. First-archive and irreducibly oversized-contract cases still
fail closed. Future source reads consume the original step/token/tool allowances.

The [read-only snapshot diagnostic](worker-context-tail-diagnostic.json) opened the
retained live database with SQLite `mode=ro`, made a temporary offline backup and
ran compaction only on that backup. With the same 21,759-byte input budget, the
new context was 12,326 bytes. Original messages 14/15/16 read back byte-for-byte
with their recorded SHA256 values. Worker state/budgets, evidence, receipts and
events were unchanged. No Provider or tool action executed, and the original
failed live run was not modified or relabelled.

Seven new synthetic-ledger cases cover whole-tail archival/readback, immutable
source tampering, unchanged counters/evidence, incomplete/pending/uncertain or
missing-receipt rejection, source-tool availability and mandatory initial contracts.
The [context group](worker-context-tail-tests.xml) passed **25 tests in 9.13 seconds**;
the [expanded context/constraint/Worker/revision/tool/memory group](worker-context-tail-integration-tests.xml)
passed **97 tests in 22.80 seconds**. Ruff check and format passed for the two
changed Python files. These groups overlap earlier results and are local/Mock
regressions, not live task-quality or frozen full-suite acceptance.
