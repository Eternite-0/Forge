import SettingsView, { SettingsRow, type SettingId } from "../SettingsView";
import { toolLabel } from "../presentation";
import { Row, Quest, DataState, text, rows, display } from "../workspaceTypes";

export function StateNotice({ state, empty = "暂无数据" }: { state: DataState; empty?: string }) {
  if (state.loading && state.value == null) return <p className="notice" role="status">正在加载…</p>;
  if (state.error) return <p className="notice error" role="alert">{state.error}</p>;
  if (state.value == null || (Array.isArray(state.value) && !state.value.length) ||
      (typeof state.value === "object" && state.value !== null && "items" in state.value && Array.isArray((state.value as Row).items) && !(state.value as {items: unknown[]}).items.length)) return <p className="notice">{empty}</p>;
  return null;
}

export function TechnicalDetails({ value }: { value: unknown }) {
  return <details><summary>技术详情</summary><pre>{display(value)}</pre></details>;
}

export function WorkspaceFrame({ settingsOpen, setting, onSetting, onClose, title, children, taskInspector = false }: {
  settingsOpen: boolean; setting: SettingId; onSetting: (id: SettingId) => void; onClose: () => void; title: string; children: React.ReactNode; taskInspector?: boolean;
}) {
  return settingsOpen ? <SettingsView selected={setting} onSelect={onSetting} onClose={onClose}><aside className="settings-page" aria-label={title}>{children}</aside></SettingsView>
    : <aside className={"workspace-panel" + (taskInspector ? " task-inspector" : "")} aria-label={title} onKeyDown={event => {
      if (!taskInspector || event.defaultPrevented || event.key !== "Escape" || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
      event.preventDefault();
      onClose();
    }}>{children}</aside>;
}

export function SettingInfo({ setting }: { setting: SettingId }) {
  const information: Partial<Record<SettingId, [string, string][]>> = {
    shortcuts: [["打开设置", "Ctrl + ,"], ["发送消息", "Enter"], ["换行", "Shift + Enter"]],
  };
  return <>{information[setting]?.map(([label, value]) => <SettingsRow key={label} label={label}><span>{value}</span></SettingsRow>)}</>;
}

export function CoreOverview({ settings, connected }: { settings: Row; connected: boolean }) {
  const workspacePath = text(settings.workspace_root ?? settings.workspace_id);
  return <div className="general-settings">
    <section><h2 className="settings-block-heading">工作空间与连接</h2><div className="settings-group">
      <SettingsRow label="工作空间" description="当前长期对话与任务所在的工作空间。"><span className="settings-workspace-path" title={workspacePath}>{workspacePath}</span></SettingsRow>
      <SettingsRow label="运行状态" description="连接状态不代表供应商、模型、权限和预算均已就绪。">{connected ? "Core 已连接" : "Core 未连接"}</SettingsRow>
    </div></section>
    <section><h2 className="settings-block-heading">模型与运行</h2><div className="settings-group">
      <SettingsRow label="供应商" description="Core 当前使用的模型供应商。">{text(settings.provider_id)}</SettingsRow>
      <SettingsRow label="Router 模型" description="长期对话与任务协调使用的模型。">{text(settings.router_model_id)}</SettingsRow>
      <SettingsRow label="上下文上限" description="当前 Router 的上下文预算。">{text(settings.context_limit)}</SettingsRow>
    </div></section>
  </div>;
}

export function names(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.map((item) => toolLabel(item)).join("、") : "无";
  return text(value);
}

export function GrantPanel({ quest, state, workspace, scope, setScope, blocked, submit }: {
  quest: Quest; state: DataState; workspace: string; scope: string; setScope: (value: string) => void;
  blocked: boolean; submit: (path: string, body: unknown) => void;
}) {
  const revision = quest.plan_revision;
  const act = (target: string, revoke: boolean) => {
    if (!workspace || !target.trim() || typeof revision !== "number") return;
    if (!window.confirm(`${revoke ? "撤销" : "授权"}工作区范围？\n工作区：${workspace}\n范围：${target}\n计划版本：${revision}\n仅对当前任务生效。`)) return;
    submit(`/api/quests/${encodeURIComponent(quest.id)}/grants${revoke ? "/revoke" : ""}`,
      { scope: target, expected_plan_revision: revision });
  };
  return <section className="grant-section"><div className="section-heading"><span>工作区权限</span></div>
    <p className="notice">任务 {quest.title} · 计划版本 {text(revision)}<br />工作区：<span className="break">{workspace || "未知，请刷新"}</span></p>
    <StateNotice state={state} empty="尚未授权任何路径" />
    {rows(state.value).map((grant, index) => <div className="plan-node" key={text(grant.resource_scope, String(index))}><strong className="break">{text(grant.resource_scope)}</strong><small>计划版本 {text(grant.plan_revision)} · {grant.revoked_at ? `已撤销 ${text(grant.revoked_at)}` : "已授权"}</small>{!grant.revoked_at && <button className="button" disabled={blocked || !workspace || typeof revision !== "number"} onClick={() => act(text(grant.resource_scope, ""), true)}>撤销范围</button>}</div>)}
    <label className="field-label">明确授权范围（相对工作区或工作区内绝对路径）<input value={scope} onChange={(event) => setScope(event.target.value)} placeholder="例如 output/reports" /></label>
    <p className="notice">不预选整个工作区；服务端会校验并显示规范化后的实际范围。</p>
    <button className="button" disabled={blocked || !workspace || !scope.trim() || typeof revision !== "number"} onClick={() => act(scope, false)}>授权此范围</button>
  </section>;
}

