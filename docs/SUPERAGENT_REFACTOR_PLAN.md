# FORGE Super-agent architecture correction

Date: 2026-11-16. Starting commit: `4b220d8`. Frontend is outside this change.

## Architecture decision

FORGE builds its own general-purpose DeepSeek-style Super-agent. Its product is
one continuous Router conversation with independently executing hidden Workers.
Quest, DAG, team contracts, approvals and evidence are internal infrastructure.
The Router dispatches independent tasks; it does not do detailed task execution.
An execution Worker may use a team internally. Codex and Claude Code inform the
execution harness, context and permissions, rather than the overall architecture.

Primary basis: [DeepSeek Harness article](https://x.com/DeepSeekHarness/status/2116857257245987236),
especially sections 2, 3, 5 and 6, and [official prototype](https://github.com/DeepSeek-ai/dsh-superagent-prototype)
at `8c32a9dc5d72c033816c6a8110b37f40cf94fba4`.
The article describes more than the public, single-repository DSH plugin implements.
FORGE adopts the two-layer principles, not its GitHub-only deployment or DSH backend.

## Completed differential audit

Detailed source evidence is in [DeepSeek research](SUPERAGENT_DEEPSEEK_RESEARCH.md),
[harness audit](SUPERAGENT_HARNESS_AUDIT.md) and
[validation baseline](SUPERAGENT_VALIDATION_BASELINE.md).

| Concern | Existing foundation | Gap and minimum correction |
| --- | --- | --- |
| Persistent Router | Stable `main`, Router ID, durable turns, snapshot/CAS, compaction | Add autonomous delegation to the actual Core lifecycle; keep the Router lightweight |
| Isolated Workers | Hidden session, per-run messages, leases and budgets | Add private per-task execution directories; keep approved input and receipt checks |
| Event triggers | Transactional events/Outbox, Inbox, fenced delivery, retries/dead letters | Durable source/Thread/Trigger intake with code admission and semantic cold-start decisions |
| Subscription/pass-through | Worker commands target one Worker | Many-to-many Thread subscriptions, direct follow-up delivery and explicit handback |
| Inline/Spill | Full message archives, exact hashes and bounded source reads | Apply bounded previews plus complete external content references to triggers/recall |
| Chain of Recall | Sourced lexical/semantic recall, archive links | Typed, bounded traversal across Thread, Trigger, Quest, Worker, event, source and project references |
| Persistent state | SQLite migrations and atomic state/event commits | Persist new trigger/subscription/mandate/watchdog state; use existing contracts and recovery |
| Watchdog | Background exception retry and Worker lease recovery | Independent supervision task, persisted health, timeout/task-exit detection, safe recovery |
| Workspaces | One authoritative workspace binding, optional labels | Generic local manifest/directory discovery and task association, independent of GitHub |
| Model intelligence/teams | Capability provenance, speed/cost, bounded composition | Reuse selection for autonomous independent task assignments rather than ask users to pick Agents |

The initial worktree was clean. The existing Registry, settings, workspace scope,
provider configuration, composition, transaction and migration baseline passed
163 tests. A configured local Provider proxy answered authenticated model discovery
and advertised the configured DeepSeek model; generation is still to be validated.

## Minimum-change implementation sequence

1. Add durable Trigger/Thread subscriptions and bounded full-content readback;
   reuse the existing transaction, event and Worker Inbox mechanisms.
2. Add typed Chain of Recall and private Worker execution directories; expose
   bounded read tools to the Router and Worker while preserving authorization.
3. Add a trusted host delegation mandate for new conversational tasks. Create
   a minimal internal execution contract and select a Worker through the existing
   Model Intelligence Registry/Composer. A mandate never impersonates `local_user`,
   grants external action approval or expands the existing workspace boundary.
   Existing manual plans and authenticated approvals retain their behavior.
4. Connect cold-start semantic filtering, direct follow-ups, timer/file/external
   intake and results to the single Router. Persist intake deduplication and
   processing state so restarts do not create duplicate tasks or lose events.
5. Connect an independent Watchdog and generic workspace association to Core
   startup/shutdown. Recover only replay-safe work; unresolved effects remain
   unresolved and block replay under the existing evidence mechanisms.
6. Test migration/rollback, dedupe/fanout/handback, scoped readback, autonomy
   boundaries, concurrency, restart, supervision and integration. Run bounded
   real Provider scenarios and retain redacted results, actual usage, failed
   observations and explicit limitations. No synthetic result counts as live.

## Acceptance

One user conversation accepts new work while hidden Workers run concurrently.
New work is dispatched without asking the user to manage sessions or allocate
Agents. Follow-up events reach subscribed Workers without Router inference.
Worker results return through the Router. State and object references survive
restart. Watchdog detects and handles crashes/timeouts without replaying unknown
side effects. Cross-project continuity uses recalled references rather than
requiring repeated task explanations. High-risk tool authorization is still
enforced at execution, independent of autonomous task scheduling.

No frontend rewrite, external runtime dependency, cosmetic Manager layer,
deletion of historical evidence, or manual Entire checkpoint is part of this plan.

## Implementation closure

The sequence above has been implemented in the existing backend. Final evidence,
the complete regression result, retained baseline failures and exact operating
limits are recorded in the [implementation report](SUPERAGENT_IMPLEMENTATION_REPORT.md)
and [acceptance index](audit/superagent/acceptance-index.json). The initial
readiness observation in this audit is historical; both final real Provider
scenarios now pass on the same recorded source snapshot.
