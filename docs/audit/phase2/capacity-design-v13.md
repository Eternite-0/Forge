# Capacity design notes for v13

This is a read-only diagnosis of retained v12 failures. Planner preflight is **not implemented**. The current implementation scope remains a separately reviewed capacity preview; no budget or approval is expanded. Numeric observations and limitations are retained in `capacity-design-v13.json`.

| Observation | L2 draft report | L3 research |
| --- | ---: | ---: |
| Complete canonical packet bytes | 54,421 | 68,149 |
| Existing projected messages, placeholder system only | 51,017 | 65,302 |
| Complete receipt field | 23,997 | 34,631 |
| Artifact canonical content | 12,461 | 12,329 |
| Structured delivery | 4,038 | 6,953 |
| Constraints before / after existing projection | 7,998 / 2,997 | 7,725 / 2,991 |
| Ideal saving from repeated long string leaves | 406 | 532 |
| Remaining node allowance at failure | 37,061 | 40,238 |

The measured raw packet hashes remain `29cb5b35d1ac9f431064d9ffa137d92aa8c0717edebf51690725207f411f3575` for L2 and `f6d0fd266015410da021b87f4e9e8d9c91c005a3aef626e12b011ee29f15005a` for L3. These observations preserve every packet field. Historical review/PASS binding must continue to hash the original packet, even if a lossless wire representation changes.

Existing constraint projection already saves about 5 KiB. The original goal is distinct text and is not a substring of the original request. The artifact and delivery repeat conclusions semantically, but no measured delivery string of at least 64 bytes appears exactly or as a substring in its artifact. Omitting either would change the content. Exact path/hash deduplication yields only the small upper bounds above, before reference overhead. The L3 raw-packet cap also rejects before wire projection.

L2 allocated 130k/90k/90k under a 360k global cap, leaving 50k for coordination. The source node used 39,869 reported tokens including review. The draft node had a fixed JSON check but no fresh model review, then emitted `review_contract_or_budget_failed`. Its local 52,939 counter exceeds its 37,510 reported Worker usage by 15,429; this report does not establish the cause or infer an unsettled Gateway attempt.

L3 allocated 120k/55k/50k/110k/90k/85k/65k under 630k, leaving 55k. Research consumed 79,762 tokens including one genuine `NEEDS_REVISION` review. After its revised delivery, the fixed review-input floor was 48,956, exceeding the remaining 40,238 even before revision-receipt and verdict allowances. It therefore did not enter a capacity revision.

The minimum useful future Planner preflight needs a structured `required_input_paths` node contract and a verified input inventory. Current Planner input contains filenames as text, with no actual byte snapshot. Read only explicitly supplied local paths under existing governance bounds; verify actual bytes/hash rather than trusting manifest claims or the larger repository `source_bytes` associated with excerpts. New plans can then bind each node to original inputs or declared predecessor artifacts without guessing roles from objective prose.

Calculate a documented admission envelope from the same serialized inputs, source receipts, Provider framing and output contracts used at runtime. Include a complete Worker request, a fresh review and one bounded correction/review cycle where the contract permits correction. This is a reserve policy, not proof of future model usage. Unknown artifact sizes remain unknown. Reject an impossible envelope through the existing bounded Planner repair, preserving the original total cap and required specializations. Recheck with the Composer's actual selected model and contract.

The global check must include already charged calls, opening usage and unresolved reservations in every applicable Gateway scope. Declared node budgets plus an explicit coordination reserve must fit that residual allowance; a preview cannot refund calls or authorize spending. A new artifact-writing contract may put full conclusions in the artifact and keep the delivery concise with exact artifact/receipt references. It must preserve every assigned requirement and use the normal approval path.

Meaningful tests cover actual Unicode/escaping cost, manifest tampering and path bounds, an underfunded node with a valid global sum, one bounded repair within the same cap, unknown future output size, a later smaller model window, concurrent unresolved reservations, and unchanged legacy/manual contracts and historical packet hashes. No source or runtime state was changed for this analysis.

The subsequent shared-collector/preview refactor was checked against both retained failures using SQLite `mode=ro` and `BEGIN`. Both original raw hashes remained identical and `total_changes` stayed zero. Local configured-Gateway payload construction matched preview native-input accounting at 54,123 bytes for L2 and 68,412 for L3. The configuration used a placeholder endpoint, JSON-schema support and `reasoning_effort=none`; no Provider generation, credential lookup, reservation or HTTP call occurred. This checks serialization, not another live execution. No actionable source issue was found in the observed refactor. Focused/full validation remains owned by the implementation agents.
