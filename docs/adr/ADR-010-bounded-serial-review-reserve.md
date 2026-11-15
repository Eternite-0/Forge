# ADR-010 — Explicit sharing of serial review capacity

Status: implementation under verification; no live acceptance is asserted here.

## Problem

The original Phase 2 scenario totals remain 180,000 / 360,000 / 630,000 tokens.
Every Provider request, including Router, semantic recall, planning, team
composition, Worker execution and independent review, counts against the original
durable Gateway ledger. Unknown requests retain their reservations.

The first capacity policy allocates a complete worst-case review and correction
cycle permanently to each node. This is safe for independent allocations but can
reject a serial chain even when its observed usage is much smaller. Offline V17
measurements retained in the audit directory show this rejection; they do not
establish that the task itself requires that many model tokens.

## Decision

Add an explicitly approved `serial-capacity-v1` policy. It is available only for
one connected success chain and an approved team concurrency of one. The plan
declares a finite baseline per node and one shared emergency reserve. Their sum,
plus the coordination reserve, must fit the original Quest ceiling. A baseline
must cover the measured initial Worker request and output allowance. Each node's
approved execution ceiling is its baseline plus the shared reserve.

The reserve is allocated once in each applicable Worker accounting scope, with a
durable marker. Starting another node allocates its baseline without allocating
that same reserve again. Existing allocations, actual charges and unresolved
reservations are never reset or refunded by this policy. Legacy contracts retain
their original allocation rules.

At each start, Core rechecks the approved plan/team identity, serial dependency,
current durable usage and remaining coordination capacity. It protects the
baselines of all unfinished successors and requires room for the current complete
review and correction cycle. It admits only an execution ceiling within the
approved maximum. Actual requests and fresh review still pass through the same
Gateway with the original total cap. Unsettled model attempts block another
serial start. This policy grants no new tool permission or success evidence.

## Consequences

The shared reserve can be exhausted. It does not guarantee that every node can
take every worst-case repair branch. Exhaustion produces a truthful blocked
result and preserves its evidence. The 65,536-byte review packet limit, complete
source provenance, source recall, independent current PASS, fixed semantic tests,
bounded retries and user plan/team approvals remain mandatory.

Cost-capped plans are excluded from this first version. Serial execution does
not demonstrate parallel execution; that capability requires its separate tests.
Final acceptance must identify the frozen source and distinguish local protocol
tests from actual model runs, including all failed runs.

## Verified delivery and adaptive planning boundaries

The explicitly scoped `worker_read_completed_delivery` tool returns the full
eight-field original delivery of a named explicitly approved success-path ancestor. Its graph
node and approved team member bind the same finite
`required_predecessor_deliveries` list. Core checks the producer's succeeded run,
original result event and assistant message, delivery SHA-256 and approved
revision lineage, then rechecks completion evidence. The complete delivery and
metadata count as source bytes in Worker and independent review contexts. Core
does not manufacture a file or receipt for this logical handoff. Its conclusions
remain untrusted claims; the successor still needs its own actual execution and
fresh acceptance.

Router may choose the structured `replan_remaining` action for observed feedback
only when the approved graph is stopped and partly completed, with all attempts
settled. A pending replacement team or plan prevents another request. The
durable request binds the original feedback event and parent plan. Planner uses
a claim token and revalidates that binding before staging a remaining DAG.
Completed contracts, original evidence and counters are preserved. Normal user
plan and team approvals remain necessary, and the revision chain is limited to
eight. Historical packets and ordinary graph canonical forms remain unchanged.

## Explicit Core resource quantification

The capacity Planner wire offers `allocation_mode="core_measured_serial"` as
an explicit alternative to its legacy `model_declared` allocation. The model
still supplies the complete semantic DAG, role objectives, tools, success
criteria, input paths, predecessor handoffs and source/artifact/delivery byte
bounds. Core does not change those fields, the coordination reserve or the
original task ceiling. This mode supports only the same strict token-only
success chain. Model-supplied node budgets and serial-pool numbers are resource
placeholders rather than approved contracts.

Core measures every node with the production native request serializers and
the same complete source/review/correction policy. Initial baselines cover the
measured initial work plus a declared 4,096-token role/framing allowance; one
shared pool covers the largest remaining complete contingency. Its role/framing
allowance is also repeated for both independent reviews and every declared
correction request (one for read-only work, two for artifact-writing work).
These explicit policy margins reserve capacity, not predicted actual usage;
the actual selected team's complete framing is still measured separately.
Only resource numbers are iterated, for at most eight deterministic passes. A
pass must remeasure precisely the same resource vector before it is accepted;
non-convergence, packet/context overflow or original-cap overflow rejects the
proposal through the existing single repair opportunity. No extra model or tool
calls, receipts, budget reservations or allocation refunds occur in sizing.
The final ordinary preflight is mandatory. Its concrete baseline/pool numbers
and sizing provenance enter the digested proposal for normal plan/team approval.

For a remaining DAG, Core preserves the original pool and every completed node
baseline exactly. An unfinished baseline is the maximum of its measured initial
work and its complete cycle minus that unchanged pool. The original accounting
and residual-budget checks remain active. A parent without an approved serial
policy cannot silently become a serial revision through this mode. Ordinary
manual and legacy model-declared proposal behavior remains unchanged.

## Autonomous semantic planning protocol

The background Planner now explicitly selects `resource_allocation_mode="core_measured"`.
Its strict wire contains only `allocation_mode="core_measured"`, semantic nodes
and edges. Node budgets and graph token/coordination/pool numbers are absent from
that schema. Model output therefore cannot select the legacy resource algorithm
or invent a larger task cap. Manual/legacy generation retains its existing wire
and explicitly selected serial mode.

Core binds the original source/operator token-ceiling intersection and a total
coordination reserve of ten percent (rounded upward). A single node or non-chain
DAG receives separately funded complete cycles. A strict success chain receives
the existing shared serial reserve. New autonomous contracts declare 1,024 tokens
of role/framing headroom for each initial, review and correction stage; legacy
serial sizing retains its 4,096-token policy. All resource numbers must converge
within eight measurements, fit the original ceiling and current durable residual,
and pass the ordinary final preflight. Actual selected team contracts are measured
again before approval. These margins are policy allowances, not usage forecasts.

The model still owns all semantic choices, source paths and byte bounds. Core
does not shrink an excessive source/delivery/artifact bound, remove a requirement,
rewire edges or reduce review/correction rounds. Invalid own-output input paths
and actual cycle witnesses are reported through a bounded pure diagnostic helper;
normal validators remain authoritative. Legacy and original-source evidence is
preserved.

A deterministic `InvalidGraphProposal` after the one allowed model repair marks
that background notice terminal with its actual notice-attempt count. Both real
candidate rejection events retain their source lease, call identity, response
hash and safe diagnostics. This prevents five notice deliveries from silently
turning one bounded two-call stage into ten model calls. Transport failures,
unresolved usage, unsent busy handling and execution fencing retain their
existing bounded accounting rules. A terminal planning notice never claims
execution or successful acceptance.

## Typed criteria and deterministic source sizing

V20 replaced arbitrary criterion strings with a typed verifier kind and a
nullable artifact path. Production V21 removes the duplicated path: the model
selects the verifier kind and the node's explicit `expected_artifact` once.
Core serializes those exact choices into the existing verifier ID. It does
not choose a verifier or substitute a weaker criterion. Manual and explicitly
selected older wire contracts retain their original representation.

The production model chooses input paths, predecessor deliveries and complete
artifact/delivery bounds. Core derives the source-receipt bound from those
choices using the same measurement function as capacity preflight: complete
canonical immutable receipts, full producer artifact/delivery bounds, and the
existing finite metadata allowance. This removes model byte arithmetic from
the wire; it does not shorten content, reduce output bounds or alter a prior
approved contract. Excessive derived bounds still fail the original limits.
The derived source bound and its provenance enter the normal digested plan for
approval. These decisions address the retained V19 planning failures; they do
not themselves establish a passing live task.

## Single dependency declaration and approved Composer resources

Production V21 expresses each dependency once, in the model's topological node
order. A predecessor declaration selects its exact node key, edge condition,
artifact-read flag and full delivery sources. Core translates that declaration
into the existing canonical edge and input fields. Artifact paths come from
the selected producer's explicit output; full delivery sources may include
its approved success ancestors. Verified file inputs must belong to the actual
inventory. Unknown, self, forward, cyclic or inconsistent output dependencies
are rejected. Core does not reorder, add or remove model choices. Runtime's
review/correction loop remains separate from DAG dependencies.

For newly measured plans, the production Composer submits semantic roles and
strategy. Core binds each role's single-call output ceiling to the approved
measurement and computes cumulative input as the node baseline minus that
output. Source ceilings bound steps, tool calls, duration, depth and concurrency;
the serial reserve enters the team total once. Historical/manual allocations
retain their existing contract. The unchanged TeamCompositionService rechecks
coverage, capabilities, tools, actual native framing, residual budgets and user
approval. An allowance measured with one output ceiling cannot be silently
replaced by a larger model-selected ceiling.

After its one repair, a deterministically invalid production team proposal
terminates that notice. Safe durable rejection records contain the call and
response hashes, schema field locations and numeric capacity diagnostics. They
contain no raw model contract text. Transport failures, unresolved usage and
ownership fencing keep the existing conservative handling. The acceptance
driver can observe a terminal failed Composer stage without waiting for its
entire polling timeout; it retains the original failed acceptance outcome.

## Diagnostic projections and delivery validation feedback

V21 exposed two feedback failures. A malformed criterion prevented the sole
Planner repair from seeing independent excessive source selections. Worker
delivery byte violations shared a generic `ValueError` branch with JSON parsing,
so feedback incorrectly described them as syntax errors. The original failed
runs remain unchanged; their unsaved full responses cannot be reconstructed
from truncated echoes.

V22 adds a diagnostic source projection with strict dependency/output validation
and the same authoritative byte formula. It can report all bounded source
overflows even while the full criterion wire remains invalid. It never supplies
a missing verifier, creates a proposal, changes model selections or grants
approval. Invalid projections report unavailable diagnostics. Full wire and
normal capacity admission remain mandatory.

Worker validation distinguishes canonical delivery capacity from JSON parsing
and schema errors. Capacity feedback reports actual and approved byte counts;
independent schema feedback uses bounded field locations and validator codes.
The complete eight fields, source references and approved size limit remain
required. Accurate feedback creates no new correction call, token allowance or
review exemption. Existing conservative held review/correction reserves remain.

## Input acquisition and complete task context

The V22 live L1 request exposed a missing cumulative stage. Preflight measured
one source-bearing synthesis request, while runtime first asked the model to
select the source reads. After that actual call settled, the full synthesis
request exceeded its remaining initial allowance by 666. Compaction removed
the source bodies before their first synthesis, and repeated file reads
exceeded the original receipt bound. The V22 run remains failed.

V23 measures acquisition and synthesis separately through the existing Worker
message/native request constructors, including the assistant tool-call frame.
Their cumulative initial allocation and the unchanged complete review/correction
reserve must fit the original ceiling. Every individual request must also fit
the original context. This corrects an omitted stage; it neither increases the
task cap nor transfers protected review resources to initial execution.

Worker objectives may refer to the original request. V23 therefore supplies the
two bounded current task sources, original request and latest goal update, when
their existing message IDs, user role, conversation and hashes validate. Runtime
and preflight share that source loader and message constructor. These are task
data, not authority, and are retained through the existing context archive.
There is no accumulation of all historical messages. Missing or altered bound
sources fail closed; legacy contracts without source bindings keep their shape.

A versioned exact criterion decoder and complete diagnostic projection address
the separate Planner validation ordering failure. Only explicit known verifier
selections with matching output paths can compile; no default or weaker verifier
is supplied. Diagnostic-only measurements grant no proposal or execution right.
The complete rejected draft is subject to original native request admission;
silent repair-draft truncation is removed for the new protocol.
