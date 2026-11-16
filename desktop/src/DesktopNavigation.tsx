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
    {view === "chat" ? <><path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-6 3V6a2 2 0 0 1 2-2Z" /><path d="M7 9h10M7 13h6" /></> : view === "quests" ? <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></> : <><path d="M12 3.5 20.5 12 12 20.5 3.5 12Z" /><circle cx="12" cy="12" r="2.5" /></>}
  </svg>;
}
export default function DesktopNavigation({ compact, activeView, connected, workspaceId, workspaceRoot, connection, onNavigate, onSettings, onActivity, onClose, pendingCount = 0, children }: DesktopNavigationProps) {
  const first = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const navigate = (view: PrimaryView) => { onNavigate(view); if (window.matchMedia("(max-width: 1000px)").matches) onClose?.(); };
  useEffect(() => {
    if (compact || !window.matchMedia("(max-width: 1000px)").matches) return;
    const previous = document.activeElement;
    first.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && closeRef.current) { event.preventDefault(); closeRef.current(); if (previous instanceof HTMLElement) previous.focus(); } };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [compact]);
  const chat = <nav className="conversation-nav" aria-label="对话入口"><button type="button" className={`nav-item conversation-entry${activeView === "chat" ? " active" : ""}`} data-view="chat" aria-label="对话" aria-current={activeView === "chat" ? "page" : undefined} onClick={() => navigate("chat")}><NavigationIcon view="chat" /><span className="nav-label">与 FORGE 对话</span></button></nav>;
  return <>
    {!compact && onClose && <button type="button" className="codex-sidebar-backdrop" aria-label="收起导航" onClick={onClose} tabIndex={-1} />}
    <aside className="rail sidebar codex-sidebar" aria-label="主导航" hidden={compact}>
    <nav className="codex-icon-rail" aria-label="主要功能">
      {([{ view: "chat", label: "对话" }, { view: "quests", label: "后台任务" }, { view: "memory", label: "记忆中心" }] as const).map((item, index) => <button ref={index === 0 ? first : undefined} key={item.view} type="button" className={`codex-rail-button${activeView === item.view ? " active" : ""}`} aria-label={item.label} title={item.label} aria-current={activeView === item.view ? "page" : undefined} onClick={() => navigate(item.view)}><NavigationIcon view={item.view} />{item.view === "quests" && pendingCount > 0 && <span className="codex-rail-dot" aria-label={`${pendingCount} 项待确认`} />}</button>)}
      {onActivity && <button type="button" className="codex-rail-button" aria-label="运行活动" title="运行活动" onClick={onActivity}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M3 12h4l3-7 4 14 3-7h4" /></svg></button>}
      <button type="button" className="codex-rail-button codex-rail-settings" aria-label="设置" title="设置 (Ctrl+,)" onClick={onSettings}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z" /><circle cx="12" cy="12" r="3.5" /></svg></button>
    </nav>
    <WorkspaceSidebar connected={connected} workspaceId={workspaceId} workspaceRoot={workspaceRoot} connection={connection} active={!compact && activeView !== null} navigation={chat}
      footer={<div className="codex-connection-state"><span className={`status-dot${connected ? " on" : ""}`} /><span>{connected ? "Core 已连接" : "Core 未连接"}</span></div>}
    >{children}</WorkspaceSidebar>
  </aside></>;
}
