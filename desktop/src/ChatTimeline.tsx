import { invoke } from "@tauri-apps/api/core";
import { Children, isValidElement, useState, type ReactNode, type RefObject } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeExternalUrl } from "./logic";
import { mergeTimeline, messageMetadata, type ChatMessage, type Outgoing, type TimelineEvent, type TimelineItem } from "./timeline";

type Row = Record<string, unknown>;
type Quest = { id: string; title: string; status: string; version: number };
const text = (value: unknown, fallback = "未知") => value == null || value === "" ? fallback : String(value);

function contentText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (isValidElement<{ children?: ReactNode }>(node)) return contentText(node.props.children);
  return Children.toArray(node).map(contentText).join("");
}

function MarkdownMessage({ content, native, onInspect }: { content: string; native: boolean; onInspect: () => void }) {
  const [status, setStatus] = useState("");
  const copy = async (value: string) => {
    onInspect();
    try { await navigator.clipboard.writeText(value); setStatus("已复制"); }
    catch { setStatus("无法复制，请手动选择代码。"); }
  };
  const link = (href: string | undefined, children: ReactNode) => {
    const safe = safeExternalUrl(href);
    if (!safe) return <span>{children}</span>;
    if (native) return <button type="button" className="markdown-link" onClick={() => { void invoke("open_external", { url: safe }).catch(() => setStatus("无法在系统浏览器中打开链接。")); }}>{children}</button>;
    return <a href={safe} target="_blank" rel="noopener noreferrer">{children}</a>;
  };
  return <div className="markdown"><ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={{
    a({ href, children }) {
      return link(href, children);
    },
    img({ src, alt }) { return <span className="image-placeholder">图片未自动加载{alt ? `：${alt}` : ""}{safeExternalUrl(src) && <> · {link(src, "打开图片链接")}</>}</span>; },
    pre({ children }) { const code = contentText(children); return <div className="code-block"><div className="code-toolbar"><span>代码</span><button type="button" onClick={() => void copy(code)}>复制</button></div><pre>{children}</pre></div>; },
  }}>{content}</ReactMarkdown>{status && <span className="copy-status" role="status">{status}</span>}</div>;
}

type DisplayItem = Exclude<TimelineItem, { type: "event" }> | { type: "group"; questId: string | null; events: TimelineEvent[] };
function eventTitle(event: TimelineEvent): string {
  const labels: Record<string, string> = { "quest.proposed": "创建了任务", "quest.status_changed": "任务状态已更新", "quest.plan_proposed": "提出了计划", "quest.plan_approved": "计划已批准", "team.proposed": "提出了团队", "team.approved": "团队已批准", "team.reassigned": "团队已调整", "team.revoked": "团队批准已撤销", "worker.proposed": "提出了 Worker", "worker.started": "Worker 已启动", "worker.blocked": "Worker 已阻塞", "worker.failed": "Worker 执行失败", "worker.result": "已记录 Worker 结果", "worker.retry": "Worker 正在重试", "worker.help_requested": "Worker 请求协助", "tool.intent_prepared": "已准备工具操作", "tool.receipt_recorded": "已记录工具回执", "tool.execution_uncertain": "工具结果未知", "approval.requested": "需要审批", "approval.decided": "审批已处理", "approval.revoked": "审批已撤销", "evidence.json_checked": "已检查 JSON 语法", "task.verified": "任务已验证" };
  if (event.kind === "context_archive") return "上下文已归档";
  if (event.kind === "semantic_test") return event.status === "passed" ? "独立语义验证通过" : "独立语义验证";
  if (event.kind === "tool_receipt") {
    const detail = event.detail && typeof event.detail === "object" ? event.detail as Row : {};
    const tools: Record<string, string> = { file_read: "读取了文件", file_write: "写入了文件", directory_list: "查看了目录", directory_create: "创建了目录", shell_inspect: "运行了命令", python_compile: "检查了 Python 语法", git_status: "检查了 Git 状态", numeric_ast: "执行了数值计算", browser_get: "访问了网页", mcp_call: "调用了 MCP 工具" };
    return tools[text(detail.tool, "")] || "执行了工具";
  }
  return labels[text(event.status, "")] || event.title;
}

function EventLine({ event, onQuest, onInspect }: { event: TimelineEvent; onQuest: (id: string) => void; onInspect: () => void }) {
  const detail = event.detail && typeof event.detail === "object" && !Array.isArray(event.detail) ? event.detail as Row : {};
  const target = typeof detail.path === "string" ? detail.path : typeof detail.command === "string" ? detail.command : "";
  const failed = event.status === "failed" || event.status === "worker.failed" || event.status === "tool.execution_uncertain" || (typeof detail.exit_code === "number" && detail.exit_code !== 0);
  const unknown = event.status === "unknown" || event.status === "uncertain" || event.status === "tool.execution_uncertain";
  return <details onToggle={onInspect} className={`event-line${failed ? " failed" : ""}${unknown ? " unknown" : ""}`}><summary><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="m8 10 2 2-2 2m4 0h4"/></svg><span>{eventTitle(event)}{target && <span className="event-target"> {target}</span>}{(failed || unknown) && <span className="event-failed"> · {unknown ? "结果未知" : "失败"}{typeof detail.exit_code === "number" ? `（退出码 ${detail.exit_code}）` : ""}</span>}{event.execution_mode === "mock" && <small className="event-mode"> · 模拟</small>}</span></summary>
    <div className="event-detail"><p>{event.execution_mode === "live" ? "真实执行" : event.execution_mode === "mock" ? "模拟" : "来源未知"} · {event.created_at ? new Date(event.created_at).toLocaleString() : "时间未知"}</p>{event.quest_id && <button className="text-button" onClick={() => onQuest(event.quest_id!)}>查看关联任务</button>}<dl>{Object.entries(detail).map(([key, value]) => <div key={key}><dt>{({ path: "路径", tool: "工具", result_sha256: "结果摘要", command: "命令", exit_code: "退出码", scope: "范围", source_count: "来源数" } as Record<string, string>)[key] || key}</dt><dd>{text(value)}</dd></div>)}</dl><small>记录 ID：{event.event_id}</small></div></details>;
}
function groupEvents(items: TimelineItem[]): DisplayItem[] {
  const grouped: DisplayItem[] = [];
  for (const item of items) {
    if (item.type !== "event") { grouped.push(item); continue; }
    const last = grouped[grouped.length - 1], questId = item.value.quest_id || null;
    if (last?.type === "group" && last.questId === questId) last.events.push(item.value);
    else grouped.push({ type: "group", questId, events: [item.value] });
  }
  return grouped;
}

export default function ChatTimeline({ messages, events, outgoing, quests, approvals, native,
  connected, historyMore, historyBusy, timelineError, conversationError, approvalError,
  canRetry, scrollRef, onScroll, onInspect, onLoadOlder, onRetry, onQuest, onApproval }: {
  messages: ChatMessage[]; events: TimelineEvent[]; outgoing: Outgoing[]; quests: Quest[]; approvals: Row[];
  native: boolean; connected: boolean; historyMore: boolean; historyBusy: boolean; timelineError: string;
  conversationError: string; approvalError: string; canRetry: boolean; scrollRef: RefObject<HTMLDivElement>;
  onScroll: () => void; onInspect: () => void; onLoadOlder: () => void; onRetry: (item: Outgoing) => void;
  onQuest: (id: string) => void; onApproval: (id: string) => void;
}) {
  const items = groupEvents(mergeTimeline(messages, events, outgoing));
  const active = quests.filter((quest) => !["completed", "cancelled", "failed", "rejected"].includes(quest.status)).slice(0, 3);
  return <div className="timeline" ref={scrollRef} onScroll={onScroll} onMouseUp={() => { if (window.getSelection()?.toString()) onInspect(); }} aria-label="对话历史">
    {historyMore && <button className="text-button history" disabled={historyBusy} onClick={onLoadOlder}>{historyBusy ? "正在加载…" : "加载更早的消息与记录"}</button>}
    {conversationError && <p className="notice error" role="alert">{conversationError}</p>}
    {timelineError && <p className="notice error" role="alert">过程记录暂不可用：{timelineError}</p>}
    {!items.length && connected && <div className="empty-conversation"><span className="empty-mark">F</span><h2>从这里开始</h2><p>描述目标，或继续之前的任务。所有结果都会回到这个对话。</p></div>}
    {items.map((item) => {
      if (item.type === "group") return <div className="event-group" key={`${item.events[0].kind}:${item.events[0].event_id}`}>{item.events.length > 2 && <details className="earlier-events" onToggle={onInspect}><summary>查看较早的 {item.events.length - 2} 条{item.questId ? "任务记录" : "后台活动"}</summary>{item.events.slice(0, -2).map((event) => <EventLine key={`${event.kind}:${event.event_id}`} event={event} onQuest={onQuest} onInspect={onInspect} />)}</details>}{item.events.slice(-2).map((event) => <EventLine key={`${event.kind}:${event.event_id}`} event={event} onQuest={onQuest} onInspect={onInspect} />)}</div>;
      if (item.type === "outgoing") return <article key={`outgoing:${item.value.requestId}`} className="message from-user"><div className="message-meta"><span className="message-avatar">你</span><strong>你</strong><span>{item.value.status === "sending" ? "发送中" : "发送失败"}</span></div><div className="message-body"><MarkdownMessage content={item.value.content} native={native} onInspect={onInspect} />{item.value.status === "failed" && <div className="outgoing-error" role="alert">{item.value.error || "请求未完成"} · 请求 ID：<code>{item.value.requestId}</code><button className="text-button" disabled={!canRetry} onClick={() => onRetry(item.value)}>使用原请求重试</button></div>}</div></article>;
      const message = item.value, meta = messageMetadata(message.metadata);
      return <article key={message.id} className={`message ${message.role === "user" ? "from-user" : "from-agent"}`}><div className="message-meta"><span className="message-avatar">{message.role === "user" ? "你" : "F"}</span><strong>{message.role === "user" ? "你" : "FORGE"}</strong>{meta.execution_mode === "mock" && <span>模拟</span>}{meta.execution_mode === "live" && <span>真实执行</span>}{message.role !== "user" && meta.execution_mode !== "live" && meta.execution_mode !== "mock" && <span>来源未知</span>}<time>{message.created_at ? new Date(message.created_at).toLocaleString() : ""}</time></div><div className="message-body"><MarkdownMessage content={message.content} native={native} onInspect={onInspect} /></div></article>;
    })}
    {connected && (active.length > 0 || approvals.length > 0) && <section className="timeline-updates" aria-label="任务与审批更新"><div className="timeline-updates-label">当前任务与审批</div>{approvals.slice(0, 3).map((item) => <button className="timeline-update" key={text(item.id)} onClick={() => onApproval(text(item.quest_id, ""))}><span><strong>需要审批 · {text(item.action_type)}</strong><small>风险 {text(item.risk)} · {text(item.resource_scope)}</small></span><span aria-hidden="true">›</span></button>)}{active.map((quest) => <button className="timeline-update" key={quest.id} onClick={() => onQuest(quest.id)}><span><strong>{quest.title}</strong><small>状态：{quest.status} · 版本 {quest.version}</small></span><span aria-hidden="true">›</span></button>)}</section>}
    {connected && approvalError && <p className="notice error" role="alert">审批状态暂不可用：{approvalError}</p>}
  </div>;
}
