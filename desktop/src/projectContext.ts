type Row = Record<string, unknown>;

export interface ProjectFields {
  project_id?: string;
  project_name?: string;
  project_root?: string;
  workspace_id?: string;
  workspace_root?: string;
  metadata?: unknown;
  project_context?: unknown;
}

export interface ProjectDescriptor {
  key: string;
  name: string;
  path: string;
  source: "workspace" | "metadata";
}

const nonempty = (value: unknown): string => typeof value === "string" ? value.trim() : "";
const record = (value: unknown): Row => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const directoryName = (path: string): string => path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "";
const pathIdentity = (path: string): string => {
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/, "");
  return /^(?:[a-z]:\/|\/\/)/i.test(normalized) ? normalized.toLowerCase() : normalized;
};

// Real Core rows preserve SQLite metadata as JSON text; fixtures may already be objects.
function metadataFor(quest: ProjectFields): Row {
  if (typeof quest.metadata !== "string") return record(quest.metadata);
  if (quest.metadata.length > 65536) return {};
  try { return record(JSON.parse(quest.metadata)); } catch { return {}; }
}

export function currentWorkspaceProject(workspaceId: string, workspaceRoot: string, workspaceName = ""): ProjectDescriptor {
  return {
    key: `workspace:${workspaceId || pathIdentity(workspaceRoot) || "current"}`,
    name: workspaceName || directoryName(workspaceRoot) || workspaceId || "工作空间",
    path: workspaceRoot,
    source: "workspace",
  };
}

export function explicitProjectName(quest: ProjectFields): string {
  const metadata = metadataFor(quest), project = record(metadata.project);
  return nonempty(quest.project_name) || nonempty(metadata.project_name) || nonempty(project.name);
}

/** Metadata creates display groups only; it never grants a root or a managed project. */
export function projectFor(quest: ProjectFields, workspaceId: string, workspaceRoot: string, workspaceName: string): ProjectDescriptor {
  const context = record(quest.project_context), binding = record(context.workspace);
  const sameBinding = context.version === 1 && context.scope === "current_workspace" && !!workspaceId && binding.id === workspaceId;
  const currentRoot = workspaceRoot || (sameBinding ? nonempty(binding.root) : "");
  const currentName = workspaceName || (sameBinding ? nonempty(binding.name) : "");
  const current = currentWorkspaceProject(workspaceId, currentRoot, currentName || "当前工作空间");
  const metadata = metadataFor(quest), project = record(metadata.project);
  const projectId = nonempty(quest.project_id) || nonempty(metadata.project_id) || nonempty(project.id);
  const explicitRoot = nonempty(quest.project_root) || nonempty(metadata.project_root) || nonempty(project.root);
  const explicitWorkspaceId = nonempty(quest.workspace_id) || nonempty(metadata.workspace_id);
  const explicitWorkspaceRoot = nonempty(quest.workspace_root) || nonempty(metadata.workspace_root);
  const path = explicitRoot || explicitWorkspaceRoot || (!projectId && (!explicitWorkspaceId || explicitWorkspaceId === workspaceId) ? currentRoot : "");
  const currentFallback = !projectId && !explicitRoot && (!explicitWorkspaceId || explicitWorkspaceId === workspaceId) && (!explicitWorkspaceRoot || pathIdentity(explicitWorkspaceRoot) === pathIdentity(currentRoot));
  const name = explicitProjectName(quest) || (currentFallback ? current.name : "") || directoryName(path) || projectId || explicitWorkspaceId || currentName || "当前工作空间";
  const sameWorkspace = !projectId && (explicitWorkspaceId === workspaceId && !!workspaceId || !!path && !!currentRoot && pathIdentity(path) === pathIdentity(currentRoot));
  const key = projectId ? `project:${projectId}` : sameWorkspace ? current.key : explicitRoot ? `root:${pathIdentity(explicitRoot)}` : explicitWorkspaceId ? `workspace:${explicitWorkspaceId}` : explicitWorkspaceRoot ? `root:${pathIdentity(explicitWorkspaceRoot)}` : current.key;
  const source = projectId || explicitRoot || explicitProjectName(quest) || explicitWorkspaceId && explicitWorkspaceId !== workspaceId || explicitWorkspaceRoot && pathIdentity(explicitWorkspaceRoot) !== pathIdentity(currentRoot) ? "metadata" : "workspace";
  return { key, name, path, source };
}
