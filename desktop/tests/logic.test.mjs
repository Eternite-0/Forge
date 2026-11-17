import test from "node:test";
import assert from "node:assert/strict";
import { appendUnique, isNearBottom, operatorConfig, pendingMessage, preservedScrollTop, RequestGuard, retainIfEqual, safeExternalUrl, shouldSubmitOnEnter } from "../src/logic.ts";

test("a failed send retries with the same request ID until the draft changes", () => {
  let next = 0;
  const id = () => `request-${++next}`;
  const first = pendingMessage("  continue the task  ", null, id);
  assert.deepEqual(first, { content: "continue the task", requestId: "request-1" });
  assert.equal(pendingMessage("continue the task", first, id), first);
  assert.equal(pendingMessage("another task", first, id).requestId, "request-2");
});

test("Enter submits only outside IME composition and without Shift", () => {
  const key = { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 };
  assert.equal(shouldSubmitOnEnter(key), true);
  assert.equal(shouldSubmitOnEnter({ ...key, isComposing: true }), false);
  assert.equal(shouldSubmitOnEnter({ ...key, keyCode: 229 }), false);
  assert.equal(shouldSubmitOnEnter({ ...key, shiftKey: true }), false);
});

test("history pagination retains order and removes overlapping records", () => {
  assert.deepEqual(appendUnique([{ id: "a" }, { id: "b" }], [{ id: "b" }, { id: "c" }]), [
    { id: "a" }, { id: "b" }, { id: "c" },
  ]);
});

test("delayed results cannot overwrite a newer Core generation or selected view", async () => {
  const guard = new RequestGuard();
  guard.setConnection("workspace-a:generation-1");
  guard.setContext("panel", "teams:quest-a");
  let resolveOld;
  const delayed = new Promise((resolve) => { resolveOld = resolve; });
  const old = guard.start("panel");
  guard.setContext("panel", "approvals:quest-b");
  const current = guard.start("panel");
  resolveOld("old quest");
  await delayed;
  assert.equal(guard.accepts(old), false);
  assert.equal(guard.accepts(current), true);
  guard.setConnection("workspace-a:generation-2");
  assert.equal(guard.accepts(current), false);
  guard.setConnection("workspace-a:generation-1");
  guard.start("panel");
  assert.equal(guard.accepts(old), false);
  const newest = guard.start("conversation");
  guard.dispose();
  assert.equal(guard.accepts(newest), false);
});

test("operator settings never serialize a secret and reject credentials in a URL", () => {
  const form = { providerEnabled: true, providerId: "example", kind: "openai_compatible",
    baseUrl: "https://example.invalid/v1", credentialRef: "FORGE_PROVIDER_KEY_EXAMPLE",
    supportsTools: true, supportsJsonSchema: true, allowInsecureLoopback: false, routerModel: "model", contextLimit: "8192",
    tokenLimit: "10000", concurrencyLimit: "1", hosts: "example.invalid\n" };
  const policy = { enforced: true, savedLimit: 10000 };
  const config = operatorConfig(form, policy);
  assert.equal(config.provider.credential_ref, "FORGE_PROVIDER_KEY_EXAMPLE");
  assert.equal(JSON.stringify(config).includes("secret"), false);
  assert.throws(() => operatorConfig({ ...form, baseUrl: "https://user:password@example.invalid" }, policy), /不能包含/);
  assert.throws(() => operatorConfig({ ...form, baseUrl: "http://127.0.0.1:8788/v1" }, policy), /HTTP/);
  assert.equal(operatorConfig({ ...form, baseUrl: "http://127.0.0.1:8788/v1", allowInsecureLoopback: true }, policy).provider.allow_insecure_loopback, true);
  assert.throws(() => operatorConfig({ ...form, baseUrl: "http://example.invalid", allowInsecureLoopback: true }, policy), /HTTP/);
});

test("Markdown links admit only credential-free HTTP(S), and history keeps its viewport", () => {
  for (const value of ["javascript:alert(1)", "data:text/html,test", "file:///secret", "vbscript:foo", "https://user:password@example.invalid", "/relative"]) assert.equal(safeExternalUrl(value), null);
  assert.equal(safeExternalUrl("https://example.invalid/path"), "https://example.invalid/path");
  assert.equal(preservedScrollTop(220, 1000, 1500), 720);
  assert.equal(isNearBottom(400, 500, 950), true);
  assert.equal(isNearBottom(200, 500, 950), false);
});

test("unchanged polling retains the same timeline array", () => {
  const original = [{ id: "one", text: "unchanged" }];
  assert.equal(retainIfEqual(original, [{ id: "one", text: "unchanged" }]), original);
  assert.notEqual(retainIfEqual(original, [{ id: "one", text: "updated" }]), original);
});
