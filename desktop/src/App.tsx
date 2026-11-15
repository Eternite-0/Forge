import { invoke, isTauri } from "@tauri-apps/api/core";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import ChatTimeline from "./ChatTimeline";
import WorkerActivity, { useWorkerActivity } from "./ConversationRuntime";
import SettingsView, { SettingsRow, SettingsSection, type SettingId } from "./SettingsView";
import DesktopNavigation from "./DesktopNavigation";
import ConversationToolbar from "./ConversationToolbar";
import ConversationComposer from "./ConversationComposer";
import BudgetOverview from "./BudgetOverview";
import QuestWorkspace from "./QuestWorkspace";
import ModelProfileSummary from "./ModelProfileSummary";
import OperatorSettings from "./OperatorSettings";
import ProviderCredentials from "./ProviderCredentials";
import FilePreviewWorkspace, { PreviewDivider, type PreviewRequest } from "./FilePreviewWorkspace";
import usePreviewLayout, { defaultPreviewWidth, previewWidthBounds, previewWidthFromRatio, previewWidthRatio } from "./usePreviewLayout";
import type { ArtifactDiff } from "./artifactHistory";
import { nativeFileSource, relativeFilePath } from "./workspaceFiles";
import { statusLabel, riskLabel, toolLabel } from "./presentation";
import { ApiError, browserConnection, request, type Connection } from "./api";
import { appendUnique, errorMessage, isNearBottom, pendingMessage, preservedScrollTop, RequestGuard, retainIfEqual, shouldSubmitOnEnter, type PendingMessage } from "./logic";
import { messageMetadata, type ChatMessage as Message, type Outgoing, type TimelineEvent } from "./timeline";

type View = "chat" | "quests" | "activity" | "models" | "teams" | "memory" | "approvals" | "settings";
type Row = Record<string, unknown>;
interface TimelinePage { items: TimelineEvent[]; next_before?: string | null; has_more?: boolean }
interface Page { messages: Message[]; router?: Row; next_before?: string | null; has_more?: boolean }
interface NativeConnection { state: string; baseUrl: string; bearer: string; workspaceId: string; generation: string }
interface NativeStatus { state: string; generation: string; restartCount: number; lastErrorCode: string | null }
interface WorkspaceOperation { operationId: string; operation: "backup" | "restore"; status: "running" | "completed" | "unknown"; acknowledged?: boolean; result?: { ok: boolean; operation: string; code?: string } }
interface Quest extends Row { id: string; title: string; status: string; version: number; goal: string }
interface QuestPage { items: Quest[]; next_before?: string | null; has_more?: boolean }
interface MemoryItem extends Row { id: string; statement: string; source_refs: string[] }
interface DataState { loading: boolean; error: string; value: unknown }

const VIEW_NAMES: Record<View, string> = { chat: "对话", quests: "后台任务", activity: "运行活动", models: "模型库", teams: "任务团队", memory: "记忆中心", approvals: "审批中心", settings: "设置" };
const SETTING_VIEWS: Partial<Record<SettingId, View>> = { models: "models" };
const TASK_VIEWS: View[] = ["quests", "teams", "activity", "approvals"];
const EMPTY: DataState = { loading: false, error: "", value: null };
const text = (value: unknown, fallback = "未知") => value == null || value === "" ? fallback : String(value);
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((x): x is Row => !!x && typeof x === "object") : [];
const asRecord = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const display = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const eventKey = (event: TimelineEvent) => `${event.kind}:${event.event_id}`;
const mergeEvents = (current: TimelineEvent[], next: TimelineEvent[]) => retainIfEqual(current, [...new Map([...current, ...next].map((event) => [eventKey(event), event])).values()]);


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

function WorkspaceFrame({ settingsOpen, setting, onSetting, onClose, title, children }: {
  settingsOpen: boolean; setting: SettingId; onSetting: (id: SettingId) => void; onClose: () => void; title: string; children: React.ReactNode;
}) {
  return settingsOpen ? <SettingsView selected={setting} onSelect={onSetting} onClose={onClose}><aside className="settings-page" aria-label={title}>{children}</aside></SettingsView>
    : <aside className="workspace-panel" aria-label={title}>{children}</aside>;
}

function SettingInfo({ setting }: { setting: SettingId }) {
  const information: Partial<Record<SettingId, [string, string][]>> = {
    shortcuts: [["打开设置", "Ctrl + ,"], ["发送消息", "Enter"], ["换行", "Shift + Enter"]],
  };
  return <>{information[setting]?.map(([label, value]) => <SettingsRow key={label} label={label}><span>{value}</span></SettingsRow>)}</>;
}

function CoreOverview({ settings, connected }: { settings: Row; connected: boolean }) {
  return <div className="general-settings">
    <section><h2 className="settings-block-heading">工作空间与连接</h2><div className="settings-group">
      <SettingsRow label="工作空间" description="当前长期对话与任务所在的工作空间。">{text(settings.workspace_root ?? settings.workspace_id)}</SettingsRow>
      <SettingsRow label="运行状态" description="连接状态不代表供应商、模型、权限和预算均已就绪。">{connected ? "Core 已连接" : "Core 未连接"}</SettingsRow>
    </div></section>
    <section><h2 className="settings-block-heading">模型与运行</h2><div className="settings-group">
      <SettingsRow label="供应商" description="Core 当前使用的模型供应商。">{text(settings.provider_id)}</SettingsRow>
      <SettingsRow label="Router 模型" description="长期对话与任务协调使用的模型。">{text(settings.router_model_id)}</SettingsRow>
      <SettingsRow label="上下文上限" description="当前 Router 的上下文预算。">{text(settings.context_limit)}</SettingsRow>
    </div></section>
  </div>;
}

function names(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.map((item) => toolLabel(item)).join("、") : "无";
  return text(value);
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
function ModelEditor({ initial, version, blocked, save, cancel, defaultProvider = "" }: { initial: Row | null; version: string | null; blocked: boolean; save: (method: "POST" | "PATCH", path: string, body: unknown) => void; cancel: () => void; defaultProvider?: string }) {
  const support = (value: unknown) => value && typeof value === "object" && "supported" in value ? ((value as Row).supported === true ? "yes" : "no") : "unknown";
  const [id, setId] = useState(() => text(initial?.id, `model_${crypto.randomUUID().replace(/-/g, "")}`));
  const [provider, setProvider] = useState(text(initial?.provider, defaultProvider));
  const [modelId, setModelId] = useState(text(initial?.model_id, ""));
  const [label, setLabel] = useState(text(initial?.display_name, ""));
  const [context, setContext] = useState(text(initial?.context_window, ""));
  const [output, setOutput] = useState(text(initial?.max_output_tokens, ""));
  const [tools, setTools] = useState(support(initial?.tool_calling_support));
  const [schema, setSchema] = useState(support(initial?.structured_output_support));
  const [enabled, setEnabled] = useState(initial?.enabled !== false);
  const [scores, setScores] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [options, setOptions] = useState(false);
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
    } catch (problem) { setOptions(true); setError(errorMessage(problem)); }
  };
  return <form className="model-form" onSubmit={submit} onInvalidCapture={(event) => { const details = event.currentTarget.querySelector<HTMLDetailsElement>(".model-options"); if (details) details.open = true; setOptions(true); }}><div className="section-heading"><span>{initial ? "编辑模型档案" : "添加模型档案"}</span></div>
    <label className="field-label">显示名称<input value={label} onChange={(event) => setLabel(event.target.value)} /></label>
    <label className="field-label">模型 ID<input value={modelId} disabled={!!initial} placeholder="填写服务提供的模型名称" onChange={(event) => setModelId(event.target.value)} /></label>
    <label className="field-label">供应商 ID<input value={provider} disabled={!!initial} onChange={(event) => setProvider(event.target.value)} /></label>
    <label className="check-label"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />启用模型</label>
    <details className="settings-disclosure model-options" open={options} onToggle={(event) => setOptions(event.currentTarget.open)}>
      <summary>模型选项</summary><div className="model-options-content">
    <p className="notice">档案标识已自动生成。能力和上下文按实际资料填写，留空或未知不会自动视为支持。</p>
    <label className="field-label">档案 ID<input value={id} disabled={!!initial} onChange={(event) => setId(event.target.value)} /></label>
    <label className="field-label">上下文窗口（未知可留空）<input type="number" min="1" value={context} onChange={(event) => setContext(event.target.value)} /></label>
    <label className="field-label">最大输出 Token（未知可留空）<input type="number" min="1" value={output} onChange={(event) => setOutput(event.target.value)} /></label>
    <label className="field-label">工具调用支持<select value={tools} onChange={(event) => setTools(event.target.value)}><option value="unknown">未知</option><option value="yes">支持</option><option value="no">不支持</option></select></label>
    <label className="field-label">结构化输出支持<select value={schema} onChange={(event) => setSchema(event.target.value)}><option value="unknown">未知</option><option value="yes">支持</option><option value="no">不支持</option></select></label>
    {!initial && <details><summary>可选：手动能力评分</summary><p className="notice">留空表示未知；输入的 0–100 分仅为用户覆盖，未采样（0），并非基准测评。</p>{MODEL_CAPABILITIES.map(([name, title]) => <label className="field-label" key={name}>{title}<input type="number" min="0" max="100" step="any" value={scores[name] || ""} onChange={(event) => setScores((old) => ({ ...old, [name]: event.target.value }))} /></label>)}</details>}
    {initial && <p className="notice">现有能力测量及来源保持不变；协议支持修改标记为用户覆盖。</p>}
    </div></details>
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
  const connectionKey = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "";
  const [coreSnapshot, setCoreSnapshot] = useState<{ key: string; settings: Row; router: Row }>({ key: "", settings: {}, router: {} });
  const coreInfo = coreSnapshot.key === connectionKey ? coreSnapshot.settings : {};
  const routerInfo = coreSnapshot.key === connectionKey ? coreSnapshot.router : {};
  const workerActivity = useWorkerActivity(connection);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewExpanded, setPreviewExpanded] = useState(false);
  const previewResizeRestore = useRef<"tabs" | "closed" | null>(null);
  const previewResizeBreakpoints = useRef({ below960: false, below720: false, shellBelow720: false });
  const previewResizeState = useRef({ open: false, sidebarOpen: true });
  previewResizeState.current = { open: previewOpen, sidebarOpen: view === "chat" && !sidebarCollapsed && !(previewOpen && previewExpanded) };
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth);
  const [previewPaneSize, setPreviewPaneSize] = useState({ width: window.innerWidth - 344, height: window.innerHeight - 36 });
  const [preferredPreviewRatio, setPreferredPreviewRatio] = useState<number | null>(null);
  const initialPreviewWidth = defaultPreviewWidth(previewPaneSize.width, previewPaneSize.height);
  const previewBounds = previewWidthBounds(previewPaneSize.width);
  const previewWidthPixels = previewWidthFromRatio(preferredPreviewRatio ?? previewWidthRatio(initialPreviewWidth, previewPaneSize.width), previewPaneSize.width);
  const previewWidth = 100 * previewWidthPixels / Math.max(1, previewPaneSize.width);
  useEffect(() => {
    const resize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const [previewRequest, setPreviewRequest] = useState<PreviewRequest | null>(null);
  const [workspaceChanges, setWorkspaceChanges] = useState<number>();
  const [historyStats, setHistoryStats] = useState<{ identity: string; files: Record<string, { added: number; removed: number }> }>({ identity: "", files: {} });
  const historyIdentity = useRef(connectionKey); historyIdentity.current = connectionKey;
  const fileSource = useMemo(() => native ? nativeFileSource() : null, [native, connectionKey]);
  const previewSerial = useRef(0);
  const openPreview = (mode: PreviewRequest["mode"], path?: string, paths?: string[], receiptId?: string) => {
    captureReadingPosition();
    previewResizeRestore.current = null;
    setPreviewOpen(true); setPreviewRequest({ id: ++previewSerial.current, mode, path, paths, receiptId });
  };
  const closePreview = () => { captureReadingPosition(); previewResizeRestore.current = null; setPreviewOpen(false); setWorkspaceChanges(undefined); requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('[aria-label="查看工作空间文件"]')?.focus()); };
  const resizePreview = (width: number) => {
    captureReadingPosition();
    if (width < 160) { closePreview(); return; }
    if (width > previewBounds.maximum && previewPaneSize.width - width < 160) { setPreviewExpanded(true); return; }
    setPreferredPreviewRatio(previewWidthRatio(width, previewPaneSize.width));
  };
  const recordHistoryStats = (record: ArtifactDiff) => {
    if (historyIdentity.current !== connectionKey) return;
    setHistoryStats((old) => ({ identity: connectionKey, files: { ...(old.identity === connectionKey ? old.files : {}), [record.receipt_id]: { added: record.added, removed: record.removed } } }));
  };
  const [refreshing, setRefreshing] = useState(false);
  const [setting, setSetting] = useState<SettingId>("general");
  const openSettings = useCallback(() => {
    setSetting("general"); setSettingsOpen(true); setView("settings");
    requestAnimationFrame(() => document.querySelector<HTMLInputElement>('.settings-search input')?.focus());
  }, []);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === ",") { event.preventDefault(); openSettings(); }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [openSettings]);
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
  const [planRefresh, setPlanRefresh] = useState(0);
  const [planContext, setPlanContext] = useState("");
  const [grants, setGrants] = useState<DataState>(EMPTY);
  const [grantContext, setGrantContext] = useState("");
  const [grantScope, setGrantScope] = useState("");
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [actionNotice, setActionNotice] = useState("");
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
  const savedCredentialProvider = asRecord(asRecord(coreInfo.config).provider);
  const savedProviderId = text(savedCredentialProvider.id, "");
  const savedCredentialRef = text(savedCredentialProvider.credential_ref, "");
  useEffect(() => {
    setProviderId(savedProviderId);
    setCredentialRef(savedCredentialRef);
    setCredentialStatus("");
    if (credentialInput.current) credentialInput.current.value = "";
  }, [connectionKey, savedProviderId, savedCredentialRef]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const composerResizeFrame = useRef(0);
  const refreshSerial = useRef(0);
  const followBottom = useRef(true);
  const manualPause = useRef(false);
  const historyAnchor = useRef<{ top: number; height: number } | null>(null);
  const readingAnchor = useRef<{ key: string; offset: number; top: number } | null>(null);
  function captureReadingPosition() {
    const element = scrollRef.current;
    if (!element?.getClientRects().length || followBottom.current) return;
    const viewport = element.getBoundingClientRect();
    const candidates = Array.from(element.querySelectorAll<HTMLElement>("[data-scroll-key]"))
      .map((node) => ({ node, bounds: node.getBoundingClientRect() }))
      .filter(({ bounds }) => bounds.height > 0 && bounds.bottom > viewport.top && bounds.top < viewport.bottom);
    const preferred = candidates.filter(({ node }) => !/^group(?:-files)?:/.test(node.dataset.scrollKey || ""));
    const anchor = (preferred.length ? preferred : candidates).sort((a, b) => Math.abs(a.bounds.top - viewport.top) - Math.abs(b.bounds.top - viewport.top))[0];
    if (anchor) readingAnchor.current = { key: anchor.node.dataset.scrollKey!, offset: anchor.bounds.top - viewport.top, top: element.scrollTop };
  }
  function revealRequest(messageId: string) {
    const node = Array.from(scrollRef.current?.querySelectorAll<HTMLElement>("[data-scroll-key]") || []).find((item) => item.dataset.scrollKey === `message:${messageId}`);
    if (!node) return false;
    manualPause.current = true; followBottom.current = false; setReadingPaused(true); previewResizeRestore.current = null; setPreviewOpen(false);
    window.setTimeout(() => { node.scrollIntoView({ block: "center", behavior: "instant" }); node.setAttribute("tabindex", "-1"); node.focus({ preventScroll: true }); }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 180);
    return true;
  }
  const { workspaceRef, previewPresent } = usePreviewLayout({ open: previewOpen, width: previewExpanded || viewportWidth <= 720 ? 100 : previewWidth, visible: view === "chat", scrollRef, followBottom, readingAnchor, captureReadingPosition });
  useLayoutEffect(() => {
    const element = workspaceRef.current;
    if (!element) return;
    const shell = element.closest<HTMLElement>(".app-shell");
    const observer = new ResizeObserver(() => {
      if (!element.clientWidth) return;
      const height = (shell?.clientHeight || window.innerHeight) - 36;
      setPreviewPaneSize((old) => old.width === element.clientWidth && old.height === height ? old : { width: element.clientWidth, height });
      setPreferredPreviewRatio((old) => old ?? previewWidthRatio(defaultPreviewWidth(element.clientWidth, height), element.clientWidth));
      // Root.H / gbrq.Kg / gbrq.g3: breakpoint crossings, not file-open rules.
      // The native resize measurement adds the whole visible left-panel slot back.
      const rail = shell?.querySelector<HTMLElement>(".rail");
      const primaryRail = rail?.querySelector<HTMLElement>(".primary-rail");
      const sidebarWidth = rail && primaryRail && getComputedStyle(primaryRail).display !== "contents" ? rail.getBoundingClientRect().width : 0;
      const workspaceWidth = element.clientWidth + sidebarWidth;
      const next = { below960: workspaceWidth <= 960, below720: workspaceWidth <= 720, shellBelow720: (shell?.clientWidth || window.innerWidth) <= 720 };
      const previous = previewResizeBreakpoints.current;
      previewResizeBreakpoints.current = next;
      if (next.below960 === previous.below960 && next.below720 === previous.below720 && next.shellBelow720 === previous.shellBelow720) return;
      const current = previewResizeState.current;
      if ((next.below960 !== previous.below960 && current.sidebarOpen && next.below960) || (next.below720 !== previous.below720 && next.below720)) {
        // Like Kg, a second narrower crossing replaces the saved open/closed state.
        previewResizeRestore.current = current.open ? "tabs" : "closed";
        if (current.open) { captureReadingPosition(); setPreviewOpen(false); }
      } else if (!next.below720 && !(current.sidebarOpen && next.below960)) {
        const restore = previewResizeRestore.current;
        previewResizeRestore.current = null;
        if (restore === "tabs") setPreviewOpen(true);
      }
    });
    observer.observe(element);
    if (shell) observer.observe(shell);
    return () => observer.disconnect();
  }, [workspaceRef]);
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

  const resizeComposer = useCallback(() => {
    const input = composerInput.current;
    if (!input || !input.getClientRects().length) return;
    const timeline = scrollRef.current;
    const position = timeline?.scrollTop || 0;
    const previous = input.getBoundingClientRect().height;
    window.cancelAnimationFrame(composerResizeFrame.current);
    input.style.transition = "none";
    input.style.height = "0px";
    const natural = input.scrollHeight;
    const target = Math.max(40, Math.min(180, natural));
    input.style.overflowY = natural > 180 ? "auto" : "hidden";
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || Math.abs(previous - target) < .5) input.style.height = `${target}px`;
    else {
      input.style.height = `${previous}px`;
      void input.offsetHeight;
      composerResizeFrame.current = window.requestAnimationFrame(() => {
        input.style.transition = "height .15s cubic-bezier(0,0,.2,1)";
        input.style.height = `${target}px`;
      });
    }
    const footer = input.closest<HTMLElement>(".composer-wrap");
    input.closest<HTMLElement>(".conversation-shell")?.style.setProperty("--composer-bottom-offset", `${(footer?.offsetHeight || 160) + 12}px`);
    if (timeline) timeline.scrollTop = followBottom.current ? timeline.scrollHeight : position;
  }, []);
  useLayoutEffect(() => { resizeComposer(); }, [draft, view, sidebarCollapsed, resizeComposer]);
  useEffect(() => () => window.cancelAnimationFrame(composerResizeFrame.current), []);
  useEffect(() => {
    const footer = composerInput.current?.closest<HTMLElement>(".composer-wrap");
    if (!footer) return;
    const observer = new ResizeObserver(() => {
      const shell = footer.closest<HTMLElement>(".conversation-shell");
      shell?.style.setProperty("--conversation-footer-height", `${footer.offsetHeight}px`);
      shell?.style.setProperty("--composer-bottom-offset", `${footer.offsetHeight + 12}px`);
      if (followBottom.current && scrollRef.current?.getClientRects().length) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    });
    observer.observe(footer);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const input = composerInput.current;
    if (!input) return;
    let width = -1;
    const observer = new ResizeObserver(([entry]) => {
      if (Math.abs(entry.contentRect.width - width) < .5) return;
      width = entry.contentRect.width;
      resizeComposer();
    });
    observer.observe(input);
    return () => observer.disconnect();
  }, [resizeComposer]);
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    let height = element.clientHeight;
    const observer = new ResizeObserver(() => {
      if (element.clientHeight === height) return;
      height = element.clientHeight;
      if (followBottom.current) element.scrollTop = element.scrollHeight;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    refreshSerial.current += 1;
    setRefreshing(false);
    setActionNotice("");
  }, [contextKey]);
  useEffect(() => {
    if (!actionNotice) return;
    const timeout = window.setTimeout(() => setActionNotice(""), 4500);
    return () => window.clearTimeout(timeout);
  }, [actionNotice]);

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
        seenMessages.current.clear(); seenEvents.current.clear(); seenInitialized.current = false; followBottom.current = true; manualPause.current = false; readingAnchor.current = null; setReadingPaused(false);
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
      const timelineElement = scrollRef.current;
      if (timelineElement?.getClientRects().length && !followBottom.current) {
        const viewport = timelineElement.getBoundingClientRect();
        const candidates = Array.from(timelineElement.querySelectorAll<HTMLElement>("[data-scroll-key]"))
          .map((element) => ({ element, bounds: element.getBoundingClientRect() }))
          .filter(({ bounds }) => bounds.height > 0 && bounds.bottom > viewport.top && bounds.top < viewport.bottom);
        const preferred = candidates.filter(({ element }) => !/^group(?:-files)?:/.test(element.dataset.scrollKey || ""));
        const anchor = (preferred.length ? preferred : candidates).sort((left, right) => Math.abs(left.bounds.top - viewport.top) - Math.abs(right.bounds.top - viewport.top))[0];
        readingAnchor.current = anchor ? { key: anchor.element.dataset.scrollKey!, offset: anchor.bounds.top - viewport.top, top: timelineElement.scrollTop } : null;
      }
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
      setCoreSnapshot({ key: `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}`, settings: settings || {}, router: page.router || {} });
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
    if (view !== "chat") return;
    const element = scrollRef.current;
    if (!element) return;
    if (historyAnchor.current) {
      element.scrollTop = preservedScrollTop(historyAnchor.current.top, historyAnchor.current.height, element.scrollHeight);
      historyAnchor.current = null;
      readingAnchor.current = null;
    } else if (readingAnchor.current && !followBottom.current) {
      const anchor = readingAnchor.current;
      readingAnchor.current = null;
      const node = Array.from(element.querySelectorAll<HTMLElement>("[data-scroll-key]")).find((candidate) => candidate.dataset.scrollKey === anchor.key);
      element.scrollTop = node ? element.scrollTop + node.getBoundingClientRect().top - element.getBoundingClientRect().top - anchor.offset : anchor.top;
    } else if (followBottom.current) {
      readingAnchor.current = null;
      element.scrollTop = element.scrollHeight;
    }
  }, [messages, timelineEvents, outgoing, view, coreSnapshot, previewOpen, previewWidth]);

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
      setQuestLoadError("");
    } catch (error) { if (guard.accepts(ticket)) setQuestLoadError(errorMessage(error)); }
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
    setQuestPlan((previous) => ({ loading: true, error: "", value: planContext === questContextKey ? previous.value : null }));
    request<Row>(connection, `/api/quests/${encodeURIComponent(selectedId)}/plan`)
      .then((value) => { if (active) setQuestPlan({ loading: false, error: "", value }); })
      .catch((error) => { if (active) setQuestPlan({ loading: false, error: error instanceof ApiError && error.status === 404 ? "" : errorMessage(error), value: null }); });
    return () => { active = false; };
  }, [connection, selectedId, view, questContextKey, selected?.version, selected?.plan_revision, planRefresh]);

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
  const refreshCurrentView = async () => {
    if (!connection || refreshing) return;
    const serial = ++refreshSerial.current;
    setRefreshing(true);
    setPlanRefresh((value) => value + 1);
    try { await Promise.all([refreshConversation(), refreshPanel(), refreshGrants()]); }
    finally { if (refreshSerial.current === serial) setRefreshing(false); }
  };
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
      seenMessages.current.clear(); seenEvents.current.clear(); seenInitialized.current = false; followBottom.current = true; manualPause.current = false; readingAnchor.current = null; setReadingPaused(false);
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
    setActionBusy(true); setActionError(""); setActionNotice("");
    try {
      await request(connection, path, { method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      if (!guard.accepts(ticket)) return;
      setActionNotice(path.includes("/decision") ? "审批决定已记录" : path.includes("/plan/approve") ? "计划批准已记录" : path.includes("/teams/approve") ? "团队批准已记录" : path.includes("/grants") ? "工作区权限已更新" : path.includes("/memory/") ? "记忆已撤销" : path === "/api/settings" ? "运行配置已保存，重启 Core 后生效" : "更改已保存");
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
  const title = VIEW_NAMES[view];
  const taskView = TASK_VIEWS.includes(view);
  const openTaskView = (destination: View, id = selectedId) => { setSettingsOpen(false); setSelectedId(id); setView(destination); setActionError(""); };
  const openConversation = () => { setSettingsOpen(false); setView("chat"); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="消息内容"]')?.focus()); };
  const currentWorkspace = connection ? text(coreInfo.workspace_root, connection.workspace_id) : "尚未连接";
  const currentWorkspaceName = currentWorkspace.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "工作空间";
  const routerModel = !connection ? "尚未连接" : coreInfo.provider_configured === false ? "未配置模型" : text(coreInfo.router_model_id, "模型信息未读取");
  const routerStatus = !connection ? "未连接 Core" : sending ? "正在回复" : coreInfo.provider_configured === false ? "供应商未配置" : "Core 已连接";
  const selectSetting = (id: SettingId) => { setSetting(id); setView(SETTING_VIEWS[id] || "settings"); setActionError(""); };
  const decideApproval = (item: Row, decision: "APPROVED" | "REJECTED") => {
    if (window.confirm(`${decision === "APPROVED" ? "批准" : "拒绝"}此操作？\n风险：${text(item.risk)}\n范围：${text(item.resource_scope)}\n计划版本：${text(item.plan_revision)}\n参数摘要：${text(item.parameter_digest)}`)) void perform(`/api/approvals/${encodeURIComponent(text(item.id))}/decision`, { decision, expected_digest: item.parameter_digest, expected_plan_revision: item.plan_revision });
  };
  const grantPanel = (view === "quests" || view === "approvals") && selected && <GrantPanel quest={selected} state={visibleGrants} workspace={grantContext === `${questContextKey}|${view}` ? workspaceRoot : ""} scope={grantScope} setScope={setGrantScope} blocked={actionBusy || writesBlocked} submit={(path, body) => void perform(path, body)} />;
  const compactNavigation = view !== "chat" || sidebarCollapsed || previewOpen && previewExpanded;
  const home = !messages.length && !timelineEvents.length && !outgoing.length;
  const conversationFiles = [...new Set(timelineEvents.flatMap((event) => {
    const detail = event.detail && typeof event.detail === "object" ? event.detail as Row : {};
    if (event.kind !== "tool_receipt" || event.status !== "succeeded" || event.execution_mode !== "live" || typeof detail.exit_code === "number" && detail.exit_code !== 0 || !["file_write", "file_revise"].includes(text(detail.tool, "")) || typeof detail.path !== "string") return [];
    try { return [relativeFilePath(detail.path)]; } catch { return []; }
  }))];

  return <div className={`app-shell${compactNavigation ? " compact-navigation" : ""}${view === "chat" && !settingsOpen ? " chat-open" : ""}${settingsOpen ? " settings-open" : ""}${taskView && !settingsOpen ? " tasks-open" : ""}${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
    <header className="desktop-chrome">
      <div className="desktop-location"><button type="button" className="sidebar-toggle" hidden={settingsOpen || taskView || previewOpen && previewExpanded} aria-label={sidebarCollapsed ? "展开工作空间侧栏" : "收起工作空间侧栏"} aria-expanded={!sidebarCollapsed} title={sidebarCollapsed ? "展开工作空间侧栏" : "收起工作空间侧栏"} onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16" /></svg></button><strong>FORGE</strong></div>
      <div className="connection-indicator"><span className={connection ? "status-dot on" : "status-dot"} />{connection ? "Core 已连接" : "Core 未连接"}</div>
    </header>
    <DesktopNavigation compact={compactNavigation} activeView={settingsOpen ? null : taskView ? "quests" : view === "memory" ? "memory" : "chat"}
      connected={!!connection} workspaceId={connection?.workspace_id || ""} workspaceRoot={connection ? text(coreInfo.workspace_root, "") : ""}
      onNavigate={(destination) => { setSettingsOpen(false); setView(destination); setActionError(""); }} onSettings={openSettings}>
        {!connection && <div className="connect-banner" role="status"><div><strong>{native ? "正在连接本地 Core" : "连接开发 Core"}</strong><p>{native ? "等待桌面运行时提供安全会话。" : "输入本次 Core 启动密钥。密钥不会保存在浏览器中。"}</p>{connectionError && <p className="error">{connectionError}</p>}</div>{!native && <div className="connect-controls"><input ref={secretInput} type="password" autoComplete="off" aria-label="Core 启动密钥" placeholder="本次启动密钥" /><button className="button primary" onClick={() => void connectBrowser()}>连接</button></div>}</div>}
    </DesktopNavigation>
    <div ref={workspaceRef} className={`chat-workspace${previewOpen ? " preview-open" : ""}${previewExpanded && previewOpen ? " preview-expanded" : ""}`} hidden={view !== "chat"}>
    <main className={`conversation-shell${home ? " home-state" : ""}`}>
      <ConversationToolbar home={home && !previewOpen} visible={view === "chat"} status={routerStatus} onOpenFiles={() => openPreview("files")} onOpenChanges={() => openPreview("changes")} changesCount={workspaceChanges} conversationFiles={conversationFiles.length} onOpenConversationFiles={() => openPreview("conversation", undefined, conversationFiles)} sessionTitle={routerInfo.router_session_id ? `Router 会话：${text(routerInfo.router_session_id)}` : undefined}>
        <WorkerActivity state={workerActivity} quests={quests} onQuest={(id) => openTaskView("quests", id)} onActivity={() => openTaskView("activity", "")} />
      </ConversationToolbar>
      {writesBlocked && <div className="maintenance-banner" role="status">{restoreFenced === true ? "恢复后写操作已锁定，请先人工核对。" : workspaceOperation?.status === "unknown" && !workspaceOperation.acknowledged ? "维护结果未知，请人工核查；写操作已暂停。" : runtimeStatus?.state === "maintenance" || workspaceOperation?.status === "running" ? "正在备份或恢复，完成后可退出；写操作已暂停。" : "正在核对工作空间恢复状态；写操作暂不可用。"}</div>}
      <ChatTimeline messages={messages} events={timelineEvents} outgoing={outgoing} quests={quests} workerRuns={workerActivity.items} connection={connection} onHistoryLoaded={recordHistoryStats}
        approvals={pendingApprovals} native={native} connected={!!connection} historyMore={hasMore || timelineHasMore}
        historyBusy={historyBusy} timelineError={timelineError} conversationError={conversationError}
        approvalError={approvalLoadError} canRetry={!!connection && !sending && !writesBlocked} scrollRef={scrollRef}
        onScroll={() => { const element = scrollRef.current; if (!element || manualPause.current) return; followBottom.current = isNearBottom(element.scrollTop, element.clientHeight, element.scrollHeight); if (followBottom.current) setHasNew(false); }}
        onInspect={() => { manualPause.current = true; followBottom.current = false; setReadingPaused(true); }}
        onLoadOlder={() => void loadOlder()} onRetry={(item) => void sendPending(item, false)}
        onQuest={(id) => openTaskView("quests", id)}
        onOpenFile={(path) => openPreview("files", path)}
        onOpenHistory={(receiptId, path) => openPreview("history", path, undefined, receiptId)} historyStats={historyStats.identity === connectionKey ? historyStats.files : {}}
        onApproval={(id) => openTaskView("approvals", id)}
        approvalsBlocked={actionBusy || writesBlocked} onApprovalDecision={decideApproval} />
      {actionError && <p className="notice error action-error" role="alert">{actionError}</p>}
      {(hasNew || readingPaused) && <button className="new-messages" onClick={() => { manualPause.current = false; followBottom.current = true; setReadingPaused(false); setHasNew(false); if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }}>{hasNew ? "有新消息 · 回到最新 ↓" : "回到最新 ↓"}</button>}
      <ConversationComposer inputRef={composerInput} value={draft} onChange={setDraft}
        onKeyDown={(event) => { if (shouldSubmitOnEnter({ key: event.key, shiftKey: event.shiftKey, isComposing: event.nativeEvent.isComposing, keyCode: event.nativeEvent.keyCode })) { event.preventDefault(); void send(); } }}
        onSend={() => void send()} onConfigure={(id) => { setSettingsOpen(true); selectSetting(id); }}
        disabled={!connection || sending || writesBlocked || !draft.trim()} sending={sending} workspace={currentWorkspace} workspaceName={currentWorkspaceName}
        native={native} model={routerModel} provider={text(coreInfo.provider_id, "")} status={routerStatus} visible={view === "chat"} error={sendError} requestId={pending?.requestId} />
    </main>
    {previewPresent && <PreviewDivider width={previewWidthPixels} minimum={previewBounds.minimum} maximum={previewBounds.maximum} onResize={resizePreview} onReset={() => resizePreview(initialPreviewWidth)} />}
    <FilePreviewWorkspace source={fileSource} native={native} identity={connectionKey} connection={connection} onRequest={revealRequest} onHistoryLoaded={recordHistoryStats} workspaceName={currentWorkspaceName} open={previewOpen} present={previewPresent} visible={view === "chat"} expanded={previewExpanded} onToggleExpanded={() => { captureReadingPosition(); setPreviewExpanded((value) => !value); }} request={previewRequest} onClose={closePreview} onChanges={setWorkspaceChanges} />
    {!previewOpen && home && <button type="button" className="home-files-button" aria-label="查看工作空间文件" onClick={() => openPreview("files")}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d="M2 5h5l1.5 1.5H14v6H2V5Zm0 0V3h5l1.5 2" /></svg>文件</button>}
    </div>
    {view !== "chat" && <WorkspaceFrame settingsOpen={settingsOpen} setting={setting} onSetting={selectSetting} onClose={openConversation} title={title}>
      <header className="inspector-header"><div><h2>{title}</h2></div><button className={`icon-button refresh-button${refreshing || visiblePlan.loading ? " is-refreshing" : ""}`} aria-label="刷新当前视图" title="刷新当前视图" aria-busy={refreshing || visiblePlan.loading} disabled={!connection || refreshing || visiblePlan.loading} onClick={() => void refreshCurrentView()}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 6.7M20 4v7h-7" /></svg></button></header>
      <div className={`inspector-body${view === "quests" ? " quest-body" : ""}`}>
        {actionBusy && <p className="action-progress" role="status"><span className="loading-spinner" aria-hidden="true" />正在处理操作…</p>}
        {!settingsOpen && taskView && <><p className="workspace-description">围绕同一个任务查看计划、团队、执行与审批。进展和交付始终回到长期对话。</p><div className="workspace-tabs">{TASK_VIEWS.map((destination) => <button key={destination} className={view === destination ? "text-button active" : "text-button"} aria-current={view === destination ? "page" : undefined} onClick={() => openTaskView(destination)}>{VIEW_NAMES[destination]}</button>)}</div></>}
        {view === "quests" && <QuestWorkspace quests={quests} selected={selected || null} selectedId={selectedId}
          workspaceId={connection?.workspace_id || ""} workspaceRoot={connection ? text(coreInfo.workspace_root, "") : ""} workspaceName={currentWorkspaceName}
          connected={!!connection} error={questLoadError} hasMore={!!questsBefore}
          onSelect={(id) => { setSelectedId(id); setView("quests"); }} onLoadMore={() => void loadMoreQuests()}
          plan={plan} planNotice={<StateNotice state={visiblePlan} empty="暂无计划" />} blocked={actionBusy || writesBlocked || visiblePlan.loading}
          onApprove={() => { if (selected && plan && window.confirm(`批准计划？\n任务版本：${selected.version}\n计划版本：${text(plan.plan_revision)}\n摘要：${text(plan.digest)}\n请先检查任务节点、权限和预算。`)) void perform(`/api/quests/${encodeURIComponent(selected.id)}/plan/approve`, { digest: plan.digest, plan_revision: plan.plan_revision, expected_version: selected.version }); }}
          grants={grantPanel} />}
        {(view === "teams" || view === "approvals" || view === "activity") && <label className="field-label task-scope">任务范围<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}><option value="">{view === "activity" ? "全部任务" : "选择任务"}</option>{quests.map((quest) => <option key={quest.id} value={quest.id}>{quest.title}</option>)}</select></label>}
        {view === "teams" && <p className="notice">成员在各自的私有执行上下文中工作。批准前核对模型、工具权限与预算；团队批准绑定当前任务和计划版本。</p>}
        {view === "activity" && <p className="notice">这里显示执行成员的运行记录。执行成功或待验证均不代表任务已完成；最终结果以任务状态为准。</p>}
        {view === "approvals" && <p className="notice">按任务核对操作、风险、实际范围与参数摘要。批准仅适用于对应计划版本，工作区路径授权保留在下方。</p>}
        {view === "models" && <p className="notice">模型档案供 Router 与团队选型使用。启用档案不代表供应商连接已就绪；能力、协议与费用的未知值保持未知。</p>}
        {view === "memory" && <div className="workspace-intro"><h3>检索之前的工作</h3><p>查找任务、路径、决策与失败记录。每条记忆保留来源、有效期和冲突信息，撤销后不再参与检索。</p></div>}
        {view === "memory" && <form className="search" onSubmit={(event) => { event.preventDefault(); setMemorySearch(memoryQuery); }}><input value={memoryQuery} onChange={(event) => setMemoryQuery(event.target.value)} placeholder="搜索任务、路径、决策或失败" aria-label="搜索记忆" /><button className="button" type="submit">搜索</button></form>}
        {view !== "quests" && (view !== "settings" || visiblePanel.loading || visiblePanel.error) && <StateNotice state={visiblePanel} empty={view === "teams" || view === "approvals" ? selectedId ? "此任务暂无记录" : "选择任务后查看" : view === "memory" ? "没有匹配的记忆。你可以试试任务名称、文件路径或决策关键词。" : "暂无数据"} />}
        {view === "activity" && panelItems.length > 0 && <><div className="list">{panelItems.map((item, index) => <div className="data-row" key={text(item.run_id, String(index))}><strong>{text(item.role, "执行成员")} · {statusLabel(item.state)}</strong><small>{text(item.created_at, "时间未知")}</small><dl><dt>任务</dt><dd className="break">{quests.find((quest) => quest.id === item.quest_id)?.title || text(item.quest_id)}</dd><dt>模型</dt><dd>{text(item.provider_id)} / {text(item.model_id)}</dd><dt>已执行步数</dt><dd>{text(item.steps)}</dd><dt>工具调用</dt><dd>{text(item.tool_spent)}</dd><dt>执行来源</dt><dd>{item.execution_mode === "live" ? "真实执行" : item.execution_mode === "mock" ? "模拟" : "来源未知"}</dd></dl><TechnicalDetails value={item} /></div>)}</div>{activityCursor && <button className="text-button more" disabled={visiblePanel.loading} onClick={() => void loadPanelPage()}>加载更早的活动</button>}</>}
        {view === "models" && <><div className="button-row"><button className="button" disabled={!connection || writesBlocked} onClick={() => { guard.start("model"); setModelEditor({ initial: null, version: null }); }}>添加模型</button></div>{modelEditor && <ModelEditor key={modelEditor.initial ? text(modelEditor.initial.id) : "new"} initial={modelEditor.initial} version={modelEditor.version} defaultProvider={savedProviderId || text(coreInfo.provider_id, "")} blocked={actionBusy || writesBlocked} save={(method, path, body) => void saveModel(method, path, body)} cancel={() => setModelEditor(null)} />}{panelItems.length > 0 && <div className="list">{panelItems.map((item, index) => <div className="data-row" key={text(item.id, String(index))}><ModelProfileSummary model={item} actions={<button className="button" disabled={writesBlocked} onClick={() => void openModel(text(item.id))}>编辑 / 启停</button>}><TechnicalDetails value={item} /></ModelProfileSummary></div>)}</div>}</>}
        {view === "teams" && panelItems.length > 0 && <div className="list">{panelItems.map((item, index) => <div className="data-row" key={text(item.id, String(index))}><strong>团队提案 · {statusLabel(item.status)}</strong><small>计划版本 {text(item.plan_revision)}</small>{typeof item.body === "object" && item.body !== null && <><dl><dt>总 Token 预算</dt><dd>{text(((item.body as Row).limits as Row | undefined)?.max_total_tokens)}</dd><dt>费用上限</dt><dd>{text(((item.body as Row).limits as Row | undefined)?.max_cost_microunits)} 微单位 · {text(((item.body as Row).limits as Row | undefined)?.currency, "币种未知")}</dd></dl>{rows((item.body as Row).members).map((member, memberIndex) => <div className="plan-node" key={text(member.role_id, String(memberIndex))}><strong>{text(member.role_id)}</strong><small>模型：{text(member.provider_id)} / {text(member.model_id)}</small><small>工具权限：{names(member.tool_scope)}</small><small>Token 预算：{text(member.estimated_tokens)}</small></div>)}</>}<TechnicalDetails value={item} />{item.status === "proposed" && selected && typeof item.proposal_digest === "string" && <button className="button primary" disabled={actionBusy || writesBlocked} onClick={() => { if (window.confirm(`批准团队？\n任务版本：${selected.version}\n计划版本：${text(item.plan_revision)}\n摘要：${text(item.proposal_digest)}\n请先检查成员、权限和预算。`)) void perform(`/api/quests/${encodeURIComponent(selected.id)}/teams/approve`, { expected_version: selected.version, plan_revision: item.plan_revision, proposal_digest: item.proposal_digest }); }}>批准此团队</button>}</div>)}</div>}
        {view === "approvals" && panelItems.length > 0 && <><div className="list">{panelItems.map((item, index) => <div className="data-row" key={text(item.id, String(index))}><strong>{toolLabel(item.action_type)} · {statusLabel(item.status)}</strong><dl><dt>风险</dt><dd>{riskLabel(item.risk)}</dd><dt>范围</dt><dd className="break">{text(item.resource_scope)}</dd><dt>计划版本</dt><dd>{text(item.plan_revision)}</dd><dt>参数摘要</dt><dd className="mono break">{text(item.parameter_digest)}</dd><dt>原因</dt><dd>{text(item.reason)}</dd><dt>过期时间</dt><dd>{text(item.expires_at)}</dd></dl>{item.status === "PENDING" && <div className="button-row">{(["APPROVED", "REJECTED"] as const).map((decision) => <button key={decision} className={decision === "APPROVED" ? "button primary" : "button"} disabled={actionBusy || writesBlocked || typeof item.parameter_digest !== "string" || typeof item.plan_revision !== "number"} onClick={() => { if (window.confirm(`${decision === "APPROVED" ? "批准" : "拒绝"}此操作？\n风险：${text(item.risk)}\n范围：${text(item.resource_scope)}\n计划版本：${text(item.plan_revision)}\n参数摘要：${text(item.parameter_digest)}`)) void perform(`/api/approvals/${encodeURIComponent(text(item.id))}/decision`, { decision, expected_digest: item.parameter_digest, expected_plan_revision: item.plan_revision }); }}>{decision === "APPROVED" ? "批准" : "拒绝"}</button>)}</div>}</div>)}</div>{approvalCursor && <button className="text-button more" disabled={visiblePanel.loading} onClick={() => void loadPanelPage()}>加载更早的审批</button>}</>}
        {view === "memory" && panelItems.length > 0 && <><div className="list">{(panelItems as MemoryItem[]).map((item) => <div className="data-row" key={item.id}><strong>{item.statement}</strong><small>{text(item.layer)} · {text(item.kind)} · {statusLabel(item.status)} · {text(item.freshness, "新鲜度未知")}</small><dl><dt>来源</dt><dd className="break">{item.source_refs?.join(", ") || "未知"}</dd><dt>记录时间</dt><dd>{text(item.created_at)}</dd><dt>有效至</dt><dd>{text(item.valid_until, "未指定")}</dd><dt>可信度</dt><dd>{text(item.confidence, "未知")}</dd><dt>关联任务</dt><dd className="break">{Array.isArray(item.related_quest_ids) ? item.related_quest_ids.join(", ") || "无" : "未知"}</dd></dl>{item.conflicted === true && <p className="warning">存在冲突，请核对来源。</p>}{/^[a-f0-9]{32}$/i.test(item.id) && <button className="button" disabled={actionBusy || writesBlocked} onClick={() => { if (window.confirm(`撤销这条记忆？\n${item.statement}`)) void perform(`/api/memory/${encodeURIComponent(item.id)}/revoke`); }}>撤销记忆</button>}</div>)}</div>{memoryCursor && <button className="text-button more" disabled={visiblePanel.loading} onClick={() => void loadPanelPage()}>加载更多记忆</button>}</>}
        {view === "settings" && <>
          <SettingInfo setting={setting} />
          {setting === "general" && <CoreOverview settings={Array.isArray(visiblePanel.value) ? visiblePanel.value[0] as Row : coreInfo} connected={!!connection} />}
          {setting === "environment" && <div className="connection-settings">
            {Array.isArray(visiblePanel.value) && <OperatorSettings key={text((visiblePanel.value[0] as Row)?.revision, "0")} settings={visiblePanel.value[0] as Row} budget={visiblePanel.value[1] as Row} blocked={writesBlocked || actionBusy} save={(value) => void perform("/api/settings", value)} />}
            <SettingsSection title="API Key" description="先保存连接配置，再保存密钥。密钥保存在系统凭据库。">
              {native ? <ProviderCredentials providerId={providerId} onProviderId={setProviderId} credentialRef={credentialRef} onCredentialRef={setCredentialRef} inputRef={credentialInput} status={credentialStatus} blocked={writesBlocked} onAction={(operation) => void credential(operation)} /> : <p className="notice">浏览器开发模式不提供系统凭据库。请在桌面应用中管理凭据。</p>}
            </SettingsSection>
            {runtimeStatus && <details className="settings-disclosure runtime-details"><summary>运行详情</summary><div className="settings-disclosure-body settings-facts"><SettingsRow label="运行状态"><span>{text(runtimeStatus.state)}</span></SettingsRow><SettingsRow label="进程代次"><span className="break">{text(runtimeStatus.generation)}</span></SettingsRow><SettingsRow label="重启次数"><span>{runtimeStatus.restartCount}</span></SettingsRow><SettingsRow label="最近错误"><span>{text(runtimeStatus.lastErrorCode, "无")}</span></SettingsRow></div></details>}
          </div>}
          {setting === "storage" && Array.isArray(visiblePanel.value) && <><BudgetOverview budget={visiblePanel.value[1] as Row} /><TechnicalDetails value={visiblePanel.value[1]} /></>}
          {setting === "maintenance" && <>{!native && <p className="notice">请在桌面应用中备份、恢复或重启本地 Core。恢复后的写入锁定与人工复核仍然适用。</p>}{native && <><div className="section-heading spaced"><span>工作空间维护</span></div><p className="notice">备份和恢复会暂时停止 Core；恢复成功后写操作保持锁定，需人工核对。</p><label className="field-label">备份目录的绝对路径<input value={workspacePath} onChange={(event) => setWorkspacePath(event.target.value)} placeholder="例如 D:/Backup/FORGE" /></label><div className="button-row"><button className="button" disabled={writesBlocked || actionBusy || !workspacePath.trim()} onClick={() => void maintainWorkspace("backup")}>备份</button><button className="button" disabled={writesBlocked || actionBusy || !workspacePath.trim()} onClick={() => void maintainWorkspace("restore")}>恢复</button><button className="button" disabled={writesBlocked || actionBusy} onClick={() => void retryRuntime()}>重启 Core</button></div>{workspaceOperation && <p className={workspaceOperation.status === "unknown" || workspaceOperation.result?.ok === false ? "notice error" : "notice"} role="status">{workspaceOperation.status === "running" ? "正在备份或恢复，完成后可退出。" : workspaceOperation.status === "unknown" ? (workspaceOperation.acknowledged ? "未知结果已归档；这不表示操作成功，也不会解除恢复锁定。" : "维护结果未知，请人工核查；不要重复发起。") : workspaceOperation.result?.ok ? (workspaceOperation.operation === "restore" ? "恢复完成；写操作已锁定，请先人工核对。" : "备份完成。") : `维护未完成：${text(workspaceOperation.result?.code)}`}{workspaceOperation.operationId && <> 操作 ID：<code>{workspaceOperation.operationId}</code></>}</p>}{workspaceOperation?.status === "unknown" && !workspaceOperation.acknowledged && workspaceOperation.operationId && <><label className="field-label">人工核查说明<input value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} /></label><button className="button" disabled={actionBusy || !reviewReason.trim() || runtimeStatus?.state !== "ready"} onClick={() => void acknowledgeUnknown()}>归档未知结果</button><p className="notice">仅归档，不表示成功，不解除恢复锁或预算限制。</p></>}</>}</>}
          {setting === "connections" && <><SettingsRow label="供应商" description="当前 Core 的模型连接。">{text(coreInfo.provider_id)}</SettingsRow><SettingsRow label="Router 模型" description="运行配置中的默认模型。">{text(coreInfo.router_model_id)}</SettingsRow><SettingsSection title="API Key" className="credential-section">{native ? <ProviderCredentials providerId={providerId} onProviderId={setProviderId} credentialRef={credentialRef} onCredentialRef={setCredentialRef} inputRef={credentialInput} status={credentialStatus} blocked={writesBlocked} onAction={(operation) => void credential(operation)} /> : <p className="notice">浏览器开发模式不提供系统凭据库。请在桌面应用中管理凭据。</p>}</SettingsSection></>}
          {restoreFenced === true && <div className="restore-warning"><strong>恢复待复核，后台与写入已暂停</strong><p>{restoreStatus?.review_available === true ? "请先核对当前工作区与回滚目录，退出应用后按 README 的离线维护步骤提交复核；此界面不会自动解除锁定。" : "复核记录不可用或损坏，请人工检查；不要尝试自动解锁。"}</p><div className="settings-facts"><SettingsRow label="清单摘要"><span className="break">{text(restoreStatus?.manifest_sha256)}</span></SettingsRow><SettingsRow label="回滚目录"><span className="break">{text(restoreStatus?.rollback_directory)}</span></SettingsRow></div></div>}
        </>}
        {view === "approvals" && grantPanel}
        {actionError && <p className="notice error" role="alert">{actionError}</p>}
      </div>
    </WorkspaceFrame>}
    {actionNotice && <div className="action-toast" role="status"><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><circle cx="10" cy="10" r="7.5" /><path d="m6.5 10 2.3 2.3 4.7-4.7" /></svg><span>{actionNotice}</span><button type="button" aria-label="关闭通知" onClick={() => setActionNotice("")}>×</button></div>}
  </div>;
}
