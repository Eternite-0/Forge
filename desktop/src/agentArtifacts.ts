import { relativeFilePath } from "./workspaceFiles.ts";
export interface AgentArtifact { receipt_id: string; path: string; sha256: string; bytes: number; created_at: string; execution_mode: "live" | "mock"; change: "created" | "revised" }
export interface AgentArtifactPage {
  conversation_id: string; workspace_id: string; run_id: string; execution_mode: "live" | "mock";
  provenance: "historical_receipts"; current_files_verified: false;
  items: AgentArtifact[]; has_more: boolean; next_before: string | null; unavailable: boolean;
}
export function validateAgentArtifacts(page: AgentArtifactPage, runId: string, workspaceId?: string): AgentArtifactPage {
  if (!page || page.conversation_id !== "main" || page.run_id !== runId
    || typeof page.workspace_id !== "string" || !page.workspace_id || workspaceId && page.workspace_id !== workspaceId
    || page.provenance !== "historical_receipts" || page.current_files_verified !== false
    || !Array.isArray(page.items) || page.items.length > 20 || typeof page.unavailable !== "boolean" || typeof page.has_more !== "boolean"
    || !(page.next_before === null || typeof page.next_before === "string" && /^[A-Za-z0-9_-]{1,96}$/.test(page.next_before))
    || page.has_more !== (page.next_before !== null) || !["live", "mock"].includes(page.execution_mode)) {
    throw new Error("历史文件来源范围不一致。");
  }
  const seen = new Set<string>();
  for (const item of page.items) {
    if (!item || typeof item.receipt_id !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(item.receipt_id)
      || typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.sha256)
      || typeof item.path !== "string" || relativeFilePath(item.path) !== item.path
      || !Number.isSafeInteger(item.bytes) || item.bytes < 0 || typeof item.created_at !== "string" || !Number.isFinite(Date.parse(item.created_at))
      || item.execution_mode !== page.execution_mode || !["created", "revised"].includes(item.change) || seen.has(item.receipt_id)) {
      throw new Error("历史文件回执不完整。");
    }
    seen.add(item.receipt_id);
  }
  return page;
}
