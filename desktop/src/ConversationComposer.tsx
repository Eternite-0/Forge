import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import DisclosurePopover from "./DisclosurePopover";
import { request, type Connection } from "./api";
import { errorMessage } from "./logic";

export default function ConversationComposer({ inputRef, value, onChange, onKeyDown, onSend, onConfigure, disabled, sending, workspace, workspaceName, native, model, provider, status, visible, error, requestId, connection }: {
  inputRef: RefObject<HTMLTextAreaElement>; value: string; onChange: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void; onSend: () => void;
  onConfigure: (setting: "environment" | "models", modelId?: string) => void;
  disabled: boolean; sending: boolean; workspace: string; workspaceName: string; native: boolean;
  model: string; provider: string; status: string; visible: boolean; error: string; requestId?: string;
  connection?: Connection | null;
}) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<{ id: string; model_id: string; display_name: string; provider: string; enabled: boolean; quarantine_reason?: string }[]>([]);
  const [modelError, setModelError] = useState("");
  const [modelsLoading, setModelsLoading] = useState(false);
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "";
  useEffect(() => { setModels([]); setModelError(""); setOpen(false); }, [identity]);
  useEffect(() => {
    if (!open || !connection) return;
    const controller = new AbortController();
    setModelsLoading(true); setModelError(""); setModels([]);
    request<typeof models>(connection, "/api/models", { signal: controller.signal })
      .then((items) => { if (!controller.signal.aborted) setModels(Array.isArray(items) ? items : []); })
      .catch((problem) => { if (!controller.signal.aborted) setModelError(errorMessage(problem)); })
      .finally(() => { if (!controller.signal.aborted) setModelsLoading(false); });
    return () => controller.abort();
  }, [open, identity, connection?.bearer]);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => { if (!visible) setOpen(false); }, [visible]);
  const configure = (setting: "environment" | "models", modelId?: string) => { setOpen(false); onConfigure(setting, modelId); };
  return <footer className="composer-wrap composer-root"><div className="composer-stack">
    <div className="composer-context sr-only">
      <span title={workspace}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 7h7l2 2h9v10H3V7Zm0 0V5h7l2 2" /></svg><span className="composer-workspace-name">{workspaceName}</span></span>
      <span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="12" rx="1.5" /><path d="M2 20h20M9 16v4m6-4v4" /></svg>{native ? "本地" : "开发连接"}</span>
    </div>
    <div className="composer composer-body">
      <div className="composer-input"><textarea ref={inputRef} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={onKeyDown} placeholder="问点什么" aria-label="消息内容" /></div>
      <div className="composer-bottom composer-footer">
        <button type="button" className="composer-context-button composer-options-button" aria-label="运行设置与权限" title="运行设置与权限" onClick={() => configure("environment")}><svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true"><path d="M3 6h14M3 14h14" /><circle cx="7" cy="6" r="2" fill="var(--app-color-background-surface)" /><circle cx="13" cy="14" r="2" fill="var(--app-color-background-surface)" /></svg></button>
        <span className="composer-keyboard sr-only">Enter 发送 · Shift + Enter 换行</span>
        <div className="composer-model-anchor">
          <button ref={trigger} type="button" className="composer-model" aria-label="查看模型配置" aria-expanded={open} aria-controls="router-model-configuration" title={[provider, model].filter(Boolean).join(" / ")} onClick={() => setOpen((value) => !value)}><span>{model}</span><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m5 6 3 3 3-3" /></svg></button>
          <DisclosurePopover open={open && visible} onClose={close} trigger={trigger} id="router-model-configuration" label="模型配置" className="composer-model-menu">
            <div className="model-menu-current"><span className="model-menu-label">当前模型</span><strong className="router-model">{model}</strong><span className="router-provider">{provider || "供应商未记录"}</span></div>
            <div className="composer-model-options" aria-label="可用模型">
              {modelsLoading && <p className="notice" role="status">正在读取模型…</p>}
              {modelError && <p className="notice error" role="alert">{modelError}</p>}
              {!modelsLoading && !modelError && !models.filter((item) => item.enabled && !item.quarantine_reason && item.provider === provider).length && <p className="notice">当前供应商暂无可选模型。</p>}
              {models.filter((item) => item.enabled && !item.quarantine_reason && item.provider === provider).map((item) => <button type="button" key={item.id} onClick={() => configure("environment", item.model_id)}><span>{item.display_name || item.model_id}</span>{item.model_id === model && <span aria-label="当前模型">✓</span>}</button>)}
            </div>
            <p className="model-menu-note">选择模型后保存运行配置，重启 Core 生效。</p>
            <div className="model-menu-actions"><button type="button" aria-label="环境" onClick={() => configure("environment")}>运行配置<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></button><button type="button" onClick={() => configure("models")}>模型库<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></button></div>
          </DisclosurePopover>
        </div>
        <button className="send-button" aria-label="发送消息" disabled={disabled} onClick={onSend}>{sending ? <span className="loading-spinner" aria-hidden="true" /> : <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 16V4m-5 5 5-5 5 5" /></svg>}</button>
      </div>
    </div>
  </div>{error && <p className="send-error" role="alert">{error} 草稿已保留。请求 ID：<code>{requestId}</code></p>}</footer>;
}
