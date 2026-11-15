import { useState, type FormEvent } from "react";
import { errorMessage, operatorConfig, type OperatorForm } from "./logic";
import { SettingsSection } from "./SettingsView";

type Row = Record<string, unknown>;
const text = (value: unknown, fallback = "未知"): string => value == null || value === "" ? fallback : String(value);

/** Same revision-bound settings submission, grouped by the decisions the user makes. */
export default function OperatorSettings({ settings, budget, blocked, save }: {
  settings: Row; budget: Row; blocked: boolean; save: (value: unknown) => void;
}) {
  const saved = settings.config && typeof settings.config === "object" ? settings.config as Row : {};
  const provider = saved.provider && typeof saved.provider === "object" ? saved.provider as Row : {};
  const [form, setForm] = useState<OperatorForm>({
    providerEnabled: !!saved.provider, providerId: text(provider.id, ""), kind: text(provider.kind, "openai_compatible"),
    baseUrl: text(provider.base_url, ""), credentialRef: text(provider.credential_ref, ""),
    allowInsecureLoopback: provider.allow_insecure_loopback === true,
    supportsTools: provider.supports_tools === true, supportsJsonSchema: provider.supports_json_schema === true,
    routerModel: text(saved.router_model_id, ""), contextLimit: text(saved.router_context_limit ?? settings.context_limit, ""),
    tokenLimit: text(saved.model_token_limit ?? budget.token_limit, ""), concurrencyLimit: text(saved.model_concurrency_limit ?? budget.concurrency_limit, ""),
    hosts: Array.isArray(saved.network_hosts) ? saved.network_hosts.join("\n") : "",
  });
  const [error, setError] = useState("");
  const [connectionOptions, setConnectionOptions] = useState(() => !!saved.provider && (!provider.id || !provider.credential_ref));
  const [advanced, setAdvanced] = useState(false);
  const change = <K extends keyof OperatorForm>(key: K, value: OperatorForm[K]) => setForm((old) => ({ ...old, [key]: value }));
  const revealOptions = (formElement?: HTMLFormElement) => {
    // Native validation can run before React commits; make invalid hidden fields focusable immediately.
    formElement?.querySelectorAll<HTMLDetailsElement>(".operator-disclosure").forEach((element) => { element.open = true; });
    setConnectionOptions(true);
    setAdvanced(true);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    try {
      const config = operatorConfig(form);
      if (!Number.isInteger(settings.revision)) throw new Error("配置版本未知，请先刷新。");
      if (!window.confirm(`保存配置版本 ${settings.revision} 的修改？\n重启 Core 后生效；已有预算账本不会重置。\n请核对供应商、模型、预算和网络范围。`)) return;
      setError(""); save({ expected_revision: settings.revision, config });
    } catch (problem) { revealOptions(event.currentTarget as HTMLFormElement); setError(errorMessage(problem)); }
  };
  return <form className="config-form operator-settings settings-blocks" onSubmit={submit} onInvalidCapture={(event) => revealOptions(event.currentTarget)}>
    <p className="notice operator-revision">配置版本 {text(settings.revision)} · {settings.restart_required === true ? "修改待重启生效" : "当前配置已生效或未设置"}</p>
    {Array.isArray(settings.environment_overrides) && settings.environment_overrides.length > 0 && <p className="warning">进程环境优先覆盖：{settings.environment_overrides.join("、")}</p>}
    <SettingsSection title="供应商连接" description="填写模型服务提供的地址。">
      <label className="check-label"><input type="checkbox" checked={form.providerEnabled} onChange={(event) => { change("providerEnabled", event.target.checked); if (event.target.checked && (!form.providerId.trim() || !form.credentialRef.trim())) setConnectionOptions(true); }} />配置供应商</label>
      {form.providerEnabled && <>
        <label className="field-label">协议<select value={form.kind} onChange={(event) => change("kind", event.target.value)}><option value="openai_compatible">OpenAI compatible</option><option value="deepseek">DeepSeek</option><option value="anthropic">Anthropic</option></select></label>
        <label className="field-label">服务地址<input type="url" value={form.baseUrl} onChange={(event) => change("baseUrl", event.target.value)} placeholder="https://example.invalid/v1" /></label>
        <details className="operator-disclosure operator-connection-options" open={connectionOptions} onToggle={(event) => setConnectionOptions(event.currentTarget.open)}>
          <summary><span>连接选项</span><svg data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></summary>
          <div className="operator-disclosure-content">
            <p className="operator-options-note">首次连接需要填写标识与凭据引用；能力按服务说明选择。</p>
            <label className="field-label">供应商 ID<input value={form.providerId} onChange={(event) => change("providerId", event.target.value)} /></label>
            <label className="field-label">凭据引用<input value={form.credentialRef} onChange={(event) => change("credentialRef", event.target.value)} placeholder="FORGE_PROVIDER_KEY_EXAMPLE" /></label>
            <label className="check-label"><input type="checkbox" checked={form.supportsTools} onChange={(event) => change("supportsTools", event.target.checked)} />供应商支持工具调用</label>
            <label className="check-label"><input type="checkbox" checked={form.supportsJsonSchema} onChange={(event) => change("supportsJsonSchema", event.target.checked)} />供应商支持结构化输出</label>
          </div>
        </details>
      </>}
    </SettingsSection>
    <SettingsSection title="Router 模型" description="填写对话使用的模型名称与上下文上限。">
      <label className="field-label">Router 模型 ID<input value={form.routerModel} onChange={(event) => change("routerModel", event.target.value)} /></label>
      <label className="field-label">上下文上限<input type="number" min="2049" value={form.contextLimit} onChange={(event) => change("contextLimit", event.target.value)} /></label>
    </SettingsSection>
    <details className="operator-disclosure operator-advanced" open={advanced} onToggle={(event) => setAdvanced(event.currentTarget.open)}>
      <summary><span>高级设置</span><svg data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></summary>
      <div className="operator-disclosure-content settings-blocks">
        <SettingsSection title="预算与并发">
          <label className="field-label">模型 Token 上限<input type="number" min="0" value={form.tokenLimit} onChange={(event) => change("tokenLimit", event.target.value)} /></label>
          <label className="field-label">并发上限<input type="number" min="1" max="64" value={form.concurrencyLimit} onChange={(event) => change("concurrencyLimit", event.target.value)} /></label>
        </SettingsSection>
        <SettingsSection title="网络访问" description="允许的访问范围仍需遵循操作审批。">
          {form.providerEnabled && <label className="check-label"><input type="checkbox" checked={form.allowInsecureLoopback} onChange={(event) => change("allowInsecureLoopback", event.target.checked)} />允许本机 HTTP（仅 127.0.0.1 / ::1）</label>}
          <label className="field-label">允许的网络主机，每行一个<textarea value={form.hosts} onChange={(event) => change("hosts", event.target.value)} rows={3} placeholder="example.invalid" /></label>
        </SettingsSection>
      </div>
    </details>
    <div className="settings-form-actions">
      {error && <p className="notice error" role="alert">{error}</p>}
      <button className="button primary" type="submit" disabled={blocked}>保存配置</button>
    </div>
  </form>;
}
