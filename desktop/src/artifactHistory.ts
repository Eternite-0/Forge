import { request, type Connection } from "./api.ts";
import { relativeFilePath } from "./workspaceFiles.ts";

export interface ArtifactDiff {
  receipt_id: string;
  base_receipt_id: string | null;
  quest_id: string;
  run_id: string;
  path: string;
  execution_mode: "live" | "mock";
  change: "created" | "revised";
  created_at: string;
  sha256: string;
  base_sha256: string | null;
  added: number;
  removed: number;
  text: string;
  truncated: boolean;
  origin: { original_request_message_id: string | null; goal_update_message_id: string | null } | null;
}

const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const identifier = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{1,200}$/.test(value);
const nullableIdentifier = (value: unknown) => value === null || identifier(value);
const digest = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

/** Receipt identity, content and counts come only from the recorded Core artifact. */
export async function readArtifactDiff(connection: Connection, receiptId: string, signal?: AbortSignal): Promise<ArtifactDiff> {
  if (!identifier(receiptId)) throw new Error("回执标识无效，无法读取记录的更改。");
  const result = record(await request<unknown>(connection, `/api/artifacts/receipts/${encodeURIComponent(receiptId)}/diff`, { signal }));
  const origin = result.origin === null ? null : record(result.origin);
  if (result.receipt_id !== receiptId || !nullableIdentifier(result.base_receipt_id) || !identifier(result.quest_id) || !identifier(result.run_id)
    || typeof result.path !== "string" || !["live", "mock"].includes(String(result.execution_mode)) || !["created", "revised"].includes(String(result.change))
    || typeof result.created_at !== "string" || !Number.isFinite(Date.parse(result.created_at)) || !digest(result.sha256) || !(result.base_sha256 === null || digest(result.base_sha256))
    || !count(result.added) || !count(result.removed) || typeof result.text !== "string" || result.text.length > 1024 * 1024 || typeof result.truncated !== "boolean"
    || origin !== null && (!nullableIdentifier(origin.original_request_message_id) || !nullableIdentifier(origin.goal_update_message_id))) {
    throw new Error("Core 返回了无效的历史更改记录。");
  }
  const path = relativeFilePath(result.path);
  if (result.change === "created" && (result.base_receipt_id !== null || result.base_sha256 !== null)
    || result.change === "revised" && (result.base_receipt_id === null || result.base_sha256 === null)) {
    throw new Error("历史更改的基础回执不完整。");
  }
  return { ...result, path } as unknown as ArtifactDiff;
}
