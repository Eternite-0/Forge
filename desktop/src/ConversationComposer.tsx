import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import DisclosurePopover from "./DisclosurePopover";

export default function ConversationComposer({ inputRef, value, onChange, onKeyDown, onSend, onConfigure, disabled, sending, workspace, workspaceName, native, model, provider, status, visible, error, requestId }: {
  inputRef: RefObject<HTMLTextAreaElement>; value: string; onChange: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void; onSend: () => void;
  onConfigure: (setting: "environment" | "models") => void;
  disabled: boolean; sending: boolean; workspace: string; workspaceName: string; native: boolean;
  model: string; provider: string; status: string; visible: boolean; error: string; requestId?: string;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => { if (!visible) setOpen(false); }, [visible]);
  const configure = (setting: "environment" | "models") => { setOpen(false); onConfigure(setting); };
  return <footer className="composer-wrap"><div className="composer-stack">
    <div className="composer-context sr-only">
      <span title={workspace}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 7h7l2 2h9v10H3V7Zm0 0V5h7l2 2" /></svg><span className="composer-workspace-name">{workspaceName}</span></span>
      <span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="12" rx="1.5" /><path d="M2 20h20M9 16v4m6-4v4" /></svg>{native ? "本地" : "开发连接"}</span>
    </div>
    <div className="composer">
      <textarea ref={inputRef} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={onKeyDown} placeholder="给 Router 发送消息…" aria-label="消息内容" />
      <div className="composer-bottom">
        <span className="composer-router" title={status}><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m12 3 7.5 4.5v9L12 21l-7.5-4.5v-9L12 3Z" /><path d="m4.5 7.5 7.5 5 7.5-5M12 12.5V21" /></svg>Router</span>
        <span className="composer-keyboard sr-only">Enter 发送 · Shift + Enter 换行</span>
        <div className="composer-model-anchor">
          <button ref={trigger} type="button" className="composer-model" aria-label="查看 Router 配置" aria-expanded={open} aria-controls="router-model-configuration" title={[provider, model].filter(Boolean).join(" / ")} onClick={() => setOpen((value) => !value)}><span>{model}</span><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m5 6 3 3 3-3" /></svg></button>
          <DisclosurePopover open={open && visible} onClose={close} trigger={trigger} id="router-model-configuration" label="Router 模型配置" className="composer-model-menu">
            <div className="model-menu-current"><span className="model-menu-label">当前 Router</span><strong className="router-model">{model}</strong><span className="router-provider">{provider || "供应商未记录"}</span></div>
            <div className="model-menu-actions"><button type="button" aria-label="环境" onClick={() => configure("environment")}>运行配置<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></button><button type="button" onClick={() => configure("models")}>模型库<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></button></div>
          </DisclosurePopover>
        </div>
        <button className="send-button" aria-label="发送消息" disabled={disabled} onClick={onSend}>{sending ? <span className="loading-spinner" aria-hidden="true" /> : <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 16V4m-5 5 5-5 5 5" /></svg>}</button>
      </div>
    </div>
  </div>{error && <p className="send-error" role="alert">{error} 草稿已保留。请求 ID：<code>{requestId}</code></p>}</footer>;
}
