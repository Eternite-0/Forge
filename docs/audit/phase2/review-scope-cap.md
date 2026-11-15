# Independent review scope and bounded verdict capacity

Date: 2026-11-15 (Asia/Shanghai). This is a local/Mock regression and read-only
diagnostic record, not a successful live rerun.

## Retained live failure

`20261115T012115Z-L2-8e64f5` stopped after 96.416 seconds. A SQLite connection
opened with `mode=ro` confirmed 19 settled Gateway attempts, 92,916 input and
10,436 output tokens, six successful live read/list tool calls, and no unknown
remote outcomes. No report artifact was produced. The first read-only
`source-research` node was still `review_pending`; its two dependent nodes had
not executed.

The first independent review requested creation of the final report from this
read-only research node. Its approved objective and role contract instead
required source analysis; the final report belonged to the later writing node.
The Worker retained its scope and explicitly declined to claim a write. Review
of the expanded revision then stopped at `review_context_limit` before a second
Reviewer model call. The run's earlier stop reason mentioning unsettled attempts
is a boundary observation; the final durable ledger contains no unknown attempts.

## Changes

The Reviewer system instruction now evaluates the current node objective,
success criteria and approved role contract. The original user request remains
available in full, supplies applicable constraints, and still governs a node
assigned the corresponding final deliverable. Missing assigned artifacts continue
to fail provenance checks before model judgment. Revision instructions grant no
additional authority.

The original `_packet` and PASS hash binding are unchanged. No source, receipt,
delivery or constraint is removed. Single-response output remains bounded by
8,192, the role cap and the model cap; it additionally fits the remaining model
context and original cumulative Worker token allowance. Adaptive capacity below
1,024 is refused, while an existing role/model cap below 1,024 remains valid.
Insufficient original cost authority still fails closed. Original step, time,
review-round, reservation and unknown-outcome rules remain in force.

For the native Gateway, the Core computes input reservation from the actual
Gateway payload using the same serialized-byte accounting as admission. This
includes schema instructions, response format and configured reasoning options.
The final output cap is reflected in that exact payload before admission.

## Exact retained L2 construction

Reconstruction used the existing database in read-only mode and made no network
call. Its unchanged packet is 59,601 UTF-8 bytes, SHA-256
`1b5e7f48459f751f9a79ca309762cb97da7c4e5ae38095f265d3c915aad04e4a`.
The six receipt entries contain no identical full source contents to deduplicate.
With the clarified scope instruction, fixed output 8,192 would reserve 67,953.
The adaptive native request reserves input 59,761 plus output 5,775 = 65,536,
within the unchanged context ceiling. Prior cumulative Worker usage is 61,083
against the original 130,000 allocation. This construction establishes capacity,
not Reviewer verdict quality or live task completion.

## Validation

The focused group covers source/constraint reconstruction, current-node scope,
missing assigned artifacts, native schema overhead, context and cumulative-token
boundaries, the 1,024 minimum and lower original output caps, exact actual Gateway
admission/settlement, bounded settled truncation, unknown results, artifact
revision and semantic feedback recovery. Model fixtures and HTTP MockTransport
remain explicitly offline; real SQLite, governed local tools and receipt chains
are exercised. Results are retained in
`review-scope-cap-tests.xml`; live rerun outcomes must be reported separately.
