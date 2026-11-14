import test from "node:test";
import assert from "node:assert/strict";
import { mergeTimeline } from "../src/timeline.ts";

test("timeline sorts real events and messages, deduplicates, and reconciles the outgoing request", () => {
  const message = { id: "m1", role: "user", content: "hello", created_at: "2026-01-01T00:00:02Z", metadata: JSON.stringify({ request_id: "r1" }) };
  const event = { kind: "tool_receipt", event_id: "e1", seq: null, created_at: "2026-01-01T00:00:03Z", quest_id: null, title: "Tool execution recorded", execution_mode: "mock" };
  const outgoing = { requestId: "r1", content: "hello", status: "sending", createdAt: "2026-01-01T00:00:01Z" };
  const items = mergeTimeline([message, message], [event, event], [outgoing]);
  assert.deepEqual(items.map((item) => item.type), ["message", "event"]);
  assert.equal(items[1].value.quest_id, null);
  assert.equal(items[1].value.execution_mode, "mock");
});

test("a failed uncommitted send remains visible alongside a new draft", () => {
  const failed = { requestId: "r2", content: "original", status: "failed", error: "network", createdAt: "2026-01-01T00:00:01Z" };
  const items = mergeTimeline([], [], [failed]);
  assert.equal(items[0].type, "outgoing");
  assert.equal(items[0].value.requestId, "r2");
  assert.equal(items[0].value.content, "original");
});
