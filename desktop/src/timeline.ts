import type { PendingMessage } from "./logic";

export type Metadata = Record<string, unknown>;
export interface ChatMessage { id: string; role: string; content: string; created_at?: string; metadata?: Metadata | string }
export interface TimelineEvent { kind: string; event_id: string; seq: number | null; created_at: string; quest_id?: string | null; worker_id?: string | null; run_id?: string | null; title: string; status?: string | null; detail?: unknown; execution_mode?: string | null }
export interface Outgoing extends PendingMessage { status: "sending" | "failed"; error?: string; createdAt: string }
export type TimelineItem = { type: "message"; value: ChatMessage } | { type: "event"; value: TimelineEvent } | { type: "outgoing"; value: Outgoing };

export function messageMetadata(value: unknown): Metadata {
  if (typeof value === "string") { try { const parsed: unknown = JSON.parse(value); return parsed && typeof parsed === "object" ? parsed as Metadata : {}; } catch { return {}; } }
  return value && typeof value === "object" ? value as Metadata : {};
}

/** Merge actual persisted records with local unsent bubbles; never infer association. */
export function mergeTimeline(messages: ChatMessage[], events: TimelineEvent[], outgoing: Outgoing[]): TimelineItem[] {
  const messageMap = new Map(messages.map((item) => [item.id, item]));
  const eventMap = new Map(events.map((item) => [`${item.kind}:${item.event_id}`, item]));
  const committed = new Set([...messageMap.values()].map((item) => messageMetadata(item.metadata).request_id).filter((value): value is string => typeof value === "string"));
  const all: { time: string; key: string; item: TimelineItem }[] = [
    ...[...messageMap.values()].map((value) => ({ time: value.created_at || "", key: `message:${value.id}`, item: { type: "message" as const, value } })),
    ...[...eventMap.values()].map((value) => ({ time: value.created_at || "", key: `event:${value.kind}:${value.event_id}`, item: { type: "event" as const, value } })),
    ...outgoing.filter((value) => !committed.has(value.requestId)).map((value) => ({ time: value.createdAt, key: `outgoing:${value.requestId}`, item: { type: "outgoing" as const, value } })),
  ];
  all.sort((a, b) => {
    const first = Date.parse(a.time), second = Date.parse(b.time);
    return (Number.isFinite(first) && Number.isFinite(second) ? first - second : a.time.localeCompare(b.time)) || a.key.localeCompare(b.key);
  });
  return all.map((entry) => entry.item);
}
