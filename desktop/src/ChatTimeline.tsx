import { useRef, type RefObject, type ReactNode } from "react";
import AnimatedDetails from "./AnimatedDetails";
import type { WorkerRun } from "./ConversationRuntime";
import { questDisplayTitle, riskLabel, statusLabel, toolLabel } from "./presentation";
import { taskProgress, redundantDelegatedProgress } from "./taskProgress";
import TaskProgressList from "./components/conversation/TaskProgressList";
import { processFileChanges } from "./components/conversation/ProcessTranscript";
import { buildProcessTimeline, historyWorkerRuns } from "./processTimeline";
import { mergeTimeline, type ChatMessage, type Outgoing } from "./timeline";
import type { TimelineEvent } from "./timeline";
import MessageList from "./components/conversation/MessageList";
import { Chevron } from "./components/conversation/Icons";
import { groupEvents, text, type EventViewMemory, type HistoryProps, type Quest, type Row } from "./components/conversation/eventModel";

export { default as SafeMarkdownPreview } from "./components/Markdown";

export default function ChatTimeline({ children, messages, events, outgoing, quests, workerRuns, approvals, native, workspaceName,
  connected, historyMore, historyBusy, timelineError, conversationError, approvalError,
  canRetry, scrollRef, onScroll, onInspect, onLoadOlder, onRetry, onQuest, onOpenFile, onOpenHistory, historyStats, connection, onHistoryLoaded, onApproval, approvalsBlocked, onApprovalDecision }: {
  children?: ReactNode;
  messages: ChatMessage[]; events: TimelineEvent[]; outgoing: Outgoing[]; quests: Quest[]; workerRuns: WorkerRun[]; approvals: Row[]; workspaceName?: string;
  native: boolean; connected: boolean; historyMore: boolean; historyBusy: boolean; timelineError: string;
  conversationError: string; approvalError: string; canRetry: boolean; scrollRef: RefObject<HTMLDivElement>;
  onScroll: () => void; onInspect: () => void; onLoadOlder: () => void; onRetry: (item: Outgoing) => void;
  onQuest: (id: string) => void; onApproval: (id: string) => void;
  onOpenFile?: (path: string) => void;
  approvalsBlocked: boolean; onApprovalDecision: (item: Row, decision: "APPROVED" | "REJECTED") => void;
} & HistoryProps) {
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "disconnected";
  const memoryRef = useRef<{ identity: string; value: EventViewMemory }>({ identity, value: { expansion: new Map(), commandScroll: new Map() } });
  if (memoryRef.current.identity !== identity) memoryRef.current = { identity, value: { expansion: new Map(), commandScroll: new Map() } };
  const memory = memoryRef.current.value;
  const items = groupEvents(mergeTimeline(messages, events, outgoing));
  const historicalRuns = historyWorkerRuns(workerRuns, events);
  const delegatedQuests = new Set(workerRuns.flatMap(run => run.quest_id ? [run.quest_id] : []));
  const progress = taskProgress(quests, events, messages).filter(card =>
    !approvals.some(approval => approval.quest_id === card.quest.id && approval.status === "PENDING") && !redundantDelegatedProgress(card, delegatedQuests, events));
  // Only suppress an action when the exact same immutable receipt already has
  // a visible history button. Other files, tasks and unavailable routes stay intact.
  const visibleHistoryReceipts = new Set(onOpenHistory ? buildProcessTimeline(items, historicalRuns)
    .flatMap(item => item.type === "process" ? processFileChanges(item).slice(0, 1).map(file => file.receiptId) : []) : []);
  return <div className="timeline" ref={scrollRef} onScroll={onScroll} onMouseUp={() => { if (window.getSelection()?.toString()) onInspect(); }} aria-label="对话历史">
    {historyMore && <button className="text-button history" disabled={historyBusy} onClick={onLoadOlder}>{historyBusy ? "正在加载…" : "加载更早的消息与记录"}</button>}
    {conversationError && <p className="notice error" role="alert">{conversationError}</p>}
    {timelineError && <p className="notice error" role="alert">过程记录暂不可用：{timelineError}</p>}
    {!items.length && !workerRuns.length && !(connected && (progress.length || approvals.length)) && <div className="empty-conversation"><h2>{workspaceName ? <>你想让我们在 <span className="welcome-project-name" title={workspaceName}>{workspaceName}</span> 中构建什么？</> : "你想构建什么？"}</h2></div>}
    {(items.length > 0 || workerRuns.length > 0) && <MessageList items={items} identity={identity} quests={quests} workerRuns={historicalRuns} memory={memory} native={native} canRetry={canRetry}
      onRetry={onRetry} onQuest={onQuest} onOpenFile={onOpenFile} onOpenHistory={onOpenHistory} historyStats={historyStats}
      connection={connection} onHistoryLoaded={onHistoryLoaded} onInspect={onInspect} />}
    {children}
    {connected && <TaskProgressList cards={progress} onQuest={onQuest} onOpenHistory={onOpenHistory} visibleHistoryReceipts={visibleHistoryReceipts} />}
    {connected && approvals.length > 0 && <section className="timeline-updates" aria-label="任务与审批更新">
      <div className="timeline-updates-label">需要你处理</div>
      {approvals.slice(0, 3).map((item) => {
        const quest = quests.find((candidate) => candidate.id === item.quest_id);
        const expiresAt = typeof item.expires_at === "string" ? Date.parse(item.expires_at) : NaN;
        const canDecide = !approvalsBlocked && item.status === "PENDING" && !item.consumed_at && !(Number.isFinite(expiresAt) && expiresAt <= Date.now()) &&
          typeof item.parameter_digest === "string" && /^[a-f0-9]{64}$/.test(item.parameter_digest) && typeof item.plan_revision === "number";
        return <div className="timeline-update approval-card" key={text(item.id)}>
          <button className="approval-summary" aria-label={`需要审批 · ${text(item.action_type)} 风险 ${text(item.risk)} · ${text(item.resource_scope)}`} onClick={() => onApproval(text(item.quest_id, ""))}>
            <span><strong>{quest ? questDisplayTitle(quest.title, quest.id, quest.goal) : "关联任务暂未加载"}</strong><small>{statusLabel(item.status, "待审批")} · {toolLabel(item.action_type)} · {riskLabel(item.risk)}</small></span><span aria-hidden="true">›</span>
          </button>
          <div className="approval-scope"><span>{text(item.resource_scope)}</span><small>计划版本 {text(item.plan_revision)}</small></div>
          <AnimatedDetails className="approval-details" onInspect={onInspect} summary={<><span>操作详情</span><Chevron /></>}><dl><dt>参数摘要</dt><dd className="mono break">{text(item.parameter_digest)}</dd><dt>原因</dt><dd>{text(item.reason)}</dd><dt>过期时间</dt><dd>{text(item.expires_at)}</dd></dl></AnimatedDetails>
          <div className="button-row">{(["APPROVED", "REJECTED"] as const).map((decision) => <button key={decision} className={decision === "APPROVED" ? "button primary" : "button"} disabled={!canDecide} onClick={() => { if (canDecide && !(Number.isFinite(expiresAt) && expiresAt <= Date.now())) onApprovalDecision(item, decision); }}>{decision === "APPROVED" ? "批准" : "拒绝"}</button>)}</div>
        </div>;
      })}
      {approvals.length > 3 && <p className="notice">已加载的审批中另有 {approvals.length - 3} 项待处理，审批页按任务筛选 · <button className="text-button" onClick={() => onApproval(text(approvals[0]?.quest_id, ""))}>按任务查看审批</button></p>}
    </section>}
    {connected && approvalError && <p className="notice error" role="alert">审批状态暂不可用：{approvalError}</p>}
  </div>;
}
