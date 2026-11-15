# Phase 2 — Cumulative budgets and bounded Quest identity

Date: 2026-11-15 (Asia/Shanghai). Production edits are limited to the assigned
TeamComposer eligibility check and Router snapshot/commit/stability checks.
Tests use scripted Mock models, real SQLite and the native Worker path. No live
Provider call or historical Phase 1 report modification occurs in this subtask.

## Cumulative allowance versus per-request context

`TeamComposerEngine._candidate` stores `input_tokens + output_tokens` as a
member's `estimated_tokens`; `WorkerManager.start` uses that value as the
cumulative token limit across model turns and review. The former eligibility
check additionally required that entire allowance to fit one context window,
rejecting a finite 96,000-token assignment on a 64,000-window model even though
every actual request was bounded. Root updated the Composer prompt to distinguish
the cumulative allowance from each request's context limit.

Eligibility now requires known model context/output limits, a response output
cap below the context window and no higher than model output capacity. Cumulative
allowance still must fit team and approved node limits. Native Worker requests
use the minimum of remaining cumulative allowance, approved model context and
current registry context; ContextService fits input after reserving output and
tool overhead. Reviewer independently checks cumulative remaining tokens and
its actual packet against the approved context. No budget ceiling is removed
or increased by this change.

Ten new cases in `test_cumulative_team_budget_phase2.py` cover all four selection
strategies with a 96,000 cumulative allowance/64,000 context, aggregate budget
denial, unknown context/output denial, output equal to context denial, approved
node-budget denial and a native Worker with 12,000 cumulative tokens but a
4,096 request context/500 output cap. Its unverified summary remains
`review_pending`; the fixture does not claim domain completion.

The existing team/composer/reassignment/intelligence tests and these ten cases
passed **40 tests in 3.61 seconds**. [JUnit](cumulative-budget-tests.xml).

### Integration follow-up: finite cumulative node ceiling

The integration owner later raised `TaskNodeProposal.budget_tokens`' schema ceiling
from 100,000 to 1,000,000 because a node may need several request windows plus review
and repair. `GraphProposal.max_total_tokens` remains capped at 1,000,000, and
the sum of node budgets must still fit that graph total. The live scenario ceilings
remain L1 180,000, L2 360,000 and L3 630,000, including coordination and review.
Per-request context/output bounds, finite team/global limits and explicit approval
bindings continue to apply. Existing approved budgets are not increased in place.

Planner instructions now explain conservative UTF-8 admission reservations and
require budgets for cumulative input/tool results, independent review and repair,
leaving room for coordination within the remaining task allowance. This schema
change provides no extra live allocation or unlimited budget; an earlier L1 run
with a lower approved node budget keeps that contract and its eventual outcome.
The integration owner's graph/cumulative focused group passed **31 tests**;
[JUnit](cumulative-node-budget-tests.xml). This is local/Mock evidence, not an
additional live run or a full-backend count.

## Quest identity versus general recalled facts

The earlier Router treated any omitted general-memory record as unresolved
Quest identity. The Phase 2 source index can produce many matching constraint
and message sources, so an unambiguous named Quest was turned into `ambiguous`
when the ordinary fact packet reached its item limit. The opt-in continuity
probe's offline fixture exposed this after increasing its finite admission
ceiling enough to reach the scripted response.

The Router now performs a separate bounded recall for task/Core Quest records.
The MemoryService output label for those records is `source_type=core_record`
and `kind=task`; storage uses `source_type=quest`. The lookup returns at most
100 items/65,536 bytes. Existing candidate collection still stops at 512 before
source filtering: a hidden matching Quest behind that bound remains unresolved.
General memories retain their context and related Quest IDs. Ambiguity uses the
identity lookup's omissions/cursor, plus all known related IDs; more than one
candidate still requires clarification. An exact user-supplied ID remains
explicit. Snapshot stability checks include the new lookup to fence races.

Five new tests cover 60 matching constraint sources with one unique Quest;
two matching Quests; 101 identity results; 520 pre-filter message candidates
hiding a Quest; and the same capped search with an explicit user Quest ID.

The offline legacy continuity probe fixture now explicitly selects a finite
75,000-token ceiling for both outcomes. Invalid schema consumes the one allowed
repair, so its expected settled attempts are two. The opt-in manual script's
30,000 default is retained; a current source-heavy run may require its existing
explicit `FORGE_CONTEXT_PROBE_TOKEN_LIMIT` override. This changes no live result
or historical report, and makes no automatic Provider request.

Quest identity plus context/memory/Router/Planner and offline-probe regressions
passed **67 tests in 32.53 seconds**. [JUnit](quest-identity-probe-tests.xml).
Ruff lint and formatting passed for both source files and three changed/new
test files. The first diagnostic group remains at
`cumulative-budget-probe-tests.xml`: five test fixture/assertion issues and the
genuine ordinary-memory omission failure were corrected rather than hidden.
Focused groups overlap; only the integration owner's frozen suite gives a
full-backend count.
