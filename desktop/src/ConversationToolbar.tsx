import { useCallback, useRef, useState } from "react";
import DisclosurePopover from "./DisclosurePopover";

export default function ConversationToolbar({ home, status, onOpenFiles, onOpenTasks, onOpenSettings, onOpenMemory, onOpenActivity, onConfigure, pendingCount = 0, agentOpen, onToggleAgent }: {
  home: boolean; status: string; sessionTitle?: string;
  onOpenFiles?: () => void; onOpenTasks: () => void; agentOpen: boolean; onToggleAgent: () => void;
  onOpenSettings: () => void; onOpenMemory: () => void; onOpenActivity: () => void; onConfigure: () => void; pendingCount?: number;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [focusLast, setFocusLast] = useState(false);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const closeMenu = useCallback(() => setMenuOpen(false), []);
  return <header className="topbar conversation-topbar" data-home={home} data-tauri-drag-region>
    <div className="topbar-title"><h1>FORGE</h1></div>
    <div className="conversation-status">
      {status === "供应商未配置" ? <button type="button" className="conversation-tasks-toggle" onClick={onConfigure}>连接模型</button> :
        status === "正在回复" && <span className="router-status" role="status" data-status={status}>正在回复…</span>}
      <button type="button" className="conversation-tasks-toggle" aria-label="查看任务" onClick={onOpenTasks}>任务{pendingCount > 0 && <span className="task-attention-count" aria-label={`${pendingCount} 项待确认`}>{pendingCount}</span>}</button>
      <div className="conversation-menu-anchor">
        <button ref={menuTrigger} type="button" className="conversation-more-toggle" aria-label="更多对话操作" title="更多对话操作" aria-expanded={menuOpen} aria-controls="conversation-actions-menu" aria-haspopup="dialog" onClick={() => { setFocusLast(false); setMenuOpen(value => !value); }} onKeyDown={event => {
          if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
            event.preventDefault();
            if (menuOpen) document.querySelector<HTMLButtonElement>(`#conversation-actions-menu .conversation-menu-item:${event.key === "ArrowUp" ? "last" : "first"}-child`)?.focus({ preventScroll: true });
            else { setFocusLast(event.key === "ArrowUp"); setMenuOpen(true); }
          }
        }}><svg viewBox="0 0 20 20" width="20" height="20" fill="currentColor" aria-hidden="true"><circle cx="4" cy="10" r="1.5" /><circle cx="10" cy="10" r="1.5" /><circle cx="16" cy="10" r="1.5" /></svg></button>
        <DisclosurePopover open={menuOpen} onClose={closeMenu} trigger={menuTrigger} id="conversation-actions-menu" label="对话操作" className="conversation-actions-menu" focusLast={focusLast}>
          {onOpenFiles && <button type="button" className="conversation-menu-item" aria-label="查看工作空间文件" onClick={() => { closeMenu(); onOpenFiles(); }}>工作空间文件</button>}
          <button type="button" className="conversation-menu-item" onClick={() => { closeMenu(); onOpenMemory(); }}>记忆</button>
          <button type="button" className="conversation-menu-item" onClick={() => { closeMenu(); onOpenActivity(); }}>运行活动</button>
          {!home && <button type="button" className="conversation-menu-item" aria-label="切换 Agent 面板" aria-expanded={agentOpen} aria-controls="conversation-agent-panel" onClick={() => { closeMenu(); onToggleAgent(); }}>执行详情</button>}
          <button type="button" className="conversation-menu-item" onClick={() => { closeMenu(); onOpenSettings(); }}>设置</button>
        </DisclosurePopover>
      </div>
    </div>
  </header>;
}
