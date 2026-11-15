# Independent live study audit — v16

The original 90-row study scores are independently consistent: **25 task successes and 65 task failures**. All 90 rows completed their model stages; stage completion is distinct from task success. No original score or sample was changed.

The independent checker uses its own task algorithms and answer/source/constraint validation, importing neither the study scorer nor any FORGE module. It recomputed the fixed dataset and oracle hashes, verified both script hashes against the v16 frozen manifest, checked all 90 persisted row records, and matched every task-success score and reported summary. There were no mismatches, over-budget rows or unresolved attempts.

Observed usage is **91,487 input + 19,756 output tokens across 135 model calls**. The sum of independent row caps is 1,440,000 tokens. Cost remains unknown.

| Case | Expected answers | Successful rows / 15 |
| --- | --- | --- |
| code-1 | `[0, 7]` | 14 / 15 |
| schedule-1 | `[2, 2, -1]` | 0 / 15 |
| governance-1 | `[1, 0]` | 9 / 15 |
| code-2 | `[-1, 2, 8, 4, -1]` | 0 / 15 |
| schedule-2 | `[9, 3, 1, -1, 3]` | 0 / 15 |
| governance-2 | `[2, 1, 2]` | 2 / 15 |

The low scores reflect actual wrong answers. For `(0,-2)`, the code returns `2`, while ten code-2 rows returned `-2` at that position. The schedule-1 single-model sample returned `[-1,3,3]` instead of `[2,2,-1]`. The governance-1 sample counted a revoked write, returning `[1,1]` instead of `[1,0]`. Source citations and valid JSON did not establish correct reasoning.

Governance false acceptance counts were 4/6 for model claims, 3/6 for receipt verification, and 3/6 for independent review. Independent review also rejected two correct candidates. The fixed oracle gate had zero false accepts/rejects on its six samples; this is a property of the stated fixed specification and cannot establish general safety or correctness.

The study is a small single-model controlled policy comparison. Variants use independently sampled candidates and unequal observed call counts. Dynamic recomposition additionally receives deterministic failed-check feedback. Those differences prevent attributing its three successes to team selection alone. No variant demonstrates broad superiority, and none certifies autonomous complex-Quest acceptance.

The JSON companion includes exact original artifact hashes, all 90 row references, independent expected/returned answers, per-case distributions and limitations. No Provider calls, database reads, credentials or resampling were used in this audit.
