import test from "node:test";
import assert from "node:assert/strict";
import { buildProcessTimeline } from "../src/processTimeline.ts";

const at = second => `2026-11-16T00:00:${String(second).padStart(2, "0")}Z`;
const message = (id, role, second, metadata) => ({ type: "message", value: { id, role, content: id, created_at: at(second), metadata } });
const event = (id, second, options = {}) => ({ type: "event", value: { kind: "tool_receipt", event_id: id, created_at: at(second), seq: null, title: id, status: "succeeded", ...options } });
const life = (id, second, run_id, status) => event(id, second, { kind: "event", run_id, worker_id: "shared", status });

test("process keeps progress, tools and exact run lifecycle in chronological order with the trailing reply outside", () => {
  const source = [message("user", "user", 0), message("progress", "assistant", 1, { source: "router_notice" }),
    life("start-a", 2, "a", "worker.started"), life("start-b", 3, "b", "worker.started"),
    event("tool-a", 4, { run_id: "a" }), message("update", "assistant", 5, { phase: "commentary" }),
    event("tool-b", 6, { run_id: "b" }), life("done-a", 7, "a", "worker.result"), message("reply", "assistant", 8)];
  const runs = [{ run_id: "a", state: "succeeded" }, { run_id: "b", state: "succeeded" }];
  const items = buildProcessTimeline(source, runs), process = items[1];
  assert.deepEqual(items.map(item => item.type), ["message", "process", "message"]);
  assert.equal(process.answerId, "reply");
  assert.deepEqual(process.records.map(record => record.type), ["message", "agents", "tools", "message", "tools", "agents"]);
  assert.deepEqual(process.records[1].events.map(event => event.event_id), ["start-a", "start-b"]);
  const displayedEvents = process.records.flatMap(record => record.events || []);
  assert.deepEqual(displayedEvents.map(event => event.event_id), ["start-a", "start-b", "tool-a", "tool-b", "done-a"]);
  assert.equal(new Set(displayedEvents.map(event => event.event_id)).size, displayedEvents.length);
  assert.equal(process.startedAtMs, Date.parse(at(0)));
  assert.equal(process.finishedAtMs, Date.parse(at(8)));
  assert.equal(process.durationMs, 8000);
});

test("snapshots never replace historical events or create a historical completion timestamp", () => {
  const source = [message("user", "user", 0), event("old", 2, { run_id: "old", worker_id: "shared" }),
    event("new", 4, { run_id: "new", worker_id: "shared" }), message("reply", "assistant", 5)];
  const items = buildProcessTimeline(source, [{ run_id: "new", worker_id: "shared", state: "succeeded", created_at: at(3) }]);
  const records = items[1].records;
  assert.deepEqual(records.map(record => record.type), ["tools", "snapshot", "tools"]);
  assert.deepEqual(records.flatMap(record => record.events || []).map(event => event.event_id), ["old", "new"]);
  assert.equal(records[1].run.state, "succeeded");
  assert.equal(items[1].durationMs, 5000);
  assert.equal(items[1].answerId, "reply");
});

test("a running unrelated window does not consume a previous trailing reply", () => {
  const source = [message("first", "user", 0), event("one", 1, { run_id: "finished" }), message("answer", "assistant", 2),
    message("second", "user", 3), life("started", 4, "running", "worker.started"), message("working", "assistant", 5)];
  const items = buildProcessTimeline(source, [{ run_id: "finished", state: "succeeded", created_at: at(1) }, { run_id: "running", state: "running", created_at: at(4) }]);
  assert.equal(items[1].answerId, "answer");
  assert.equal(items[2].value.id, "answer");
  const active = items.at(-1);
  assert.equal(active.type, "process");
  assert.equal(active.active, true);
  assert.equal(active.answerId, undefined);
  assert.equal(active.records.at(-1).value.id, "working");
});

test("notice, commentary and any reply followed by more records stay inside the process", () => {
  for (const metadata of [{ source: "router_notice" }, { phase: "commentary" }, { channel: "commentary" }]) {
    const items = buildProcessTimeline([message("user", "user", 0), event("tool", 1), message("notice", "assistant", 2, metadata)], []);
    assert.equal(items.length, 2);
    assert.equal(items[1].answerId, undefined);
    assert.equal(items[1].records.at(-1).value.id, "notice");
  }
  const followed = buildProcessTimeline([message("user", "user", 0), event("tool", 1), message("reply", "assistant", 2), event("later", 3)], []);
  assert.equal(followed[1].answerId, undefined);
  assert.deepEqual(followed[1].records.map(record => record.type), ["tools", "message", "tools"]);
});

test("ordinary chat remains visible even with undated activity, and user keys survive history prepends", () => {
  const chat = [message("user", "user", 3), message("reply", "assistant", 4)];
  const items = buildProcessTimeline(chat, [{ run_id: "undated", state: "queued" }]);
  assert.deepEqual(items.slice(0, 2), chat);
  assert.equal(items[2].records[0].type, "snapshot");
  assert.equal(items[2].active, false); // Queued is waiting, not running elapsed time.
  const source = [message("user", "user", 3), event("tool", 4), message("reply", "assistant", 5)];
  const oldKey = buildProcessTimeline(source, [])[1].key;
  const prepended = buildProcessTimeline([message("older", "user", 0), message("older-reply", "assistant", 1), ...source], []);
  assert.equal(prepended.find(item => item.type === "process").key, oldKey);
});

test("tool batches accept the existing grouped input without changing event order", () => {
  const events = [event("a", 1).value, event("b", 2).value];
  const items = buildProcessTimeline([message("user", "user", 0), { type: "group", questId: "q", events }, message("reply", "assistant", 3)], []);
  assert.deepEqual(items[1].records[0].events, events);
  assert.deepEqual(buildProcessTimeline([message("reply", "assistant", 3)], []), [message("reply", "assistant", 3)]);
});

test("display duration uses the request and trailing reply, while active work supplies only a live start", () => {
  const source = [message("user", "user", 1), event("work", 4, { run_id: "run" }), message("answer", "assistant", 9)];
  const finished = buildProcessTimeline(source, [{ run_id: "run", state: "succeeded" }])[1];
  assert.equal(finished.startedAtMs, Date.parse(at(1)));
  assert.equal(finished.finishedAtMs, Date.parse(at(9)));
  assert.equal(finished.durationMs, 8000);
  const active = buildProcessTimeline(source, [{ run_id: "run", state: "running" }])[1];
  assert.equal(active.startedAtMs, Date.parse(at(1)));
  assert.equal(active.finishedAtMs, undefined);
  assert.equal(active.durationMs, undefined);
});

test("an older display window stops at its last recorded activity and never includes the next user's wait", () => {
  const source = [message("first", "user", 0), event("work", 3), message("notice", "assistant", 5, { source: "router_notice" }),
    message("second", "user", 40), event("later", 45)];
  const windows = buildProcessTimeline(source, []).filter(item => item.type === "process");
  assert.equal(windows[0].durationMs, 5000);
  assert.equal(windows[0].finishedAtMs, Date.parse(at(5)));
  assert.equal(windows[1].startedAtMs, Date.parse(at(40)));
  assert.equal(windows[1].durationMs, 5000);
  const outgoing = { type: "outgoing", value: { requestId: "pending", content: "goal", createdAt: at(20), status: "sending" } };
  assert.equal(buildProcessTimeline([outgoing, event("work", 23)], [])[1].durationMs, 3000);
});

test("invalid request time falls back to loaded records, while missing or reversed endpoints stay unknown", () => {
  const invalidUser = { ...message("invalid", "user", 0), value: { ...message("invalid", "user", 0).value, created_at: "invalid" } };
  const recovered = buildProcessTimeline([invalidUser, event("start", 3), event("last", 7)], [])[1];
  assert.equal(recovered.startedAtMs, Date.parse(at(3)));
  assert.equal(recovered.durationMs, 4000);
  const unknown = buildProcessTimeline([invalidUser, event("invalid", 1, { created_at: "invalid" })], [])[1];
  assert.equal(unknown.startedAtMs, undefined);
  assert.equal(unknown.durationMs, undefined);
  const reversed = buildProcessTimeline([message("future", "user", 10), event("one", 1), event("two", 2)], [])[1];
  assert.equal(reversed.startedAtMs, Date.parse(at(10)));
  assert.equal(reversed.finishedAtMs, undefined);
  assert.equal(reversed.durationMs, undefined);
});

test("a lone snapshot has its own start without a fabricated end; two recorded endpoints may be simultaneous", () => {
  const source = [message("user", "user", 0), message("answer", "assistant", 1)];
  const snapshot = buildProcessTimeline(source, [{ run_id: "run", state: "succeeded", created_at: at(8) }]).at(-1);
  assert.equal(snapshot.startedAtMs, Date.parse(at(8)));
  assert.equal(snapshot.finishedAtMs, undefined);
  assert.equal(snapshot.durationMs, undefined);
  const lone = buildProcessTimeline([event("one", 3)], [])[0];
  assert.equal(lone.startedAtMs, Date.parse(at(3)));
  assert.equal(lone.durationMs, undefined);
  const simultaneous = buildProcessTimeline([event("one", 3), event("two", 3)], [])[0];
  assert.equal(simultaneous.durationMs, 0);
});

test("conversation snapshots only enrich exact loaded run history; queued is not a running clock", async () => {
  const { historyWorkerRuns } = await import("../src/processTimeline.ts");
  const runs = [{ run_id: "a", worker_id: "shared", state: "queued", created_at: at(1) }, { run_id: "b", worker_id: "shared", state: "running", created_at: at(1) }];
  assert.deepEqual(historyWorkerRuns(runs, []), []);
  const events = [life("queued", 2, "a", "worker.started").value];
  assert.deepEqual(historyWorkerRuns(runs, events), [runs[0]]);
  const queued = buildProcessTimeline([message("u", "user", 0), { type: "event", value: events[0] }], historyWorkerRuns(runs, events));
  assert.equal(queued[1].active, false);
  const plain = buildProcessTimeline([message("u", "user", 0), message("reply", "assistant", 4)], historyWorkerRuns(runs, []));
  assert.deepEqual(plain.map(item => item.type), ["message", "message"]);
});
