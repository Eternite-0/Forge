import test from "node:test";
import assert from "node:assert/strict";
import { ApiError, browserConnection, request } from "../src/api.ts";

const connection = { base_url: "http://127.0.0.1:1234", bearer: "test-bearer", generation: "test" };

test("authenticated requests send only the in-memory bearer", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  let captured;
  globalThis.fetch = async (url, init) => {
    captured = { url, init };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  assert.deepEqual(await request(connection, "/api/conversation"), { ok: true });
  assert.equal(captured.init.headers.Authorization, "Bearer test-bearer");
  assert.equal(captured.url, "http://127.0.0.1:1234/api/conversation");
});

test("permission and stale-version errors remain distinguishable", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  for (const status of [401, 409]) {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: "stale or unauthorized" }), { status });
    await assert.rejects(request(connection, "/api/approvals/a/decision", { method: "POST", body: "{}" }),
      (error) => error instanceof ApiError && error.status === status && error.message.includes("stale or unauthorized"));
  }
  globalThis.fetch = async () => { throw new TypeError("connection refused"); };
  await assert.rejects(request(connection, "/api/conversation"),
    (error) => error instanceof ApiError && error.status === 0 && error.message.includes("connection refused"));
});

test("a successful header with an interrupted response body is a transport failure", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  const stream = new ReadableStream({
    start(controller) { controller.error(new TypeError("response body disconnected")); },
  });
  globalThis.fetch = async () => new Response(stream, { status: 200 });
  await assert.rejects(request(connection, "/api/conversation/message", { method: "POST", body: "{}" }),
    (error) => error instanceof ApiError && error.status === 0 && error.message.includes("响应不完整"));
});

test("browser bootstrap derives a bearer without persisting or sending the secret", async (t) => {
  const originalFetch = globalThis.fetch;
  const previousLocal = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const previousSession = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  t.after(() => {
    globalThis.fetch = originalFetch;
    if (previousLocal) Object.defineProperty(globalThis, "localStorage", previousLocal); else delete globalThis.localStorage;
    if (previousSession) Object.defineProperty(globalThis, "sessionStorage", previousSession); else delete globalThis.sessionStorage;
  });
  let writes = 0;
  for (const name of ["localStorage", "sessionStorage"]) Object.defineProperty(globalThis, name, { configurable: true, value: { setItem() { writes += 1; } } });
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(url.endsWith("/health") ? { session: "test-challenge" } : { messages: [] }), { status: 200 });
  };
  const secret = "ab".repeat(32);
  const connected = await browserConnection("http://127.0.0.1:8765", secret);
  assert.match(connected.bearer, /^[a-f0-9]{64}$/);
  assert.notEqual(connected.bearer, secret);
  assert.equal(JSON.stringify(calls).includes(secret), false);
  assert.equal(writes, 0);
});
