import { useEffect, useRef, type ReactNode } from "react";
import ShellIcon from "./components/ShellIcon";
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
  return <ShellIcon className="nav-icon" name={view === "chat" ? "compose" : view === "quests" ? "clock" : "memory"} />;
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
    {onActivity && <button type="button" className="nav-item" onClick={onActivity}><ShellIcon name="activity" /><span className="nav-label">运行活动</span></button>}
  </nav>;
  return <>
    {!compact && onClose && <button type="button" className="codex-sidebar-backdrop" aria-label="收起导航" onClick={onClose} tabIndex={-1} />}
    <aside ref={sidebar} className="rail sidebar codex-sidebar" aria-label="主导航" hidden={compact}>
    <WorkspaceSidebar connected={connected} workspaceId={workspaceId} workspaceRoot={workspaceRoot} connection={connection} active={!compact && activeView !== null} navigation={navigation}
      footer={<><button type="button" className="nav-item codex-settings-entry" title="设置 (Ctrl+,)" onClick={onSettings}><ShellIcon name="settings" /><span>设置</span></button><div className="codex-connection-state"><span className={`status-dot${connected ? " on" : ""}`} /><span>{connected ? "Core 已连接" : "Core 未连接"}</span></div></>}
    >{children}</WorkspaceSidebar>
  </aside></>;
}
