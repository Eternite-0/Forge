import test from "node:test";
import assert from "node:assert/strict";
import { readArtifactDiff } from "../src/artifactHistory.ts";

const connection = { base_url: "http://127.0.0.1:8765", bearer: "memory-only", generation: "one", workspace_id: "test" };
const fixture = () => ({ receipt_id: "receipt_one", base_receipt_id: null, quest_id: "quest_one", run_id: "run_one", path: "src/file.ts", execution_mode: "live", change: "created", created_at: "2026-01-01T00:00:00Z", sha256: "a".repeat(64), base_sha256: null, added: 1, removed: 0, text: "--- /dev/null\n+++ b/src/file.ts\n@@ -0,0 +1 @@\n+recorded\n", truncated: false, origin: { original_request_message_id: "msg_original", goal_update_message_id: null } });

test("artifact history reads only the exact receipt endpoint with the in-memory bearer and abort signal", async (t) => {
  const controller = new AbortController(), calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => { calls.push({ url, options }); return Response.json(fixture()); });
  const result = await readArtifactDiff(connection, "receipt_one", controller.signal);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/artifacts/receipts/receipt_one/diff");
  assert.equal(calls[0].options.headers.Authorization, "Bearer memory-only");
  assert.equal(calls[0].options.signal, controller.signal);
  assert.equal(calls[0].options.method, undefined);
  assert.deepEqual(result, fixture());
  for (const invalid of ["../receipt_one", "receipt_one?path=another", "", "x".repeat(201)]) await assert.rejects(readArtifactDiff(connection, invalid), /回执标识无效/);
  assert.equal(calls.length, 1);
});

test("a mismatched or malformed history response cannot become a trusted diff", async (t) => {
  let response = fixture();
  t.mock.method(globalThis, "fetch", async () => Response.json(response));
  for (const fields of [{ receipt_id: "receipt_other" }, { path: "../secret" }, { path: "C:/secret" }, { added: -1 }, { removed: 0.5 }, { sha256: "missing" }, { execution_mode: "unknown" }, { created_at: "invalid" }, { origin: {} }, { text: "x".repeat(1024 * 1024 + 1) }]) {
    response = { ...fixture(), ...fields };
    await assert.rejects(readArtifactDiff(connection, "receipt_one"));
  }
  response = { ...fixture(), change: "revised" };
  await assert.rejects(readArtifactDiff(connection, "receipt_one"), /基础回执不完整/);
  response = { ...fixture(), change: "revised", base_receipt_id: "receipt_base", base_sha256: "b".repeat(64), execution_mode: "mock", truncated: true };
  const revised = await readArtifactDiff(connection, "receipt_one");
  assert.equal(revised.execution_mode, "mock");
  assert.equal(revised.base_receipt_id, "receipt_base");
  assert.equal(revised.truncated, true);
});

test("unavailable history remains an explicit Core error without a fallback to current files or Git", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => { calls.push(url); return Response.json({ detail: "History unavailable" }, { status: 404 }); });
  await assert.rejects(readArtifactDiff(connection, "receipt_one"), /404.*History unavailable/);
  assert.deepEqual(calls, ["http://127.0.0.1:8765/api/artifacts/receipts/receipt_one/diff"]);
});
