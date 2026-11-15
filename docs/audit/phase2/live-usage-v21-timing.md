# V21 raw failure and settlement timing boundary

The original L1 driver flag `remaining_workers_blocked_with_unsettled_model_attempts` remains unchanged. The final driver settlement wait reports zero unsettled attempts after 0.002 seconds, and the independent final snapshot has nine settled Gateway attempts with zero final unknown usage.

The earlier intermediate unsettled snapshot and exact attempt settlement timestamps were not persisted. Final settlement cannot reconstruct that earlier instant, invalidate the original flag or prove successful execution. L1 remains failed: its one Worker is blocked after three steps and two repairs, with no independent review. L2/L3 remain their original Planner failures.

The adjacent JSON binds all original failure flags, the exact driver bytes, original settlement-wait fields and final independent accounting. This addendum reads only finished JSON artifacts, performs no database or Provider operation, and changes no original record. Phase 2 remains incomplete.
