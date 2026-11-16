import ConversationWorkspace from "./components/ConversationWorkspace";
import WorkspaceInspector from "./components/WorkspaceInspector";
import useWorkspaceController from "./hooks/useWorkspaceController";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Titlebar from "./components/Titlebar";
import DesktopNavigation, { type PrimaryView } from "./DesktopNavigation";
import { settingMetadata, type SettingId } from "./SettingsView";
import { text } from "./workspaceTypes";

export default function App() {
  const workspace = useWorkspaceController();
  const { view, setting, settingsOpen, taskView, connection, native, connectionError, secretInput, connectBrowser, actionNotice, setActionNotice } = workspace;
  const shell = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 1000px)").matches);
  const [sidebarOverlay, setSidebarOverlay] = useState(false);
  const taskPageOpen = narrow && taskView && !settingsOpen;
  const sidebarLayerOpen = narrow && sidebarOverlay && !taskPageOpen;
  const compact = narrow ? !sidebarOverlay : workspace.compactNavigation;
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1000px)");
    const change = () => { setNarrow(media.matches); setSidebarOverlay(false); };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useLayoutEffect(() => {
    const background = Array.from(shell.current?.querySelectorAll<HTMLElement>(":scope > .chat-workspace, :scope > .desktop-chrome, :scope > .rail") || []);
    if (taskPageOpen && !shell.current?.querySelector(".task-inspector")?.contains(document.activeElement)) {
      shell.current?.querySelector<HTMLButtonElement>('.task-inspector [aria-label="关闭任务详情"]')?.focus({ preventScroll: true });
    }
    background.forEach(element => {
      element.toggleAttribute("inert", taskPageOpen);
      if (taskPageOpen) element.setAttribute("aria-hidden", "true");
      else element.removeAttribute("aria-hidden");
    });
    return () => background.forEach(element => { element.removeAttribute("inert"); element.removeAttribute("aria-hidden"); });
  }, [taskPageOpen, connection]);
  useLayoutEffect(() => {
    if (!sidebarLayerOpen) return;
    shell.current?.querySelector<HTMLButtonElement>('.codex-icon-rail button')?.focus({ preventScroll: true });
    const content = Array.from(shell.current?.querySelectorAll<HTMLElement>(":scope > .chat-workspace, :scope > .settings-view") || []);
    content.forEach(element => element.setAttribute("inert", ""));
    return () => content.forEach(element => element.removeAttribute("inert"));
  }, [sidebarLayerOpen, settingsOpen]);
  const closeSidebarOverlay = () => setSidebarOverlay(false);
  const openSettings = () => { closeSidebarOverlay(); workspace.openSettings(); };
  const openFiles = () => { closeSidebarOverlay(); workspace.openConversation(); workspace.openPreview("files"); };
  const navigate = (destination: string) => {
    closeSidebarOverlay();
    if (destination === "chat") workspace.openConversation();
    else if (destination === "memory") workspace.selectSetting("memory");
    else if (destination === "quests" || destination === "activity" || destination === "teams" || destination === "approvals") workspace.openTaskView(destination, "");
    else if (destination.startsWith("settings:") && Object.prototype.hasOwnProperty.call(settingMetadata, destination.slice(9))) workspace.selectSetting(destination.slice(9) as SettingId);
  };
  const activeView: PrimaryView | null = view === "memory" ? "memory" : settingsOpen ? null : taskView ? "quests" : "chat";
  const connectionSetup = !connection && <section className="simple-connection" aria-label="连接 FORGE">
      <div className="connect-banner" role="status"><div><strong>{native ? "正在连接 FORGE" : "连接 FORGE"}</strong><p>{native ? "正在启动本地服务，请稍候。" : "输入本次启动密钥即可开始。密钥不会保存在浏览器中。"}</p>{connectionError && <p className="error">{connectionError}</p>}</div>{!native && <form className="connect-controls" onSubmit={(event) => { event.preventDefault(); void connectBrowser(); }}><input ref={secretInput} type="password" autoComplete="off" aria-label="Core 启动密钥" placeholder="本次启动密钥" /><button type="submit" className="button primary">连接</button></form>}</div>
    </section>;
  return <div ref={shell} className={`app-shell simple-shell codex-shell${compact ? " compact-navigation" : ""}${view === "chat" && !settingsOpen ? " chat-open" : ""}${settingsOpen ? " settings-open" : ""}${taskView && !settingsOpen ? " tasks-open" : ""}${taskPageOpen ? " task-page-open" : ""}`}>
    <Titlebar route={settingsOpen ? `settings:${setting}` : view} onNavigate={navigate} sidebarCollapsed={compact} onToggleSidebar={() => {
      workspace.captureReadingPosition();
      if (narrow) setSidebarOverlay(value => !value); else workspace.setSidebarCollapsed(value => !value);
    }} connected={!!connection} onSettings={openSettings} onShortcuts={() => navigate("settings:shortcuts")} onFiles={openFiles} onRefresh={() => void workspace.refreshCurrentView()} />
    <DesktopNavigation compact={compact || taskPageOpen} activeView={activeView} connected={!!connection} workspaceId={connection?.workspace_id || ""}
      workspaceRoot={text(workspace.coreInfo.workspace_root, "")} connection={connection} onNavigate={navigate} onSettings={openSettings}
      onActivity={() => navigate("activity")} pendingCount={workspace.pendingApprovals.length}
      onClose={narrow ? () => { closeSidebarOverlay(); requestAnimationFrame(() => shell.current?.querySelector<HTMLButtonElement>('.sidebar-toggle[aria-expanded]')?.focus()); } : undefined} />
    <ConversationWorkspace workspace={workspace} connectionSetup={connectionSetup} />
    <WorkspaceInspector workspace={workspace} />
    {actionNotice && <div className="action-toast" role="status"><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10" cy="10" r="7.5" /><path d="m6.5 10 2.3 2.3 4.7-4.7" /></svg><span>{actionNotice}</span><button type="button" aria-label="关闭通知" onClick={() => setActionNotice("")}>×</button></div>}
  </div>;
}
