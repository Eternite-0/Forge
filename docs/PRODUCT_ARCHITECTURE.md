# Product Architecture

## Product definition

FORGE is a self-built, general-purpose DeepSeek-style Super-agent. Its product is
one continuous conversation with a persistent Router that organizes private
Workers and reacts to events. Users do not open sessions, allocate Agents, or
approve routine internal plans to start work.

The primary architecture reference is DeepSeek Harness's *Exploring Super-agent
Design: Towards a More Autonomous Agent System* and its [official prototype](https://github.com/DeepSeek-ai/dsh-superagent-prototype).
The [source audit and minimum-change plan](SUPERAGENT_REFACTOR_PLAN.md) fixes the
reference revisions and records the difference between the article and the
GitHub-scoped prototype. FORGE owns its implementation and generalizes project
identity beyond GitHub. Codex and Claude Code inform the execution harness.

## User-facing continuity versus internal concurrency

The product intentionally separates the conversational surface from execution concurrency:

- **Conversation**: the stable user-visible timeline.
- **Router**: the persistent user-facing Agent identity. It receives all user messages and owns the response.
- **Quest**: a durable objective inferred or explicitly requested by the user.
- **Task Node**: an internal execution contract; autonomous dispatch uses coarse independent assignments without DAG edges.
- **Worker Session**: a private, isolated execution context assigned to one or more task nodes.
- **Event**: a durable fact about state transition, progress, request, result or failure.

A single conversation can contain multiple simultaneous Quests. The Router resolves follow-up references using active Quest state, recency, project context and explicit user references. If ambiguity is material, it asks one concise clarification rather than silently attaching a message to the wrong Quest.

## Router responsibilities

1. Maintain conversational continuity and the Router Task Capsule.
2. Classify each message as chat, create, continue, update, status or approval.
3. Resolve the target Quest.
4. Identify independent work and choose bounded assignments, roles and models.
5. Delegate execution under a source-bound host mandate and existing contracts.
6. Preserve tool authorization independently from task scheduling.
7. Dispatch hidden Worker Sessions; detailed execution planning belongs to Workers.
8. Consume Worker events and decide which ones merit user-facing delivery.
9. Coordinate recovery or request input on blockers and changed user goals.
10. Produce one coherent user-facing response.

The Router is not a monolithic prompt with an ever-growing transcript. It operates over bounded recent turns, a compact capsule, Quest state, event cursor and retrieved evidence.

Quest, plan, team and approval records support execution internally. They are not
the product's organizing interface. Existing authenticated manual workflows remain
available for compatibility; normal conversational delegation uses a distinct
`router_mandate:<id>` authority, never a fabricated human approval.

## Event routing and persistent state

Durable sources admit user, timer, file, result and external events. The Fast Path
checks source policy and actors in code, deduplicates ingress, and delivers
subscribed Thread events directly into Worker Inboxes. The Slow Path asks the
Router to ignore, dispatch or attach only events without a subscribed destination.
Thread subscriptions are many-to-many. Workers explicitly hand back unrelated
events rather than handling them outside their assignment.

SQLite transactions keep Conversation, Router, Worker, Quest, Event, Trigger,
Thread, subscriptions and host mandates recoverable together with the existing
CAS and Outbox boundaries. Inference runs outside write transactions. Ingress,
dispatch leases and deliveries are fenced and idempotent. An event arriving while
a Worker drafts its final result is consumed before result publication.

Small source content stays inline; bounded previews carry references and hashes
for spilled content. `read_trigger` and `recall_object` retrieve bounded slices
without dropping the complete source. Typed Chain of Recall references connect
conversations, requests, projects, tasks, runs, events, evidence, receipts and
archives. Every hop checks conversation and task scope; references convey context,
not permission to act.

## Worker visibility policy

Worker sessions are hidden by default. The UI may show a task/activity panel with Worker role, state, progress summary and artifacts, but never exposes each Worker as a separate chat that the user must manage.

Workers send structured messages:
- `accepted`
- `progress`
- `help_requested`
- `blocked`
- `artifact_ready`
- `review_requested`
- `completed`
- `failed`

The Router decides whether to surface an event immediately, batch it, or wait for the final result. Important blockers and approvals are surfaced immediately. Routine progress can be coalesced to avoid spamming the conversation.

Each Worker owns private messages and a per-run `cwd`, temporary directory and
output directory. Fixed process tools use that environment. This is process and
directory isolation, not an OS security sandbox. Workspace read/write boundaries,
network allowlists, action intent/receipt evidence and risky-action approvals
remain enforced by the Tool Engine.

## Recovery and local project identity

A separate Watchdog monitors loop health, leases, Workers and environment state.
It restarts exited loops with bounded backoff and requeues expired Workers only
when existing accounting and action evidence permit recovery. Unknown upstream
or tool effects remain blocked against replay. The default CLI also has a parent
process supervisor that cleans up the child process tree before restart; the
native desktop host retains its existing private bootstrap protocol.

Stale heartbeats report stalled pending loops without cancelling or duplicating
them. The parent handles process exits; it does not detect and restart a live
but fully hung event loop. Native desktop and direct uvicorn execution do not
gain the CLI parent. Semantic goal-drift review by an independent model Watchdog
Session is not implemented.

Workspace discovery recognizes explicit local project paths, common manifest
markers, Git directories and ordinary folders. Associations persist separately
from tool grants. A uniquely named project in the original request is associated
automatically; multiple distinct named projects do not silently select one root.

## Model intelligence

The Model Registry is a resource catalog, not a role catalog. A model can serve many roles; a role can be assigned to different models. Capability scores must carry provenance, sample count, timestamp and confidence. Unknown is not zero and must not be fabricated.

Autonomous composition reuses the existing measured capability, context, output,
speed and cost selection. The Router chooses how many independent assignments
are useful, within a host limit of four; the Composer selects eligible models.
Tool, duration, concurrency and host contract allocation limits remain enforced
across the team. Cumulative token stopping is operator-configurable and defaults
to accounting only; real acceptance explicitly enables its cumulative ceilings.
Local GPU status is available through a fixed read-only diagnostic tool; it does
not imply GPU compute scheduling.

Current autonomous Workers have fixed local tools rather than unrestricted
terminal execution. Browser and MCP retain their separately authorized HTTPS
interfaces and are outside the default delegation allowlist. Timers and file
watches are registered by trusted local host integrations, not natural-language
model tools. Workers can plan steps within their own loop; automatic nested-team
creation is not implemented. The implementation report records the exact tested
tool, recovery and model-selection boundaries.

## Reference implementation boundaries

DeepSeek Super-agent defines the overall two-layer architecture. Codex informs
harness boundaries, execution, tool safety and context compaction. Claude Code's
public repository and documentation inform subagent contracts, hooks and
permissions; its complete core runtime is not public. FORGE does not shell out to
these products as execution backends. Offline protocol tests and bounded real
Provider observations are recorded separately; neither establishes arbitrary-task
correctness or unlimited autonomy.
