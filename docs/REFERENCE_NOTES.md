# Reference Notes — What to learn, not copy

## DeepSeek DSH / Super-agent
Study the public `dsh-superagent-prototype` for persistent Router, worker isolation, durable tickets, session messaging, notices/outbox and event-triggered work. FORGE generalizes the task entry point beyond GitHub tickets and builds a model registry/team composer.

## OpenAI Codex
Study the public Codex repository for Agent Loop, turn/item/event model, tool execution, permissions, context compaction and App Server separation. FORGE builds its own equivalents. Do not embed Codex CLI as a Worker backend.

## Claude Code
Use official public docs and public repository materials for subagent concepts, hooks, tool permission patterns and memory/context practices. Do not claim access to Anthropic's complete proprietary engine source.

## Architectural rule
A reference is not a dependency. Every borrowed concept must be re-expressed in FORGE domain types, tests and architecture decisions.
