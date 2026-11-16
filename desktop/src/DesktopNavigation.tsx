import { useEffect, useRef, type ReactNode } from "react";
import WorkspaceSidebar from "./WorkspaceSidebar";
import type { Connection } from "./api";

export type PrimaryView = "chat" | "quests" | "memory";
export interface DesktopNavigationProps {
  compact: boolean; activeView: PrimaryView | null; connected: boolean;
  workspaceId: string; workspaceRoot: string; connection?: Connection | null;
  onNavigate: (view: PrimaryView) => void; onSettings: () => void;
  onActivity?: () => void; onClose?: () => void; pendingCount?: number; children?: ReactNode;
}
function NavigationIcon({ view }: { view: PrimaryView }) {
  return <svg className="nav-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {view === "chat" ? <path d="m4 10 8-7 8 7v10h-6v-7h-4v7H4Z" fill="currentColor" strokeWidth="1" /> : view === "quests" ? <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></> : <><circle cx="11.5" cy="12" r="3.5" /><path d="M15 8.5v5a2.5 2.5 0 0 0 5 0V12a8.5 8.5 0 1 0-3.5 6.9" /></>}
  </svg>;
}
export default function DesktopNavigation({ compact, activeView, connected, workspaceId, workspaceRoot, connection, onNavigate, onSettings, onActivity, onClose, pendingCount = 0, children }: DesktopNavigationProps) {
  const first = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const navigate = (view: PrimaryView) => { onNavigate(view); if (window.matchMedia("(max-width: 760px)").matches) onClose?.(); };
  useEffect(() => {
    if (compact || !window.matchMedia("(max-width: 760px)").matches) return;
    const previous = document.activeElement;
    first.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (event.key === "Escape" && closeRef.current) { event.preventDefault(); closeRef.current(); if (previous instanceof HTMLElement) previous.focus(); }
      if (event.key === "Tab") {
        const controls = Array.from(sidebar.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), summary, [tabindex="0"]') || []).filter((element) => !element.closest('[inert], [hidden], [aria-hidden="true"]') && element.getClientRects().length > 0);
        const start = controls[0]; const end = controls[controls.length - 1];
        if (start && end && event.shiftKey && document.activeElement === start) { event.preventDefault(); end.focus(); }
        else if (start && end && !event.shiftKey && document.activeElement === end) { event.preventDefault(); start.focus(); }
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [compact]);
  const navigation = <nav className="codex-feature-list" aria-label="主要功能">
    {([{ view: "chat", label: "与 FORGE 对话" }, { view: "quests", label: "后台任务" }, { view: "memory", label: "记忆中心" }] as const).map((item, index) => <button ref={index === 0 ? first : undefined} key={item.view} type="button" className={`nav-item${item.view === "chat" ? " conversation-entry" : ""}${activeView === item.view ? " active" : ""}`} data-view={item.view} aria-current={activeView === item.view ? "page" : undefined} onClick={() => navigate(item.view)}><NavigationIcon view={item.view} /><span className="nav-label">{item.label}</span>{item.view === "quests" && pendingCount > 0 && <span className="codex-pending-count" aria-label={`${pendingCount} 项待确认`}>{pendingCount}</span>}</button>)}
    {onActivity && <button type="button" className="nav-item" onClick={onActivity}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M3 12h4l3-7 4 14 3-7h4" /></svg><span className="nav-label">运行活动</span></button>}
  </nav>;
  return <>
    {!compact && onClose && <button type="button" className="codex-sidebar-backdrop" aria-label="收起导航" onClick={onClose} tabIndex={-1} />}
    <aside ref={sidebar} className="rail sidebar codex-sidebar" aria-label="主导航" hidden={compact}>
    <WorkspaceSidebar connected={connected} workspaceId={workspaceId} workspaceRoot={workspaceRoot} connection={connection} active={!compact && activeView !== null} navigation={navigation}
      footer={<><button type="button" className="nav-item codex-settings-entry" title="设置 (Ctrl+,)" onClick={onSettings}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z" /><circle cx="12" cy="12" r="3.5" /></svg><span>设置</span></button><div className="codex-connection-state"><span className={`status-dot${connected ? " on" : ""}`} /><span>{connected ? "Core 已连接" : "Core 未连接"}</span></div></>}
    >{children}</WorkspaceSidebar>
  </aside></>;
}
