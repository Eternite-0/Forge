import { agentRunning } from "./conversationAgents.ts";
import type { WorkerRun } from "./ConversationRuntime";
import type { DisplayItem } from "./components/conversation/eventModel";
import { messageMetadata, type ChatMessage, type TimelineEvent, type TimelineItem } from "./timeline.ts";

export type ProcessRecord =
  | { type: "message"; value: ChatMessage }
  | { type: "tools"; events: TimelineEvent[] }
  | { type: "agents"; events: TimelineEvent[]; runs: WorkerRun[] }
  | { type: "snapshot"; run: WorkerRun };

export interface ProcessGroup {
  type: "process";
  key: string;
  records: ProcessRecord[];
  active: boolean;
  runIds: string[];
  /** A displayed trailing reply, not a claim that the backend marked a turn complete. */
  answerId?: string;
  /** Display interval starts at the user request, or the earliest loaded record. */
  startedAtMs?: number;
  /** Last recorded activity/reply time; this is not a task-completion claim. */
  finishedAtMs?: number;
  durationMs?: number;
}

export type ProcessTimelineItem = Exclude<TimelineItem, { type: "event" }> | ProcessGroup;
type OrderedItem = TimelineItem | { type: "snapshot"; run: WorkerRun };

const timestamp = (value: string | undefined) => {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : null;
};
const itemTime = (item: OrderedItem) => timestamp(item.type === "snapshot" ? item.run.created_at
  : item.type === "outgoing" ? item.value.createdAt : item.value.created_at);

const lifecycle = (event: TimelineEvent) => event.kind === "event" && /^worker\./.test(event.status || "");
const itemKey = (item: OrderedItem) => item.type === "snapshot" ? `run:${item.run.run_id}`
  : item.type === "event" ? `event:${item.value.kind}:${item.value.event_id}`
    : item.type === "outgoing" ? `outgoing:${item.value.requestId}` : `message:${item.value.id}`;
const isUser = (item: OrderedItem) => item.type === "outgoing" || item.type === "message" && item.value.role === "user";
const canBeReply = (message: ChatMessage) => {
  const meta = messageMetadata(message.metadata);
  return message.role === "assistant" && meta.source !== "router_notice" && meta.phase !== "commentary" && meta.channel !== "commentary";
};

/** A current run may enrich its exact recorded history, never invent it. */
export function historyWorkerRuns(runs: WorkerRun[], events: TimelineEvent[]): WorkerRun[] {
  const ids = new Set(events.flatMap(event => event.run_id ? [event.run_id] : []));
  return runs.filter(run => ids.has(run.run_id));
}

/** Chronological display windows do not assert task ownership or invent a completion fact. */
export function buildProcessTimeline(items: (TimelineItem | DisplayItem)[], workerRuns: WorkerRun[]): ProcessTimelineItem[] {
  const ordered: OrderedItem[] = items.flatMap(item => item.type === "group"
    ? item.events.map(value => ({ type: "event" as const, value })) : [item]);
  const runs = new Map(workerRuns.filter(run => typeof run.run_id === "string" && run.run_id).map(run => [run.run_id, run]));
  const recordedLifecycle = new Set(ordered.flatMap(item => item.type === "event" && lifecycle(item.value) && item.value.run_id ? [item.value.run_id] : []));
  // An activity snapshot has no terminal timestamp. Show it as current state only,
  // and leave every persisted event at its original position rather than adopting it.
  const snapshots: OrderedItem[] = [...runs.values()].filter(run => !recordedLifecycle.has(run.run_id))
    .map(run => ({ type: "snapshot" as const, run }));
  snapshots.sort((left, right) => {
    const first = itemTime(left), second = itemTime(right);
    return first !== null && second !== null ? first - second : first !== null ? -1 : second !== null ? 1 : 0;
  });
  for (const snapshot of snapshots) {
    const time = itemTime(snapshot);
    const position = time === null ? -1 : ordered.findIndex(item => {
      const other = itemTime(item);
      return other !== null && other > time;
    });
    if (position < 0) ordered.push(snapshot);
    else ordered.splice(position, 0, snapshot);
  }

  const output: ProcessTimelineItem[] = [];
  let segment: OrderedItem[] = [], boundary = "", boundaryTime: number | null = null;
  const flush = () => {
    if (!segment.length) return;
    const hasEvents = segment.some(item => item.type === "event");
    // Ordinary messages stay ordinary when the only extra information is an
    // independently read activity snapshot; there is no recorded process to hide.
    if (!hasEvents) {
      let snapshotsOnly: OrderedItem[] = [];
      const flushSnapshots = () => {
        if (!snapshotsOnly.length) return;
        output.push(makeGroup(snapshotsOnly, `${boundary || itemKey(snapshotsOnly[0])}:${itemKey(snapshotsOnly[0])}`, runs));
        snapshotsOnly = [];
      };
      for (const item of segment) {
        if (item.type === "snapshot") snapshotsOnly.push(item);
        else { flushSnapshots(); if (item.type !== "event") output.push(item); }
      }
      flushSnapshots();
      segment = [];
      return;
    }
    const group = makeGroup(segment, boundary || itemKey(segment[0]), runs);
    const tail = segment[segment.length - 1];
    // The API does not expose a final channel. Only a trailing ordinary reply
    // may be displayed outside; a notice or progress message is never promoted.
    if (!group.active && tail.type === "message" && canBeReply(tail.value)) {
      group.answerId = tail.value.id;
      group.records = group.records.filter(record => !(record.type === "message" && record.value.id === tail.value.id));
      output.push(group, tail);
    } else output.push(group);
    setGroupTiming(group, segment, boundaryTime);
    segment = [];
  };
  for (const item of ordered) {
    if (isUser(item)) {
      flush();
      if (item.type === "message" || item.type === "outgoing") output.push(item);
      boundary = itemKey(item);
      boundaryTime = itemTime(item);
    } else segment.push(item);
  }
  flush();
  return output;
}

function makeGroup(items: OrderedItem[], key: string, runs: Map<string, WorkerRun>): ProcessGroup {
  const records: ProcessRecord[] = [];
  const runIds = new Set<string>();
  for (const item of items) {
    if (item.type === "snapshot") {
      runIds.add(item.run.run_id);
      records.push(item);
    } else if (item.type === "message") records.push(item);
    else if (item.type === "event") {
      const event = item.value;
      if (event.run_id) runIds.add(event.run_id);
      const previous = records[records.length - 1];
      if (lifecycle(event)) {
        const run = event.run_id ? runs.get(event.run_id) : undefined;
        if (previous?.type === "agents" && previous.events[0].status === event.status && previous.events[0].execution_mode === event.execution_mode) {
          previous.events.push(event);
          if (run && !previous.runs.some(item => item.run_id === run.run_id)) previous.runs.push(run);
        } else records.push({ type: "agents", events: [event], runs: run ? [run] : [] });
      } else if (previous?.type === "tools") previous.events.push(event);
      else records.push({ type: "tools", events: [event] });
    }
  }
  const group: ProcessGroup = { type: "process", key: `process:${key}`, records, active: [...runIds].some(id => {
    const run = runs.get(id);
    return !!run && agentRunning(run);
  }), runIds: [...runIds] };
  setGroupTiming(group, items, null);
  return group;
}

/** Measure only this loaded display interval, without inventing a snapshot end. */
function setGroupTiming(group: ProcessGroup, items: OrderedItem[], boundaryTime: number | null) {
  delete group.startedAtMs;
  delete group.finishedAtMs;
  delete group.durationMs;
  const times = items.map(itemTime).filter((time): time is number => time !== null);
  const start = boundaryTime ?? (times.length ? Math.min(...times) : null);
  if (start === null) return;
  group.startedAtMs = start;
  if (group.active) return;
  const recorded = items.filter(item => item.type !== "snapshot");
  const reply = group.answerId ? recorded.find(item => item.type === "message" && item.value.id === group.answerId) : undefined;
  const recordedTimes = recorded.map(itemTime).filter((time): time is number => time !== null);
  const end = reply ? itemTime(reply) : recordedTimes.length ? recordedTimes[recordedTimes.length - 1] : null;
  // A lone creation timestamp supplies a start, never a zero-duration completion.
  if (end === null || end < start || times.length + Number(boundaryTime !== null) < 2) return;
  group.finishedAtMs = end;
  group.durationMs = end - start;
}
