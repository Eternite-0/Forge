import type { ReactNode } from "react";
import WorkspaceSidebar from "./WorkspaceSidebar";
import type { Connection } from "./api";

export type PrimaryView = "chat" | "quests" | "memory";
export interface DesktopNavigationProps {
  compact: boolean; activeView: PrimaryView | null; connected: boolean;
  workspaceId: string; workspaceRoot: string; connection?: Connection | null;
  onNavigate: (view: PrimaryView) => void; onSettings: () => void;
  onActivity?: () => void; pendingCount?: number; children: ReactNode;
}
function NavigationIcon({ view }: { view: PrimaryView }) {
  return <svg className="nav-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {view === "chat" ? <><path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-6 3V6a2 2 0 0 1 2-2Z" /><path d="M7 9h10M7 13h6" /></> : view === "quests" ? <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></> : <><path d="M12 3.5 20.5 12 12 20.5 3.5 12Z" /><circle cx="12" cy="12" r="2.5" /></>}
  </svg>;
}
export default function DesktopNavigation({ compact, activeView, connected, workspaceId, workspaceRoot, connection, onNavigate, onSettings, onActivity, pendingCount = 0, children }: DesktopNavigationProps) {
  const chat = <nav className="conversation-nav"><button type="button" className={`nav-item conversation-entry${activeView === "chat" ? " active" : ""}`} data-view="chat" aria-label="对话" aria-current={activeView === "chat" ? "page" : undefined} onClick={() => onNavigate("chat")}><NavigationIcon view="chat" /><span className="nav-label">长期对话</span></button></nav>;
  return <aside className="rail sidebar" aria-label="主导航" hidden={compact}>
    <WorkspaceSidebar connected={connected} workspaceId={workspaceId} workspaceRoot={workspaceRoot} connection={connection} active={!compact && activeView !== null} navigation={chat}
      topNavigation={<nav className="utility-nav">{([{ view: "quests", name: "后台任务", label: "后台任务" }, { view: "memory", name: "记忆中心", label: "记忆" }] as const).map((item) => <button key={item.view} type="button" className={`nav-item utility-nav-item${activeView === item.view ? " active" : ""}`} data-view={item.view} aria-label={item.name} aria-current={activeView === item.view ? "page" : undefined} onClick={() => onNavigate(item.view)}><NavigationIcon view={item.view} /><span>{item.label}</span>{item.view === "quests" && pendingCount > 0 && <span className="nav-badge" title="待审批操作">{pendingCount}</span>}</button>)}{onActivity && <button type="button" className="nav-item utility-nav-item" onClick={onActivity}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M3 12h4l3-7 4 14 3-7h4" /></svg><span>运行活动</span></button>}</nav>}
      footer={<button type="button" className="profile-button" aria-label="设置" title="设置 (Ctrl+,)" onClick={onSettings}><span className="profile-avatar" aria-hidden="true">F</span><span>设置</span><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></button>}
    >{children}</WorkspaceSidebar>
  </aside>;
}
