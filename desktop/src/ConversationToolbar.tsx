import { useCallback, useRef, useState } from "react";
import DisclosurePopover from "./DisclosurePopover";
import ShellIcon from "./components/ShellIcon";

export default function ConversationToolbar({ home, status, title, changeCount, onOpenChanges, onOpenFiles, onOpenTasks, onOpenSettings, onOpenMemory, onOpenActivity, onConfigure, pendingCount = 0, agentOpen, onToggleAgent }: {
  home: boolean; status: string; title: string;
  changeCount: number; onOpenChanges: () => void;
  onOpenFiles?: () => void; onOpenTasks: () => void; agentOpen: boolean; onToggleAgent: () => void;
  onOpenSettings: () => void; onOpenMemory: () => void; onOpenActivity: () => void; onConfigure: () => void; pendingCount?: number;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [focusLast, setFocusLast] = useState(false);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const closeMenu = useCallback(() => setMenuOpen(false), []);
  return <header className="topbar conversation-topbar" data-home={home} data-tauri-drag-region>
    <div className="topbar-title">
      {!home && <ShellIcon name="folder" size={15} />}
      <h1 title={home ? "长期对话" : title}>{home ? "对话" : title}</h1>
    </div>
    <div className="conversation-status">
      {status === "供应商未配置" ? <button type="button" className="conversation-tasks-toggle" onClick={onConfigure}>连接模型</button> :
        status === "正在回复" && <span className="router-status" role="status" data-status={status}>等待响应…</span>}
      {home && onOpenFiles && <button type="button" className="thread-tool home-open-files" aria-label="打开工作空间文件" title="打开工作空间文件" onClick={onOpenFiles}><svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true" focusable="false"><rect x="2.75" y="3.25" width="14.5" height="13.5" rx="3" /><path d="M12.75 3.5v13" /></svg></button>}
      {!home && <>{changeCount > 0 && <button type="button" className="thread-tool" aria-label={`改动 ${changeCount}`} title={`查看对话中的 ${changeCount} 个文件改动`} onClick={onOpenChanges}><ShellIcon name="changes" size={16} /><span aria-hidden="true">{changeCount}</span></button>}
      <div className="conversation-menu-anchor">
        <button ref={menuTrigger} type="button" className="conversation-more-toggle" aria-label="更多对话操作" title="更多对话操作" aria-expanded={menuOpen} aria-controls="conversation-actions-menu" aria-haspopup="dialog" onClick={() => { setFocusLast(false); setMenuOpen(value => !value); }} onKeyDown={event => {
          if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
            event.preventDefault();
            if (menuOpen) document.querySelector<HTMLButtonElement>(`#conversation-actions-menu .conversation-menu-item:${event.key === "ArrowUp" ? "last" : "first"}-child`)?.focus({ preventScroll: true });
            else { setFocusLast(event.key === "ArrowUp"); setMenuOpen(true); }
          }
        }}><svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" aria-hidden="true" focusable="false"><circle cx="5" cy="6" r="1.7" /><path d="M10 6h6" /><circle cx="5" cy="14" r="1.7" /><path d="M10 14h6" /></svg></button>
        <DisclosurePopover open={menuOpen} onClose={closeMenu} trigger={menuTrigger} id="conversation-actions-menu" label="对话操作" className="conversation-actions-menu" focusLast={focusLast}>
          {onOpenFiles && <button type="button" className="conversation-menu-item" aria-label="查看工作空间文件" onClick={() => { closeMenu(); onOpenFiles(); }}><ShellIcon name="folder" size={16} />工作空间文件</button>}
          <button type="button" className="conversation-menu-item" onClick={() => { closeMenu(); onOpenMemory(); }}><ShellIcon name="memory" size={16} />记忆</button>
          <button type="button" className="conversation-menu-item" onClick={() => { closeMenu(); onOpenTasks(); }}><ShellIcon name="tasks" size={16} />任务管理</button>
          <button type="button" className="conversation-menu-item" onClick={() => { closeMenu(); onOpenSettings(); }}><ShellIcon name="settings" size={16} />设置</button>
        </DisclosurePopover>
      </div></>}
    </div>
  </header>;
}
