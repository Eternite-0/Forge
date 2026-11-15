import type { ReactNode } from "react";
import WorkspaceSidebar from "./WorkspaceSidebar";

export type PrimaryView = "chat" | "quests" | "memory";

export interface DesktopNavigationProps {
  compact: boolean;
  activeView: PrimaryView | null;
  connected: boolean;
  workspaceId: string;
  workspaceRoot: string;
  onNavigate: (view: PrimaryView) => void;
  onSettings: () => void;
  children: ReactNode;
}

function NavigationIcon({ view }: { view: PrimaryView }) {
  return <svg className="nav-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {view === "chat" ? <><path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-6 3V6a2 2 0 0 1 2-2Z" /><path d="M7 9h10M7 13h6" /></>
      : view === "quests" ? <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></>
        : <><path d="M12 3.5 20.5 12 12 20.5 3.5 12Z" /><circle cx="12" cy="12" r="2.5" /></>}
  </svg>;
}

/** Keep every destination mounted once while its layout changes with the workspace panel. */
export default function DesktopNavigation({
  compact, activeView, connected, workspaceId, workspaceRoot, onNavigate, onSettings, children,
}: DesktopNavigationProps) {
  return <aside className={`rail${compact ? " rail-compact" : ""}`} aria-label="主导航">
    <div className="primary-rail">
      <div className="rail-brand" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z" /><path d="m8 15 4-7 4 7M10 12h4" /></svg>
      </div>
      <nav className="utility-nav">
        {([{ view: "quests", name: "后台任务", label: "任务" }, { view: "memory", name: "记忆中心", label: "记忆" }] as const).map((item) => <button
          key={item.view}
          type="button"
          className={`nav-item utility-nav-item${activeView === item.view ? " active" : ""}`}
          data-view={item.view}
          data-tooltip={item.label}
          aria-label={item.name}
          aria-current={activeView === item.view ? "page" : undefined}
          title={item.name}
          onClick={() => onNavigate(item.view)}
        ><NavigationIcon view={item.view} /></button>)}
      </nav>
      <button type="button" className="profile-button" aria-label="设置" title="设置 (Ctrl+,)" onClick={onSettings}>
        <span className="profile-avatar" aria-hidden="true">F</span>
      </button>
    </div>
    <WorkspaceSidebar
      connected={connected}
      workspaceId={workspaceId}
      workspaceRoot={workspaceRoot}
      active={!compact}
      navigation={<nav className="conversation-nav"><button
        type="button"
        className={`nav-item conversation-entry${activeView === "chat" ? " active" : ""}`}
        data-view="chat"
        data-tooltip="对话"
        aria-label="对话"
        aria-current={activeView === "chat" ? "page" : undefined}
        title="对话"
        onClick={() => onNavigate("chat")}
      ><NavigationIcon view="chat" /><span className="nav-label">对话</span></button></nav>}
    >{children}</WorkspaceSidebar>
  </aside>;
}
