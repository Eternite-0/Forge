# Remaining Phase 2 requirements — v16 planning audit

This is a read-only audit of the original sprint request and v15 source/evidence,
before v16 changes. It does not alter historical evidence or claim new live results.

| Final standard | V15 implementation and proof boundary |
| --- | --- |
| One durable conversation | Implemented; live scenarios retain a single main conversation. |
| Understand complex goals | Router task classification and source-bound Planner exist; v15 complex outputs remain incomplete. |
| Create Quests | Implemented and observed in all three v15 live runs. |
| Plan a DAG | Implemented and live-observed; remaining-running-DAG replanning is missing. |
| Select registered models | Implemented with tool/capability checks; current live inventory has one requested model, so diverse-model selection quality is unproven. |
| Form adaptive teams | Approved role/model contracts and same-DAG replacement exist; bounded hierarchical expansion and remaining-DAG changes are missing. |
| Parallel background work | Real concurrent SQLite/lifespan local tests exist; v15 real scenarios did not complete a genuinely parallel multi-Quest workflow. |
| Structured collaboration | All ten message types map to durable events with assignment identity checks; message-specific required payload fields are incomplete. |
| Detect and repair errors | Bounded repair exists; historical L1 has live self-repair, while v15 whole-task acceptance fails. |
| Independent review | Real fresh-context same-model review exists. A different model was not a hard requirement. |
| Automatic rework | NEEDS_REVISION creates bounded same-assignment revision; observed in v15 L3, without final PASS. |
| Work after compaction | Source-bound Router/Worker archives exist; v15 Worker archives lack source-readback calls, and full complex continuity is unproven. |
| Cross-task memory | Source indexing, filters, conflicts and bounded lexical recall exist; real semantic retrieval and cross-task live reuse need implementation/proof. |
| Continue after Core restart | Historical bounded continuation segments exist; no entire complex task is accepted through restart. |
| Trustworthy evidence | Receipt, source hash, immutable approval and current-review gates exist; they correctly reject the incomplete v15 tasks. |

## Required scope versus implementation choices

The original sections 3.1, 4.3 and Planner role explicitly require changing a plan
after execution feedback and replanning the remaining DAG. This is a real gap.
Bounded hierarchical teams and dynamic specialist expansion are also explicit;
arbitrary recursive or unbounded Agent creation is expressly disallowed.

Section 7.3 requires semantic retrieval. It does not prescribe embeddings, a learned
index, a separate model or a new dependency. A bounded model semantic selector over
verified candidates, followed by current-source revalidation, can satisfy it. Pure
keyword aliases cannot be relabeled semantic retrieval.

Coding requires reading/writing/modifying code, running tests and fixing errors.
The fixed numeric-AST tool demonstrates a constrained example. A narrowly governed
patch-and-test interface can meet the coding loop without exposing arbitrary shell
or unrestricted repository execution; those broad execution powers were not asked
for and must not become prerequisites for Phase 2 completion.

The current model-performance module already measures task/tool/retry/schema,
average/P95 latency, tokens, costs when known, and review pass rate with source
references, sample counts and confidence intervals for rates. Repeating the v6
claim that all these metrics are absent would be incorrect. Missing price remains
unknown, and transport success is never task success.

Multimodal tasks and concurrent multi-Quest operation appear in section 3.3. They
need explicit capability handling and bounded proof, not an unsupported claim that
the current text-only proxy actually accepts image input. Observed service limits
are not the service's true maxima; no unbounded probing is justified.

## Minimal implementation priorities

1. Admit source-bound task limits and complete review/revision feasibility before
   approving a team; keep original scenario totals and source/PASS evidence.
2. Add real semantic recall with one bounded Gateway call, strict candidate-ID
   validation, no truth promotion, and source revalidation after the model returns.
3. Validate native ACP payloads for the ten supported message classes while keeping
   historical nonexecution events readable; no new coordination chat or authority.
4. Add approval-bound remaining-DAG revisions retaining completed nodes, receipts,
   budgets, immutable history and unresolved-operation fences.
5. Demonstrate controlled code revision/test repair and full actual compaction,
   source recall, restart and successor continuation on the same complex live run.
6. Run the fixed live study below; preserve failures and unknown observations.

## Live algorithm study design

Use a versioned, fixed dataset spanning code-contract analysis, integer scheduling,
and source-based architecture comparison at three difficulty levels. Public task
instructions and visible inputs are separate from deterministic hidden scoring
fixtures. Dataset/scorer/code hashes are recorded before any live call. Each case
and variant gets the same maximum total Gateway-token budget, context ceiling,
tool and repair limits; all coordination, compression and independent-review calls
count toward that cap. Equal caps do not mean equal observed token use.

Team variants are single model, static team, greedy capability selection, coverage,
complementarity and dynamic recomposition. With only one live model, record that
model-choice assignments can be identical; such a study measures coordination
policies on one model, not diverse-model selection gains. Do not invent model
profiles or call the one-model result a capability-specialist comparison.

Context variants are full context, recent truncation, conventional summary,
task-centric compression and task-centric plus original-source recall. Summary
generation is a real budgeted call. The same exact preselected task constraints,
decision/evidence obligations and distractors apply across variants. Evaluate
actual returned task answers, citations and recovered obligations, not summary
similarity. Full-context cases that cannot fit retain their real budget failure.

Governance variants are model claim, receipt verification, fresh independent
review and evidence-gated completion. Run identical fixed tasks that can produce
incorrect or insufficiently evidenced answers. Independently score the final
accepted/rejected decisions against hidden deterministic test outcomes; the
reviewer's own verdict cannot serve as ground truth. A receipt proves a tool ran,
not that its output solves the task.

Report per-case success, false acceptance/rejection, evidence completeness, actual
token usage, latency, repair/review counts and known-cost coverage. Unknown price
or unresolved usage remains null. All runs use the original Gateway, never a
direct proxy client. Use new output paths, retain failed cases, and label the small
single-model sample exploratory. Repeating a run until it passes is not evaluation.

No Provider calls, credentials or runtime databases were accessed by this audit.
