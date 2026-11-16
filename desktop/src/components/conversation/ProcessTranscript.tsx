import { useEffect, useState } from "react";
import type { WorkerRun } from "../../ConversationRuntime";
import type { ProcessGroup } from "../../processTimeline";
import { relativeFilePath } from "../../workspaceFiles";
import TurnDiff from "../TurnDiff";
import type { TurnDiffFile } from "../TurnDiff";
import AgentActivityRow from "./AgentActivityRow";
import AssistantMessage from "./AssistantMessage";
import EventGroup from "./EventGroup";
import { Chevron } from "./Icons";
import { eventKey, eventView, type EventViewMemory, type HistoryProps, type Quest, type Row } from "./eventModel";

interface ProcessProps extends HistoryProps {
  process: ProcessGroup;
  runs: WorkerRun[];
  quests: Quest[];
  memory: EventViewMemory;
  native: boolean;
  onInspect: () => void;
  onQuest: (id: string) => void;
  onOpenFile?: (path: string) => void;
}

/** Display the recorded work in order; the reply stays outside this disclosure. */
export default function ProcessTranscript({ process, runs, quests, memory, native, onInspect, onQuest, onOpenFile, ...history }: ProcessProps) {
  if (!memory.processes) memory.processes = new Map();
  const [, update] = useState(0);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!process.active || process.startedAtMs === undefined) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [process.active, process.startedAtMs]);
  const saved = memory.processes.get(process.key);
  // Keep conversational progress visible, but do not open a stream of logs
  // just because a background run has not produced an answer yet.
  const expanded = saved?.expanded ?? (!process.answerId && process.records.some(record => record.type === "message"));
  const events = process.records.flatMap(record => record.type === "tools" || record.type === "agents" ? record.events : []);
  const failed = events.some(event => /failed/.test(event.status || "") || typeof (event.detail as Row | undefined)?.exit_code === "number" && (event.detail as Row).exit_code !== 0);
  const unknown = events.some(event => /unknown|uncertain/.test(event.status || ""));
  const mock = events.some(event => event.execution_mode === "mock");
  const currentRuns = runs.filter(run => process.runIds.includes(run.run_id));
  const currentFailed = currentRuns.some(run => run.state === "failed");
  const currentUnknown = currentRuns.some(run => !run.state || /unknown|uncertain/.test(run.state));
  const elapsed = process.active && process.startedAtMs !== undefined ? Math.max(0, now - process.startedAtMs) : process.durationMs;
  const duration = elapsed === undefined ? "" : `用时 ${Math.floor(elapsed / 60000)} 分钟 ${Math.floor(elapsed / 1000) % 60} 秒`;
  const label = process.active ? "正在处理" : "处理记录";
  return <details className="process-transcript" data-process-key={process.key} data-scroll-key={`process:${process.key}`} data-active={process.active} open={expanded}>
    <summary aria-label={`${expanded ? "收起" : "展开"}处理记录`} aria-expanded={expanded} onClick={event => {
      event.preventDefault(); onInspect();
      memory.processes!.set(process.key, { expanded: !expanded }); update(value => value + 1);
    }}><span>{label}</span>{expanded && duration && <small>{duration}</small>}{(failed || currentFailed) && <small className="event-failed">{currentFailed ? "当前运行失败" : "含失败记录"}</small>}{(unknown || currentUnknown) && <small className="event-unknown">{currentUnknown ? "当前状态未知" : "含未知结果"}</small>}{mock && <small>含模拟记录</small>}<Chevron /></summary>
    <div className="process-content" hidden={!expanded} onClickCapture={() => { memory.processes!.set(process.key, { expanded: true }); }} onKeyDownCapture={() => { memory.processes!.set(process.key, { expanded: true }); }}>
      {process.records.map(record => {
        if (record.type === "message") return <AssistantMessage key={`message:${record.value.id}`} message={record.value} native={native} onInspect={onInspect} />;
        if (record.type === "agents") return <AgentActivityRow key={`agent:${eventKey(record.events[0])}`} events={record.events} runs={record.runs} memory={memory} onInspect={onInspect} onQuest={onQuest} />;
        if (record.type === "snapshot") return <AgentActivityRow key={`snapshot:${record.run.run_id}`} events={[]} runs={[record.run]} memory={memory} onInspect={onInspect} onQuest={onQuest} />;
        return <EventGroup key={`tools:${eventKey(record.events[0])}`} item={{ type: "group", questId: record.events[0].quest_id || null, events: record.events }}
          quests={quests} runs={runs} memory={memory} visible={expanded} transcript onInspect={onInspect} onQuest={onQuest} onOpenFile={onOpenFile} {...history} />;
      })}
    </div>
  </details>;
}

/** The closing file summary uses real successful receipts, independently of process visibility. */
export function processFileChanges(process: ProcessGroup, historyStats?: HistoryProps["historyStats"]): TurnDiffFile[] {
  const files = new Map<string, TurnDiffFile>();
  const events = process.records.flatMap(record => record.type === "tools" ? record.events : []);
  for (const event of events) {
    const detail = event.detail as Row | undefined;
    if (event.kind !== "tool_receipt" || event.status !== "succeeded" || event.execution_mode !== "live" || !detail || !["file_write", "file_revise"].includes(String(detail.tool)) || typeof detail.path !== "string" || typeof detail.exit_code === "number" && detail.exit_code !== 0) continue;
    try {
      const path = relativeFilePath(detail.path);
      files.set(path, { path, receiptId: event.event_id, scrollKey: `file:${eventKey(event)}`, ...historyStats?.[event.event_id] });
    } catch { /* Invalid recorded paths cannot become file actions. */ }
  }
  return [...files.values()];
}

export function ProcessFileChanges({ process, memory, historyStats, onInspect, onOpenFile, onOpenHistory }: Pick<ProcessProps, "process" | "memory" | "historyStats" | "onInspect" | "onOpenFile" | "onOpenHistory">) {
  const [, update] = useState(0);
  const files = processFileChanges(process, historyStats);
  if (!files.length) return null;
  const events = process.records.flatMap(record => record.type === "tools" ? record.events : []);
  const state = eventView(memory, events[0]);
  return <TurnDiff files={files} groupKey={process.key} initialExpanded={state.groupFilesExpanded || false}
    onExpandedChange={value => { state.groupFilesExpanded = value; update(count => count + 1); }} onInspect={onInspect} onOpenFile={onOpenFile} onOpenHistory={onOpenHistory} />;
}
