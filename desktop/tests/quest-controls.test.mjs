import test from "node:test";
import assert from "node:assert/strict";
import { teamReassignmentProposal, userQuestTransitions } from "../src/questControls.ts";

const schema = { type: "object", properties: { result: { type: "string" } }, required: ["result"], additionalProperties: false };
const team = {
  roles: [{ id: "coding", task_node_key: "implement", required_capabilities: { coding: 70 }, tools: ["file_read"], input_tokens: 100, output_tokens: 200, execution_token_budget: 300, max_steps: 4, max_tool_calls: 2, max_duration_seconds: 60, depth: 0, dynamic: false, responsibility: "Implement assigned work", input_contract: ["approved node"], output_schema: schema, acceptance_criteria: ["valid evidence"] }],
  limits: { max_members: 1, max_depth: 0, max_concurrency: 1, max_total_tokens: 300, max_total_tool_calls: 2, max_cost_microunits: null, currency: null, allowed_tools: ["file_read"] },
};
test("team reassignment preserves the recorded contracts, tool scopes and limits in the existing wire format", () => {
  const wire = teamReassignmentProposal(team, "greedy");
  assert.deepEqual(wire.limits, team.limits);
  assert.deepEqual(wire.roles[0].required_capabilities, [{ name: "coding", minimum: 70 }]);
  assert.deepEqual(JSON.parse(wire.roles[0].output_schema_json), schema);
  assert.deepEqual(wire.roles[0].tools, ["file_read"]);
  assert.equal(wire.roles[0].task_node_key, "implement");
  assert.equal("execution_token_budget" in wire.roles[0], false);
  assert.deepEqual(team.roles[0].required_capabilities, { coding: 70 });
});
test("an unrepresentable cumulative allocation or incomplete role contract cannot silently change a team", () => {
  assert.throws(() => teamReassignmentProposal({ ...team, roles: [{ ...team.roles[0], execution_token_budget: 900 }] }, "greedy"), /累计 Token/);
  assert.throws(() => teamReassignmentProposal({ ...team, roles: [{ ...team.roles[0], output_schema: null }] }, "greedy"), /合约不完整/);
});
test("task controls never offer execution or completion transitions reserved to the trusted runtime", () => {
  for (const state of ["proposed", "awaiting_approval", "approved", "queued", "running", "blocked", "paused", "needs_input", "reviewing", "completed", "failed", "cancelled"]) {
    assert.equal(userQuestTransitions(state).some(target => ["approved", "queued", "running", "reviewing", "completed"].includes(target)), false);
  }
  assert.deepEqual(userQuestTransitions("running"), ["blocked", "paused", "needs_input", "failed", "cancelled"]);
  assert.deepEqual(userQuestTransitions("completed"), []);
});
