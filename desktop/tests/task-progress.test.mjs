import test from "node:test";
import assert from "node:assert/strict";
import { taskProgress } from "../src/taskProgress.ts";

const at = second => `2026-11-16T12:00:${String(second).padStart(2, "0")}Z`;
const quest = (id, status) => ({ id, status, title: "整理资料" });
const user = [{ id: "u", role: "user", content: "整理资料", created_at: at(10) }];
const receipt = (id, second, overrides = {}) => ({ kind: "tool_receipt", event_id: `receipt-${id}`, seq: null, quest_id: id, created_at: at(second), title: "record", status: "succeeded", execution_mode: "live", detail: { tool: "file_write", path: "output/report.md" }, ...overrides });

test("current state overrides old events, deduplicates tasks and prioritizes attention", () => {
  const cards = taskProgress([quest("running", "queued"), quest("running", "running"), quest("input", "needs_input"), quest("closed", "cancelled")], [receipt("running", 1, { status: "failed" })], user);
  assert.deepEqual(cards.map(card => [card.quest.id, card.quest.status]), [["input", "needs_input"], ["running", "running"]]);
  assert.equal(cards[1].result, undefined);
});

test("completed history stays out unless exactly linked to the latest loaded turn", () => {
  const quests = [quest("old", "completed"), quest("a", "completed"), quest("b", "completed")];
  assert.deepEqual(taskProgress(quests, [receipt("old", 1), receipt("a", 12), receipt("b", 14)], user).map(card => card.quest.id), ["b"]);
  assert.equal(taskProgress(quests, [receipt("b", 14)], []).length, 0);
  assert.equal(taskProgress(quests, [receipt("b", 14)], [{ ...user[0], created_at: "invalid" }]).length, 0);
  assert.equal(taskProgress([quest("old", "failed")], [receipt("old", 1)], user).length, 0);
});

test("result route only uses same-task live successful writes, never proximity or another task", () => {
  const cards = taskProgress([quest("a", "completed")], [receipt("a", 11, { detail: { tool: "file_read", path: "input.md" } }), receipt("b", 12)], user);
  assert.equal(cards.length, 1);
  assert.equal(cards[0].result, undefined);
  const valid = taskProgress([quest("a", "completed")], [receipt("a", 11)], user)[0];
  assert.deepEqual(valid.result, { receiptId: "receipt-a", path: "output/report.md" });
});

test("mock, failed, uncertain and unsafe paths cannot become result actions", () => {
  for (const override of [{ execution_mode: "mock" }, { status: "failed" }, { status: "uncertain" },
    { detail: { tool: "file_write", path: "../secret" } }, { detail: { tool: "file_write", path: "C:/secret" } },
    { detail: { tool: "file_write", path: "/secret" } }, { detail: { tool: "file_write", path: "valid.md", exit_code: 1 } }]) {
    assert.equal(taskProgress([quest("a", "completed")], [receipt("a", 12, override)], user)[0].result, undefined);
  }
});

test("persisted assistant text without a verified result contract cannot create a result action", () => {
  const messages = [...user, { id: "assistant", role: "assistant", content: "结果是 output/report.md", created_at: at(12), metadata: { quest_id: "a" } }];
  const cards = taskProgress([quest("a", "completed")], [receipt("a", 11, { kind: "event", status: "quest.status_changed", detail: { to: "completed" } })], messages);
  assert.equal(cards[0].result, undefined);
});
