# Independent V23 historical boundary

V23 remains a historical failed boundary. Phase 2 is not accepted on this source.

Frozen suite: 1710 passed, 5 failed, 0 errors, 0 skipped, 1 warning(s), 412.81 seconds; suite gate FAILED. All 231 archived source files match the original byte/hash manifest. The archive is historical source, not the current source version.

- Original test failure: `tests.test_m4_adversarial::test_expired_planner_attempt_cannot_modify_new_attempt[False]`.
- Original test failure: `tests.test_m4_adversarial::test_expired_planner_attempt_cannot_modify_new_attempt[True]`.
- Original test failure: `tests.test_m4_integration::test_user_turn_automatically_plans_once_without_extra_user_message`.
- Original test failure: `tests.test_worker_runtime::test_v7_worker_opening_migrates_once_with_known_reserves`.
- Original test failure: `tests.test_worker_runtime::test_v8_active_worker_enters_output_review_without_changing_team_or_budget`.

- 20261115T124831Z-L1-e59339: strict failed; 17,772 known tokens, 0 unknown attempts, 21.647 seconds; 0 Workers, 2 retained Planner rejection events; diagnostics: planning_failed:DomainError:Invalid_graph_proposal.
- 20261115T124831Z-L2-f05578: strict failed; 20,196 known tokens, 0 unknown attempts, 26.283 seconds; 0 Workers, 2 retained Planner rejection events; diagnostics: planning_failed:DomainError:Invalid_graph_proposal.
- 20261115T124831Z-L3-c7b618: strict failed; 23,763 known tokens, 0 unknown attempts, 31.023 seconds; 0 Workers, 2 retained Planner rejection events; diagnostics: planning_failed:DomainError:Invalid_graph_proposal.

Four static records and the original frozen preservation observation are retained. Read-only usage audit snapshots each report zero database changes. Full record/hash details are in the adjacent JSON. Later source changes do not rewrite these original failures.
