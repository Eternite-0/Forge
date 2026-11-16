import ConversationWorkspace from "./components/ConversationWorkspace";
import WorkspaceInspector from "./components/WorkspaceInspector";
import useWorkspaceController from "./hooks/useWorkspaceController";
import "./styles/simple-shell.css";

export default function App() {
  const workspace = useWorkspaceController();
  const { view, settingsOpen, taskView, connection, native, connectionError, secretInput, connectBrowser, actionNotice, setActionNotice } = workspace;
  return <div className={`app-shell simple-shell compact-navigation${view === "chat" && !settingsOpen ? " chat-open" : ""}${settingsOpen ? " settings-open" : ""}${taskView && !settingsOpen ? " tasks-open" : ""}`}>
    {!connection && <section className="simple-connection" aria-label="连接 FORGE">
      <div className="connect-banner" role="status"><div><strong>{native ? "正在连接 FORGE" : "连接 FORGE"}</strong><p>{native ? "正在启动本地服务，请稍候。" : "输入本次启动密钥即可开始。密钥不会保存在浏览器中。"}</p>{connectionError && <p className="error">{connectionError}</p>}</div>{!native && <form className="connect-controls" onSubmit={(event) => { event.preventDefault(); void connectBrowser(); }}><input ref={secretInput} type="password" autoComplete="off" aria-label="Core 启动密钥" placeholder="本次启动密钥" /><button type="submit" className="button primary">连接</button></form>}</div>
    </section>}
    <ConversationWorkspace workspace={workspace} />
    <WorkspaceInspector workspace={workspace} />
    {actionNotice && <div className="action-toast" role="status"><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10" cy="10" r="7.5" /><path d="m6.5 10 2.3 2.3 4.7-4.7" /></svg><span>{actionNotice}</span><button type="button" aria-label="关闭通知" onClick={() => setActionNotice("")}>×</button></div>}
  </div>;
}
