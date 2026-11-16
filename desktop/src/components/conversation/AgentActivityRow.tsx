import { Fragment, useState } from "react";
import { WorkerRunDetails, type WorkerRun } from "../../ConversationRuntime";
import { statusLabel } from "../../presentation";
import type { TimelineEvent } from "../../timeline";
import AgentAvatar from "../AgentAvatar";
import { Chevron } from "./Icons";
import { eventKey, eventTitle, type EventViewMemory } from "./eventModel";

interface AgentActivityRowProps {
  events: TimelineEvent[];
  runs: WorkerRun[];
  memory: EventViewMemory;
  onInspect: () => void;
  onQuest: (id: string) => void;
}

const lifecycleLabel = (event: TimelineEvent) => {
  const labels: Record<string, string> = {
    "worker.started": "开始工作", "worker.result": "已返回结果", "worker.completed": "已完成", "worker.failed": "执行失败",
    "worker.retry": "正在重试", "worker.blocked": "已阻塞", "worker.help_requested": "请求协助",
    "worker.progress": "有新进展", "worker.proposed": "已提出执行提案",
  };
  return labels[event.status || ""] || eventTitle(event);
};

/** Lifecycle sentences describe recorded events; a current snapshot cannot rewrite them. */
export default function AgentActivityRow({ events, runs, memory, onInspect, onQuest }: AgentActivityRowProps) {
  const records = events.length ? events.map(event => ({
    event,
    run: event.run_id ? runs.find(run => run.run_id === event.run_id) : undefined,
    runId: event.run_id || undefined,
    workerId: event.worker_id || undefined,
    key: `agent-event:${eventKey(event)}`,
  })) : runs.map(run => ({ event: undefined, run, runId: run.run_id, workerId: run.worker_id, key: `agent-snapshot:${run.run_id}` }));
  if (!memory.subagents) memory.subagents = new Map();
  for (const record of records) if (!memory.subagents.has(record.key)) memory.subagents.set(record.key, {});
  const saved = records.map(record => memory.subagents!.get(record.key)!);
  const [expanded, setExpanded] = useState(() => saved.some(state => state.expanded));
  const primary = records[0];
  if (!primary) return null;
  const identities = [...new Map(records.map(record => [record.runId || record.workerId || record.key, record])).values()];
  const names = identities.length > 3 ? identities.slice(0, 2) : identities;
  const label = events.length ? lifecycleLabel(events[0]) : ` · 当前状态：${statusLabel(primary.run?.state)}`;
  const nameOf = (record: typeof primary) => record.run?.role || record.workerId || "子智能体";
  const sentence = `${names.map(nameOf).join("和")}${identities.length > 3 ? `及另外 ${identities.length - 2} 个子智能体` : ""}${label}`;
  const toggle = () => {
    onInspect();
    for (const state of saved) { state.manual = true; state.expanded = !expanded; }
    setExpanded(!expanded);
  };
  return <details className="subagent-entry agent-activity" data-run-id={primary.runId} data-state={primary.event?.status || primary.run?.state || "unknown"}
    data-scroll-key={primary.key} data-event-ids={events.map(event => event.event_id).join(" ")} open={expanded}>
    <summary className="agent-activity-summary" aria-expanded={expanded} aria-label={sentence} onClick={event => { event.preventDefault(); toggle(); }}>
      <span className="agent-activity-avatars">{identities.map((record, index) => <span key={record.key} data-run-id={record.runId} hidden={index >= 4}><AgentAvatar identity={record.workerId || record.runId || record.key} size={16} /></span>)}</span>
      <span className="agent-activity-label">{names.map((record, index) => <Fragment key={record.key}>
        {index > 0 && (index === names.length - 1 ? "和" : "、")}
        <span className="agent-activity-name" data-run-id={record.runId}>{nameOf(record)}</span>
      </Fragment>)}{identities.length > 3 && `及另外 ${identities.length - 2} 个子智能体`}{label}</span>
      <Chevron />
    </summary>
    <div className="agent-activity-body">{records.map(record => <div className="agent-activity-record" key={record.key} data-event-id={record.event?.event_id} data-run-id={record.runId}>
      {record.event && <p className="agent-activity-origin">{record.event.execution_mode === "mock" ? "模拟记录" : record.event.execution_mode === "live" ? "真实执行记录" : "执行来源未记录"}</p>}
      <WorkerRunDetails run={record.run} runId={record.runId} workerId={record.workerId}
        historicalEvent={record.event ? eventTitle(record.event, runs) : undefined} historicalTime={record.event?.created_at}
        initialExpanded={memory.subagents!.get(record.key)?.detailsExpanded ?? true} onExpandedChange={value => { memory.subagents!.get(record.key)!.detailsExpanded = value; }} onInspect={onInspect} scrollKey={`${record.key}:details`} />
      {(record.event?.quest_id || record.run?.quest_id) && <button type="button" className="text-button agent-activity-quest" onClick={() => { onInspect(); onQuest((record.event?.quest_id || record.run?.quest_id)!); }}>任务详情</button>}
    </div>)}</div>
  </details>;
}
