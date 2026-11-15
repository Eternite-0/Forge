# V20 Composer read-only observations and replay

The original `20261115T101049Z-L1-6b5894` approved its plan but produced no team or Worker and failed strict acceptance. Its ten Composer calls settled; the last inbox error is invalid JSON. Rejected raw teams were not persisted, so their resource choices cannot be reconstructed.

Complete actual native Composer input reservations were 23,871–24,308, including the existing 64-byte allowance; with the actual 8,192 output reserve, the maximum was 32,500, below 65,536. This is not a request-context overflow observation.

The approved node allocated 153,902 tokens after a Planner measurement of 149,806 with output 2,048 and a 4,096 role margin. These counterfactual measurements execute the original verified ZIP code against the same complete persisted source contracts; they are not the rejected real responses:

| Output ceiling | Schema | Complete requirement | Allocation minus requirement |
| ---: | --- | ---: | ---: |
| 2,048 | planner_legacy_default | 149,806 | 4,096 |
| 2,048 | composer_displayed_strict | 150,394 | 3,508 |
| 3,072 | planner_legacy_default | 151,854 | 2,048 |
| 3,072 | composer_displayed_strict | 152,442 | 1,460 |
| 4,096 | planner_legacy_default | 153,902 | 0 |
| 4,096 | composer_displayed_strict | 154,490 | -588 |
| 8,192 | planner_legacy_default | 162,094 | -8,192 |
| 8,192 | composer_displayed_strict | 162,682 | -8,780 |

Increasing 2,048 to 8,192 adds 12,288 in this read-only initial-plus-one-correction cycle. Selecting the displayed strict schema adds 588 for this identical full role contract. Smaller complete contracts can fit; the resource rules have no necessary arithmetic contradiction. Resource/schema choices remain a concrete rejection risk under the fixed approved allocation.

All source ZIP entries were rehashed against the original manifest. SQLite used mode=ro, query_only and BEGIN with zero changes; no Store, Provider call, credential read, source edit or existing-evidence overwrite occurred. The adjacent JSON binds source, driver, database, ledger and replay details. These observations do not accept Phase 2 or any failed V20 task.
