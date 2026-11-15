# Reference Traceability Matrix

This document distinguishes inspiration from copied code. FORGE has its own domain model and runtime.

| Reference | Publicly documented concept | FORGE adaptation | What not to copy |
|---|---|---|---|
| DeepSeek DSH Super-agent | Persistent main Session, Engine, Ticket lifecycle, Executor Sessions, notices/relay, durable outbox/effects | Persistent Router, Quest lifecycle, hidden Worker Sessions, event inbox/outbox and selective result relay | GitHub-centric Source/Ticket assumptions and DSH-specific runtime dependencies |
| OpenAI Codex | Shared harness, Agent Loop, thread manager, bidirectional App Server protocol, detailed event stream, compaction | Native Agent Loop, Core/UI boundary, typed event protocol, resumable Router/Worker context and compaction | Codex CLI as an execution backend or copying internal implementation |
| Claude Code | Subagent orchestration concepts, permission modes, project/user memory, multi-window continuity guidance | Role contracts, scoped tool permissions, memory hierarchy, task capsules and context-window recovery | Assuming public docs reveal all proprietary implementation details |

## DSH implementation areas to inspect
- `docs/007-architecture.md`: Runtime composition root, Engine, Store, Outbox, Notices and DshExecutor responsibilities.
- `docs/001-domain.md`: Trigger → Eval → Ticket → Assignment → Executor flow.
- `docs/003-execution.md`: Worker Session lifecycle and human-blocked stages.
- `src/app/notices.ts`, `src/app/outbox.ts`, `src/app/sessions.ts`: event relay, durable delivery and Session integration.

## Codex areas to inspect
- App Server article: process boundary, Thread Manager, core threads and bidirectional JSON-RPC notifications/requests.
- Agent loop article: model/tool loop and compaction evolution.
- Repository: inspect current source paths for thread state, event notifications, permissions and compaction; paths can change across versions.

## Claude Code areas to inspect
- Official subagents documentation for delegation and context isolation.
- Official memory documentation for project/user memory separation and imports.
- Official CLI/permissions documentation for tool allow/deny and approval modes.
- Public GitHub repository is not the full proprietary Claude Code engine source.

## Required engineering practice
Before adopting any reference mechanism, write an ADR with: problem, source reference, FORGE adaptation, security implications, tests and rejected alternatives.
