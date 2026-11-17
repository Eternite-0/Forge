import test from "node:test";
import assert from "node:assert/strict";
import { scopedAgents, agentRole, agentState, agentOutcome, agentMilestone, agentActive } from "../src/conversationAgents.ts";

const run = { run_id: "r1", state: "running", source: { kind: "message", id: "m1" }, lease_status: "active" };
const page = { conversation_id: "main", workspace_id: "w1", items: [run], has_more: false, next_before: null };
test("subagent feed rejects different conversation/workspace instead of using global activity", () => {
  assert.deepEqual(scopedAgents(page, "main", "w1"), [run]);
  assert.throws(() => scopedAgents({ ...page, conversation_id: "other" }), /对话/);
  assert.throws(() => scopedAgents(page, "main", "other"), /工作空间/);
  assert.throws(() => scopedAgents({ ...page, workspace_id: undefined }, "main", "w1"), /工作空间/);
});
test("feed filters unproven/malformed rows and deduplicates exact run, not worker", () => {
  assert.deepEqual(scopedAgents({ ...page, items: [{ ...run, source: undefined }, { ...run, run_id: "" }, run, run, { ...run, run_id: "r2" }] }).map(row => row.run_id), ["r1", "r2"]);
  assert.deepEqual(scopedAgents({ ...page, items: [] }), []);
});
test("review, failure and expired leases are never labelled as completed work", () => {
  assert.equal(agentState({ ...run, lease_status: "expired" }), "租约已过期 · 等待恢复");
  assert.equal(agentOutcome("awaiting_review"), "等待独立验证");
  assert.equal(agentOutcome("failed"), "执行失败");
  assert.equal(agentActive({ ...run, state: "review_pending" }), true);
  assert.equal(agentActive({ ...run, state: "succeeded" }), false);
  assert.equal(agentOutcome("invented"), "尚无已记录结果");
});
test("roles and milestone kinds use readable labels, not raw event names", () => {
  assert.equal(agentRole("coding"), "编码");
  assert.equal(agentRole("research_1"), "研究 1");
  assert.equal(agentRole("math_2"), "数学 2");
  assert.equal(agentRole("资料整理"), "资料整理");
  assert.equal(agentMilestone("task.verified"), "结果已通过验证");
  assert.equal(agentRole(undefined), "子智能体");
  assert.equal(agentMilestone("worker.result"), "提交结果，等待验证");
  assert.equal(agentMilestone("some.secret.event"), "更新运行记录");
});
