import ShellIcon, { type ShellIconName } from "./components/ShellIcon";
import type { KeyboardEvent, RefObject } from "react";

export default function ConversationComposer({ inputRef, value, onChange, onKeyDown, onSend, disabled, sending, error, requestId, model, onFiles, onConfigure, home, workspaceName, native, connected }: {
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
  home: boolean;
  workspaceName: string;
  native: boolean;
  connected: boolean;
}) {
  const suggestions: { title: string; prompt: string; icon: ShellIconName }[] = [
    { title: "了解这个项目", prompt: "请阅读当前工作空间，向我介绍这个项目的用途和主要结构。", icon: "folder" },
    { title: "检查代码问题", prompt: "请检查当前项目的代码，找出值得优先修复的问题，先给我分析和建议。", icon: "search" },
    { title: "整理一份计划", prompt: "请根据当前项目的情况，帮我整理一份清晰的下一步工作计划。", icon: "document" },
  ];
  return <footer className="composer-wrap composer-root">
    <div className="composer-stack">
      {error && <div className="send-error">
        <p role="alert">消息未能发送，草稿已保留。</p>
        <details><summary>查看发送失败原因</summary><div>{error}{requestId && <> · 请求 ID：<code>{requestId}</code></>}</div></details>
      </div>}
      {home && !value.trim() && <div className="composer-suggestions" role="group" aria-label="试试这些问题，点击填入草稿">
        {suggestions.map(item => <button type="button" key={item.title} onClick={() => { onChange(item.prompt); inputRef.current?.focus(); }} title="填入草稿，不会自动发送"><span className="composer-suggestion-icon" aria-hidden="true"><ShellIcon name={item.icon} size={18} /></span><span>{item.title}</span></button>)}
      </div>}
      <div className="composer-card">
      <form className="composer composer-body" onSubmit={(event) => { event.preventDefault(); if (!disabled) onSend(); }}>
        <div className="composer-input"><textarea ref={inputRef} rows={1} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={onKeyDown}
          placeholder="发消息给 FORGE" aria-label="消息内容" aria-keyshortcuts="Enter" /></div>
        <div className="composer-utilities">
          <button type="button" className="composer-utility" onClick={onFiles} aria-label="从输入栏打开工作空间文件" title="打开工作空间文件">
            <ShellIcon name="plus" size={17} /><span>文件</span>
          </button>
          <button type="button" className="composer-utility composer-model-label" onClick={onConfigure} aria-label="打开对话模型配置" title={`对话模型：${model}`}>
            <span>{model}</span><ShellIcon name="chevronDown" size={13} />
          </button>
          <button type="submit" className="send-button" aria-label={sending ? "正在发送消息" : "发送消息"} disabled={disabled} title="发送消息">
          {sending ? <span className="loading-spinner" aria-hidden="true" /> : <ShellIcon name="arrowUp" size={18} />}
          </button>
        </div>
      </form>
      <div className="composer-workspace-context">
        <span className="composer-device-context">{native ? "本机" : "浏览器"}</span>
        <button type="button" onClick={onFiles} title={connected ? workspaceName : "连接后显示工作空间"} aria-label="查看当前工作空间文件">
          <ShellIcon name="folder" size={13} /><span>{connected ? workspaceName : "尚未连接工作空间"}</span>
        </button>
      </div>
      </div>
    </div>
  </footer>;
}
