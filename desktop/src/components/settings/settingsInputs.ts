export type PayloadFormat = "text" | "json";
export type AdmissionMode = "candidate" | "explicit" | "ignore";

function identifier(value: string, label: string): string {
  const result = value.trim();
  if (!result || result.length > 256 || result.includes("\0")) throw new Error(`${label}需为 1–256 个字符。`);
  return result;
}

export function eventPayload(value: string, format: PayloadFormat): Record<string, unknown> {
  if (format === "text") {
    if (!value.trim()) throw new Error("请填写事件内容。");
    return { content: value.trim() };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(value, (_key, item: unknown) => {
      if (typeof item === "number" && !Number.isFinite(item)) throw new Error("Non-finite JSON number");
      return item;
    });
  } catch { throw new Error("事件内容必须是有效的 JSON 对象，数值须在有限范围内。"); }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("事件内容必须是 JSON 对象。");
  return parsed as Record<string, unknown>;
}

export function sourceInput(source: string, kind: string, mode: AdmissionMode, actors: string) {
  const allowed = actors.trim() ? [...new Set(actors.split(/[\r\n,]+/).map((value) => value.trim()).filter(Boolean).map((value) => identifier(value, "来源用户")))] : null;
  if (allowed && allowed.length > 256) throw new Error("来源用户最多 256 项。");
  return { source_id: identifier(source, "事件源标识"), kind: identifier(kind, "事件源类型"), mode, allowed_actors: allowed };
}

export function triggerInput(source: string, external: string, thread: string, content: string, format: PayloadFormat, actor: string, addressed: boolean) {
  return { source_id: identifier(source, "事件源标识"), external_id: identifier(external, "事件标识"), thread_key: identifier(thread, "主题标识"), payload: eventPayload(content, format), actor: actor.trim() ? identifier(actor, "来源用户") : null, addressed };
}

export function scheduleInput(source: string, thread: string, content: string, format: PayloadFormat, date: string, interval: string) {
  // datetime-local represents the user's local clock; the API expects epoch seconds.
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(date)) throw new Error("请选择本地执行时间。");
  const timestamp = new Date(date).getTime();
  if (!Number.isFinite(timestamp) || timestamp < 0) throw new Error("执行时间无效。");
  const intervalSeconds = interval.trim() ? Number(interval) : null;
  if (intervalSeconds !== null && (!Number.isFinite(intervalSeconds) || intervalSeconds < 1)) throw new Error("重复间隔至少为 1 秒，留空则只执行一次。");
  return { source_id: identifier(source, "事件源标识"), thread_key: identifier(thread, "主题标识"), payload: eventPayload(content, format), due_at: timestamp / 1000, interval_seconds: intervalSeconds };
}

export function fileWatchInput(source: string, value: string, thread: string) {
  const path = value.trim().replace(/\\/g, "/");
  if (!path || path.startsWith("/") || /^[a-z]:/i.test(path) || path.includes("\0") || path.split("/").some((part) => !part || part === "." || part === "..")) throw new Error("请填写工作空间内的相对文件路径。");
  return { source_id: identifier(source, "事件源标识"), path, thread_key: thread.trim() ? identifier(thread, "主题标识") : null };
}

export const PROBE_DIMENSIONS = ["mathematics", "coding", "research", "planning", "architecture", "design", "vision"] as const;
export function modelProbeInput(profileId: string, dimensions: string[]) {
  if (!profileId.trim() || profileId.length > 512) throw new Error("请选择模型档案。");
  if (!dimensions.length || new Set(dimensions).size !== dimensions.length || dimensions.some((dimension) => !(PROBE_DIMENSIONS as readonly string[]).includes(dimension))) throw new Error("请选择至少一个有效的测量维度。");
  return { profile_id: profileId, dimensions };
}
