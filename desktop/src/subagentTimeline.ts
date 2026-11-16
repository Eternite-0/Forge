import type { WorkerRun } from "./ConversationRuntime";
import type { TimelineEvent } from "./timeline";
import type { DisplayItem } from "./components/conversation/eventModel";

export type ConversationEntry = DisplayItem | { type: "subagent"; run: WorkerRun; events: TimelineEvent[] };

const timestamp = (value: string | undefined) => {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : null;
};

/** Runs own only events with the exact run ID; a shared Worker or Quest is insufficient. */
export function integrateSubagents(items: DisplayItem[], workerRuns: WorkerRun[]): ConversationEntry[] {
  const runs = new Map(workerRuns.filter((run) => typeof run.run_id === "string" && run.run_id).map((run) => [run.run_id, run]));
  if (!runs.size) return items;
  const eventsByRun = new Map<string, TimelineEvent[]>();
  const entries: { entry: ConversationEntry; time: number | null; order: number }[] = [];
  let order = 0;
  for (const item of items) {
    if (item.type !== "group") {
      entries.push({ entry: item, time: timestamp(item.type === "outgoing" ? item.value.createdAt : item.value.created_at), order: order++ });
      continue;
    }
    const remaining: TimelineEvent[] = [];
    for (const event of item.events) {
      if (event.run_id && runs.has(event.run_id)) {
        const owned = eventsByRun.get(event.run_id) || [];
        owned.push(event);
        eventsByRun.set(event.run_id, owned);
      } else remaining.push(event);
    }
    if (remaining.length) entries.push({ entry: { ...item, events: remaining }, time: timestamp(remaining[0].created_at), order: order++ });
  }
  const additions = [...runs.values()].map((run, index) => {
    const events = eventsByRun.get(run.run_id) || [];
    return { entry: { type: "subagent" as const, run, events }, time: timestamp(run.created_at) ?? timestamp(events[0]?.created_at), order: order + index };
  }).sort((left, right) => left.time !== null && right.time !== null ? left.time - right.time || left.order - right.order
    : left.time !== null ? -1 : right.time !== null ? 1 : left.order - right.order);
  // Insert new activity without reordering any existing message, including undated history.
  for (const addition of additions) {
    const position = addition.time === null ? -1 : entries.findIndex((item) => item.time !== null && item.time > addition.time!);
    if (position < 0) entries.push(addition);
    else entries.splice(position, 0, addition);
  }
  return entries.map(({ entry }) => entry);
}
