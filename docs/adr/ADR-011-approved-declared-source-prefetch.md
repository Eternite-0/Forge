# ADR-011: Prefetch approved declared sources with real Core evidence

Status: accepted design; V23 implementation and frozen acceptance pending.

## Problem

V22 lost its complete input context before the first synthesis because admission
omitted the model request that selected the reads. V23 staging measured that
request, synthesis, action completions and final delivery separately. A complete
constructed plan then required 375,493 units against L2's 360,000 ceiling and
667,495 against L3's 630,000 ceiling. These are conservative admission bounds,
not predictions of model token use. The failed measurements remain preserved.

Reversible Worker tool-text projection was independently measured on 192
complete requests. It preserved their information but saved too little to close
the budget gap. That projection is not being integrated in this candidate.

## Decision

Introduce an explicit `core-declared-source-prefetch-v1` policy for new plans.
Keep `native-input-roundtrip-v1` and all legacy contracts on their original
execution and measurement paths. A policy change requires a new digested plan
and team approval; existing approved contracts are never reinterpreted.

The Planner still selects the exact required file paths and predecessor
deliveries. After the Worker has been claimed and its approved bindings checked,
Core obtains that finite read set before the first model request. File reads use
the real governed ToolEngine and produce real intents and receipts. Completed
delivery reads use the existing Core reader and validate the producer's actual
successful delivery and evidence; they do not invent an action receipt.

Every prefetch has an explicit durable Core origin and exact admission binding.
It must not masquerade as a model request or a model-authored tool call. Complete
results and their provenance enter the Worker context as explicit Core source
data in user messages. Source content remains untrusted. No fabricated assistant
or native tool conversation is used to supply it.

Prefetch consumes the original tool allowance and obeys existing scopes, grants,
source byte limits, lease fences and approval checks. A restart may resume only
the unfinished, known-safe portion. An unresolved admitted action retains its
uncertainty and is not automatically replayed. Missing or altered bindings,
receipts, producer evidence or source messages fail closed.

Runtime, preflight and context sizing must share the complete source-message
serialization. Prefetch removes a pure source-selection model request; it does
not remove a required source or stage of actual computation. Numeric execution,
writing, final delivery and the full review/correction reserve remain measured.
Synthetic preflight values never create execution evidence.

## Action evidence sizing

The V23 Planner declares `action_evidence_allowance_bytes` between 0 and 32,768
for each node. This is a component of total source sizing, not a separate hard
pool, tool permission or record of an executed action. Core adds it to the
unchanged selected-source formula and existing metadata allowance. The total
source ceiling remains enforced against complete actual receipts. Plan, team,
member and Worker capsule must bind the same selected allowance. Original task
budgets, the 65,536-byte limits and review/retry bounds remain unchanged.

## Required verification

Before a completion claim, verify actual ToolEngine and completed-delivery
prefetch, precise budget consumption, restart idempotence, uncertain-call fences,
source and policy tamper rejection, archive/readback and completion evidence.
Then freeze the integrated source, run the full suite and all three original
strict live levels. A feasible constructed plan or scripted Gateway response is
offline evidence only. Real model repair, independent review, parallel execution
and restart continuity require their own recorded live evidence.
