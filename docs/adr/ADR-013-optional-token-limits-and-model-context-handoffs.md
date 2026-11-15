# ADR-013: Optional cumulative token limits and model context handoffs

Status: implementation and acceptance in progress, 2026-11-15.

The user explicitly removed cumulative token budgets from the current sprint.
The default operator policy therefore does not stop execution at a workspace,
Quest, team or Worker token total. `FORGE_ENFORCE_TOKEN_BUDGETS=true` retains the
existing optional limits. Disabled mode reports `token_limit: null` and
`token_budgets_enforced: false`; it does not substitute an arbitrarily large
limit. Existing numeric plan allocations remain estimates and exact binding
records. Usage settlement, unresolved-attempt handling, concurrency, tool
authority, deadlines, review counts and model context/output ceilings remain
independent requirements.

The historical V27 runs used 180,000/360,000/630,000 cumulative limits and failed.
Their records remain immutable. New acceptance scenarios explicitly identify
the user's changed protocol; they cannot establish success under the old caps.
Every sent model attempt still needs known, settled usage for acceptance.

The context design references the open-source `openai/codex` repository at commit
`ee344ca9458351a6ea102795fa73110ad3ede5dc`:

- [Local compaction and replacement history](https://github.com/openai/codex/blob/ee344ca9458351a6ea102795fa73110ad3ede5dc/codex-rs/core/src/compact.rs)
- [Handoff summary prompt](https://github.com/openai/codex/blob/ee344ca9458351a6ea102795fa73110ad3ede5dc/codex-rs/prompts/templates/compact/prompt.md)
- [History and tool-pair maintenance](https://github.com/openai/codex/blob/ee344ca9458351a6ea102795fa73110ad3ede5dc/codex-rs/core/src/context_manager/history.rs)

FORGE adapts the handoff and checkpoint approach to its own Gateway and SQLite
archives. Model summaries carry progress, decisions, constraints, unresolved
work and references. Summaries are unverified context, with no ability to alter
approvals or evidence. Core rebuilds current task and approval state separately.
Original messages stay available through integrity-checked readback.

Compaction runs outside write transactions. Core atomically binds a successful
handoff to the exact source frontier and previous checkpoint. Empty, truncated,
oversized or failed responses do not advance the archive head. Complete source
batches are measured through the Gateway's native serializer. Tool calls and
results must retain valid pairing, and unresolved actions cannot be replayed or
turned into completion evidence by a summary.

Offline protocol tests establish implementation behavior. Real-provider task
continuation and full acceptance remain separate gates until recorded.
