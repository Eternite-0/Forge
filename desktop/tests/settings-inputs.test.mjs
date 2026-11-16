import test from "node:test";
import assert from "node:assert/strict";
import { eventPayload, fileWatchInput, modelProbeInput, scheduleInput, sourceInput, triggerInput } from "../src/components/settings/settingsInputs.ts";

test("a local timer date becomes epoch seconds and an empty repeat stays one-shot", () => {
  const input = scheduleInput("clock", "daily", "Read the latest report", "text", "2026-11-20T09:30", "");
  assert.equal(input.due_at, new Date(2026, 10, 20, 9, 30).getTime() / 1000);
  assert.equal(input.interval_seconds, null);
  assert.deepEqual(input.payload, { content: "Read the latest report" });
  for (const interval of ["0", "0.5", "-5", "Infinity", "invalid"]) assert.throws(() => scheduleInput("clock", "daily", "report", "text", "2026-11-20T09:30", interval), /至少为 1 秒/);
  assert.throws(() => scheduleInput("clock", "daily", "report", "text", "not-a-date", "60"), /执行时间/);
});

test("source admission distinguishes unrestricted actors from an explicit actor list", () => {
  assert.deepEqual(sourceInput("timer", "timer", "explicit", ""), { source_id: "timer", kind: "timer", mode: "explicit", allowed_actors: null });
  assert.deepEqual(sourceInput("build", "external", "candidate", "alice\nbob,alice").allowed_actors, ["alice", "bob"]);
});

test("event retries retain an explicit identity and structured payloads cannot become arrays", () => {
  const event = triggerInput("external", "original-id", "build", '{"message":"done","attempt":2}', "json", "alice", true);
  assert.equal(event.external_id, "original-id");
  assert.deepEqual(event.payload, { message: "done", attempt: 2 });
  assert.equal(event.actor, "alice");
  assert.equal(event.addressed, true);
  for (const content of ["null", "[]", '"string"', "undefined"]) assert.throws(() => eventPayload(content, "json"), /JSON 对象/);
  for (const content of ['{"value":1e999}', '{"nested":[{"value":-1e999}]}']) assert.throws(() => eventPayload(content, "json"), /有限范围/);
});

test("file watches normalize workspace paths and reject traversal or absolute paths", () => {
  assert.deepEqual(fileWatchInput("files", "reports\\latest.txt", ""), { source_id: "files", path: "reports/latest.txt", thread_key: null });
  for (const path of ["../report", "reports/../file", "/etc/config", "D:\\report.txt", "\\\\server\\file", "./report", "reports//file"]) assert.throws(() => fileWatchInput("files", path, ""), /相对文件路径/);
});

test("model probes accept the backend dimensions and reject unsupported or duplicate requests", () => {
  assert.deepEqual(modelProbeInput("profile", ["mathematics", "coding"]), { profile_id: "profile", dimensions: ["mathematics", "coding"] });
  for (const dimensions of [[], ["coding", "coding"], ["writing"], ["math"]]) assert.throws(() => modelProbeInput("profile", dimensions), /有效的测量维度/);
});
