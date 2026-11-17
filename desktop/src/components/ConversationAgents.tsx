import { useEffect, useRef, useState } from "react";
import type { Connection } from "../api";
import { agentRole, agentState, agentOutcome, agentMilestone, agentActive, type ConversationAgent } from "../conversationAgents";
import useConversationAgents from "../hooks/useConversationAgents";
import { statusLabel } from "../presentation";
import AgentAvatar from "./AgentAvatar";
import "../styles/conversation-agents.css";

type Feed = ReturnType<typeof useConversationAgents>;
export function ConversationAgents({ feed, onSelect }: { feed: Feed; onSelect: (run: ConversationAgent) => void }) {
  const [expanded, setExpanded] = useState(false);
  if (!feed.items.length || feed.error || !feed.connected) return null;
  return <section className="conversation-agents" aria-label="本对话子智能体" data-scroll-key="conversation-agents">
    <button className="conversation-agents-toggle" type="button" aria-expanded={expanded} aria-controls="conversation-agent-runs" onClick={() => setExpanded(!expanded)}>
      <span className="agent-avatar-stack">{feed.items.slice(0, 3).map(run => <AgentAvatar key={run.run_id} identity={run.worker_id || run.run_id} size={18} />)}</span>
      <span>本对话子智能体 · {feed.items.length}{feed.hasMore ? "+" : ""}</span><span aria-hidden="true">{expanded ? "⌃" : "⌄"}</span>
    </button>
    {expanded && <div id="conversation-agent-runs" className="conversation-agent-runs">
      <p className="conversation-agent-caption">本对话已委派的工作 · 已加载 {feed.items.length} 项{feed.hasMore ? "（还有更早记录）" : ""}</p>
      {[true, false].map(active => {
        const items = feed.items.filter(run => agentActive(run) === active);
        return items.length > 0 && <div key={String(active)}><p className="conversation-agent-caption">{active ? "进行中与待处理" : "已结束"}</p>{items.map(run => <button type="button" className="conversation-agent-row" key={run.run_id} onClick={() => onSelect(run)}>
        <AgentAvatar identity={run.worker_id || run.run_id} size={20} />
        <span><strong>{agentRole(run.role)}</strong><span>{run.task_title || "已委派任务"}</span></span>
        <small data-state={run.state}>{agentState(run) || statusLabel(run.state)}{run.execution_mode === "mock" ? " · 模拟" : ""}</small><span aria-hidden="true">›</span>
      </button>)}</div>; })}
      {feed.hasMore && <button type="button" className="conversation-agents-older" disabled={feed.loading} onClick={feed.loadOlder}>{feed.loading ? "正在读取…" : "加载更早的子智能体"}</button>}
    </div>}
  </section>;
}

export function ConversationAgentDetail({ connection, runId, onBack, onActivity, onTask }: { connection: Connection; runId: string; onBack: () => void; onActivity: () => void; onTask: (id: string) => void }) {
  const feed = useConversationAgents(connection, runId);
  const panel = useRef<HTMLElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  useEffect(() => {
    back.current?.focus({ preventScroll: true });
    const media = window.matchMedia("(max-width: 900px)");
    const changed = () => setNarrow(media.matches);
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    if (!narrow || !panel.current?.parentElement) return;
    const siblings: HTMLElement[] = [];
    let branch: HTMLElement = panel.current;
    while (branch.parentElement) {
      siblings.push(...Array.from(branch.parentElement.children).filter(node => node !== branch) as HTMLElement[]);
      branch = branch.parentElement;
      if (branch === document.body) break;
    }
    const previous = siblings.map(node => node.inert);
    siblings.forEach(node => { node.inert = true; });
    return () => siblings.forEach((node, index) => { node.inert = previous[index]; });
  }, [narrow]);
  const run = feed.detail;
  return <aside ref={panel} className="conversation-agent-detail" role={narrow ? "dialog" : "complementary"} aria-modal={narrow || undefined} aria-labelledby="conversation-agent-heading"
    onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onBack(); }
      if (event.key === "Tab" && narrow) {
        const elements = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], [tabindex="0"]') || []);
        const first = elements[0], last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
    <header><button ref={back} type="button" onClick={onBack} aria-label="返回对话">← 返回</button><h2 id="conversation-agent-heading">子智能体详情</h2></header>
    <div className="conversation-agent-detail-body">
      {feed.loading && <p role="status">正在读取委派记录…</p>}
      {feed.error && <div role="status"><p>暂时无法读取此运行，未显示过期状态。</p><button type="button" onClick={feed.refresh}>重新读取</button></div>}
      {run && <>
        <div className="conversation-agent-identity"><AgentAvatar identity={run.worker_id || run.run_id} size={28} /><h3>{agentRole(run.role)}</h3></div>
        <p className="conversation-agent-status">{agentState(run) || statusLabel(run.state)}{run.execution_mode === "mock" ? " · 模拟运行" : ""}</p>
        <section><h4>委派任务</h4><p>{run.task_title || "已委派任务"}</p>{run.task_objective && <p className="agent-objective">{run.task_objective}</p>}</section>
        <dl><div><dt>模型</dt><dd>{run.model_id || "未记录"}</dd></div><div><dt>委派来源</dt><dd>{run.source.kind === "message" ? "本对话请求" : "已登记触发事件"}</dd></div>{run.delegation_state === "revoked" && <div><dt>委派授权</dt><dd>已撤销</dd></div>}</dl>
        <section><h4>结果</h4><p>{agentOutcome(run.outcome?.kind)}</p><p className="conversation-agent-caption">执行成功不等于任务已验收。这里只显示 Core 已记录的状态，不展示内部思考或未核实草稿。</p></section>
        <section><h4>里程碑</h4>{run.milestones?.length ? <ol className="agent-milestones">{run.milestones.map(item => <li key={item.event_id}><span>{agentMilestone(item.event_type)}</span><time dateTime={item.created_at}>{Number.isNaN(Date.parse(item.created_at)) ? "" : new Date(item.created_at).toLocaleString()}</time></li>)}</ol> : <p className="conversation-agent-caption">尚无可公开的里程碑。</p>}</section>
      </>}
      {run?.quest_id && <button type="button" className="conversation-agent-global" onClick={() => onTask(run.quest_id!)}>打开关联任务</button>}
      <button type="button" className="conversation-agent-global" onClick={onActivity}>查看全部运行活动</button>
    </div>
  </aside>;
}
