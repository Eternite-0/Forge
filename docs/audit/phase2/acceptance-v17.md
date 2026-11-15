# Phase 2 continued acceptance — v17 (historical)

Date: 2026-11-15 (Asia/Shanghai). Integration owner: root.
Branch: `codex/phase1-continuous`. **The complete backend suite passed; all three
strict live runs failed before Planner model dispatch. V17 does not complete
Phase 2.** This is a historical record of the source below. Subsequent source
changes require their own versioned checks and do not rewrite these outcomes.
Frontend development runs separately; this backend acceptance round did not edit
or validate the frontend and does not establish desktop release readiness.

## Frozen backend result

The [201-file manifest](source-manifest-v17.json) has canonical SHA-256
`ebecda4c36c0ba005c2483e5242e417aba694b277dab83f9fa206c709c3c2e92`.
Its executable snapshot SHA-256 is
`1c6939df9771475b20cce875ddaa3018ea97c6e96cb026d6dd2c88f88e3d3475`.
All three final driver records have that executable snapshot hash. Backend source
remained frozen through this suite and the three runs; later source versions are
outside this acceptance record.

| Check | Result | Evidence |
| --- | --- | --- |
| Complete backend suite | 1,405 passed; zero failed, errored or skipped; one existing warning; 427.75 seconds | [JUnit](frozen-tests-v17.xml), [console](frozen-tests-v17.log) |
| Ruff | Passed | [Log](frozen-ruff-v17.log) |
| Formatting | All 198 Python files formatted | [Log](frozen-format-v17.log) |
| Installed dependency consistency | Passed | [Log](frozen-pip-v17.log) |
| Whitespace validation | Passed | [Log](frozen-diff-v17.log) |
| Fixed offline mechanism benchmark | Completed in 4.813476 seconds; zero Provider calls | [JSON](benchmark-final-v17.json) |
| Historical evidence and tests | 355 Phase 1 evidence files and nine original reports byte-identical; all 364 original test identities retained; 15 AST changes disclosed | [Preservation audit](preservation-v17.json) |
| Independent freeze observation | All 201 source entries, executable hash, static checks and preservation checked before subsequent source changes | [JSON](acceptance-independent-v17-freeze.json), [report](acceptance-independent-v17-freeze.md) |
| Independent historical boundary check | All 201 archived source entries rehashed; final full-suite and three failed strict records checked | [JSON](acceptance-independent-v17-boundary.json), [report](acceptance-independent-v17-boundary.md) |

JUnit reports 427.689 seconds; 427.75 seconds is the console measurement. The
warning is the existing Starlette/httpx TestClient deprecation. The independent
freeze observation explicitly covers source/static/preservation evidence, not
full-suite or strict live acceptance. The final XML and driver records are
separate evidence.

The suite includes explicit source-bound task constraints, complete native
request framing, predecessor delivery integrity, finite serial shared-budget
accounting, independent review, recovery, memory, Agent coordination and existing
governance/persistence regressions. Local Mock responses and scripted local HTTP
transport exercise mechanisms; they do not establish real-model task quality.
The offline benchmark is a fixed synthetic comparison with no statistical
generalization or task-success claim.

## Three strict live failures

The native FORGE Gateway used Provider `wb-local`, requesting
`deepseek-v4.1-flash`. Proxy-reported identity does not independently establish
the upstream model. All three runs kept their original scenario limits:
180,000 / 360,000 / 630,000 total tokens for L1/L2/L3, zero additional Gateway
HTTP retries and at most three review rounds. No credentials appear in this
report.

| Level / original run record | Strict result | Seconds | Model / tool calls | Charged input / output | Final unknown attempts |
| --- | --- | --- | --- | --- | --- |
| [L1 `20261115T092655Z-L1-c3a404`](../../../.local/phase2/runs/20261115T092655Z-L1-c3a404/run.json) | Failed | 376.207 | 2 / 0 | 3,661 / 572 | 0 |
| [L2 `20261115T092655Z-L2-350db9`](../../../.local/phase2/runs/20261115T092655Z-L2-350db9/run.json) | Failed | 376.101 | 2 / 0 | 3,747 / 619 | 0 |
| [L3 `20261115T092655Z-L3-bf61b1`](../../../.local/phase2/runs/20261115T092655Z-L3-bf61b1/run.json) | Failed | 376.139 | 2 / 0 | 4,127 / 717 | 0 |

Every final driver records `planner_did_not_produce_plan`. Its durable Planner
inbox reports `planning_failed:DomainError:Quest_is_too_large_to_plan`, with five
notice attempts and a final dead notice. The error occurs before Planner model
dispatch. Each final ledger contains exactly one `memory_semantic_retrieval`
attempt and one `router` attempt, both settled with known input/output charges;
there is no Planner attempt. Each run's four background notice retries therefore
represent repeated local failures, with zero HTTP retries, Worker self-repairs
or review revision requests.

Independent SQLite inspection used read-only, query-only transactions. In all
three databases the Quest remains `proposed`, plan revision is zero, and there
are zero plan, Worker and action-receipt rows. The only durable event types are
one `core.started` and one `quest.proposed`. The reader made zero local database
changes. These database observations agree with the final driver snapshots.
The separate [live usage audit](live-usage-v17-audit.json) retains its own
read-only reconstruction of the finished runs.

All three strict checks fail `completed_quest` and `approved_current_dag`.
Remaining task-specific checks are unknown because their required observations
never occurred:

- L1: one executed Worker, read-only tool receipts, ranked findings or an explicit
  insufficient-evidence finding, and independent delivery review.
- L2: model-generated DAG, three or four specialist Workers, actual source
  citations, artifact receipt/hash integrity, and independent content review.
- L3: model-generated DAG, the required research/mathematics/architecture/coding/
  experiment/review specializations, real `numeric_ast` receipts, independent
  fixed semantic acceptance, artifact hashes, restart continuity and context
  compaction continuity.

Budget and concurrency checks that pass without any Worker execution do not
demonstrate task completion. All runs have zero context archives, zero semantic
test runs, zero review results and only one Core start. There was no Worker
delivery, independent PASS, complex-task continuation or mid-execution restart.
The new serial allocation and Worker/reviewer mechanisms were not executed by
these three runs.

## Retained failures and evidence boundary

The original V16 full-suite result remains 1,304 passed / five failed / one
existing warning in 328.69 seconds. Its original failed XML/log and earlier live
records are retained. V17's disclosed compatibility changes and new strict wire
schema behavior were checked through the real local integration path; the passing
V17 suite does not retroactively change V16 outcomes or prove semantic equivalence
of edited test ASTs.

The separate semantic cross-Quest fixed sample retains its original failed oracle
result in [raw evidence](semantic-crossquest-v17-raw.json). Its [independent audit](semantic-crossquest-v17-independent.json)
supports the narrower source/trust-preserving retrieval observation for that one
sample. It does not turn the original oracle into a pass or prove broad memory
quality. Scripted HTTP Runtime tests likewise remain local transport evidence.

These three strict runs total six settled attempts, 11,535 charged input tokens
and 1,908 charged output tokens, with zero final unknown attempts. All cost fields
remain unknown (`null`), not zero. These ledgers do not resolve unknown historical
remote outcomes or establish a total historical cost.

V17 accepted live levels: **none**. Earlier successes belong to their original
versions. Required follow-up includes admitting the complete actual Router-created
Quest representation within finite Planner request/context limits, then proving
the original L1/L2/L3 execution and quality requirements on newly frozen source.
The retained V17 failures remain failures even if a later version passes.
