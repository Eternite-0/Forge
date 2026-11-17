# Conversation subagents

FORGE's permanent Router conversation is the entry point. A delegated Worker
is inspectable evidence of work, not a second user-facing task inbox. Keep the
main chat, draft, approvals, and files in place while inspecting it.

## Interaction model

- Show a compact conversation entry only when a real delegation is recorded.
  A normal reply, a proposed role, and a generic background event do not create
  a subagent card.
- Show what each subagent was assigned, its recorded execution state, and what
  it returned when that is available. A returned Worker result is not approval
  or proof that the overall task is complete.
- Drill into one run by its durable identity. Returning to the list or closing
  it is navigation, not cancellation. Do not offer a stop action that would
  silently cancel the parent task or other runs.
- A state change must not replace the selected run with another agent. Ended
  runs remain inspectable while present in the retained history, including
  after reconnect. This follows the [Codex TUI picker principle](https://github.com/openai/codex/blob/main/codex-rs/tui/src/app/agent_navigation.rs#L229);
  FORGE uses a different visual grouping.
- Existing workspace-wide operational activity and approval controls remain
  separate from the conversation's scoped subagent view.

## Reference and scope

The [Grok Bot reference ChatView](https://github.com/nickvasilescu/grokbot-clone/blob/7ee6a7ed8464311dbb467bb9ee7c76d9286e56be/web/src/components/ChatView.tsx)
keeps the transcript and composer in place beside an independent details
surface. Its child Bots are hidden; the [Codex subagents documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
informs FORGE's visible active/ended list and drill-in. The result is a FORGE
adaptation, not a copy of artwork or a claim of pixel parity.

Only the server's conversation-to-task-to-run relationship establishes scope.
Do not infer ownership from the active sidebar selection, a workspace-wide
page of runs, or proximity to a chat message. A source message can locate the
request that authorized a task when known; a missing source does not authorize
guessing a turn. Do not display raw execution logs or internal reasoning in
the subagent inspector.

Verify no-agent chat, multiple related runs, a run failure, a second scope,
connection changes, and refresh/restart separately. Browser fixtures establish
UI behavior, not model execution; offline Core and native app/provider tests
are distinct acceptance levels.

## Active work and finished history

The list endpoint accepts `phase=all|active|terminal` (`all` by default).
Active includes queued work, blocked or paused agents, work waiting for review,
and running agents whose lease expired. Only an authoritative succeeded, failed,
or cancelled state places a run in terminal history. Filtering takes place
before paging, so a newer block of finished runs cannot crowd out older active
agents.

A cursor belongs to one workspace, conversation, optional project, and phase.
An expired or mismatched cursor restarts that list rather than being applied to
another scope. Exact run detail does not depend on phase and remains readable
when the run moves between active and terminal.
