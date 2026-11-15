import test from "node:test";
import assert from "node:assert/strict";
import { currentWorkspaceProject, explicitProjectName, projectFor } from "../src/projectContext.ts";

test("sidebar and task fallback share a stable workspace identity without inventing a project", () => {
  const sidebar = currentWorkspaceProject("workspace-one", "D:/Projects/same");
  const task = projectFor({}, "workspace-one", "D:/Projects/same", "same");
  assert.deepEqual(task, sidebar);
  assert.equal(currentWorkspaceProject("workspace-two", "D:/Other/same").name, sidebar.name);
  assert.notEqual(currentWorkspaceProject("workspace-two", "D:/Other/same").key, sidebar.key);
  assert.equal(projectFor({ title: "pretend-project" }, "workspace-one", "D:/Projects/same", "same").key, sidebar.key);
});

test("real JSON-text Quest metadata stays a display group and never becomes a workspace binding", () => {
  const quest = { workspace_id: "actual-workspace", workspace_root: "D:/Current", metadata: JSON.stringify({ project: { id: "label-only", name: "资料整理", root: "D:/Label" }, workspace_id: "forged-workspace" }) };
  const descriptor = projectFor(quest, "actual-workspace", "D:/Current", "Current");
  assert.deepEqual(descriptor, { key: "project:label-only", name: "资料整理", path: "D:/Label", source: "metadata" });
  assert.equal(explicitProjectName(quest), "资料整理");
  const labels = ["project-a", "project-b"].map((id) => projectFor({ metadata: { project_id: id, project_name: "同名" } }, "actual-workspace", "D:/Current", "Current"));
  assert.equal(labels[0].name, labels[1].name);
  assert.notEqual(labels[0].key, labels[1].key);
  assert.equal(quest.workspace_id, "actual-workspace");
});

test("foreign or malformed context cannot supply the current workspace identity", () => {
  const context = { version: 1, scope: "current_workspace", workspace: { id: "current", name: "真实空间", root: "D:/Real" } };
  const current = projectFor({ project_context: context }, "current", "", "");
  assert.equal(current.key, "workspace:current");
  assert.equal(current.path, "D:/Real");
  assert.equal(current.name, "真实空间");
  const foreign = projectFor({ project_context: context, metadata: "{invalid" }, "another", "D:/Another", "Another");
  assert.deepEqual(foreign, currentWorkspaceProject("another", "D:/Another", "Another"));
  for (const metadata of [null, [], "[]", "null", "x".repeat(65537)]) {
    assert.equal(projectFor({ metadata }, "current", "D:/Real", "Real").key, "workspace:current");
  }
  assert.equal(projectFor({ workspace_root: "d:\\real\\" }, "current", "D:/Real", "Real").key, "workspace:current");
  assert.notEqual(projectFor({ project_root: "/DATA/One" }, "", "", "").key, projectFor({ project_root: "/data/one" }, "", "", "").key);
});
