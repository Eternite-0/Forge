import { useEffect, useRef, type ReactNode } from "react";
import ShellIcon from "./components/ShellIcon";
import WorkspaceSidebar from "./WorkspaceSidebar";
import type { Connection } from "./api";

export type PrimaryView = "chat" | "quests" | "memory";
export interface DesktopNavigationProps {
  compact: boolean; activeView: PrimaryView | null; connected: boolean;
  workspaceId: string; workspaceRoot: string; conversationTitle?: string; connection?: Connection | null;
  onNavigate: (view: PrimaryView) => void; onSettings: () => void;
  onActivity?: () => void; onClose?: () => void; pendingCount?: number; children?: ReactNode;
}
function NavigationIcon({ view }: { view: PrimaryView }) {
  return <ShellIcon className="nav-icon" name={view === "chat" ? "compose" : view === "quests" ? "clock" : "memory"} />;
}
export default function DesktopNavigation({ compact, activeView, connected, workspaceId, workspaceRoot, conversationTitle, connection, onNavigate, onSettings, onActivity, onClose, pendingCount = 0, children }: DesktopNavigationProps) {
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
  const navigation = <nav className="codex-feature-list" aria-label="当前对话">
    <button type="button" className="nav-item conversation-entry" data-view="chat" onClick={() => navigate("chat")}><NavigationIcon view="chat" /><span className="nav-label">与 FORGE 对话</span></button>
  </nav>;
  return <>
    {!compact && onClose && <button type="button" className="codex-sidebar-backdrop" aria-label="收起导航" onClick={onClose} tabIndex={-1} />}
    <aside ref={sidebar} className={`rail sidebar codex-sidebar${compact ? " sidebar-is-collapsed" : ""}`} aria-label="主导航">
    <nav className="reference-icon-rail" aria-label="主要功能">
      {([{ view: "chat", label: "与 FORGE 对话" }, { view: "quests", label: "后台任务" }, { view: "memory", label: "记忆中心" }] as const).map((item, index) => <button ref={index === 0 ? first : undefined} key={item.view} type="button" className={`reference-rail-button${activeView === item.view ? " active" : ""}`} data-view={item.view} aria-label={item.label} title={item.label} aria-current={activeView === item.view ? "page" : undefined} onClick={() => navigate(item.view)}>{item.view === "chat" ? <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M3.5 10.1 10 4.7a3.1 3.1 0 0 1 4 0l6.5 5.4v9.1a1.8 1.8 0 0 1-1.8 1.8h-4.2v-6.1a2.5 2.5 0 0 0-5 0V21H5.3a1.8 1.8 0 0 1-1.8-1.8Z" /></svg> : <NavigationIcon view={item.view} />}{item.view === "quests" && pendingCount > 0 && <span className="reference-rail-badge" aria-label={`${pendingCount} 项待确认`}>{pendingCount}</span>}</button>)}
      {onActivity && <button type="button" className="reference-rail-button" aria-label="运行活动" title="运行活动" onClick={onActivity}><ShellIcon name="ellipsis" /></button>}
      <button type="button" className="reference-rail-button reference-rail-settings" aria-label="设置" title="设置 (Ctrl+,)" onClick={onSettings}><ShellIcon name="settings" /></button>
    </nav>
    <WorkspaceSidebar connected={connected} workspaceId={workspaceId} workspaceRoot={workspaceRoot} connection={connection} conversationTitle={conversationTitle} conversationActive={activeView === "chat"} onOpenConversation={() => navigate("chat")} active={!compact && activeView !== null} navigation={navigation}
      footer={<div className="codex-connection-state"><span className={`status-dot${connected ? " on" : ""}`} /><span>{connected ? "Core 已连接" : "Core 未连接"}</span></div>}
    >{children}</WorkspaceSidebar>
  </aside></>;
}
