export default function ConversationToolbar({ home, status, sessionTitle, onOpenFiles, agentOpen, onToggleAgent }: {
  home: boolean; status: string; sessionTitle?: string;
  onOpenFiles?: () => void; agentOpen: boolean; onToggleAgent: () => void;
}) {
  return <header className="topbar conversation-topbar" data-home={home}>
    <div className="topbar-title" title={sessionTitle}><h1>FORGE</h1></div>
    <div className="conversation-status">
      <span className="router-status" title={`运行状态：${status}`} data-status={status}><span className="status-dot" /><span className="router-state-label">{status}</span></span>
      {onOpenFiles && <button type="button" className="conversation-files-toggle" aria-label="查看工作空间文件" title="工作空间文件" onClick={onOpenFiles}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d="M2 5h5l1.5 1.5H14v6H2V5Zm0 0V3h5l1.5 2" /></svg></button>}
      {!home && <button type="button" className="agent-panel-toggle" aria-label="切换 Agent 面板" aria-expanded={agentOpen} aria-controls="conversation-agent-panel" onClick={onToggleAgent} title="Agent 面板"><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><rect x="2.5" y="3" width="15" height="14" rx="2.5" /><path d="M12.5 3v14" /></svg><span>活动</span></button>}
    </div>
  </header>;
}
