# V22 raw failure and settlement timing boundary

The original L1 driver flags `remaining_workers_blocked_with_unsettled_model_attempts` and `quest_status_running` remain unchanged. Its final settlement wait reports zero unsettled attempts after 0.761 seconds; the independent final snapshot has ten settled Gateway attempts and zero final unknown usage.

The earlier intermediate unsettled snapshot and exact settlement timestamps were not persisted. Final settlement cannot reconstruct that earlier instant, invalidate the original flags or prove successful execution. L1 remains failed: its Worker is blocked with `context_budget` after three steps, three tool calls and two repairs, with no independent review. The new typed delivery-rejection event count is zero in this run. L2/L3 retain their original Planner failures.

The adjacent JSON binds the original driver bytes, all original failure flags, exact settlement-wait fields and independent final accounting. This addendum reads only finished JSON artifacts, performs no database or Provider operation and changes no original record. Phase 2 remains incomplete.
