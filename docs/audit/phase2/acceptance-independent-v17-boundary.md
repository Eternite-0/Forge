# Independent V17 historical boundary

V17 remains failed and Phase 2 incomplete on this source. The full frozen backend suite passed 1,405 tests with one warning in 427.75 seconds. Four static records and original test/evidence preservation checks pass. All 201 archived source files exactly match the original frozen byte/hash manifest; this archive is historical source, not current V18 source.

All three strict live runs failed before a Planner model call or Worker execution at `planning_failed:DomainError:Quest_is_too_large_to_plan`. Each retained two settled model attempts and five failed Planner inbox attempts. L1 used 4,233 tokens in 376.207 seconds, L2 4,366 in 376.101 seconds, and L3 4,844 in 376.139 seconds. There are no unknown attempts, plans, Workers, review passes or mid-execution restarts. Read-only usage snapshots each had total_changes=0.

Passing backend checks do not change the original strict failures. Later versions cannot promote this V17 boundary. See the JSON for the original manifest, source archive, frozen test records and complete read-only usage audit hashes.
