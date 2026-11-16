import { useEffect, useRef, useState } from "react";
import type { ActivityState, WorkerRun } from "../ConversationRuntime";
import { WorkerRunDetails } from "../ConversationRuntime";
import { statusLabel } from "../presentation";
import AgentAvatar from "./AgentAvatar";
import type { TimelineEvent } from "../timeline";

interface AgentPanelProps {
  state: ActivityState;
  sources: string[];
  events: TimelineEvent[];
  conversationFiles: number;
  onConversationFiles: () => void;
  filesChanged?: number;
  onChanges: () => void;
  onSource: (path: string) => void;
  onRun: (run: WorkerRun) => void;
  onActivity: () => void;
  onClose: () => void;
}

/** A persistent conversation companion, with identities shared by the transcript. */
export default function AgentPanel({ state, sources, events, conversationFiles, onConversationFiles, filesChanged, onChanges, onSource, onRun, onActivity, onClose }: AgentPanelProps) {
  const [expanded, setExpanded] = useState(false), [allSources, setAllSources] = useState(false);
  const [menu, setMenu] = useState(false);
  const menuAnchor = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const close = (event: PointerEvent) => { if (!menuAnchor.current?.contains(event.target as Node)) setMenu(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [menu]);
  const runs = state.connected && !state.error ? state.items : [];
  const done = runs.filter(run => run.state === "succeeded").length;
  const active = runs.filter(run => ["running", "queued"].includes(run.state || "")).length;
  const failed = runs.filter(run => run.state === "failed").length;
  const other = runs.length - active - done - failed;
  const commands = events.filter(event => event.kind === "tool_receipt" && event.status === "running" && event.execution_mode === "live" && event.detail && typeof event.detail === "object" && typeof (event.detail as Record<string, unknown>).command === "string");
  const summary = state.error ? "暂不可用" : state.loading && !runs.length ? "正在读取…" : !state.connected ? "未连接"
    : [active ? `${active} 运行中` : "", done ? `${done} 完成` : "", failed ? `${failed} 失败` : "", other ? `${other} 其他状态` : ""].filter(Boolean).join(" · ") || "暂无运行";
  return <aside className="agent-panel" aria-label="Agent 面板">
    <header className="agent-panel-header"><span>Agent</span><div ref={menuAnchor} className="agent-panel-menu-anchor" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setMenu(false); }} onKeyDown={event => { if (event.key === "Escape" && menu) { event.stopPropagation(); setMenu(false); menuAnchor.current?.querySelector<HTMLButtonElement>("button")?.focus(); } }}>
      <button type="button" className="agent-panel-more" aria-label="Agent 面板菜单" aria-expanded={menu} onClick={() => setMenu(!menu)}>···</button>
      {menu && <div className="agent-panel-menu"><button type="button" onClick={() => { setMenu(false); onActivity(); }}>查看运行活动</button><button type="button" onClick={() => { setMenu(false); onClose(); }}>隐藏 Agent 面板</button></div>}
    </div></header>
    <button type="button" className="agent-panel-changes" onClick={onChanges}>
      <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><rect x="3" y="2.5" width="14" height="15" rx="3" /><path d="M10 6v7m-3.5-3.5h7" /></svg>
      <span>变更</span>{typeof filesChanged === "number" && <small>{filesChanged}</small>}
    </button>
    {conversationFiles > 0 && <button type="button" className="agent-panel-all agent-panel-file-records" onClick={onConversationFiles}>{conversationFiles} 个文件已更改</button>}
    <section className="agent-panel-section">
      <button type="button" className="agent-panel-section-title" aria-expanded={expanded} aria-controls="agent-panel-runs" onClick={() => setExpanded(!expanded)}>子智能体<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d={expanded ? "m4 10 4-4 4 4" : "m4 6 4 4 4-4"} /></svg></button>
      <button type="button" className="agent-panel-summary" aria-label={`展开子智能体：${summary}`} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <span className="agent-avatar-stack">{runs.slice(0, 4).map(run => <AgentAvatar key={run.run_id} identity={run.worker_id || run.run_id} size={16} />)}</span><span>{summary}</span>
      </button>
      {state.error && <p className="agent-panel-notice" role="status">无法读取当前状态。<button type="button" onClick={onActivity}>查看运行活动</button></p>}
      <div id="agent-panel-runs" className="agent-panel-runs" hidden={!expanded}>
        {runs.map(run => <div className="agent-panel-run" key={run.run_id} data-run-id={run.run_id}>
          <button type="button" className="agent-panel-run-button" onClick={() => onRun(run)} title={run.model_id || undefined}><AgentAvatar identity={run.worker_id || run.run_id} size={16} /><span>{run.role || "子智能体"}</span><small data-state={run.state}>{statusLabel(run.state)}</small></button>
          <WorkerRunDetails run={run} runId={run.run_id} />
        </div>)}
        {state.hasMore && <button type="button" className="agent-panel-all" onClick={onActivity}>查看更早的运行</button>}
      </div>
    </section>
    {commands.length > 0 && <section className="agent-panel-section"><div className="agent-panel-sources-heading">后台进程</div>{commands.map(event => <button type="button" className="agent-panel-source" key={event.event_id || event.seq} onClick={onActivity}><svg viewBox="0 0 18 18" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><rect x="2" y="2" width="14" height="14" rx="3" /><path d="m5 6 3 3-3 3m5 0h3" /></svg><span>{String((event.detail as Record<string, unknown>).command || event.title || "运行中的命令")}</span></button>)}</section>}
    <section className="agent-panel-section agent-panel-sources"><div className="agent-panel-sources-heading"><span>来源</span><button type="button" aria-label="浏览工作空间文件" onClick={() => onSource("")}>＋</button></div>
      {sources.length ? <>{sources.slice(0, allSources ? sources.length : 3).map(path => <button type="button" className="agent-panel-source" key={path} onClick={() => onSource(path)} title={path}>
        <svg viewBox="0 0 18 18" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d="M4 2h6l4 4v10H4V2Zm6 0v4h4" /></svg><span>{path.split(/[\\/]/).pop()}</span>
      </button>)}{sources.length > 3 && <button type="button" className="agent-panel-all" aria-expanded={allSources} onClick={() => setAllSources(!allSources)}>{allSources ? "收起来源" : `查看全部 ${sources.length} 项`}</button>}</> : <p className="agent-panel-empty">暂无文件来源</p>}
    </section>
  </aside>;
}
