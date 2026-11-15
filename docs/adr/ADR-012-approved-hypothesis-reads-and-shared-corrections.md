# ADR-012: Approved hypothesis reads and shared correction allowance

Status: implemented for V26; actual-model acceptance pending.

The V25 L1 run acquired its three complete declared inputs through real governed
Core reads. Its first model response then selected a tool batch rejected by the
unchanged complete-source byte cap. That rejected batch was not persisted as an
assistant selection, so the historical evidence does not identify its exact
tools. A self-repair request followed, but the initial-call allowance could not
fund another complete source-bearing request while retaining the full separate
review correction reserve. The original failure remains a failed run.

Initial self-repair and later reviewer-requested correction now share the
already approved correction-call allowance. The first correction request records
the current immutable reservation insertion frontier. Later non-refunded Worker
calls consume that allowance even when timestamps coincide or the clock changes.
An exhausted allowance cannot be replenished by a later repair or review request.
The original cumulative budgets, source/output caps, review limits and approval
requirements still apply. Existing events without a frontier retain their
timestamp interpretation. Core-prefetch prompts explicitly distinguish completed
reads from further model-selected actions.

Cross-Quest recall exposed a separate protocol gap: a remembered path could enter
the new goal as a hypothesis, but a Worker with an empty initial verified inventory
had no approved way to reopen that path. V26 therefore lets the Planner explicitly
select `hypothesis_read_paths` for each pending node. The resulting exact map is
bound into the approved plan, team, member and Worker capsule. Paths are bounded,
unique, exact workspace-relative names. They cannot also be declared verified
inputs or predecessor artifacts. A nonempty scope requires the approved
`file_read` tool and a positive action-evidence allowance.

These paths remain unverified references. Core does not prefetch them or add them
to the verified inventory. The model must select the actual read, which passes
the normal Tool Engine, workspace governance and complete cumulative source-byte
checks. Capacity measurement prices the subsequent source-bearing request using
an empty observation placeholder and the full selected evidence allowance; it
does not read the file or fabricate a receipt. The current delivery still needs
its own independent review. Older contracts without an explicit hypothesis map
retain their declared-input and owned-artifact scope.

Offline tests cover native tool execution, absence of recalled bodies in the first
request, exact scope and capsule binding, oversized or unapproved reads, shared
correction accounting, and independent completion through local HTTP. These are
protocol tests, not evidence of actual-model or general cross-task learning.
Frozen full-suite and real Provider results are recorded separately.
