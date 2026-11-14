# Router, Quest and Worker Protocol

## Core loop

1. Load a bounded snapshot of RouterState, recent turns, Quest summaries and events.
2. Call the configured Provider outside the database write transaction for a structured decision.
3. Validate the decision and resolve Quest references; ask the user when multiple targets remain plausible.
4. Commit the user message, decision effects, response and applicable cursor changes atomically after version checks.
5. Authenticated user approval is required for governed actions; model output cannot grant it.

M4–M5 implement durable DAG scheduling, Worker event consumption and the native Worker Loop. Mock protocol tests remain distinct from real provider demonstrations; the current real JSON completion demonstration proves syntax validation only. Chain-of-Recall memory remains M6 work. Worker help creates a durable Router composition request and a pending team proposal; no model event grants approval.

## Message envelope

Every internal message should include:
- `message_id`
- `conversation_id`
- `quest_id` (nullable)
- `task_node_id` (nullable)
- `sender_type` and `sender_id`
- `message_type`
- `correlation_id`
- `payload`
- `created_at`

## Event envelope

Events are append-only and sequence-numbered. Consumers must be idempotent. Event delivery is at-least-once; handlers record their last processed sequence. Do not assume exactly-once delivery.

## Router-to-Worker messages

`ASSIGN_TASK`, `CANCEL_TASK`, `PAUSE_TASK`, `RESUME_TASK`, `REQUEST_STATUS`, `REQUEST_REVISION`.

## Worker-to-Router messages

`ACCEPTED`, `PROGRESS`, `HELP_REQUESTED`, `BLOCKED`, `ARTIFACT_READY`, `REVIEW_REQUESTED`, `RESULT`, `FAILED`.

## Notification policy

- Immediate: approval needed, blocked, destructive-risk warning, user clarification needed, final result.
- Coalesced: routine progress, repeated tool status, heartbeat.
- Silent by default: internal reasoning, raw chain-of-thought, low-level polling.
- Never expose hidden Worker private reasoning. Surface concise status, decisions and evidence.

## Quest resolution

Resolution priority:
1. Explicit Quest ID or task card reference.
2. Direct semantic match against active Quest title/goal and recent conversation.
3. Ask clarification if multiple plausible targets remain; do not guess a current Quest.

Do not attach an unrelated new request to the most recently active Quest just because it is recent.
