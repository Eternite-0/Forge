import test from "node:test";
import assert from "node:assert/strict";
import { integrateSubagents } from "../src/subagentTimeline.ts";

const event = (id, run_id, created_at = "2026-01-01T00:00:02Z") => ({ kind: "tool_receipt", event_id: id, run_id, worker_id: "shared-worker", quest_id: "shared-quest", created_at, seq: null, title: id, status: "succeeded" });
const message = (id, created_at) => ({ type: "message", value: { id, role: "assistant", content: id, created_at } });

test("each activity run owns its exact events once, without adopting another run of the same Worker", () => {
  const old = event("old", "old-run"), current = event("current", "new-run"), other = event("other", null);
  const entries = integrateSubagents([{ type: "group", questId: "shared-quest", events: [old, current, other] }],
    [{ run_id: "new-run", worker_id: "shared-worker", quest_id: "shared-quest", state: "running", created_at: "2026-01-01T00:00:01Z" }]);
  assert.deepEqual(entries.find((item) => item.type === "subagent").events.map((item) => item.event_id), ["current"]);
  assert.deepEqual(entries.find((item) => item.type === "group").events.map((item) => item.event_id), ["old", "other"]);
  const allEvents = entries.flatMap((item) => item.type === "group" || item.type === "subagent" ? item.events : []);
  assert.equal(allEvents.length, 3);
  assert.equal(new Set(allEvents.map((item) => item.event_id)).size, 3);
});

test("a new Worker appears before any process event arrives while existing message ordering is preserved", () => {
  const items = [message("undated"), message("before", "2026-01-01T00:00:00Z"), message("after", "2026-01-01T00:00:03Z")];
  const entries = integrateSubagents(items, [{ run_id: "new-run", state: "queued", created_at: "2026-01-01T00:00:01Z" }]);
  assert.deepEqual(entries.map((item) => item.type === "subagent" ? item.run.run_id : item.value.id), ["undated", "before", "new-run", "after"]);
  assert.deepEqual(entries.find((item) => item.type === "subagent").events, []);
});

test("unavailable activity retains historical events without presenting an obsolete current run", () => {
  const items = [{ type: "group", questId: "shared-quest", events: [event("old", "old-run")] }];
  assert.equal(integrateSubagents(items, []), items);
  const duplicate = { run_id: "new-run", state: "running" };
  const entries = integrateSubagents(items, [duplicate, { ...duplicate, state: "failed" }]);
  assert.equal(entries.filter((item) => item.type === "subagent").length, 1);
  assert.equal(entries.find((item) => item.type === "subagent").run.state, "failed");
  assert.equal(entries.find((item) => item.type === "group").events[0].run_id, "old-run");
});
