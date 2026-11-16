import { useState } from "react";
import type { WorkerRun } from "../ConversationRuntime";
import { questDisplayTitle, statusLabel } from "../presentation";
import type { TimelineEvent } from "../timeline";
import EventGroup from "./conversation/EventGroup";
import { Chevron } from "./conversation/Icons";
import { StatusIndicator, type RunState } from "./conversation/RunStatus";
import { text, type EventViewMemory, type HistoryProps, type Quest } from "./conversation/eventModel";
import AgentAvatar from "./AgentAvatar";

export interface SubagentEntryProps extends HistoryProps {
  run: WorkerRun;
  events: TimelineEvent[];
  quests: Quest[];
  memory: EventViewMemory;
  onQuest: (id: string) => void;
  onOpenFile?: (path: string) => void;
  onInspect: () => void;
}

const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString() : "未知";

/** The current activity snapshot and the recorded process events remain distinct. */
export default function SubagentEntry({ run, events, quests, memory, onQuest, onOpenFile, onInspect, ...history }: SubagentEntryProps) {
  if (!memory.subagents) memory.subagents = new Map();
  if (!memory.subagents.has(run.run_id)) memory.subagents.set(run.run_id, {});
  const saved = memory.subagents.get(run.run_id)!;
  const [expanded, setExpanded] = useState(() => saved.expanded ?? false);
  // Details stay mounted for history/cache retention; the right panel owns the overview.
  const visible = saved.manual ? expanded : false;
  const quest = quests.find((item) => item.id === run.quest_id);
  const state: RunState = run.state === "running" || run.state === "queued" ? "running" : run.state === "succeeded" ? "completed"
    : run.state === "failed" ? "failed" : !run.state || run.state === "unknown" || run.state === "uncertain" ? "unknown" : "recorded";
  const mode = (run as WorkerRun & { execution_mode?: unknown }).execution_mode;
  const currentSource = mode === "mock" ? "模拟" : mode === "live" ? "真实执行" : "执行来源未记录";
  const hasMock = events.some((event) => event.execution_mode === "mock");
  return <details className="subagent-entry" data-state={run.state || "unknown"} data-run-id={run.run_id} data-scroll-key={`subagent:${run.run_id}`} open={visible}>
    <summary aria-expanded={visible} aria-label={`子智能体 ${text(run.role, "角色未记录")} · ${statusLabel(run.state)}`} onClick={(event) => {
      event.preventDefault();
      onInspect();
      saved.manual = true;
      saved.expanded = !visible;
      setExpanded(!visible);
    }}>
      <AgentAvatar identity={run.worker_id || run.run_id} className="subagent-avatar" />
      <strong className="subagent-role">{text(run.role, "角色未记录")}</strong>
      <span className="subagent-state" data-state={run.state || "unknown"}><span className="subagent-status-icon"><StatusIndicator state={state} /></span>{statusLabel(run.state)}</span>
      <Chevron />
    </summary>
    <div className="subagent-body" onPointerDownCapture={() => { saved.manual = true; }} onKeyDownCapture={() => { saved.manual = true; }}>
      <p className="subagent-scope">{quest ? questDisplayTitle(quest.title, quest.id) : "关联任务暂未加载"} · {text(run.provider_id, "供应商未记录")} · {currentSource}</p>
      <p className="subagent-run-meta"><span>{text(run.model_id, "模型未记录")}</span><span>已记录 {count(run.steps)} 步 · {count(run.tool_spent)} 次工具</span>{hasMock && <span className="subagent-mode">含模拟记录</span>}</p>
      <p className="subagent-record-notice">当前状态来自最近读取的执行记录。下方为已加载且关联本次 Run 的历史事件。</p>
      {events.length ? <EventGroup item={{ type: "group", questId: run.quest_id || null, events }} quests={quests} runs={[run]} memory={memory} visible={visible}
        onQuest={onQuest} onOpenFile={onOpenFile} onInspect={onInspect} {...history} /> : <p className="subagent-record-notice">尚未加载本次执行的过程记录。</p>}
      <div className="subagent-actions">{run.quest_id && <button type="button" className="text-button" onClick={() => { onInspect(); onQuest(run.quest_id!); }}>任务详情</button>}
        <details><summary>技术详情</summary><span className="subagent-run-id">执行 ID · {run.run_id}</span></details>
      </div>
    </div>
  </details>;
}
