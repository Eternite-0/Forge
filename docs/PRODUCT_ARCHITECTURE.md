# Product Architecture

## Product definition

FORGE is a goal-driven, persistent-router autonomous Agent system. Its primary interface is one continuous conversation. The user does not open or switch task sessions to work with different Agents.

## User-facing continuity versus internal concurrency

The product intentionally separates the conversational surface from execution concurrency:

- **Conversation**: the stable user-visible timeline.
- **Router**: the persistent user-facing Agent identity. It receives all user messages and owns the response.
- **Quest**: a durable objective inferred or explicitly requested by the user.
- **Task Node**: a unit of work inside a Quest DAG.
- **Worker Session**: a private, isolated execution context assigned to one or more task nodes.
- **Event**: a durable fact about state transition, progress, request, result or failure.

A single conversation can contain multiple simultaneous Quests. The Router resolves follow-up references using active Quest state, recency, project context and explicit user references. If ambiguity is material, it asks one concise clarification rather than silently attaching a message to the wrong Quest.

## Router responsibilities

1. Maintain conversational continuity and the Router Task Capsule.
2. Classify each message as chat, create, continue, update, status or approval.
3. Resolve the target Quest.
4. Plan and maintain Quest DAGs.
5. Compose a team from model capability profiles and role contracts.
6. Request approval under Review autonomy.
7. Dispatch hidden Worker Sessions.
8. Consume Worker events and decide which ones merit user-facing delivery.
9. Replan on blocker, failure, evidence gap or changed user goal.
10. Produce one coherent user-facing response.

The Router is not a monolithic prompt with an ever-growing transcript. It operates over bounded recent turns, a compact capsule, Quest state, event cursor and retrieved evidence.

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

## Model intelligence

The Model Registry is a resource catalog, not a role catalog. A model can serve many roles; a role can be assigned to different models. Capability scores must carry provenance, sample count, timestamp and confidence. Unknown is not zero and must not be fabricated.

## Reference implementation boundaries

DSH/Super-agent informs persistent Router, hidden Workers, durable task routing and event handoff. Codex informs harness boundaries, event-oriented execution, tool safety and context compaction. Claude Code public documentation informs subagent contracts, hooks and permission concepts. FORGE implements its own runtime and data model rather than shelling out to these products.
