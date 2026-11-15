import { invoke } from "@tauri-apps/api/core";

export interface WorkspaceFile { path: string; name: string; directory: boolean; size?: number; modified?: number }
export interface FileChange { path: string; status: string; added?: number; removed?: number; previousPath?: string }
export interface ChangeSet { files: FileChange[]; repository: boolean; truncated?: boolean }
export interface WorkspaceDocument { path: string; kind: "text" | "pdf" | "image" | "unsupported"; text?: string; bytes?: number[]; mime?: string; size: number; modified?: number }
export interface FileDiff { text: string; binary?: boolean; truncated?: boolean; scope?: "worktree" | "staged" | "new" }
export interface FileSource {
  list(path: string): Promise<WorkspaceFile[]>;
  read(path: string): Promise<WorkspaceDocument>;
  changes(): Promise<ChangeSet>;
  diff(path: string): Promise<FileDiff>;
}
const MAX_FILE = 24 * 1024 * 1024;
const TEXT_LIMIT = 2 * 1024 * 1024;
export function relativeFilePath(input: string): string {
  const path = input.replace(/\\/g, "/").replace(/^\.\//, "");
  if (!path || path.startsWith("/") || /^[A-Za-z]:/.test(path) || path.split("/").some((part) => part === ".." || part === "" || part.includes(":")) || /[\x00-\x1f]/.test(path)) throw new Error("文件路径不在当前工作空间中。");
  return path;
}
export function fileKind(path: string): WorkspaceDocument["kind"] {
  if (/\.pdf$/i.test(path)) return "pdf";
  if (/\.(png|jpg|jpeg|gif|webp|avif|bmp)$/i.test(path)) return "image";
  if (/\.(zip|exe|dll|db|sqlite3?|woff2?|ttf|mp4|mp3|docx?|xlsx?|pptx?|bin|ico)$/i.test(path)) return "unsupported";
  return "text";
}
export function nativeFileSource(): FileSource {
  return {
    list: (path) => invoke("workspace_files_list", { path }),
    read: (path) => invoke("workspace_file_read", { path: relativeFilePath(path) }),
    changes: () => invoke("workspace_changes"),
    diff: (path) => invoke("workspace_file_diff", { path: relativeFilePath(path) }),
  };
}
export function importedFileSource(input: File[]): FileSource {
  const directory = input[0]?.webkitRelativePath.split("/")[0];
  const sharedRoot = directory && input.every((file) => file.webkitRelativePath.startsWith(`${directory}/`)) ? `${directory}/` : "";
  const files = new Map(input.map((file) => [relativeFilePath(sharedRoot ? file.webkitRelativePath.slice(sharedRoot.length) : file.webkitRelativePath || file.name), file]));
  return {
    async list(directory) {
      const prefix = directory ? `${relativeFilePath(directory)}/` : "";
      const entries = new Map<string, WorkspaceFile>();
      for (const [path, file] of files) {
        if (!path.startsWith(prefix)) continue;
        const suffix = path.slice(prefix.length), name = suffix.split("/")[0], child = prefix + name;
        if (!entries.has(child)) entries.set(child, { path: child, name, directory: suffix.includes("/"), size: file.size, modified: file.lastModified });
      }
      return [...entries.values()].sort((a, b) => Number(b.directory) - Number(a.directory) || a.name.localeCompare(b.name));
    },
    async read(value) {
      const path = relativeFilePath(value), file = files.get(path);
      if (!file) throw new Error("此文件未在选择的文件中，请选择所在目录或文件。");
      if (file.size > MAX_FILE) throw new Error("文件超过 24 MB 预览上限。");
      const kind = fileKind(path);
      if (kind === "text") {
        if (file.size > TEXT_LIMIT) throw new Error("文本超过 2 MB 预览上限。");
        const bytes = await file.arrayBuffer();
        let text: string;
        try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { throw new Error("此文件不是 UTF-8 文本，暂不支持预览。"); }
        if (text.includes("\0")) return { path, kind: "unsupported", size: file.size };
        return { path, kind, text, size: file.size, modified: file.lastModified };
      }
      return { path, kind, bytes: kind === "unsupported" ? undefined : [...new Uint8Array(await file.arrayBuffer())], mime: kind === "pdf" ? "application/pdf" : file.type, size: file.size, modified: file.lastModified };
    },
    async changes() { return { files: [], repository: false }; },
    async diff() { throw new Error("导入文件不包含 Git 历史，无法显示更改。"); },
  };
}
