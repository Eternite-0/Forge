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

test("partial heads preserve paged snapshots without repeated reconciliation", () => {
  const state = new AgentBuckets();
  state.acceptActive([run("head")], false);
  state.acceptDetail(run("older", "review_pending"));
  for (let i = 0; i < 3; i++) state.acceptActive([run("head")], false);
  assert.equal(state.items.find(row => row.run_id === "older").state, "review_pending");
  assert.equal(state.items.find(row => row.run_id === "older").last_confirmed, true);
  assert.deepEqual(state.reconcileIds, []);
  state.acceptActive([run("replacement")], false);
  assert.deepEqual(state.reconcileIds, ["head"]);
  state.acceptDetail(run("head"));
  state.acceptActive([run("replacement")], false);
  assert.deepEqual(state.reconcileIds, []);
  assert.equal(state.items.find(row => row.run_id === "head").last_confirmed, true);
  state.acceptActive([run("replacement")], true);
  assert.deepEqual(state.reconcileIds.sort(), ["head", "older"]);
});
test("head re-entry confirms paged state; loss fails closed without reviving terminal", () => {
  const state = new AgentBuckets();
  state.acceptDetail(run("older"));
  state.acceptActive([run("older")], false);
  assert.equal(state.items[0].last_confirmed, false);
  state.unavailable();
  assert.equal(state.items[0].state, "unknown");
  state.acceptTerminal([run("older", "succeeded")]);
  state.acceptDetail(run("older"));
  assert.equal(state.items[0].state, "succeeded");
  assert.equal(state.items[0].last_confirmed, false);
});
test("paged state copy explicitly labels its observation, not a current running claim", async () => {
  const { agentState } = await import("../src/conversationAgents.ts");
  assert.equal(agentState({ ...run("older"), last_confirmed: true }), "上次确认：运行中");
  assert.equal(agentState({ ...run("older", "unknown"), last_confirmed: true }), null);
});

test("last-confirmed expired lease never labels an agent as running", async () => {
  const { agentState } = await import("../src/conversationAgents.ts");
  assert.equal(agentState({ ...run("older"), lease_status: "expired", last_confirmed: true }), "上次确认：租约已过期 · 等待恢复");
  assert.equal(agentState({ ...run("head"), lease_status: "expired" }), "租约已过期 · 等待恢复");
});

test("live overview count excludes paged and expired observations", async () => {
  const { agentRunning } = await import("../src/conversationAgents.ts");
  const rows = [run("fresh"), { ...run("paged"), last_confirmed: true }, { ...run("expired"), lease_status: "expired" }, run("queued", "queued")];
  assert.deepEqual(rows.filter(agentRunning).map(row => row.run_id), ["fresh"]);
});
