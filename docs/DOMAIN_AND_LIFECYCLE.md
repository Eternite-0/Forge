# Domain Model and Lifecycle

## Entity distinctions

| Entity | Purpose | User-visible |
|---|---|---|
| Workspace | Persistent local project/account boundary | Yes |
| Conversation | Stable user-facing message timeline | Yes |
| RouterState | Router identity, capsule and event cursor | Indirect |
| Quest | Durable goal spanning time | As task card |
| TaskNode | DAG unit with dependencies and acceptance criteria | Summary only |
| WorkerSession | Private runtime context | No separate chat |
| ModelProfile | Provider model plus capability/performance metadata | Model Library |
| RoleTemplate | Responsibility and tool contract | Agent Studio |
| AgentInstance | Role + model + session + assignment | Activity panel |
| Event | Append-only state/progress fact | Selected events |
| Artifact | File/output with hash and provenance | Yes |
| Evidence | Verifiable support for claims and completion | Review panel |
| Approval | User decision bound to action/plan | Approval Center |

## Quest state machine

`PROPOSED -> AWAITING_APPROVAL -> APPROVED -> QUEUED -> RUNNING`

Non-terminal states: `BLOCKED`, `PAUSED`, `NEEDS_INPUT`, `REVIEWING`.

Terminal states: `COMPLETED`, `FAILED`, `CANCELLED`, `REJECTED`.

Only the domain transition service may change lifecycle state. Illegal transitions must return a domain error and must not partially write state.

## Router message intent

- `CHAT`: ordinary conversation, no Quest mutation.
- `CREATE_QUEST`: new goal, plan proposal.
- `CONTINUE_QUEST`: resume or query an existing Quest.
- `UPDATE_QUEST`: alter a goal or constraint.
- `STATUS_QUERY`: summarize current work.
- `APPROVAL_RESPONSE`: identify an approval-related message; an authenticated user decision is required separately.
- `AMBIGUOUS`: request clarification when the target cannot be resolved safely.

The Router uses a structured model decision and deterministic validation; without a configured provider it fails explicitly. Mock tests do not validate real model quality. Ambiguous Quest references are not resolved by choosing the most recent Quest.

## Worker lifecycle

`PROPOSED -> APPROVED -> QUEUED -> RUNNING -> WAITING/BLOCKED -> COMPLETED`

The M5 native runtime stores approved assignments and checkpoints hidden Worker execution. Runtime runs move through queued/running/paused/blocked/review_pending, then succeeded only after independent verification; failed/cancelled are terminal execution states. A model result alone remains review_pending. A Worker cannot self-authorize additional tools or child Workers. Help and bounded reassignment create separately approved team proposals, preserve completed-node provenance and do not refund cumulative budget.
