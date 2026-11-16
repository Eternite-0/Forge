import Titlebar from "./components/Titlebar";
import RuntimeHealth from "./components/RuntimeHealth";
import { settingMetadata, type SettingId } from "./SettingsView";
import DesktopNavigation from "./DesktopNavigation";
import ConversationWorkspace from "./components/ConversationWorkspace";
import WorkspaceInspector from "./components/WorkspaceInspector";
import useWorkspaceController from "./hooks/useWorkspaceController";
import { text, VIEW_NAMES, type View } from "./workspaceTypes";

export default function App() {
  const workspace = useWorkspaceController();
  const { compactNavigation, view, settingsOpen, setting, taskView, sidebarCollapsed, setSidebarCollapsed, connection, coreInfo, pendingApprovals, openSettings, openTaskView, setSettingsOpen, setView, setActionError, native, connectionError, secretInput, connectBrowser, openPreview, selectSetting, refreshCurrentView, actionNotice, setActionNotice } = workspace;
  return <div className={`app-shell${compactNavigation ? " compact-navigation" : ""}${view === "chat" && !settingsOpen ? " chat-open" : ""}${settingsOpen ? " settings-open" : ""}${taskView && !settingsOpen ? " tasks-open" : ""}${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
    <Titlebar health={<RuntimeHealth connection={connection} />} route={settingsOpen ? "settings:" + setting : view} onNavigate={(route) => { const settingId = route.startsWith("settings:") ? route.slice(9) : ""; if (settingId in settingMetadata) { setSettingsOpen(true); selectSetting(settingId as SettingId); } else if (route in VIEW_NAMES) { setSettingsOpen(false); setView(route as View); setActionError(""); } }} sidebarCollapsed={sidebarCollapsed} onToggleSidebar={() => setSidebarCollapsed((value) => !value)} connected={!!connection} onSettings={openSettings} onShortcuts={() => { setSettingsOpen(true); selectSetting("shortcuts"); }} onFiles={() => { setSettingsOpen(false); setView("chat"); openPreview("files"); }} onRefresh={() => void refreshCurrentView()} />
    <DesktopNavigation compact={compactNavigation} connection={connection} pendingCount={pendingApprovals.length} onActivity={() => openTaskView("activity", "")} activeView={settingsOpen ? null : taskView ? "quests" : view === "memory" ? "memory" : "chat"}
      connected={!!connection} workspaceId={connection?.workspace_id || ""} workspaceRoot={connection ? text(coreInfo.workspace_root, "") : ""}
      onNavigate={(destination) => { if (destination === "memory") selectSetting("memory"); else { setSettingsOpen(false); setView(destination); setActionError(""); } }} onSettings={openSettings}>
        {!connection && <div className="connect-banner" role="status"><div><strong>{native ? "正在连接本地 Core" : "连接开发 Core"}</strong><p>{native ? "等待桌面运行时提供安全会话。" : "输入本次 Core 启动密钥。密钥不会保存在浏览器中。"}</p>{connectionError && <p className="error">{connectionError}</p>}</div>{!native && <div className="connect-controls"><input ref={secretInput} type="password" autoComplete="off" aria-label="Core 启动密钥" placeholder="本次启动密钥" /><button className="button primary" onClick={() => void connectBrowser()}>连接</button></div>}</div>}
    </DesktopNavigation>
    <ConversationWorkspace workspace={workspace} />
    <WorkspaceInspector workspace={workspace} />
    {actionNotice && <div className="action-toast" role="status"><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10" cy="10" r="7.5" /><path d="m6.5 10 2.3 2.3 4.7-4.7" /></svg><span>{actionNotice}</span><button type="button" aria-label="关闭通知" onClick={() => setActionNotice("")}>×</button></div>}
  </div>;
}
