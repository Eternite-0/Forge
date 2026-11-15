import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import DisclosurePopover from "./DisclosurePopover";

export default function ConversationToolbar({ home, visible, status, sessionTitle, onOpenFiles, onOpenChanges, changesCount, conversationFiles, onOpenConversationFiles, children }: {
  home: boolean; visible: boolean; status: string; sessionTitle?: string; children: ReactNode;
  onOpenFiles?: () => void; onOpenChanges?: () => void; changesCount?: number;
  conversationFiles?: number; onOpenConversationFiles?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => { if (!visible) setOpen(false); }, [visible]);
  return <header className="topbar conversation-topbar" hidden={home}>
    <div className="topbar-title" title={sessionTitle}><h1>长期对话</h1></div>
    <div className="conversation-status">
      <span className="router-status" title={status} data-status={status}><span className="status-dot" />Router<span className="router-state-label">{status}</span></span>
      {onOpenFiles && <button type="button" className="conversation-files-toggle" aria-label="查看工作空间文件" title="工作空间文件" onClick={onOpenFiles}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d="M2 5h5l1.5 1.5H14v6H2V5Zm0 0V3h5l1.5 2" /></svg></button>}
      <div className="worker-menu-anchor">
        <button ref={trigger} type="button" className="worker-toggle" aria-label="查看 Worker 执行" aria-expanded={open} aria-controls="conversation-workers" onClick={() => setOpen((value) => !value)}>Worker<svg data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></button>
        <DisclosurePopover open={open && visible} onClose={close} trigger={trigger} id="conversation-workers" label="Worker 执行详情" className="worker-drawer">
          {onOpenChanges && <button type="button" className="conversation-changes-overview" onClick={() => { setOpen(false); onOpenChanges(); }}><span>文件变更</span><span>{typeof changesCount === "number" ? `${changesCount} 个文件` : "查看"}<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></span></button>}
          {onOpenConversationFiles && typeof conversationFiles === "number" && conversationFiles > 0 && <button type="button" className="conversation-file-records" title="已加载对话中成功写入的真实文件记录；不代表当前 Git 变更总数。" onClick={() => { setOpen(false); onOpenConversationFiles(); }}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d="M4 2.5h5l3 3v8H4v-11Zm5 0v3h3M6 9h4M8 7v4" /></svg>{conversationFiles} 个文件已更改</button>}
          {children}
        </DisclosurePopover>
      </div>
    </div>
  </header>;
}
