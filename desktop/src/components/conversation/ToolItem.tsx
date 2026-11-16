import { useState } from "react";
import AnimatedDetails from "../../AnimatedDetails";
import InlineArtifactDiff from "../../InlineArtifactDiff";
import { WorkerRunDetails, type WorkerRun } from "../../ConversationRuntime";
import { riskLabel, statusLabel, toolLabel } from "../../presentation";
import type { TimelineEvent } from "../../timeline";
import CommandCard from "./CommandCard";
import { StatusIndicator, type RunState } from "./RunStatus";
import { ActivityIcon, Chevron } from "./Icons";
import { eventKey, eventTitle, eventView, text, workerIdentity, type EventViewMemory, type HistoryProps, type Quest, type Row } from "./eventModel";

export default function ToolItem({ event, quests, runs, memory, groupExpanded, onQuest, onOpenFile, onInspect, onOpenHistory, historyStats, connection, onHistoryLoaded }: { event: TimelineEvent; quests: Quest[]; runs: WorkerRun[]; memory: EventViewMemory; groupExpanded: boolean; onQuest: (id: string) => void; onOpenFile?: (path: string) => void; onInspect: () => void } & HistoryProps) {
  const view = eventView(memory, event), key = eventKey(event);
  const [expanded, setExpanded] = useState(view.expanded || false);
  const [commandExpanded, setCommandExpanded] = useState(view.commandExpanded || false);
  const detail = event.detail && typeof event.detail === "object" && !Array.isArray(event.detail) ? event.detail as Row : {};
  const relatedQuest = quests.find((quest) => quest.id === event.quest_id);
  const target = typeof detail.path === "string" ? detail.path : typeof detail.command === "string" ? detail.command : "";
  const filePath = typeof detail.path === "string" ? detail.path : "";
  const fileParts = filePath.replace(/\\/g, "/").split("/");
  const fileName = fileParts.pop() || filePath;
  const fileDirectory = fileParts.length ? `${fileParts.join("/")}/` : "";
  const openFile = () => { if (filePath && onOpenFile) { onInspect(); onOpenFile(filePath); } };
  const failed = event.status === "failed" || event.status === "worker.failed" || event.status === "tool.execution_uncertain" || (typeof detail.exit_code === "number" && detail.exit_code !== 0);
  const unknown = event.status === "unknown" || event.status === "uncertain" || event.status === "tool.execution_uncertain";
  const state: RunState = unknown ? "unknown" : failed ? "failed" : ["running", "prepared", "pending"].includes(event.status || "") ? "running" : event.status === "succeeded" ? "completed" : "recorded";
  const canReadHistory = !!onOpenHistory && event.kind === "tool_receipt" && event.status === "succeeded" && ["live", "mock"].includes(event.execution_mode || "") && ["file_write", "file_revise"].includes(text(detail.tool, "")) && !!filePath && !failed;
  if (!view.inline) view.inline = {};
  const record = <>
    {workerIdentity(event, runs) && <p className="event-origin" title={workerIdentity(event, runs)}>{workerIdentity(event, runs)}</p>}
    {(event.worker_id || event.run_id) && <WorkerRunDetails run={event.run_id ? runs.find((run) => run.run_id === event.run_id) : undefined} runId={event.run_id} workerId={event.worker_id}
      historicalEvent={eventTitle(event, runs)} historicalTime={event.created_at} initialExpanded={view.workerExpanded || false}
      onExpandedChange={(value) => { view.workerExpanded = value; }} onInspect={onInspect} scrollKey={`event-worker:${key}`} />}
    <p>{event.execution_mode === "live" ? "真实执行" : event.execution_mode === "mock" ? "模拟" : "来源未知"} · {event.created_at ? new Date(event.created_at).toLocaleString() : "时间未知"}</p>
    {event.quest_id && <button className="text-button" aria-label="查看关联任务" onClick={() => onQuest(event.quest_id!)}>查看关联任务{relatedQuest ? ` · ${relatedQuest.title}` : ""}</button>}
    <dl>{Object.entries(detail).filter(([key]) => !(typeof detail.command === "string" && ["stdout", "output", "stderr"].includes(key))).map(([key, value]) => <div key={key}><dt>{({ path: "路径", tool: "工具", result_sha256: "结果摘要", command: "命令", exit_code: "退出码", scope: "范围", source_count: "来源数", status: "状态", risk: "风险" } as Record<string, string>)[key] || key}</dt><dd>{key === "tool" ? toolLabel(value) : key === "status" ? statusLabel(value) : key === "risk" ? riskLabel(value) : text(value)}</dd></div>)}</dl>
    <small>记录 ID：{event.event_id}</small>
  </>;
  return <AnimatedDetails onInspect={onInspect} scrollKey={`event:${key}`} initialExpanded={expanded}
    onExpandedChange={(value) => { view.expanded = value; setExpanded(value); }}
    className={`event-line tool-item${failed ? " failed" : ""}${unknown ? " unknown" : ""}`}
    summaryTitle={[workerIdentity(event, runs), target].filter(Boolean).join("\n")}
    summaryLabel={canReadHistory ? `${target || eventTitle(event, runs)}${event.execution_mode === "mock" ? " · 模拟" : ""}` : undefined}
    summaryAction={filePath && onOpenFile ? <button type="button" className="event-file-open" aria-label={`预览文件 ${filePath}`} title={`预览 ${filePath}`} onClick={openFile}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><rect x="2" y="2.5" width="12" height="11" rx="2" /><path d="M9 3v10" /></svg></button> : undefined}
    summary={<><span className="tool-item-state" data-state={state}>{state === "running" || failed || unknown ? <StatusIndicator state={state} /> : <ActivityIcon event={event} />}</span><span className="event-label tool-name">{canReadHistory && expanded ? "已编辑的文件" : target || eventTitle(event, runs)}</span>{(failed || unknown) && <span className="event-failed event-risk"> · {unknown ? "结果未知" : "失败"}{typeof detail.exit_code === "number" ? `（退出码 ${detail.exit_code}）` : ""}</span>}{event.execution_mode === "mock" && <small className="event-mode"> · 模拟</small>}<Chevron /></>}>
    <div className="event-detail">
      {typeof detail.command === "string" && <CommandCard detail={detail} status={event.status} executionMode={event.execution_mode} visible={groupExpanded && expanded} expanded={commandExpanded} onExpandedChange={(value) => { view.commandExpanded = value; setCommandExpanded(value); }} savedScrollTop={memory.commandScroll.get(key) || 0} onScroll={(scrollTop) => memory.commandScroll.set(key, scrollTop)} onInspect={onInspect} />}
      {filePath && !canReadHistory && <button type="button" className="activity-file-row" title={filePath} disabled={!onOpenFile} onClick={openFile}><ActivityIcon event={event} /><span className="activity-file-copy"><span className="activity-file-directory">{fileDirectory}</span><span className="activity-file-name">{fileName}</span></span><span className="activity-file-action">预览</span></button>}
      {canReadHistory && <InlineArtifactDiff connection={connection || null} receiptId={event.event_id} path={filePath} visible={groupExpanded && expanded} memory={view.inline} onInspect={onInspect} onLoaded={onHistoryLoaded} onOpenHistory={onOpenHistory} />}
      <AnimatedDetails className="event-record" initialExpanded={view.recordExpanded || false} onExpandedChange={(value) => { view.recordExpanded = value; }} onInspect={onInspect} summary={<><span>记录详情</span><Chevron /></>}><div className="event-record-content">{record}</div></AnimatedDetails>
    </div></AnimatedDetails>;
}


