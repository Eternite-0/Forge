import test from "node:test";
import assert from "node:assert/strict";
import { IndependentRefresh } from "../src/independentRefresh.ts";
import { ConversationCursor } from "../src/conversationCursor.ts";

test("a stalled task read cannot hold chat and repeated polls coalesce per source", async () => {
  const reads = new IndependentRefresh(); let release, taskCalls = 0, chatCalls = 0;
  const task = reads.run("one", "tasks", () => { taskCalls++; return new Promise(resolve => release = resolve); });
  const duplicate = reads.run("one", "tasks", async () => { taskCalls++; });
  assert.equal(task, duplicate);
  await reads.run("one", "chat", async () => { chatCalls++; });
  assert.equal(chatCalls, 1); assert.equal(taskCalls, 1);
  await reads.run("two", "tasks", async () => { taskCalls++; });
  assert.equal(taskCalls, 2); release(); await task;
  await reads.run("one", "tasks", async () => { taskCalls++; });
  assert.equal(taskCalls, 3);
});
test("failed independent reads release their slot for retry", async () => {
  const reads = new IndependentRefresh();
  await assert.rejects(reads.run("one", "chat", async () => { throw Error("offline"); }));
  await reads.run("one", "chat", async () => {});
});
test("opaque forward cursor is session scoped, rejects old replies and supports old servers", () => {
  const cursor = new ConversationCursor();
  assert.equal(cursor.select("a"), null);
  cursor.accept("a", { resume_after: "opaque +/= token" });
  assert.equal(cursor.select("a"), "opaque +/= token");
  assert.equal(cursor.select("b"), null);
  cursor.accept("a", { resume_after: "late" });
  assert.equal(cursor.select("b"), null);
  cursor.accept("b", { resume_after: "" });
  assert.equal(cursor.select("b"), "");
  cursor.reset("b"); assert.equal(cursor.select("b"), null);
  cursor.accept("b", { resume_after: "next" }); cursor.accept("b", {});
  assert.equal(cursor.select("b"), null);
});
