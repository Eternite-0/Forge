import test from "node:test";
import assert from "node:assert/strict";
import { AgentBuckets } from "../src/agentBuckets.ts";
import { validateAgentPhase } from "../src/conversationAgents.ts";
const run = (id, state = "running") => ({ run_id: id, state, lease_status: "active", outcome: { kind: state, label: "" } });

test("active older runs enter before recent terminal history without disappearing on settlement", () => {
  const state = new AgentBuckets();
  state.acceptActive([run("older-active"), run("older-review", "review_pending")]);
  state.acceptTerminal(Array.from({ length: 101 }, (_, i) => run(`ended-${i}`, "succeeded")));
  assert.deepEqual(state.items.slice(0, 2).map(row => row.run_id), ["older-active", "older-review"]);
  state.acceptActive([run("older-review", "review_pending")]);
  assert.equal(state.items[0].state, "unknown");
  state.acceptDetail(run("older-active", "succeeded"));
  assert.deepEqual(state.items.slice(0, 2).map(row => row.run_id), ["older-active", "older-review"]);
  assert.equal(state.items[0].state, "succeeded");
});
test("disappeared active rows are unknown immediately; reconciliation is bounded and fair", () => {
  const state = new AgentBuckets();
  state.acceptActive(Array.from({ length: 20 }, (_, i) => run(String(i))));
  state.acceptActive([]);
  assert.ok(state.items.every(row => row.state === "unknown"));
  assert.equal(state.reconcileIds.length, 8);
  const batch = state.reconcileIds;
  batch.forEach(id => state.defer(id));
  assert.ok(state.reconcileIds.every(id => !batch.includes(id)));
  state.remove("8");
  assert.ok(!state.items.some(row => row.run_id === "8"));
});
test("terminal observations settle cached active rows and reject delayed active responses", () => {
  const state = new AgentBuckets();
  state.acceptActive([run("a")]);
  state.acceptTerminal([run("a", "succeeded"), run("b", "succeeded")]);
  assert.equal(state.items[0].state, "succeeded");
  state.acceptDetail(run("a"));
  assert.equal(state.items[0].state, "succeeded");
  state.acceptActive([run("a")]);
  assert.equal(state.items[0].state, "succeeded");
  state.unavailable();
  state.acceptTerminal([run("a", "succeeded"), run("b", "succeeded")]);
  assert.equal(state.items.length, 2);
  assert.equal(state.items[0].state, "succeeded");
  assert.deepEqual(state.reconcileIds, []);
});
test("phase envelopes and bucket contents fail closed, legacy is explicitly detectable", () => {
  assert.doesNotThrow(() => validateAgentPhase({ phase: "active", items: [run("a", "queued")] }, "active"));
  assert.throws(() => validateAgentPhase({ phase: "terminal", items: [] }, "active"));
  assert.throws(() => validateAgentPhase({ phase: "active", items: [run("a", "succeeded")] }, "active"));
  assert.throws(() => validateAgentPhase({ phase: "terminal", items: [run("a")] }, "terminal"));
  assert.doesNotThrow(() => validateAgentPhase({ items: [run("a", "succeeded")] }, "active"));
});

test("new active work sorts above a hundred historical terminal rows without losing row IDs", async () => {
  const { orderedAgents } = await import("../src/conversationAgents.ts");
  const state = new AgentBuckets();
  state.acceptTerminal(Array.from({ length: 101 }, (_, i) => run(`ended-${i}`, "succeeded")));
  state.acceptActive([run("new-active")]);
  assert.equal(orderedAgents(state.items)[0].run_id, "new-active");
  state.acceptDetail(run("new-active", "succeeded"));
  const rows = orderedAgents(state.items);
  assert.equal(rows.length, 102);
  assert.equal(rows.filter(row => row.run_id === "new-active").length, 1);
});
