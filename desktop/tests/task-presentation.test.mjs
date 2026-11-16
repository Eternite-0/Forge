import test from "node:test";
import assert from "node:assert/strict";
import { displayActivityTime, questDisplayTitle, taskActivityRole, taskEventLabel } from "../src/presentation.ts";

test("task labels prefer existing human title, fall back to goal, and never use a raw task ID", () => {
  const id = "03de0fc6-a4a3-416b-923f-7ffa9700e563";
  assert.equal(questDisplayTitle("  核对交付清单  ", id, "核对"), "核对交付清单");
  assert.equal(questDisplayTitle(id, id, "整理项目交付\n核对明细"), "整理项目交付");
  assert.equal(questDisplayTitle("ee8a961b3d084f798a394499f846db9f", id), "未命名任务");
  assert.equal(questDisplayTitle("quest-fixture", "quest-fixture"), "未命名任务");
  const longGoal = "📂".repeat(100);
  assert.equal(Array.from(questDisplayTitle("", id, longGoal)).length, 72);
  assert.ok(questDisplayTitle("", id, longGoal).endsWith("…"));
});

test("activity names, role and time do not substitute internal identifiers or invalid dates", () => {
  assert.equal(taskEventLabel("worker.result"), "执行结果已返回");
  assert.equal(taskEventLabel("undocumented.fragile.event"), "运行记录");
  assert.equal(taskActivityRole("quest-fixture · 资料核对", "quest-fixture"), "资料核对");
  assert.equal(taskActivityRole("quest-fixture", "quest-fixture"), "执行成员");
  assert.equal(taskActivityRole("research analyst", "quest-fixture"), "research analyst");
  assert.equal(displayActivityTime("bad date"), "时间未记录");
  assert.notEqual(displayActivityTime("2026-11-16T08:00:00Z"), "时间未记录");
});
