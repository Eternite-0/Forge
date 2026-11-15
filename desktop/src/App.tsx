import { invoke, isTauri } from "@tauri-apps/api/core";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import ChatTimeline from "./ChatTimeline";
import { ApiError, browserConnection, request, type Connection } from "./api";
import { appendUnique, errorMessage, isNearBottom, operatorConfig, pendingMessage, preservedScrollTop, RequestGuard, retainIfEqual, shouldSubmitOnEnter, type OperatorForm, type PendingMessage } from "./logic";
import { messageMetadata, type ChatMessage as Message, type Outgoing, type TimelineEvent } from "./timeline";

type View = "chat" | "quests" | "activity" | "models" | "teams" | "memory" | "approvals" | "settings";
type Row = Record<string, unknown>;
interface TimelinePage { items: TimelineEvent[]; next_before?: string | null; has_more?: boolean }
interface Page { messages: Message[]; next_before?: string | null; has_more?: boolean }
interface NativeConnection { state: string; baseUrl: string; bearer: string; workspaceId: string; generation: string }
interface NativeStatus { state: string; generation: string; restartCount: number; lastErrorCode: string | null }
interface WorkspaceOperation { operationId: string; operation: "backup" | "restore"; status: "running" | "completed" | "unknown"; acknowledged?: boolean; result?: { ok: boolean; operation: string; code?: string } }
interface Quest extends Row { id: string; title: string; status: string; version: number; goal: string }
interface QuestPage { items: Quest[]; next_before?: string | null; has_more?: boolean }
interface MemoryItem extends Row { id: string; statement: string; source_refs: string[] }
interface DataState { loading: boolean; error: string; value: unknown }

const NAV: { id: View; label: string }[] = [
  { id: "chat", label: "对话" }, { id: "quests", label: "后台任务" },
  { id: "activity", label: "运行活动" }, { id: "models", label: "模型库" },
  { id: "teams", label: "任务团队" }, { id: "memory", label: "记忆中心" },
  { id: "approvals", label: "审批中心" }, { id: "settings", label: "设置" },
];
const EMPTY: DataState = { loading: false, error: "", value: null };
const text = (value: unknown, fallback = "未知") => value == null || value === "" ? fallback : String(value);
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((x): x is Row => !!x && typeof x === "object") : [];
const display = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const timeText = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? new Date(value * 1000).toLocaleString() : text(value, "未知或需人工处理");
const eventKey = (event: TimelineEvent) => `${event.kind}:${event.event_id}`;
const mergeEvents = (current: TimelineEvent[], next: TimelineEvent[]) => retainIfEqual(current, [...new Map([...current, ...next].map((event) => [eventKey(event), event])).values()]);

function Icon({ name }: { name: View }) {
  const paths: Record<View, React.ReactNode> = {
    chat: <><path d="M4 5.5h16v10H9l-5 3v-13Z" /></>,
    quests: <><path d="M3.5 7h6l2 2H20v9.5H3.5z" /><path d="M3.5 7V5.5h6l2 1.5" /></>,
    activity: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></>,
    models: <><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></>,
    teams: <><circle cx="9" cy="8" r="3" /><path d="M3.5 19c.3-3.2 2.1-5 5.5-5s5.2 1.8 5.5 5M15 5.5a3 3 0 0 1 0 5.8M16 14c2.5.3 3.9 2 4 5" /></>,
    memory: <><path d="M12 3.5 20.5 12 12 20.5 3.5 12z" /><circle cx="12" cy="12" r="2.5" /></>,
    approvals: <><path d="M12 3.5 19 6v5.5c0 4.1-2.4 7.2-7 9-4.6-1.8-7-4.9-7-9V6z" /><path d="m8.5 12 2.3 2.3 4.7-4.7" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M12 2.8v2.1m0 14.2v2.1M2.8 12h2.1m14.2 0h2.1M5.5 5.5 7 7m10 10 1.5 1.5m0-13L17 7M7 17l-1.5 1.5" /></>,
  };
  return <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function StateNotice({ state, empty = "暂无数据" }: { state: DataState; empty?: string }) {
  if (state.loading && state.value == null) return <p className="notice" role="status">正在加载…</p>;
  if (state.error) return <p className="notice error" role="alert">{state.error}</p>;
  if (state.value == null || (Array.isArray(state.value) && !state.value.length) ||
      (typeof state.value === "object" && state.value !== null && "items" in state.value && Array.isArray((state.value as Row).items) && !(state.value as {items: unknown[]}).items.length)) return <p className="notice">{empty}</p>;
  return null;
}

function TechnicalDetails({ value }: { value: unknown }) {
  return <details><summary>技术详情</summary><pre>{display(value)}</pre></details>;
}

function ModelMeasurements({ value }: { value: unknown }) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !Object.keys(value).length) return <p className="notice">能力评分：未知</p>;
  return <div className="measurement-list">{Object.entries(value as Row).map(([name, measurement]) => {
    const entry = measurement && typeof measurement === "object" ? measurement as Row : {};
    return <div key={name}><span>{MODEL_CAPABILITIES.find(([key]) => key === name)?.[1] || name}</span><span>{text(entry.value)} · {text(entry.source)} · {entry.n_samples == null ? (entry.source === "user_override" ? "未采样（0）" : "样本未知") : `样本 ${text(entry.n_samples)}`}{entry.confidence == null ? " · 置信度未知" : ` · 置信度 ${text(entry.confidence)}`}</span></div>;
  })}</div>;
}

function names(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.map((item) => text(item)).join("、") : "无";
  return text(value);
}

function OperatorSettings({ settings, budget, blocked, save }: { settings: Row; budget: Row; blocked: boolean; save: (value: unknown) => void }) {
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
  const change = <K extends keyof OperatorForm>(key: K, value: OperatorForm[K]) => setForm((old) => ({ ...old, [key]: value }));
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    try {
      const config = operatorConfig(form);
      if (!Number.isInteger(settings.revision)) throw new Error("配置版本未知，请先刷新。");
      if (!window.confirm(`保存配置版本 ${settings.revision} 的修改？\n重启 Core 后生效；已有预算账本不会重置。\n请核对供应商、模型、预算和网络范围。`)) return;
      setError(""); save({ expected_revision: settings.revision, config });
    } catch (problem) { setError(errorMessage(problem)); }
  };
  return <form className="config-form" onSubmit={submit}>
    <p className="notice">配置版本 {text(settings.revision)} · {settings.restart_required === true ? "修改待重启生效" : "当前配置已生效或未设置"}</p>
    {Array.isArray(settings.environment_overrides) && settings.environment_overrides.length > 0 && <p className="warning">进程环境优先覆盖：{settings.environment_overrides.join("、")}</p>}
    <label className="check-label"><input type="checkbox" checked={form.providerEnabled} onChange={(event) => change("providerEnabled", event.target.checked)} />配置供应商</label>
    {form.providerEnabled && <><label className="field-label">供应商 ID<input value={form.providerId} onChange={(event) => change("providerId", event.target.value)} /></label><label className="field-label">协议<select value={form.kind} onChange={(event) => change("kind", event.target.value)}><option value="openai_compatible">OpenAI compatible</option><option value="deepseek">DeepSeek</option><option value="anthropic">Anthropic</option></select></label><label className="field-label">服务地址<input type="url" value={form.baseUrl} onChange={(event) => change("baseUrl", event.target.value)} placeholder="https://example.invalid/v1" /></label><label className="check-label"><input type="checkbox" checked={form.allowInsecureLoopback} onChange={(event) => change("allowInsecureLoopback", event.target.checked)} />允许本机 HTTP（仅 127.0.0.1 / ::1）</label><label className="field-label">凭据引用<input value={form.credentialRef} onChange={(event) => change("credentialRef", event.target.value)} placeholder="FORGE_PROVIDER_KEY_EXAMPLE" /></label><label className="check-label"><input type="checkbox" checked={form.supportsTools} onChange={(event) => change("supportsTools", event.target.checked)} />供应商支持工具调用</label><label className="check-label"><input type="checkbox" checked={form.supportsJsonSchema} onChange={(event) => change("supportsJsonSchema", event.target.checked)} />供应商支持结构化输出</label></>}
    <label className="field-label">Router 模型 ID<input value={form.routerModel} onChange={(event) => change("routerModel", event.target.value)} /></label>
    <label className="field-label">上下文上限<input type="number" min="2049" value={form.contextLimit} onChange={(event) => change("contextLimit", event.target.value)} /></label>
    <label className="field-label">模型 Token 上限<input type="number" min="0" value={form.tokenLimit} onChange={(event) => change("tokenLimit", event.target.value)} /></label>
    <label className="field-label">并发上限<input type="number" min="1" max="64" value={form.concurrencyLimit} onChange={(event) => change("concurrencyLimit", event.target.value)} /></label>
    <label className="field-label">允许的网络主机，每行一个<textarea value={form.hosts} onChange={(event) => change("hosts", event.target.value)} rows={3} placeholder="example.invalid" /></label>
    <p className="notice">网络范围不替代操作审批。此表单不接收凭据明文，费用历史与账本不会被重置。</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    <button className="button primary" type="submit" disabled={blocked}>保存配置</button>
  </form>;
}

function GrantPanel({ quest, state, workspace, scope, setScope, blocked, submit }: {
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

const MODEL_CAPABILITIES: [string, string][] = [["coding", "编程"], ["research", "研究"], ["planning", "规划"], ["architecture", "架构"], ["mathematics", "数学"], ["design", "设计"], ["vision", "视觉"], ["writing", "写作"], ["tool_calling", "工具调用"], ["instruction_following", "指令遵循"]];
function ModelEditor({ initial, version, blocked, save, cancel }: { initial: Row | null; version: string | null; blocked: boolean; save: (method: "POST" | "PATCH", path: string, body: unknown) => void; cancel: () => void }) {
  const support = (value: unknown) => value && typeof value === "object" && "supported" in value ? ((value as Row).supported === true ? "yes" : "no") : "unknown";
  const [id, setId] = useState(text(initial?.id, ""));
  const [provider, setProvider] = useState(text(initial?.provider, ""));
  const [modelId, setModelId] = useState(text(initial?.model_id, ""));
  const [label, setLabel] = useState(text(initial?.display_name, ""));
  const [context, setContext] = useState(text(initial?.context_window, ""));
  const [output, setOutput] = useState(text(initial?.max_output_tokens, ""));
  const [tools, setTools] = useState(support(initial?.tool_calling_support));
  const [schema, setSchema] = useState(support(initial?.structured_output_support));
  const [enabled, setEnabled] = useState(initial?.enabled !== false);
  const [scores, setScores] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const parseNumber = (value: string) => {
    if (!value.trim()) return null;
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < 1) throw new Error("上下文和最大输出必须为正整数或留空。");
    return Number(value);
  };
  const supportValue = (value: string) => value === "unknown" ? null : { supported: value === "yes", source: "user_override" };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    try {
      if (!label.trim()) throw new Error("请填写显示名称。");
      const fields: Row = { enabled, display_name: label.trim(), context_window: parseNumber(context), max_output_tokens: parseNumber(output), tool_calling_support: supportValue(tools), structured_output_support: supportValue(schema) };
      if (initial) {
        if (!version) throw new Error("模型版本未知，请重新打开编辑。");
        const changes: Row = { expected_updated_at: version };
        for (const field of ["enabled", "display_name", "context_window", "max_output_tokens"]) if (fields[field] !== initial[field]) changes[field] = fields[field];
        if (tools !== support(initial.tool_calling_support)) changes.tool_calling_support = fields.tool_calling_support;
        if (schema !== support(initial.structured_output_support)) changes.structured_output_support = fields.structured_output_support;
        if (Object.keys(changes).length === 1) throw new Error("没有需要保存的修改。");
        save("PATCH", `/api/models/${encodeURIComponent(text(initial.id))}`, changes);
      } else {
        if (!id.trim() || !provider.trim() || !modelId.trim()) throw new Error("请填写档案 ID、供应商 ID 和模型 ID。");
        const capabilities: Record<string, number> = {};
        for (const [name] of MODEL_CAPABILITIES) {
          const raw = scores[name]?.trim();
          if (!raw) continue;
          const value = Number(raw);
          if (!Number.isFinite(value) || value < 0 || value > 100) throw new Error("能力分数需在 0 到 100 之间。");
          capabilities[name] = value;
        }
        save("POST", "/api/models", { ...fields, id: id.trim(), provider: provider.trim(), model_id: modelId.trim(), capabilities });
      }
      setError("");
    } catch (problem) { setError(errorMessage(problem)); }
  };
  return <form className="model-form" onSubmit={submit}><div className="section-heading"><span>{initial ? "编辑模型档案" : "添加模型档案"}</span></div>
    <label className="field-label">档案 ID<input value={id} disabled={!!initial} onChange={(event) => setId(event.target.value)} /></label>
    <label className="field-label">供应商 ID<input value={provider} disabled={!!initial} onChange={(event) => setProvider(event.target.value)} /></label>
    <label className="field-label">模型 ID<input value={modelId} disabled={!!initial} onChange={(event) => setModelId(event.target.value)} /></label>
    <label className="field-label">显示名称<input value={label} onChange={(event) => setLabel(event.target.value)} /></label>
    <label className="field-label">上下文窗口（未知可留空）<input type="number" min="1" value={context} onChange={(event) => setContext(event.target.value)} /></label>
    <label className="field-label">最大输出 Token（未知可留空）<input type="number" min="1" value={output} onChange={(event) => setOutput(event.target.value)} /></label>
    <label className="field-label">工具调用支持<select value={tools} onChange={(event) => setTools(event.target.value)}><option value="unknown">未知</option><option value="yes">支持</option><option value="no">不支持</option></select></label>
    <label className="field-label">结构化输出支持<select value={schema} onChange={(event) => setSchema(event.target.value)}><option value="unknown">未知</option><option value="yes">支持</option><option value="no">不支持</option></select></label>
    <label className="check-label"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />启用模型</label>
    {!initial && <details><summary>可选：手动能力评分</summary><p className="notice">留空表示未知；输入的 0–100 分仅为用户覆盖，未采样（0），并非基准测评。</p>{MODEL_CAPABILITIES.map(([name, title]) => <label className="field-label" key={name}>{title}<input type="number" min="0" max="100" step="any" value={scores[name] || ""} onChange={(event) => setScores((old) => ({ ...old, [name]: event.target.value }))} /></label>)}</details>}
    {initial && <p className="notice">现有能力测量及来源保持不变；协议支持修改标记为用户覆盖。</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
    <div className="button-row"><button className="button primary" type="submit" disabled={blocked}>保存</button><button className="button" type="button" onClick={cancel}>取消</button></div>
  </form>;
}

export default function App() {
  const native = isTauri();
  const [connection, setConnection] = useState<Connection | null>(null);
  const [connectionError, setConnectionError] = useState("");
  const [runtimeStatus, setRuntimeStatus] = useState<NativeStatus | null>(null);
  const [view, setView] = useState<View>("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [timelineEvents, setTimelineEvents] = useState<TimelineEvent[]>([]);
  const [timelineBefore, setTimelineBefore] = useState<string | null>(null);
  const [timelineHasMore, setTimelineHasMore] = useState(false);
  const [timelineError, setTimelineError] = useState("");
  const [outgoing, setOutgoing] = useState<Outgoing[]>([]);
  const [hasNew, setHasNew] = useState(false);
  const [readingPaused, setReadingPaused] = useState(false);
  const [before, setBefore] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [conversationError, setConversationError] = useState("");
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<PendingMessage | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [quests, setQuests] = useState<Quest[]>([]);
  const [questsBefore, setQuestsBefore] = useState<string | null>(null);
  const [questLoadError, setQuestLoadError] = useState("");
  const [pendingApprovals, setPendingApprovals] = useState<Row[]>([]);
  const [approvalLoadError, setApprovalLoadError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [panel, setPanel] = useState<DataState>(EMPTY);
  const [panelContext, setPanelContext] = useState("");
  const [questPlan, setQuestPlan] = useState<DataState>(EMPTY);
  const [planContext, setPlanContext] = useState("");
  const [grants, setGrants] = useState<DataState>(EMPTY);
  const [grantContext, setGrantContext] = useState("");
  const [grantScope, setGrantScope] = useState("");
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [memoryQuery, setMemoryQuery] = useState("");
  const [memorySearch, setMemorySearch] = useState("");
  const [memoryCursor, setMemoryCursor] = useState<string | null>(null);
  const [activityCursor, setActivityCursor] = useState<string | null>(null);
  const [approvalCursor, setApprovalCursor] = useState<string | null>(null);
  const [providerId, setProviderId] = useState("");
  const [credentialRef, setCredentialRef] = useState("");
  const [credentialStatus, setCredentialStatus] = useState("");
  const [workspacePath, setWorkspacePath] = useState("");
  const [workspaceOperation, setWorkspaceOperation] = useState<WorkspaceOperation | null>(null);
  const [reviewReason, setReviewReason] = useState("");
  const [restoreFenced, setRestoreFenced] = useState<boolean | null>(null);
  const [restoreStatus, setRestoreStatus] = useState<Row | null>(null);
  const [modelEditor, setModelEditor] = useState<{ initial: Row | null; version: string | null } | null>(null);
  const secretInput = useRef<HTMLInputElement>(null);
  const credentialInput = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const manualPause = useRef(false);
  const historyAnchor = useRef<{ top: number; height: number } | null>(null);
  const seenMessages = useRef(new Set<string>());
  const seenEvents = useRef(new Set<string>());
  const seenInitialized = useRef(false);
  const historyLoaded = useRef(false);
  const questsLoaded = useRef(false);
  const nativeIdentity = useRef<{ generation: string; workspace: string } | null>(null);
  const nativePollSerial = useRef(0);
  const operationPollSerial = useRef(0);
  const maintenanceSubmitting = useRef(false);
  const requestGuard = useRef(new RequestGuard());
  const guard = requestGuard.current;
  guard.setConnection(connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "");
  guard.setContext("panel", `${view}|${selectedId}|${memorySearch}`);
  guard.setContext("grants", `${view}|${selectedId}`);
  guard.setContext("action", `${view}|${selectedId}`);
  guard.setContext("model", view);
  guard.setContext("selected-quest", selectedId);
  useEffect(() => { guard.activate(); return () => guard.dispose(); }, [guard]);
  const selected = quests.find((q) => q.id === selectedId);
  const contextKey = `${connection?.generation || ""}|${view}|${selectedId}|${memorySearch}`;
  const questContextKey = `${connection?.generation || ""}|${selectedId}`;
  const visiblePanel = panelContext === contextKey ? panel : EMPTY;
  const visiblePlan = planContext === questContextKey ? questPlan : EMPTY;
  const visibleGrants = grantContext === `${questContextKey}|${view}` ? grants : EMPTY;
  const writesBlocked = restoreFenced === true || (native && (restoreFenced === null || runtimeStatus?.state === "maintenance" || workspaceOperation?.status === "running" || (workspaceOperation?.status === "unknown" && !workspaceOperation.acknowledged)));

  const syncNative = useCallback(async () => {
    if (!native) return;
    const serial = ++nativePollSerial.current;
    void invoke<NativeStatus>("runtime_status").then((value) => { if (serial === nativePollSerial.current) setRuntimeStatus(value); }).catch(() => { if (serial === nativePollSerial.current) setRuntimeStatus(null); });
    try {
      const nativeConnection = await invoke<NativeConnection>("connection_get");
      if (serial !== nativePollSerial.current) return;
      const next: Connection = { base_url: nativeConnection.baseUrl, bearer: nativeConnection.bearer,
        workspace_id: nativeConnection.workspaceId, generation: nativeConnection.generation, state: nativeConnection.state };
      if (!next.bearer || !next.base_url || !next.generation) throw new Error("Core 尚未就绪");
      if (nativeIdentity.current?.generation !== next.generation) {
        if (nativeIdentity.current && nativeIdentity.current.workspace !== next.workspace_id) setPending(null);
        nativeIdentity.current = { generation: next.generation, workspace: next.workspace_id || "" };
        historyLoaded.current = false; questsLoaded.current = false;
        setMessages([]); setTimelineEvents([]); setBefore(null); setHasMore(false); setTimelineBefore(null); setTimelineHasMore(false); setHistoryBusy(false); setSending(false); setRestoreFenced(null);
        seenMessages.current.clear(); seenEvents.current.clear(); seenInitialized.current = false; followBottom.current = true; manualPause.current = false; setReadingPaused(false);
        setOutgoing((old) => old.map((item) => item.status === "sending" ? { ...item, status: "failed", error: "连接已更新，结果未知；可使用原请求 ID 重试。" } : item));
        setQuests([]); setQuestsBefore(null); setPendingApprovals([]); setSelectedId("");
      }
      setConnection((old) => old?.generation === next.generation && old.base_url === next.base_url ? old : next);
      setConnectionError("");
    } catch (error) {
      if (serial !== nativePollSerial.current) return;
      setConnectionError(errorMessage(error));
      setConnection(null);
    }
  }, [native]);

  useEffect(() => {
    if (!native) return;
    void syncNative();
    const timer = window.setInterval(() => void syncNative(), 4000);
    return () => { nativePollSerial.current += 1; window.clearInterval(timer); };
  }, [native, syncNative]);

  useEffect(() => {
    if (!native) return;
    let active = true;
    const poll = async () => {
      if (maintenanceSubmitting.current) return;
      const serial = ++operationPollSerial.current;
      try {
        const value = await invoke<WorkspaceOperation>("workspace_operation_status", {});
        if (active && serial === operationPollSerial.current && !maintenanceSubmitting.current) setWorkspaceOperation(value);
      } catch (error) {
        if (active && serial === operationPollSerial.current && !String(error).includes("OPERATION_NOT_FOUND")) setWorkspaceOperation({ operationId: "", operation: "restore", status: "unknown" });
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => { active = false; operationPollSerial.current += 1; window.clearInterval(timer); };
  }, [native]);

  const refreshConversation = useCallback(async () => {
    if (!connection) return;
    const ticket = guard.start("conversation");
    try {
      const [page, questResult, approvals, settings, timeline, restored] = await Promise.all([
        request<Page>(connection, "/api/conversation?limit=100"),
        request<Quest[] | QuestPage>(connection, "/api/quests?limit=100")
          .then((value) => ({ value, error: "" }))
          .catch((error) => ({ value: null, error: errorMessage(error) })),
        request<{items: Row[]}>(connection, "/api/approvals/pending?limit=20")
          .then((value) => ({ value, error: "" }))
          .catch((error) => ({ value: { items: [] }, error: errorMessage(error) })),
        request<Row>(connection, "/api/settings").catch(() => null),
        request<TimelinePage>(connection, "/api/timeline?limit=100")
          .then((value) => ({ value, error: "" }))
          .catch((error) => ({ value: null, error: errorMessage(error) })),
        request<Row>(connection, "/api/restore-status").catch(() => null),
      ]);
      if (!guard.accepts(ticket)) return;
      const fresh = page.messages.some((item) => !seenMessages.current.has(item.id)) ||
        !!timeline.value?.items.some((item) => !seenEvents.current.has(eventKey(item)));
      if (seenInitialized.current && fresh && !followBottom.current) setHasNew(true);
      page.messages.forEach((item) => seenMessages.current.add(item.id));
      timeline.value?.items.forEach((item) => seenEvents.current.add(eventKey(item)));
      seenInitialized.current = true;
      setMessages((existing) => retainIfEqual(existing, appendUnique(existing, page.messages || [])));
      if (timeline.value) setTimelineEvents((existing) => mergeEvents(existing, timeline.value!.items));
      setTimelineError(timeline.error);
      const committed = new Set(page.messages.map((item) => messageMetadata(item.metadata).request_id));
      setOutgoing((existing) => { const filtered = existing.filter((item) => !committed.has(item.requestId)); return filtered.length === existing.length ? existing : filtered; });
      if (!historyLoaded.current) { setBefore(page.next_before ?? null); setHasMore(!!page.has_more); }
      if (!historyLoaded.current && timeline.value) { setTimelineBefore(timeline.value.next_before ?? null); setTimelineHasMore(!!timeline.value.has_more); }
      if (questResult.value) {
        const questData = Array.isArray(questResult.value) ? questResult.value : questResult.value.items;
        setQuests((existing) => questsLoaded.current ? [...questData, ...existing.filter((item) => !questData.some((fresh) => fresh.id === item.id))] : questData);
        if (!questsLoaded.current) setQuestsBefore(Array.isArray(questResult.value) ? null : questResult.value.next_before ?? null);
      }
      setQuestLoadError(questResult.error);
      setPendingApprovals(approvals.value.items); setApprovalLoadError(approvals.error);
      setRestoreStatus(restored);
      setRestoreFenced(restored && typeof restored.fenced === "boolean" ? restored.fenced : settings && typeof settings.restore_fenced === "boolean" ? settings.restore_fenced : null);
      setConversationError("");
    } catch (error) {
      if (!guard.accepts(ticket)) return;
      setConversationError(errorMessage(error));
      if (error instanceof ApiError && error.status === 401) setConnection(null);
    }
  }, [connection, guard]);

  useEffect(() => {
    if (!connection) return;
    void refreshConversation();
    const timer = window.setInterval(() => void refreshConversation(), 6000);
    return () => window.clearInterval(timer);
  }, [connection, refreshConversation]);

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    if (historyAnchor.current) {
      element.scrollTop = preservedScrollTop(historyAnchor.current.top, historyAnchor.current.height, element.scrollHeight);
      historyAnchor.current = null;
    } else if (followBottom.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [messages, timelineEvents, outgoing]);

  const loadOlder = async () => {
    if (!connection || (!before && !timelineBefore) || historyBusy) return;
    const ticket = guard.start("history");
    setHistoryBusy(true);
    try {
      const [page, history] = await Promise.all([
        before ? request<Page>(connection, `/api/conversation?limit=100&before=${encodeURIComponent(before)}`) : Promise.resolve(null),
        timelineBefore ? request<TimelinePage>(connection, `/api/timeline?limit=100&before=${encodeURIComponent(timelineBefore)}`) : Promise.resolve(null),
      ]);
      if (!guard.accepts(ticket)) return;
      if (scrollRef.current) historyAnchor.current = { top: scrollRef.current.scrollTop, height: scrollRef.current.scrollHeight };
      if (page) { page.messages.forEach((item) => seenMessages.current.add(item.id)); setMessages((current) => appendUnique(page.messages || [], current)); setBefore(page.next_before ?? null); setHasMore(!!page.has_more); }
      if (history) { history.items.forEach((item) => seenEvents.current.add(eventKey(item))); setTimelineEvents((current) => mergeEvents(current, history.items)); setTimelineBefore(history.next_before ?? null); setTimelineHasMore(!!history.has_more); }
      historyLoaded.current = true;
      setConversationError(""); setTimelineError("");
    } catch (error) { if (guard.accepts(ticket)) setConversationError(errorMessage(error)); }
    finally { if (guard.accepts(ticket)) setHistoryBusy(false); }
  };

  const loadMoreQuests = async () => {
    if (!connection || !questsBefore) return;
    const ticket = guard.start("quests");
    try {
      const page = await request<QuestPage>(connection, `/api/quests?limit=100&before=${encodeURIComponent(questsBefore)}`);
      if (!guard.accepts(ticket)) return;
      setQuests((old) => appendUnique(old, page.items));
      questsLoaded.current = true;
      setQuestsBefore(page.next_before ?? null);
    } catch (error) { if (guard.accepts(ticket)) setConversationError(errorMessage(error)); }
  };

  const refreshPanel = useCallback(async () => {
    if (!connection || view === "chat" || view === "quests") return;
    const ticket = guard.start("panel");
    setPanelContext(contextKey);
    setPanel((old) => ({ ...old, loading: true, error: "" }));
    try {
      let value: unknown;
      if (view === "activity") value = await request(connection, `/api/activity?limit=100${selectedId ? `&quest_id=${encodeURIComponent(selectedId)}` : ""}`);
      else if (view === "models") value = await request(connection, "/api/models");
      else if (view === "memory") value = await request(connection, `/api/memory?limit=30&query=${encodeURIComponent(memorySearch)}`);
      else if (view === "settings") value = await Promise.all([request(connection, "/api/settings"), request(connection, "/api/budget")]);
      else if (!selectedId) value = [];
      else if (view === "teams") value = await request(connection, `/api/quests/${encodeURIComponent(selectedId)}/teams`);
      else value = await request(connection, `/api/approvals?quest_id=${encodeURIComponent(selectedId)}&limit=50`);
      if (!guard.accepts(ticket)) return;
      setPanel({ loading: false, error: "", value });
      if (view === "memory" && value && typeof value === "object") setMemoryCursor(text((value as Row).cursor, "") || null);
      if (view === "activity" && value && typeof value === "object") setActivityCursor(text((value as Row).next_before, "") || null);
      if (view === "approvals" && value && typeof value === "object") setApprovalCursor(text((value as Row).next_before, "") || null);
    } catch (error) { if (guard.accepts(ticket)) setPanel({ loading: false, error: errorMessage(error), value: null }); }
  }, [connection, view, selectedId, memorySearch, guard, contextKey]);

  useEffect(() => { setPanel(EMPTY); void refreshPanel(); }, [refreshPanel]);
  useEffect(() => {
    if (!connection || !selectedId || (view !== "quests" && view !== "teams")) { setQuestPlan(EMPTY); return; }
    let active = true;
    setPlanContext(questContextKey);
    setQuestPlan({ loading: true, error: "", value: null });
    request<Row>(connection, `/api/quests/${encodeURIComponent(selectedId)}/plan`)
      .then((value) => { if (active) setQuestPlan({ loading: false, error: "", value }); })
      .catch((error) => { if (active) setQuestPlan({ loading: false, error: error instanceof ApiError && error.status === 404 ? "" : errorMessage(error), value: null }); });
    return () => { active = false; };
  }, [connection, selectedId, view, questContextKey]);

  const refreshGrants = useCallback(async () => {
    if (!connection || !selectedId || (view !== "quests" && view !== "approvals")) { setGrants(EMPTY); return; }
    const ticket = guard.start("grants");
    setGrantContext(`${questContextKey}|${view}`);
    setGrants({ loading: true, error: "", value: null });
    try {
      const [grantRows, settings] = await Promise.all([
        request<Row[]>(connection, `/api/quests/${encodeURIComponent(selectedId)}/grants`),
        request<Row>(connection, "/api/settings"),
      ]);
      if (!guard.accepts(ticket)) return;
      setGrants({ loading: false, error: "", value: grantRows });
      setWorkspaceRoot(text(settings.workspace_root, ""));
    } catch (error) { if (guard.accepts(ticket)) setGrants({ loading: false, error: errorMessage(error), value: null }); }
  }, [connection, selectedId, view, guard, questContextKey]);
  useEffect(() => { void refreshGrants(); }, [refreshGrants]);
  useEffect(() => { setActionBusy(false); setActionError(""); }, [view, selectedId, connection?.generation]);
  useEffect(() => { setGrantScope(""); }, [selectedId]);
  useEffect(() => { setModelEditor(null); }, [view, connection?.generation]);
  useEffect(() => {
    if (!connection || !selectedId || quests.some((quest) => quest.id === selectedId)) return;
    const ticket = guard.start("selected-quest");
    request<{quest: Quest}>(connection, `/api/quests/${encodeURIComponent(selectedId)}`)
      .then(({ quest }) => { if (guard.accepts(ticket)) setQuests((old) => old.some((item) => item.id === quest.id) ? old : [quest, ...old]); })
      .catch((error) => { if (guard.accepts(ticket)) setActionError(errorMessage(error)); });
  }, [connection, selectedId, quests, guard]);

  const connectBrowser = async () => {
    const secret = secretInput.current?.value || "";
    if (secretInput.current) secretInput.current.value = "";
    try {
      const next = await browserConnection("http://127.0.0.1:8765", secret);
      historyLoaded.current = false; questsLoaded.current = false;
      setMessages([]); setTimelineEvents([]); setBefore(null); setTimelineBefore(null); setHasMore(false); setTimelineHasMore(false); setHistoryBusy(false); setSending(false); setQuests([]); setQuestsBefore(null); setPendingApprovals([]); setSelectedId("");
      seenMessages.current.clear(); seenEvents.current.clear(); seenInitialized.current = false; followBottom.current = true; manualPause.current = false; setReadingPaused(false);
      setOutgoing((old) => old.map((item) => item.status === "sending" ? { ...item, status: "failed", error: "连接已更新，结果未知；可使用原请求 ID 重试。" } : item));
      setConnection(next); setConnectionError("");
    }
    catch (error) { setConnectionError(errorMessage(error)); }
  };

  const loadPanelPage = async () => {
    if (!connection || panel.loading) return;
    const cursor = view === "memory" ? memoryCursor : view === "activity" ? activityCursor : view === "approvals" ? approvalCursor : null;
    if (!cursor) return;
    const ticket = guard.start("panel");
    setPanel((old) => ({ ...old, loading: true, error: "" }));
    try {
      const path = view === "memory"
        ? `/api/memory?limit=30&query=${encodeURIComponent(memorySearch)}&cursor=${encodeURIComponent(cursor)}`
        : view === "activity" ? `/api/activity?limit=100&before=${encodeURIComponent(cursor)}${selectedId ? `&quest_id=${encodeURIComponent(selectedId)}` : ""}`
        : `/api/approvals?quest_id=${encodeURIComponent(selectedId)}&limit=50&before=${encodeURIComponent(cursor)}`;
      const page = await request<Row>(connection, path);
      if (!guard.accepts(ticket)) return;
      setPanel((old) => {
        const current = old.value && typeof old.value === "object" ? old.value as Row : {};
        const existing = rows(current.items);
        const next = rows(page.items);
        const key = view === "activity" ? "run_id" : "id";
        const seen = new Set(existing.map((item) => item[key]));
        return { loading: false, error: "", value: { ...page, items: [...existing, ...next.filter((item) => !seen.has(item[key]))] } };
      });
      if (view === "memory") setMemoryCursor(text(page.cursor, "") || null);
      else if (view === "activity") setActivityCursor(text(page.next_before, "") || null);
      else setApprovalCursor(text(page.next_before, "") || null);
    } catch (error) { if (guard.accepts(ticket)) setPanel((old) => ({ ...old, loading: false, error: errorMessage(error) })); }
  };

  const sendPending = async (next: PendingMessage, clearComposer: boolean) => {
    if (!connection || sending || writesBlocked || maintenanceSubmitting.current) return;
    const ticket = guard.start("send");
    setPending(next); setSending(true); setSendError("");
    if (!followBottom.current) setHasNew(true);
    setOutgoing((old) => old.some((item) => item.requestId === next.requestId)
      ? old.map((item) => item.requestId === next.requestId ? { ...item, status: "sending", error: undefined } : item)
      : [...old, { ...next, status: "sending", createdAt: new Date().toISOString() }]);
    if (clearComposer) setDraft((current) => current.trim() === next.content ? "" : current);
    try {
      const result = await request<{user_message: Message; assistant_message: Message}>(connection, "/api/conversation/message", { method: "POST", body: JSON.stringify({ content: next.content, request_id: next.requestId }) });
      if (!guard.accepts(ticket)) return;
      if (!result.user_message?.id || !result.assistant_message?.id) throw new Error("Core 返回的消息记录不完整。");
      seenMessages.current.add(result.user_message.id); seenMessages.current.add(result.assistant_message.id);
      setMessages((current) => appendUnique(current, [result.user_message, result.assistant_message]));
      setOutgoing((old) => old.filter((item) => item.requestId !== next.requestId));
      setDraft((current) => current.trim() === next.content ? "" : current);
      setPending(null);
      await refreshConversation();
    } catch (error) {
      if (!guard.accepts(ticket)) return;
      const detail = errorMessage(error);
      setSendError(detail);
      setOutgoing((old) => old.map((item) => item.requestId === next.requestId ? { ...item, status: "failed", error: detail } : item));
      setDraft((current) => current ? current : next.content);
      if (error instanceof ApiError && error.status === 401) setConnection(null);
    } finally { if (guard.accepts(ticket)) setSending(false); }
  };

  const send = () => {
    const failed = outgoing.find((item) => item.status === "failed" && item.content === draft.trim());
    const next = pendingMessage(draft, failed || pending, () => crypto.randomUUID());
    if (next) void sendPending(next, true);
  };

  const perform = async (path: string, body?: unknown) => {
    if (!connection || actionBusy || writesBlocked || maintenanceSubmitting.current) return;
    const ticket = guard.start("action");
    setActionBusy(true); setActionError("");
    try {
      await request(connection, path, { method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      if (!guard.accepts(ticket)) return;
      await refreshConversation(); await refreshPanel();
      if (!guard.accepts(ticket)) return;
      if (path.includes("/grants")) await refreshGrants();
      if (selectedId && (view === "quests" || view === "teams")) {
        const value = await request<Row>(connection, `/api/quests/${encodeURIComponent(selectedId)}/plan`);
        if (guard.accepts(ticket)) setQuestPlan({ loading: false, error: "", value });
      }
    } catch (error) { if (guard.accepts(ticket)) setActionError(errorMessage(error)); }
    finally { if (guard.accepts(ticket)) setActionBusy(false); }
  };

  const credential = async (operation: "set" | "delete" | "exists") => {
    if ((writesBlocked || maintenanceSubmitting.current) && operation !== "exists") return;
    if (!native || !/^[A-Za-z0-9_-]{1,100}$/.test(providerId) || !/^FORGE_PROVIDER_KEY_[A-Z0-9_]{1,128}$/.test(credentialRef)) { setCredentialStatus("请填写有效的供应商 ID 与凭据引用。"); return; }
    const secret = credentialInput.current?.value || "";
    if (credentialInput.current) credentialInput.current.value = "";
    if (operation === "set" && !secret) { setCredentialStatus("请输入凭据。"); return; }
    if (operation === "delete" && !window.confirm(`删除 ${providerId} / ${credentialRef} 的已保存凭据？`)) return;
    try {
      const result = await invoke<Row>(`credential_${operation}`, { providerId, credentialRef, ...(operation === "set" ? { secret } : {}) });
      setCredentialStatus(operation === "exists" ? (result.exists ? "凭据已保存。" : "未找到凭据。") : operation === "set" ? "凭据已保存。" : result.deleted ? "凭据已删除。" : "未找到凭据。");
    } catch (error) { setCredentialStatus(errorMessage(error)); }
  };

  const openModel = async (id: string) => {
    if (!connection) return;
    const ticket = guard.start("model");
    setActionError("");
    try {
      const result = await request<{profile: Row; updated_at: string}>(connection, `/api/models/${encodeURIComponent(id)}`);
      if (guard.accepts(ticket)) setModelEditor({ initial: result.profile, version: result.updated_at });
    } catch (error) { if (guard.accepts(ticket)) setActionError(errorMessage(error)); }
  };

  const saveModel = async (method: "POST" | "PATCH", path: string, body: unknown) => {
    if (!connection || actionBusy || writesBlocked || maintenanceSubmitting.current) return;
    const ticket = guard.start("model");
    setActionBusy(true); setActionError("");
    try {
      await request(connection, path, { method, body: JSON.stringify(body) });
      if (!guard.accepts(ticket)) return;
      setModelEditor(null);
      await refreshPanel();
    } catch (error) { if (guard.accepts(ticket)) setActionError(errorMessage(error)); }
    finally { if (guard.accepts(ticket)) setActionBusy(false); }
  };

  const maintainWorkspace = async (operation: "backup" | "restore") => {
    if (!native || writesBlocked || actionBusy || maintenanceSubmitting.current) return;
    const path = workspacePath.trim();
    if (!path || !/^(?:[a-zA-Z]:[\\/]|\\\\)/.test(path)) { setActionError("请输入 Windows 绝对目录路径。"); return; }
    const question = operation === "restore"
      ? `恢复工作空间：${path}\n恢复将替换当前工作空间，并在完成后锁定写操作，直到人工核对。继续吗？`
      : `备份工作空间到：${path}\n维护期间 Core 会暂时停止。继续吗？`;
    if (!window.confirm(question)) return;
    setActionError("");
    operationPollSerial.current += 1;
    maintenanceSubmitting.current = true;
    setWorkspaceOperation({ operationId: "", operation, status: "unknown" });
    try { setWorkspaceOperation(await invoke<WorkspaceOperation>(`workspace_${operation}`, { path })); }
    catch { setActionError("无法确认维护请求是否已启动。请等待状态查询，结果未知时不要重试。"); }
    finally { maintenanceSubmitting.current = false; }
  };

  const acknowledgeUnknown = async () => {
    if (!native || workspaceOperation?.status !== "unknown" || !workspaceOperation.operationId || !reviewReason.trim() || actionBusy) return;
    if (!window.confirm(`归档未知维护结果 ${workspaceOperation.operationId}？\n请先核对工作区和备份。此操作不表示维护成功，也不会解除恢复锁定或预算限制。`)) return;
    operationPollSerial.current += 1; maintenanceSubmitting.current = true; setActionBusy(true); setActionError("");
    try { setWorkspaceOperation(await invoke<WorkspaceOperation>("workspace_operation_acknowledge", { operationId: workspaceOperation.operationId, reason: reviewReason.trim() })); setReviewReason(""); }
    catch (error) { setActionError(errorMessage(error)); }
    finally { maintenanceSubmitting.current = false; setActionBusy(false); }
  };

  const retryRuntime = async () => {
    if (!native || writesBlocked || actionBusy || !window.confirm("重启本地 Core？正在运行的任务可能中断。")) return;
    setActionBusy(true); setActionError("");
    try { await invoke("runtime_retry"); await syncNative(); }
    catch (error) { setActionError(errorMessage(error)); }
    finally { setActionBusy(false); }
  };

  const plan = visiblePlan.value as Row | null;
  const panelItems = (view === "memory" || view === "approvals") && visiblePanel.value && typeof visiblePanel.value === "object" && !Array.isArray(visiblePanel.value) ? rows((visiblePanel.value as Row).items) :
    view === "activity" && visiblePanel.value && typeof visiblePanel.value === "object" && !Array.isArray(visiblePanel.value) ? rows((visiblePanel.value as Row).items ?? (visiblePanel.value as Row).events) : rows(visiblePanel.value);
  const title = NAV.find((item) => item.id === view)?.label || "对话";

  return <div className={view === "chat" ? "app-shell inspector-closed" : "app-shell"}>
    <aside className="rail" aria-label="主导航">
      <div className="brand"><span className="brand-mark">F</span><span><strong>FORGE</strong><small>工作空间</small></span></div>
      <nav><span className="nav-section">工作空间</span>{NAV.map((item) => <button key={item.id} title={item.label} className={view === item.id ? "nav-item active" : "nav-item"} aria-current={view === item.id ? "page" : undefined} onClick={() => { setView(item.id); setActionError(""); }}><Icon name={item.id} /><span>{item.label}</span></button>)}</nav>
      <div className="rail-status"><span className={connection ? "status-dot on" : "status-dot"} />{connection ? "Core 已连接" : "Core 未连接"}<small>{connection?.workspace_id || (native ? "桌面模式" : "浏览器开发模式")}</small></div>
    </aside>
    <main className="conversation-shell">
      <header className="topbar"><div className="topbar-title"><Icon name="chat" /><h1>长期对话</h1></div><div className="connection-indicator"><span className={connection ? "status-dot on" : "status-dot"} />{connection ? "Core 已连接" : "Core 未连接"}</div></header>
      {!connection && <div className="connect-banner" role="status"><div><strong>{native ? "正在连接本地 Core" : "连接开发 Core"}</strong><p>{native ? "等待桌面运行时提供安全会话。" : "输入本次 Core 启动密钥。密钥不会保存在浏览器中。"}</p>{connectionError && <p className="error">{connectionError}</p>}</div>{!native && <div className="connect-controls"><input ref={secretInput} type="password" autoComplete="off" aria-label="Core 启动密钥" placeholder="本次启动密钥" /><button className="button primary" onClick={() => void connectBrowser()}>连接</button></div>}</div>}
      {writesBlocked && <div className="maintenance-banner" role="status">{restoreFenced === true ? "恢复后写操作已锁定，请先人工核对。" : workspaceOperation?.status === "unknown" && !workspaceOperation.acknowledged ? "维护结果未知，请人工核查；写操作已暂停。" : runtimeStatus?.state === "maintenance" || workspaceOperation?.status === "running" ? "正在备份或恢复，完成后可退出；写操作已暂停。" : "正在核对工作空间恢复状态；写操作暂不可用。"}</div>}
      <ChatTimeline messages={messages} events={timelineEvents} outgoing={outgoing} quests={quests}
        approvals={pendingApprovals} native={native} connected={!!connection} historyMore={hasMore || timelineHasMore}
        historyBusy={historyBusy} timelineError={timelineError} conversationError={conversationError}
        approvalError={approvalLoadError} canRetry={!!connection && !sending && !writesBlocked} scrollRef={scrollRef}
        onScroll={() => { const element = scrollRef.current; if (!element || manualPause.current) return; followBottom.current = isNearBottom(element.scrollTop, element.clientHeight, element.scrollHeight); if (followBottom.current) setHasNew(false); }}
        onInspect={() => { manualPause.current = true; followBottom.current = false; setReadingPaused(true); }}
        onLoadOlder={() => void loadOlder()} onRetry={(item) => void sendPending(item, false)}
        onQuest={(id) => { setSelectedId(id); setView("quests"); }}
        onApproval={(id) => { setSelectedId(id); setView("approvals"); }} />
      {(hasNew || readingPaused) && <button className="new-messages" onClick={() => { manualPause.current = false; followBottom.current = true; setReadingPaused(false); setHasNew(false); if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }}>{hasNew ? "有新消息 · 回到最新 ↓" : "回到最新 ↓"}</button>}
      <footer className="composer-wrap"><div className="composer"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (shouldSubmitOnEnter({ key: event.key, shiftKey: event.shiftKey, isComposing: event.nativeEvent.isComposing, keyCode: event.nativeEvent.keyCode })) { event.preventDefault(); void send(); } }} placeholder="输入消息，或接着之前的任务…" aria-label="消息内容" /><div className="composer-bottom"><span>{connection ? "Core 已连接" : "等待 Core 连接"}</span><span>Enter 发送 · Shift + Enter 换行</span><button className="send-button" aria-label="发送消息" disabled={!connection || sending || writesBlocked || !draft.trim()} onClick={() => void send()}>↑</button></div></div>{sendError && <p className="send-error" role="alert">{sendError} 草稿已保留。请求 ID：<code>{pending?.requestId}</code></p>}</footer>
    </main>
    {view !== "chat" && <aside className="inspector" aria-label={title}>
      <header className="inspector-header"><div><span className="eyebrow">WORKSPACE</span><h2>{title}</h2></div><button className="icon-button" aria-label="刷新当前视图" onClick={() => { void refreshConversation(); void refreshPanel(); }}>↻</button></header>
      <div className="inspector-body">
        {view === "quests" && <><div className="section-heading"><span>后台任务</span><span>{quests.length}{questsBefore ? "+" : ""}</span></div>{questLoadError && <p className="notice error" role="alert">任务列表加载失败：{questLoadError}</p>}{!quests.length ? <p className="notice">{connection ? "暂无后台任务" : "连接 Core 后显示任务"}</p> : <div className="list">{quests.map((quest) => <button className={selectedId === quest.id ? "list-row selected" : "list-row"} key={quest.id} onClick={() => { setSelectedId(quest.id); setView("quests"); }}><span><strong>{quest.title}</strong><small>{quest.id}</small></span><span className="state-label">{quest.status}</span></button>)}</div>}{questsBefore && <button className="text-button more" onClick={() => void loadMoreQuests()}>加载更多任务</button>}{view === "quests" && selected && <><div className="detail"><span className="eyebrow">SELECTED QUEST</span><h3>{selected.title}</h3><p>{selected.goal}</p><dl><dt>状态</dt><dd>{selected.status}</dd><dt>版本</dt><dd>{selected.version}</dd><dt>任务 ID</dt><dd className="mono">{selected.id}</dd></dl></div><div className="section-heading"><span>计划</span></div><StateNotice state={visiblePlan} empty="暂无计划" />{plan && <div className="detail"><dl><dt>状态</dt><dd>{text(plan.status)}</dd><dt>计划版本</dt><dd>{text(plan.plan_revision)}</dd><dt>摘要</dt><dd className="mono break">{text(plan.digest)}</dd><dt>总 token 预算</dt><dd>{text(plan.max_total_tokens)}</dd><dt>费用上限</dt><dd>{text(plan.max_cost_microunits)}</dd></dl>{rows(plan.nodes).map((node, index) => <div className="plan-node" key={text(node.key, String(index))}><strong>{text(node.objective)}</strong><small>状态：{text(node.status)} · Token 预算：{text(node.budget_tokens)}</small><small>允许工具：{names(node.allowed_tools)}</small><small>成功标准：{names(node.success_criteria)}</small></div>)}<TechnicalDetails value={plan} />{plan.status === "proposed" && typeof plan.digest === "string" && <button className="button primary" disabled={actionBusy || writesBlocked} onClick={() => { if (window.confirm(`批准计划？\n任务版本：${selected.version}\n计划版本：${text(plan.plan_revision)}\n摘要：${text(plan.digest)}\n请先检查任务节点、权限和预算。`)) void perform(`/api/quests/${encodeURIComponent(selected.id)}/plan/approve`, { digest: plan.digest, plan_revision: plan.plan_revision, expected_version: selected.version }); }}>批准此计划</button>}</div>}</>}</>}
        {(view === "teams" || view === "approvals" || view === "activity") && <label className="field-label">任务范围<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}><option value="">{view === "activity" ? "全部任务" : "选择任务"}</option>{quests.map((quest) => <option key={quest.id} value={quest.id}>{quest.title}</option>)}</select></label>}
        {view === "memory" && <form className="search" onSubmit={(event) => { event.preventDefault(); setMemorySearch(memoryQuery); }}><input value={memoryQuery} onChange={(event) => setMemoryQuery(event.target.value)} placeholder="搜索任务、路径、决策或失败" aria-label="搜索记忆" /><button className="button" type="submit">搜索</button></form>}
        {view !== "quests" && <StateNotice state={visiblePanel} empty={view === "teams" || view === "approvals" ? "选择任务后查看" : "暂无数据"} />}
        {view === "activity" && panelItems.length > 0 && <><div className="list">{panelItems.map((item, index) => <div className="data-row" key={text(item.run_id, String(index))}><strong>{text(item.role, "Worker")} · {text(item.state)}</strong><small>{text(item.created_at, "时间未知")}</small><dl><dt>任务</dt><dd className="break">{text(item.quest_id)}</dd><dt>模型</dt><dd>{text(item.provider_id)} / {text(item.model_id)}</dd><dt>已执行步数</dt><dd>{text(item.steps)}</dd><dt>工具调用</dt><dd>{text(item.tool_spent)}</dd></dl><TechnicalDetails value={item} /></div>)}</div>{activityCursor && <button className="text-button more" disabled={visiblePanel.loading} onClick={() => void loadPanelPage()}>加载更早的活动</button>}</>}
        {view === "models" && <><div className="button-row"><button className="button" disabled={!connection || writesBlocked} onClick={() => { guard.start("model"); setModelEditor({ initial: null, version: null }); }}>添加模型</button></div>{modelEditor && <ModelEditor key={modelEditor.initial ? text(modelEditor.initial.id) : "new"} initial={modelEditor.initial} version={modelEditor.version} blocked={actionBusy || writesBlocked} save={(method, path, body) => void saveModel(method, path, body)} cancel={() => setModelEditor(null)} />}{panelItems.length > 0 && <div className="list">{panelItems.map((item, index) => <div className="data-row" key={text(item.id, String(index))}><strong>{text(item.model_id ?? item.id)}</strong><small>{text(item.provider_id ?? item.provider)} · {item.enabled === false ? "已禁用" : item.enabled === true ? "已启用" : "状态未知"}</small><dl><dt>上下文窗口</dt><dd>{text(item.context_window)}</dd><dt>最大输出</dt><dd>{text(item.max_output_tokens)}</dd><dt>能力来源</dt><dd>{item.provenance && typeof item.provenance === "object" ? text((item.provenance as Row).source) : "未知"}</dd><dt>可用能力</dt><dd>{item.capabilities && typeof item.capabilities === "object" ? Object.keys(item.capabilities as Row).join("、") || "未知" : "未知"}</dd></dl><ModelMeasurements value={item.capability_measurements} /><button className="button" disabled={writesBlocked} onClick={() => void openModel(text(item.id))}>编辑 / 启停</button><TechnicalDetails value={item} /></div>)}</div>}</>}
        {view === "teams" && panelItems.length > 0 && <div className="list">{panelItems.map((item, index) => <div className="data-row" key={text(item.id, String(index))}><strong>团队提案 · {text(item.status)}</strong><small>计划版本 {text(item.plan_revision)}</small><p className="mono break">{text(item.proposal_digest)}</p>{typeof item.body === "object" && item.body !== null && <><dl><dt>总 token 预算</dt><dd>{text(((item.body as Row).limits as Row | undefined)?.max_total_tokens)}</dd><dt>费用上限</dt><dd>{text(((item.body as Row).limits as Row | undefined)?.max_cost_microunits)}</dd></dl>{rows((item.body as Row).members).map((member, memberIndex) => <div className="plan-node" key={text(member.role_id, String(memberIndex))}><strong>{text(member.role_id)}</strong><small>模型：{text(member.provider_id)} / {text(member.model_id)}</small><small>工具权限：{names(member.tool_scope)}</small><small>Token 预算：{text(member.estimated_tokens)}</small></div>)}</>}<TechnicalDetails value={item} />{item.status === "proposed" && selected && typeof item.proposal_digest === "string" && <button className="button primary" disabled={actionBusy || writesBlocked} onClick={() => { if (window.confirm(`批准团队？\n任务版本：${selected.version}\n计划版本：${text(item.plan_revision)}\n摘要：${text(item.proposal_digest)}\n请先检查成员、权限和预算。`)) void perform(`/api/quests/${encodeURIComponent(selected.id)}/teams/approve`, { expected_version: selected.version, plan_revision: item.plan_revision, proposal_digest: item.proposal_digest }); }}>批准此团队</button>}</div>)}</div>}
        {view === "approvals" && panelItems.length > 0 && <><div className="list">{panelItems.map((item, index) => <div className="data-row" key={text(item.id, String(index))}><strong>{text(item.action_type)} · {text(item.status)}</strong><dl><dt>风险</dt><dd>{text(item.risk)}</dd><dt>范围</dt><dd className="break">{text(item.resource_scope)}</dd><dt>计划版本</dt><dd>{text(item.plan_revision)}</dd><dt>参数摘要</dt><dd className="mono break">{text(item.parameter_digest)}</dd><dt>原因</dt><dd>{text(item.reason)}</dd><dt>过期时间</dt><dd>{text(item.expires_at)}</dd></dl>{item.status === "PENDING" && <div className="button-row">{(["APPROVED", "REJECTED"] as const).map((decision) => <button key={decision} className={decision === "APPROVED" ? "button primary" : "button"} disabled={actionBusy || writesBlocked || typeof item.parameter_digest !== "string" || typeof item.plan_revision !== "number"} onClick={() => { if (window.confirm(`${decision === "APPROVED" ? "批准" : "拒绝"}此操作？\n风险：${text(item.risk)}\n范围：${text(item.resource_scope)}\n计划版本：${text(item.plan_revision)}\n参数摘要：${text(item.parameter_digest)}`)) void perform(`/api/approvals/${encodeURIComponent(text(item.id))}/decision`, { decision, expected_digest: item.parameter_digest, expected_plan_revision: item.plan_revision }); }}>{decision === "APPROVED" ? "批准" : "拒绝"}</button>)}</div>}</div>)}</div>{approvalCursor && <button className="text-button more" disabled={visiblePanel.loading} onClick={() => void loadPanelPage()}>加载更早的审批</button>}</>}
        {view === "memory" && panelItems.length > 0 && <><div className="list">{(panelItems as MemoryItem[]).map((item) => <div className="data-row" key={item.id}><strong>{item.statement}</strong><small>{text(item.layer)} · {text(item.kind)} · {text(item.status)} · {text(item.freshness, "新鲜度未知")}</small><dl><dt>来源</dt><dd className="break">{item.source_refs?.join(", ") || "未知"}</dd><dt>记录时间</dt><dd>{text(item.created_at)}</dd><dt>有效至</dt><dd>{text(item.valid_until, "未指定")}</dd><dt>可信度</dt><dd>{text(item.confidence, "未知")}</dd><dt>关联任务</dt><dd className="break">{Array.isArray(item.related_quest_ids) ? item.related_quest_ids.join(", ") || "无" : "未知"}</dd></dl>{item.conflicted === true && <p className="warning">存在冲突，请核对来源。</p>}{/^[a-f0-9]{32}$/i.test(item.id) && <button className="button" disabled={actionBusy || writesBlocked} onClick={() => { if (window.confirm(`撤销这条记忆？\n${item.statement}`)) void perform(`/api/memory/${encodeURIComponent(item.id)}/revoke`); }}>撤销记忆</button>}</div>)}</div>{memoryCursor && <button className="text-button more" disabled={visiblePanel.loading} onClick={() => void loadPanelPage()}>加载更多记忆</button>}</>}
        {view === "settings" && <><div className="section-heading"><span>运行配置与预算</span></div><p className="notice">仅显示 Core 提供的非密钥配置；未知值保持未知。</p>{runtimeStatus && <dl><dt>运行状态</dt><dd>{text(runtimeStatus.state)}</dd><dt>进程代次</dt><dd className="break">{text(runtimeStatus.generation)}</dd><dt>重启次数</dt><dd>{runtimeStatus.restartCount}</dd><dt>最近错误</dt><dd>{text(runtimeStatus.lastErrorCode, "无")}</dd></dl>}{Array.isArray(visiblePanel.value) && <><dl><dt>工作空间</dt><dd className="break">{text((visiblePanel.value[0] as Row)?.workspace_id)}</dd><dt>供应商</dt><dd>{text((visiblePanel.value[0] as Row)?.provider_id)}</dd><dt>Router 模型</dt><dd>{text((visiblePanel.value[0] as Row)?.router_model_id)}</dd><dt>上下文上限</dt><dd>{text((visiblePanel.value[0] as Row)?.context_limit)}</dd><dt>Token 上限</dt><dd>{text((visiblePanel.value[1] as Row)?.token_limit)}</dd><dt>保守计入 Token</dt><dd>{text((visiblePanel.value[1] as Row)?.charged_tokens)}</dd><dt>已确认用量</dt><dd>{text((visiblePanel.value[1] as Row)?.known_tokens)}</dd><dt>未结算预留</dt><dd>{text((visiblePanel.value[1] as Row)?.reserved_tokens)}</dd><dt>迁移期上界</dt><dd>{text((visiblePanel.value[1] as Row)?.opening_tokens)}</dd><dt>占用并发槽</dt><dd>{text((visiblePanel.value[1] as Row)?.active_reservations)}</dd><dt>并发上限</dt><dd>{text((visiblePanel.value[1] as Row)?.concurrency_limit)}</dd><dt>费用上限</dt><dd>{text((visiblePanel.value[1] as Row)?.cost_limit_microunits)}</dd><dt>货币</dt><dd>{text((visiblePanel.value[1] as Row)?.currency)}</dd></dl>{rows((visiblePanel.value[1] as Row)?.recent_unsettled).length > 0 && <><div className="section-heading spaced"><span>未结算请求</span></div>{rows((visiblePanel.value[1] as Row)?.recent_unsettled).map((attempt, index) => <div className="plan-node" key={String(index)}><strong>{text(attempt.code)} · {text(attempt.purpose)}</strong><small>输入预留 {text(attempt.input_reserved)} · 输出预留 {text(attempt.output_reserved)}</small><small>隔离至：{timeText(attempt.recover_after)}</small></div>)}</>}<TechnicalDetails value={visiblePanel.value} /></>}{restoreFenced === true && <div className="restore-warning"><strong>恢复待复核，后台与写入已暂停</strong><p>{restoreStatus?.review_available === true ? "请先核对当前工作区与回滚目录，退出应用后按 README 的离线维护步骤提交复核；此界面不会自动解除锁定。" : "复核记录不可用或损坏，请人工检查；不要尝试自动解锁。"}</p><dl><dt>清单摘要</dt><dd className="break">{text(restoreStatus?.manifest_sha256)}</dd><dt>回滚目录</dt><dd className="break">{text(restoreStatus?.rollback_directory)}</dd></dl></div>}{native && <><div className="section-heading spaced"><span>工作空间维护</span></div><p className="notice">备份和恢复会暂时停止 Core；恢复成功后写操作保持锁定，需人工核对。</p><label className="field-label">备份目录的绝对路径<input value={workspacePath} onChange={(event) => setWorkspacePath(event.target.value)} placeholder="例如 D:/Backup/FORGE" /></label><div className="button-row"><button className="button" disabled={writesBlocked || actionBusy || !workspacePath.trim()} onClick={() => void maintainWorkspace("backup")}>备份</button><button className="button" disabled={writesBlocked || actionBusy || !workspacePath.trim()} onClick={() => void maintainWorkspace("restore")}>恢复</button><button className="button" disabled={writesBlocked || actionBusy} onClick={() => void retryRuntime()}>重启 Core</button></div>{workspaceOperation && <p className={workspaceOperation.status === "unknown" || workspaceOperation.result?.ok === false ? "notice error" : "notice"} role="status">{workspaceOperation.status === "running" ? "正在备份或恢复，完成后可退出。" : workspaceOperation.status === "unknown" ? (workspaceOperation.acknowledged ? "未知结果已归档；这不表示操作成功，也不会解除恢复锁定。" : "维护结果未知，请人工核查；不要重复发起。") : workspaceOperation.result?.ok ? (workspaceOperation.operation === "restore" ? "恢复完成；写操作已锁定，请先人工核对。" : "备份完成。") : `维护未完成：${text(workspaceOperation.result?.code)}`}{workspaceOperation.operationId && <> 操作 ID：<code>{workspaceOperation.operationId}</code></>}</p>}{workspaceOperation?.status === "unknown" && !workspaceOperation.acknowledged && workspaceOperation.operationId && <><label className="field-label">人工核查说明<input value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} /></label><button className="button" disabled={actionBusy || !reviewReason.trim() || runtimeStatus?.state !== "ready"} onClick={() => void acknowledgeUnknown()}>归档未知结果</button><p className="notice">仅归档，不表示成功，不解除恢复锁或预算限制。</p></>}</>}{Array.isArray(visiblePanel.value) && <OperatorSettings key={text((visiblePanel.value[0] as Row)?.revision, "0")} settings={visiblePanel.value[0] as Row} budget={visiblePanel.value[1] as Row} blocked={writesBlocked || actionBusy} save={(value) => void perform("/api/settings", value)} />}<div className="section-heading spaced"><span>供应商凭据</span></div>{native ? <div className="credential-form"><label className="field-label">供应商 ID<input value={providerId} onChange={(event) => setProviderId(event.target.value)} autoComplete="off" /></label><label className="field-label">凭据引用<input value={credentialRef} onChange={(event) => setCredentialRef(event.target.value)} autoComplete="off" /></label><label className="field-label">新凭据<input ref={credentialInput} type="password" autoComplete="new-password" /></label><div className="button-row"><button className="button primary" disabled={writesBlocked} onClick={() => void credential("set")}>保存</button><button className="button" onClick={() => void credential("exists")}>检查是否存在</button><button className="button" disabled={writesBlocked} onClick={() => void credential("delete")}>删除</button></div>{credentialStatus && <p className="notice" role="status">{credentialStatus}</p>}</div> : <p className="notice">浏览器开发模式不提供系统凭据库。请在桌面应用中管理凭据。</p>}</>}
        {(view === "quests" || view === "approvals") && selected && <GrantPanel quest={selected} state={visibleGrants} workspace={grantContext === `${questContextKey}|${view}` ? workspaceRoot : ""} scope={grantScope} setScope={setGrantScope} blocked={actionBusy || writesBlocked} submit={(path, body) => void perform(path, body)} />}
        {actionError && <p className="notice error" role="alert">{actionError}</p>}
      </div>
    </aside>}
  </div>;
}
