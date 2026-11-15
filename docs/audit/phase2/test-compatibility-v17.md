# V16 full-suite failures: v17 compatibility proposal

The retained complete run is 1,304 passed / five failed / one existing warning in
328.69 seconds. All five failures are in the original M4 Planner tests; their
original schema, authorization, restart, no-Worker and lease-fencing assertions
must remain intact.

`test-compatibility-v17.patch` is a proposed change only. It has not been applied
and no proposed fixture has been run against the frozen source. Source and backend
tests remain unchanged pending integration-owner thaw.

Four failures arise because authenticated/background planning now explicitly uses
the capacity-aware contract, while the original fixtures return the old wire
shape with budgets of 10 or 100 tokens, no required-input/bounds/coordination
fields and no registered capacity-capable model. These cannot satisfy a real
Worker plus complete independent review/correction cycle. The race fixtures also
inherit the production model ledger default zero, which correctly leaves no
residual capacity. Replacing the service or mocking out preflight would bypass the
integration path under test.

The proposed patch adds one explicit Mock fixture helper and uses it only in:

- `test_expired_planner_attempt_cannot_modify_new_attempt` (both old-success and
  old-failure parameter cases);
- `test_authenticated_graph_controls_survive_restart_without_running_worker`;
- `test_user_turn_automatically_plans_once_without_extra_user_message`.

It registers a clearly labeled Mock profile with 65,536 context and 4,096 output,
sets an explicit 180,000-token local fixture budget, and returns one node with
150,000 cumulative allowance plus an 18,000 coordination reserve within the
180,000 total. It declares no input paths/tools/artifact and finite 2,048 source,
3,072 artifact and 1,536 delivery byte bounds. All values are test fixture
contracts, not measured production usage. The service's real schema validation,
capacity preflight, model eligibility and residual ledger checks still run.

Static AST comparison of the proposed complete test-file changes confirms all
original assertions are identical: 13 in `test_m4_adversarial.py`, 76 in
`test_m4_integration.py`. This does not prove the patch passes; after thaw it must
run the five original failures, both whole M4 modules and the capacity tests.

The fifth failure is different: `test_graph_planner_sends_strict_wire_schema_through_gateway`
correctly requires every object property to appear in `required`, with
`additionalProperties=false`. V16 added optional-default capacity properties to
legacy GraphWire/NodeWire, so their generated strict JSON schema violates that
Provider contract before response parsing. Its assertion must remain unchanged.
The Graph/schema owner is handling the source correction separately. Prefer a
legacy wire class with the original required fields plus an explicit capacity
wire subclass requiring the new fields, preserving local manual-plan defaults.
Do not fix this by weakening the test, removing `strict`, or silently ignoring
new fields at the Provider boundary.

This proposal changes no original run record and does not certify a live Planner
fix. Real strict live failures are separately diagnosed on their exact source.
