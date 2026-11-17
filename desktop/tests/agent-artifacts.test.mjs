import test from "node:test";
import assert from "node:assert/strict";
import { validateAgentArtifacts } from "../src/agentArtifacts.ts";
const item = { receipt_id: "receipt_one", path: "src/file.ts", sha256: "a".repeat(64), bytes: 12, created_at: "2026-11-17T00:00:00Z", execution_mode: "mock", change: "created" };
const page = () => ({ conversation_id: "main", workspace_id: "w", run_id: "r", execution_mode: "mock", provenance: "historical_receipts", current_files_verified: false, items: [item], unavailable: false, has_more: false, next_before: null });
test("artifact envelope is exact workspace, conversation, run and historical provenance", () => {
  assert.equal(validateAgentArtifacts(page(), "r", "w").items.length, 1);
  for (const override of [{ workspace_id: "other" }, { conversation_id: "other" }, { run_id: "other" }, { current_files_verified: true }, { provenance: "model_claim" }]) assert.throws(() => validateAgentArtifacts({ ...page(), ...override }, "r", "w"));
});
test("unsafe paths, mixed mode, malformed receipts and duplicate receipts cannot be offered", () => {
  for (const override of [{ path: "../secret" }, { path: "C:/secret" }, { path: "/secret" }, { sha256: "bad" }, { bytes: -1 }, { execution_mode: "live" }, { receipt_id: "bad/id" }]) assert.throws(() => validateAgentArtifacts({ ...page(), items: [{ ...item, ...override }] }, "r", "w"));
  assert.throws(() => validateAgentArtifacts({ ...page(), items: [item, item] }, "r", "w"));
});
test("genuine empty and unavailable remain distinct; cursors cannot silently erase missing history", () => {
  assert.equal(validateAgentArtifacts({ ...page(), items: [] }, "r", "w").unavailable, false);
  assert.equal(validateAgentArtifacts({ ...page(), items: [], unavailable: true }, "r", "w").unavailable, true);
  assert.throws(() => validateAgentArtifacts({ ...page(), has_more: true }, "r", "w"));
  assert.throws(() => validateAgentArtifacts({ ...page(), next_before: "cursor" }, "r", "w"));
  assert.throws(() => validateAgentArtifacts({ ...page(), has_more: true, next_before: "#unsafe" }, "r", "w"));
});
test("malformed scalar fields cannot masquerade as valid history after coercion", () => {
  assert.throws(() => validateAgentArtifacts({ ...page(), workspace_id: {} }, "r"));
  for (const override of [{ receipt_id: 123 }, { sha256: ["a".repeat(64)] }, { created_at: 2026 }]) {
    assert.throws(() => validateAgentArtifacts({ ...page(), items: [{ ...item, ...override }] }, "r", "w"));
  }
});
