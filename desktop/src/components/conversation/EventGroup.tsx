import { useLayoutEffect, useState } from "react";
import AnimatedDetails from "../../AnimatedDetails";
import type { WorkerRun } from "../../ConversationRuntime";
import type { TimelineEvent } from "../../timeline";
import { relativeFilePath } from "../../workspaceFiles";
import TurnDiff from "../TurnDiff";
import ToolItem from "./ToolItem";
import { ActivityIcon, Chevron } from "./Icons";
import { activitySummary, transcriptSummary, eventKey, eventView, text, workerIdentity, type DisplayItem, type EventViewMemory, type HistoryProps, type Quest, type Row } from "./eventModel";

export default function EventGroup({ item, quests, runs, memory, visible = true, transcript = false, onQuest, onOpenFile, onInspect, onOpenHistory, historyStats, connection, onHistoryLoaded }: { item: Extract<DisplayItem, { type: "group" }>; quests: Quest[]; runs: WorkerRun[]; memory: EventViewMemory; visible?: boolean; transcript?: boolean; onQuest: (id: string) => void; onOpenFile?: (path: string) => void; onInspect: () => void } & HistoryProps) {
  const [expanded, setExpanded] = useState(() => item.events.some((event) => eventView(memory, event).groupExpanded));
  const [filesExpanded, setFilesExpanded] = useState(() => item.events.some((event) => eventView(memory, event).groupFilesExpanded));
  // Members inherit the current group state, so splits and prepended group roots restore it.
  useLayoutEffect(() => {
    for (const event of item.events) {
      eventView(memory, event).groupExpanded = expanded;
      eventView(memory, event).groupFilesExpanded = filesExpanded;
    }
  }, [item.events, memory, expanded, filesExpanded]);
  const summary = transcript ? transcriptSummary(item.events, runs) : activitySummary(item.events, runs);
  const origins = [...new Set(item.events.map((event) => workerIdentity(event, runs)).filter(Boolean))];
  const hasMock = item.events.some((event) => event.execution_mode === "mock");
  const hasFailure = item.events.some((event) => /failed/.test(event.status || "") || typeof (event.detail as Row | undefined)?.exit_code === "number" && Number((event.detail as Row).exit_code) !== 0);
  const hasUnknown = item.events.some((event) => /unknown|uncertain/.test(event.status || ""));
  const recordedFiles = new Map<string, TimelineEvent>();
  for (const event of item.events) {
    const detail = event.detail && typeof event.detail === "object" ? event.detail as Row : {};
    if (event.kind !== "tool_receipt" || event.status !== "succeeded" || event.execution_mode !== "live" || typeof detail.exit_code === "number" && detail.exit_code !== 0 || !["file_write", "file_revise"].includes(text(detail.tool, "")) || typeof detail.path !== "string") continue;
    try { const path = relativeFilePath(detail.path); if (!recordedFiles.has(path)) recordedFiles.set(path, event); } catch { /* An invalid path is not an openable file reference. */ }
  }
  const files = [...recordedFiles.entries()];
  const turnFiles = files.map(([path, event]) => ({ path, receiptId: event.event_id, scrollKey: `file:${eventKey(event)}`, ...historyStats?.[event.event_id] }));
  const iconEvent = item.events.find(event => ["file_write", "file_revise"].includes(text((event.detail as Row | undefined)?.tool, ""))) || item.events[0];
  return <AnimatedDetails className={`event-group${transcript ? " process-tool-summary" : ""}`} scrollKey={`group:${eventKey(item.events[0])}`} initialExpanded={expanded} onExpandedChange={(value) => { for (const event of item.events) eventView(memory, event).groupExpanded = value; setExpanded(value); }} onInspect={onInspect} summaryTitle={origins.join("\n")} summary={<><ActivityIcon event={iconEvent} /><span className="event-label">{summary}</span>{hasFailure && <span className="event-risk event-failed">失败</span>}{hasUnknown && <span className="event-risk event-unknown">结果未知</span>}{hasMock && <small className="event-mode"> · {item.events.every((event) => event.execution_mode === "mock") ? "模拟" : "含模拟记录"}</small>}<Chevron /></>}>
    <div className="event-detail">{!transcript && <TurnDiff files={turnFiles} groupKey={eventKey(item.events[0])} initialExpanded={filesExpanded}
      onExpandedChange={(value) => { for (const event of item.events) eventView(memory, event).groupFilesExpanded = value; setFilesExpanded(value); }}
      onInspect={onInspect} onOpenFile={onOpenFile} onOpenHistory={onOpenHistory} />}{item.events.map((event) => <ToolItem key={eventKey(event)} event={event} quests={quests} runs={runs} memory={memory} groupExpanded={expanded && visible} onQuest={onQuest} onOpenFile={onOpenFile} onOpenHistory={onOpenHistory} historyStats={historyStats} connection={connection} onHistoryLoaded={onHistoryLoaded} onInspect={onInspect} />)}</div>
  </AnimatedDetails>;
}

