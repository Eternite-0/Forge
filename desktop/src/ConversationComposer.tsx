import ShellIcon from "./components/ShellIcon";
import type { KeyboardEvent, RefObject } from "react";

export default function ConversationComposer({ inputRef, value, onChange, onKeyDown, onSend, disabled, sending, error, requestId, model, onFiles, onConfigure, onApprovals, approvalCount, home, workspaceName, native, connected }: {
  inputRef: RefObject<HTMLTextAreaElement>;
  value: string;
  onChange: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  disabled: boolean;
  sending: boolean;
  error: string;
  requestId?: string;
  model: string;
  onFiles: () => void;
  onConfigure: () => void;
  onApprovals: () => void;
  approvalCount: number;
  home: boolean;
  workspaceName: string;
  native: boolean;
  connected: boolean;
}) {
  const pendingApprovals = Math.max(0, Math.floor(Number.isFinite(approvalCount) ? approvalCount : 0));
  return <footer className="composer-wrap composer-root">
    <div className="composer-stack">
      {error && <div className="send-error">
        <p role="alert">消息未能发送，草稿已保留。</p>
        <details><summary>查看发送失败原因</summary><div>{error}{requestId && <> · 请求 ID：<code>{requestId}</code></>}</div></details>
      </div>}
      <div className="composer-card">
      {home && <div className="composer-workspace-context">
        <button type="button" onClick={onFiles} title={connected ? workspaceName : "连接后显示工作空间"} aria-label="查看当前工作空间文件">
          <ShellIcon name="folder" size={17} /><span>{connected ? workspaceName : "选择工作空间"}</span>
        </button>
        <span className="composer-device-context"><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.35" aria-hidden="true"><rect x="3" y="3.5" width="14" height="10" rx="1.5"/><path d="M1.5 16h17"/></svg>{native ? "本地" : "浏览器"}</span>
      </div>}
      <form className="composer composer-body" onSubmit={(event) => { event.preventDefault(); if (!disabled) onSend(); }}>
        <div className="composer-input"><textarea ref={inputRef} rows={1} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={onKeyDown}
          placeholder={home ? "随心输入" : "和 FORGE 一起工作"} aria-label="消息内容" aria-keyshortcuts="Enter" /></div>
        <div className="composer-utilities">
          <button type="button" className="composer-utility" onClick={onFiles} aria-label="从输入栏打开工作空间文件" title="打开工作空间文件">
            <ShellIcon name="plus" size={17} /><span>文件</span>
          </button>
          {pendingApprovals > 0 && <button type="button" className="composer-utility composer-approvals" onClick={onApprovals} disabled={!connected}
            aria-label={pendingApprovals > 0 ? `查看审批，${pendingApprovals} 项待确认` : "查看审批"}
            title={connected ? "查看需要你确认的操作，不会修改权限" : "连接后查看审批"}>
            <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="m10 2 6 2.5v5c0 3.7-2.8 6.5-6 8.5-3.2-2-6-4.8-6-8.5v-5L10 2Z"/><path d="M10 6v4M10 13h.01"/></svg>
            <span>审批</span>{pendingApprovals > 0 && <span className="composer-approval-count" aria-hidden="true">{pendingApprovals > 99 ? "99+" : pendingApprovals}</span>}
          </button>}
          <button type="button" className="composer-utility composer-model-label" onClick={onConfigure} aria-label="打开对话模型配置" title={`对话模型：${model}`}>
            <span>{model}</span><ShellIcon name="chevronDown" size={13} />
          </button>
          <button type="submit" className="send-button" aria-label={sending ? "正在发送消息" : "发送消息"} disabled={disabled} title="发送消息">
          {sending ? <span className="loading-spinner" aria-hidden="true" /> : <ShellIcon name="arrowUp" size={18} />}
          </button>
        </div>
      </form>
      </div>
    </div>
  </footer>;
}
