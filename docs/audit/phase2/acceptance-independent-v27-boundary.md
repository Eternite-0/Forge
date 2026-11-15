# Independent V27 historical boundary

V27 remains a historical failed boundary. Phase 2 is not accepted on this source.

Frozen suite: 1878 passed, 2 failed, 0 errors, 0 skipped, 1 warning(s), 777.34 seconds; suite gate FAILED. All 249 archived source files match the original byte/hash manifest. The archive is historical source, not the current source version.

- Original test failure: `tests.test_delivery_diagnostics_v22::test_delivery_bound_feedback_reports_numbers_and_preserves_reserved_review_cycle`.
- Original test failure: `tests.test_worker_intelligence_phase2::test_outer_worker_timeout_keeps_actual_gateway_attempt_unknown`.

- 20261115T141947Z-L1-777a30: strict failed; 29,134 known tokens, 0 unknown attempts, 40.381 seconds; 0 Workers, 1 retained Planner rejection events; diagnostics: .
- 20261115T141947Z-L2-d4cf0c: strict failed; 20,838 known tokens, 0 unknown attempts, 28.08 seconds; 0 Workers, 2 retained Planner rejection events; diagnostics: planning_failed:DomainError:Invalid_graph_proposal.
- 20261115T141947Z-L3-cb0c7b: strict failed; 24,728 known tokens, 0 unknown attempts, 39.825 seconds; 0 Workers, 2 retained Planner rejection events; diagnostics: planning_failed:DomainError:Invalid_graph_proposal.

Four static records and the original frozen preservation observation are retained. Read-only usage audit snapshots each report zero database changes. Full record/hash details are in the adjacent JSON. Later source changes do not rewrite these original failures.
