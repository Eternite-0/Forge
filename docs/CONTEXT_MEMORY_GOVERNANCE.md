# Context, Memory, Governance and Evidence

## Context Compaction
The system must preserve task continuity, not merely summarize chat. A Task Capsule includes goal, success criteria, confirmed facts with evidence refs, hypotheses with status, decisions, completed/pending steps, blockers, constraints, artifacts, next action and source event cursor.

Compaction:
1. Snapshot authoritative state and recent events.
2. Generate structured capsule.
3. Validate required fields and references.
4. Archive old messages rather than destroying them.
5. Rebuild context from policy + capsule + relevant evidence + recent turns.
6. Record token counts, compaction version and recovery checks.

## Chain of Recall
Router recall indexes Quests, decisions, artifacts and evidence. Every recalled fact includes source and freshness. Distinguish verified facts from model hypotheses. On conflict, prefer newer verified evidence and surface uncertainty.

### M6 implementation in progress

The Core derives a project source from the bound Workspace, Task sources from
Quest records, decision and failure sources from a fixed set of Core events, and
artifact sources from trusted evidence records. Working context remains the
unarchived Session messages; Quest records are L1, the Workspace source is L2,
and failure experience is L3. Event payloads and evidence remain hypotheses
about their content: a trusted record establishes that Core recorded an event or
verification at that time, not the truth of a domain claim or the current state
of a file. The public memory write interface rejects a caller's attempt to label
an arbitrary claim confirmed. A recall result identifies sources, timestamps,
freshness, conflicts and related Quests; unknown freshness remains unknown.

Source indexing is incremental and transactional. Event, Quest and evidence
cursors advance with the derived index; source snapshots are hashed and checked
against current Core rows and plan revisions on read. Revoked sources are
excluded. Each synchronization processes a bounded batch and reports whether
more work remains. Retrieval uses indexed terms and a bounded candidate page;
its cursor and omission count signal incomplete results and must not be treated
as proof that an omitted Quest does not exist. Candidate ambiguity must remain
visible to the Router. This is still under integration and acceptance; see
[current status](STATUS.md) and the [synthetic study](PHASE1_MILESTONE6_CONTEXT_EXPERIMENT.md).

Only evidence with a recorded plan revision is indexed as a plan-bound source.
The general `ArtifactVerifier` can create records without that revision; these
are indexed as historical, explicitly unbound observations and cannot establish
the current plan's completion or the present contents of a file. Events without
a resolvable plan revision are skipped rather than assigned the Quest's current
revision. The index is derived data and is not an authorization or
task-completion authority.

## Governance
Default Review mode. Policy engine, not model, grants permissions. Risk levels:
- R0 read-only analysis.
- R1 reversible writes inside approved workspace.
- R2 external side effects or shared resource mutation.
- R3 deletion, credential access, payment, production action.
R3 defaults to deny or explicit secondary confirmation.

Approval is bound to a specific plan/action and parameter digest. Agent cannot elevate its own permissions. Budgets cover tokens, cost, time, tool calls and concurrency.

## Evidence
Task completion requires evidence from trusted tools or independent verification. Model claims are not evidence. Test evidence records command, environment, exit code, report path and timestamp. Evidence graph links Task, Worker, ToolCall, Artifact, TestRun, Decision and Approval.
