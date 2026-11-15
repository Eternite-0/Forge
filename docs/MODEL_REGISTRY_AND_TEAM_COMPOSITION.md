# Model Registry and Dynamic Team Composition

## Model Profile dimensions

Identity: provider, model_id, display_name, version, endpoint, enabled.
Capabilities: reasoning, math, coding, architecture, research, writing, design, vision, tool_use, instruction_following.
Performance: TTFT, tokens/sec, p50/p95 latency, reliability, timeout rate.
Economics: input/output/cache price, currency, updated timestamp.
Limits: context window, max output, rate limits, concurrency.
Features: streaming, tool calls, JSON mode, image/audio/video input.
Provenance: provider metadata, FORGE benchmark, online observations, user override.
Uncertainty: sample_count, measured_at, confidence interval or confidence label.

Never fabricate model capabilities or pricing. Provider metadata may be unavailable; retain `unknown`.

## Benchmark dimensions
- Math: answer correctness and proof/derivation rubric.
- Coding: tests, hidden tests, patch correctness and regression.
- Planning: dependency completeness, feasibility, rework rate.
- Research: source validity, coverage and factual consistency.
- Tool use: schema accuracy, success and policy compliance.
- Design: visual tasks, constraint coverage and human blind review; text-only self-ratings are insufficient.
- Speed: real TTFT and generation speed measurements.
- Cost: observed usage multiplied by verified price metadata.

## Role Template
A role defines objective, responsibilities, input contract, output schema, required capabilities, allowed tools, risk class, context budget and acceptance criteria. It must not hard-bind one provider/model.

## Team composition
Initial explainable baseline:
1. Filter enabled, healthy models compatible with modality/tool requirements.
2. Score task fit using capability profiles with confidence weighting.
3. Apply cost, latency, context and concurrency constraints.
4. Select role coverage and penalize redundant capabilities/coordination overhead.
5. Return top candidates and explain selection.
6. In Review mode, ask the user to approve the plan/team.

Later research: constrained set selection, complementarity, online routing and dynamic re-composition. Compare against single-best-model and fixed-team baselines.

## M5 implementation and evidence

The current bounded composer implements single_model, greedy, coverage and complementarity strategies with capability provenance/confidence, context/output, exact tool scope, token, currency/cost and concurrency constraints. Unknown measurements remain unknown. Eight preset roles coexist with complete, bounded dynamic contracts; every approved DAG node has one member. Model identity never grants permissions.

Team approval binds the exact plan and selected profile hashes. Worker help can request a separately approved replacement; only settled unfinished assignments may be replaced, with at most three replacements per Quest and no budget refund. Completed nodes retain their original run and historical approval evidence. Execution requires a current approved team; a separate verifier-only historical lookup permits proved superseded approvals, excludes revoked contracts and checks run creation against approval/supersession time.

The synthetic-team-v2 experiment compares four strategies and confidence/coordination ablations on three fixed scenarios. It measures selection mechanics, not real provider task quality. See [M5 report](PHASE1_MILESTONE5_REPORT.md) for actual results and limitations.
