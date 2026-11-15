import { invoke } from "@tauri-apps/api/core";
import { Children, isValidElement, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import AnimatedDetails from "./AnimatedDetails";
import InlineArtifactDiff, { type InlineArtifactMemory } from "./InlineArtifactDiff";
import type { Connection } from "./api";
import type { ArtifactDiff } from "./artifactHistory";
import { WorkerRunDetails, type WorkerRun } from "./ConversationRuntime";
import { safeExternalUrl } from "./logic";
import { isClosedQuest, needsQuestAttention, riskLabel, statusLabel, toolLabel } from "./presentation";
import { mergeTimeline, messageMetadata, type ChatMessage, type Outgoing, type TimelineEvent, type TimelineItem } from "./timeline";
import { relativeFilePath } from "./workspaceFiles";

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
type EventViewState = { expanded?: boolean; recordExpanded?: boolean; groupExpanded?: boolean; commandExpanded?: boolean; workerExpanded?: boolean; groupFilesExpanded?: boolean; inline?: InlineArtifactMemory };
type EventViewMemory = { expansion: Map<string, EventViewState>; commandScroll: Map<string, number> };
const eventKey = (event: TimelineEvent) => `${event.kind}:${event.event_id}`;

// Reuse the exact safe message renderer for read-only workspace Markdown previews.
export function SafeMarkdownPreview({ content, native, onInspect = () => {} }: { content: string; native: boolean; onInspect?: () => void }) {
  return <MarkdownMessage content={content} native={native} onInspect={onInspect} />;
}

function useTransientStatus() {
  const [status, setStatus] = useState("");
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const show = (message: string) => {
    window.clearTimeout(timer.current);
    setStatus(message);
    timer.current = window.setTimeout(() => setStatus(""), 2000);
  };
  return [status, show] as const;
}
function eventView(memory: EventViewMemory, event: TimelineEvent): EventViewState {
  const key = eventKey(event);
  let state = memory.expansion.get(key);
  if (!state) { state = {}; memory.expansion.set(key, state); }
  return state;
}
function CopyReply({ content, onInspect }: { content: string; onInspect: () => void }) {
  const [status, setStatus] = useTransientStatus();
  const copy = async () => {
    onInspect();
    try { await navigator.clipboard.writeText(content); setStatus("已复制回复"); }
    catch { setStatus("无法复制，请手动选择回复。"); }
  };
  return <div className="message-actions"><button type="button" className="icon-button" aria-label="复制回复" title="复制回复" onClick={() => void copy()}><svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" /><path d="M13 7V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h3" /></svg></button>{status && <span className="copy-feedback" role="status">{status}</span>}</div>;
}

function eventTitle(event: TimelineEvent, runs: WorkerRun[] = []): string {
  const labels: Record<string, string> = { "quest.proposed": "创建了任务", "quest.status_changed": "任务状态已更新", "quest.plan_proposed": "提出了计划", "quest.plan_approved": "计划已批准", "team.proposed": "提出了团队", "team.approved": "团队已批准", "team.reassigned": "团队已调整", "team.revoked": "团队批准已撤销", "worker.proposed": "提出了执行成员", "worker.started": "执行成员已启动", "worker.blocked": "执行成员已阻塞", "worker.failed": "执行成员执行失败", "worker.result": "已记录执行成员结果", "worker.retry": "执行成员正在重试", "worker.help_requested": "执行成员请求协助", "tool.intent_prepared": "已准备工具操作", "tool.receipt_recorded": "已记录工具回执", "tool.execution_uncertain": "工具结果未知", "approval.requested": "需要审批", "approval.decided": "审批已处理", "approval.revoked": "审批已撤销", "evidence.json_checked": "已检查 JSON 语法", "task.verified": "任务已验证" };
  if (event.status?.startsWith("worker.")) {
    const run = event.run_id ? runs.find((item) => item.run_id === event.run_id) : undefined;
    const name = run?.role || event.worker_id;
    if (name) return `${name} · ${labels[event.status] || statusLabel(event.status)}`;
  }
  if (event.kind === "context_archive") return "上下文已归档";
  if (event.kind === "semantic_test") return event.status === "passed" ? "独立语义验证通过" : event.status === "failed" ? "独立语义验证失败" : "独立语义验证结果未知";
  if (event.kind === "tool_receipt") {
    const detail = event.detail && typeof event.detail === "object" ? event.detail as Row : {};
    const tools: Record<string, string> = { file_read: "已读取文件", file_write: "编辑了文件", file_revise: "编辑了文件", shell_inspect: "运行了命令", python_compile: "运行了命令", git_status: "运行了命令", numeric_ast: "运行了命令", browser_get: "读取网页" };
    return tools[text(detail.tool, "")] || "调用了一个工具";
  }
  return labels[text(event.status, "")] || event.title;
}

function activitySummary(events: TimelineEvent[], runs: WorkerRun[]): string {
  // The chat headline describes work, while the complete lifecycle stays in the record.
  const actions = events.filter((event) => event.kind === "tool_receipt" || event.kind === "context_archive" || event.kind === "semantic_test"
    || /failed|unknown|uncertain|blocked|help_requested|approval\.(requested|revoked)/.test(event.status || ""));
  const headlineEvents = actions.length ? actions : events.slice(-1);
  const successfulLiveTool = (event: TimelineEvent) => {
    const detail = event.detail as Row | undefined;
    return event.kind === "tool_receipt" && event.status === "succeeded" && event.execution_mode === "live" && !(typeof detail?.exit_code === "number" && detail.exit_code !== 0);
  };
  const edits = new Set(headlineEvents.flatMap((event) => {
    const detail = event.detail as Row | undefined;
    return successfulLiveTool(event) && ["file_write", "file_revise"].includes(text(detail?.tool, "")) && typeof detail?.path === "string" ? [detail.path] : [];
  }));
  const counts = new Map<string, { event: TimelineEvent; count: number }>();
  for (const event of headlineEvents) {
    const detail = event.detail as Row | undefined;
    const key = event.kind === "tool_receipt" ? `tool:${text(detail?.tool, "unknown")}:${event.status}:${event.execution_mode}:${detail?.exit_code}` : `${eventTitle(event, runs)}:${event.execution_mode}`;
    const existing = counts.get(key);
    if (existing) existing.count += 1;
    else counts.set(key, { event, count: 1 });
  }
  let edited = false;
  return [...counts.values()].map(({ event, count }) => {
    const detail = event.detail as Row | undefined;
    if (successfulLiveTool(event) && ["file_write", "file_revise"].includes(text(detail?.tool, ""))) {
      if (edited) return "";
      edited = true;
      return edits.size ? `编辑了${edits.size === 1 ? "一个" : "多个"}文件` : eventTitle(event, runs);
    }
    if (event.kind === "tool_receipt" && event.status === "succeeded" && event.execution_mode === "mock" && !["file_write", "file_revise"].includes(text(detail?.tool, "")) && !(typeof detail?.exit_code === "number" && detail.exit_code !== 0)) {
      return `${eventTitle(event, runs)}${count > 1 ? ` · ${count} 次调用` : ""}`;
    }
    if (event.kind === "tool_receipt" && !successfulLiveTool(event)) {
      const result = typeof detail?.exit_code === "number" && detail.exit_code !== 0 ? `失败，退出码 ${detail.exit_code}` : statusLabel(event.status, "结果未知");
      const mode = event.execution_mode === "mock" || event.execution_mode === "live" ? "" : "来源未知";
      return `${toolLabel(detail?.tool, "工具")}${count > 1 ? ` · ${count} 次调用` : ""}（${[mode, result].filter(Boolean).join(" · ")}）`;
    }
    const label = count > 1 && event.kind === "tool_receipt" ? `${toolLabel(detail?.tool, "工具")} · ${count} 次调用` : eventTitle(event, runs);
    return label;
  }).join("");
}

function ActivityIcon({ event }: { event?: TimelineEvent }) {
  const detail = event?.detail && typeof event.detail === "object" ? event.detail as Row : {};
  const tool = text(detail.tool, "");
  const shape = event?.kind === "context_archive"
    ? <path d="M2.8 5.2h10.4M2.8 8h10.4M2.8 10.8h6.4" />
    : event?.status?.startsWith("worker.")
      ? <><circle cx="8" cy="5.4" r="2.3" /><path d="M3.2 13.2c.4-2.4 2.4-3.7 4.8-3.7s4.4 1.3 4.8 3.7" /></>
      : ["file_write", "file_revise"].includes(tool)
        ? <><path d="m10.4 2.3 3.3 3.3-7.8 7.8-4 .7.7-4 7.8-7.8Z" /><path d="m8.5 4.2 3.3 3.3M2.6 10.1l3.3 3.3" /></>
        : tool === "file_read"
        ? <><path d="M8.6 1.8H4.4A1.6 1.6 0 0 0 2.8 3.4v9.2a1.6 1.6 0 0 0 1.6 1.6h6.4a1.6 1.6 0 0 0 1.6-1.6V5.4L8.6 1.8Z" /><path d="M8.6 2v3.4h3.6" /></>
        : tool && !["shell_inspect", "python_compile", "git_status", "numeric_ast"].includes(tool)
          ? <path d="M9.6 2.6a3.4 3.4 0 0 0-4.2 4.2L2.6 9.6a1.6 1.6 0 0 0 2.2 2.2l2.8-2.8a3.4 3.4 0 0 0 4.2-4.2L10 6.6 9.4 6 8.8 5.4l1.6-1.6Z" />
          : <><rect x="1.75" y="3" width="12.5" height="10" rx="2" /><path d="m4.4 6.6 1.7 1.4-1.7 1.4m4.2.2h3" /></>;
  return <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shape}</svg>;
}

function Chevron() {
  return <svg data-chevron viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>;
}

function workerIdentity(event: TimelineEvent, runs: WorkerRun[]): string {
  const run = event.run_id ? runs.find((item) => item.run_id === event.run_id) : undefined;
  if (!event.worker_id && !run) return "";
  return ["Worker", run?.role, event.worker_id, event.run_id, run?.provider_id, run?.model_id].filter(Boolean).join(" · ");
}

function updateOutputEdges(element: HTMLDivElement) {
  const overflow = Math.max(0, element.scrollHeight - element.clientHeight);
  // A reversed terminal starts at the bottom (0) and scrolls upward to negative offsets.
  const reverse = getComputedStyle(element).flexDirection === "column-reverse";
  const fromTop = Math.max(0, Math.min(overflow, reverse ? overflow + element.scrollTop : element.scrollTop));
  element.dataset.canScrollUp = String(fromTop > 1);
  element.dataset.canScrollDown = String(overflow - fromTop > 1);
}

function CommandCard({ detail, status, executionMode, visible, expanded, onExpandedChange, savedScrollTop, onScroll, onInspect }: { detail: Row; status?: string | null; executionMode?: string | null; visible: boolean; expanded: boolean; onExpandedChange: (value: boolean) => void; savedScrollTop: number; onScroll: (scrollTop: number) => void; onInspect: () => void }) {
  const [copyStatus, setCopyStatus] = useTransientStatus();
  const [longCommand, setLongCommand] = useState(false);
  const commandRef = useRef<HTMLButtonElement>(null);
  const outputRef = useRef<HTMLDivElement>(null);
  const command = text(detail.command, "");
  // The current Core normally returns a receipt digest, not stdout. Never infer output from it.
  const output = [detail.stdout, detail.output, detail.stderr].filter((value): value is string => typeof value === "string").join("\n");
  useLayoutEffect(() => {
    const element = commandRef.current;
    if (!element || !visible) return;
    const measure = () => setLongCommand(element.scrollHeight > 48);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [command, visible, expanded]);
  useLayoutEffect(() => {
    if (visible && outputRef.current) {
      outputRef.current.scrollTop = savedScrollTop;
      updateOutputEdges(outputRef.current);
    }
  }, [visible, savedScrollTop, output]);
  useLayoutEffect(() => {
    const element = outputRef.current;
    if (!element) return;
    const update = () => updateOutputEdges(element);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => observer.disconnect();
  }, [visible, output]);
  const copy = async () => {
    onInspect();
    try { await navigator.clipboard.writeText(command); setCopyStatus("已复制"); }
    catch { setCopyStatus("无法复制，请手动选择代码。"); }
  };
  const copyOutput = async () => {
    onInspect();
    try { await navigator.clipboard.writeText(output); setCopyStatus("已复制输出"); }
    catch { setCopyStatus("无法复制，请手动选择代码。"); }
  };
  const failed = status === "failed" || typeof detail.exit_code === "number" && detail.exit_code !== 0;
  const result = failed ? typeof detail.exit_code === "number" && detail.exit_code !== 0 ? `退出码 ${detail.exit_code}` : "失败" : status === "succeeded" && detail.exit_code === 0 ? "成功" : status === "cancelled" ? "已停止" : ["running", "prepared", "pending"].includes(status || "") ? "" : "结果未知";
  return <div className="command-card"><header><span>{detail.tool === "shell_inspect" ? "Shell" : toolLabel(detail.tool, "命令")}</span>{output && <button type="button" className="command-output-copy" aria-label="复制命令输出" title="复制输出" onClick={() => void copyOutput()}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><rect x="5" y="5" width="8" height="9" rx="1.5" /><path d="M10 5V3.5A1.5 1.5 0 0 0 8.5 2h-5A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" /></svg></button>}</header>
    <div className="command-line"><button ref={commandRef} type="button" className="cmd" data-expanded={expanded} aria-label={`$ ${command}`} aria-expanded={longCommand || expanded ? expanded : undefined} title={longCommand || expanded ? expanded ? "收起完整命令" : "查看完整命令" : command} onClick={() => { if (longCommand || expanded) { onInspect(); onExpandedChange(!expanded); } }}>{command}</button><button type="button" className="copy-btn" aria-label="复制" onClick={() => void copy()}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" /><path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" /></svg></button></div>
    {output ? <div ref={outputRef} className="out vertical-scroll-fade-mask" data-can-scroll-up="false" data-can-scroll-down="false" onScroll={(event) => { updateOutputEdges(event.currentTarget); if (visible) onScroll(event.currentTarget.scrollTop); }}><code>{output}</code></div> : <p className="command-no-output">暂无输出</p>}
    <footer className="command-result"><span className={failed ? "event-failed" : result === "结果未知" ? "event-unknown" : undefined}>{result === "成功" && <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><path d="m3.5 8 3 3 6-7" /></svg>}{result}{executionMode === "mock" && " · 模拟"}</span></footer>
    {copyStatus && <span className="copy-status" role="status">{copyStatus}</span>}
  </div>;
}

type HistoryProps = { connection?: Connection | null; onHistoryLoaded?: (record: ArtifactDiff) => void; onOpenHistory?: (receiptId: string, path: string) => void; historyStats?: Record<string, { added: number; removed: number }> };
function EventLine({ event, quests, runs, memory, groupExpanded, onQuest, onOpenFile, onInspect, onOpenHistory, historyStats, connection, onHistoryLoaded }: { event: TimelineEvent; quests: Quest[]; runs: WorkerRun[]; memory: EventViewMemory; groupExpanded: boolean; onQuest: (id: string) => void; onOpenFile?: (path: string) => void; onInspect: () => void } & HistoryProps) {
  const view = eventView(memory, event), key = eventKey(event);
  const [expanded, setExpanded] = useState(view.expanded || false);
  const [commandExpanded, setCommandExpanded] = useState(view.commandExpanded || false);
  const detail = event.detail && typeof event.detail === "object" && !Array.isArray(event.detail) ? event.detail as Row : {};
  const relatedQuest = quests.find((quest) => quest.id === event.quest_id);
  const target = typeof detail.path === "string" ? detail.path : typeof detail.command === "string" ? detail.command : "";
  const filePath = typeof detail.path === "string" ? detail.path : "";
  const fileParts = filePath.replace(/\\/g, "/").split("/");
  const fileName = fileParts.pop() || filePath;
  const fileDirectory = fileParts.length ? `${fileParts.join("/")}/` : "";
  const openFile = () => { if (filePath && onOpenFile) { onInspect(); onOpenFile(filePath); } };
  const failed = event.status === "failed" || event.status === "worker.failed" || event.status === "tool.execution_uncertain" || (typeof detail.exit_code === "number" && detail.exit_code !== 0);
  const unknown = event.status === "unknown" || event.status === "uncertain" || event.status === "tool.execution_uncertain";
  const canReadHistory = !!onOpenHistory && event.kind === "tool_receipt" && event.status === "succeeded" && ["live", "mock"].includes(event.execution_mode || "") && ["file_write", "file_revise"].includes(text(detail.tool, "")) && !!filePath && !failed;
  if (!view.inline) view.inline = {};
  const record = <>
    {workerIdentity(event, runs) && <p className="event-origin" title={workerIdentity(event, runs)}>{workerIdentity(event, runs)}</p>}
    {(event.worker_id || event.run_id) && <WorkerRunDetails run={event.run_id ? runs.find((run) => run.run_id === event.run_id) : undefined} runId={event.run_id} workerId={event.worker_id}
      historicalEvent={eventTitle(event, runs)} historicalTime={event.created_at} initialExpanded={view.workerExpanded || false}
      onExpandedChange={(value) => { view.workerExpanded = value; }} onInspect={onInspect} scrollKey={`event-worker:${key}`} />}
    <p>{event.execution_mode === "live" ? "真实执行" : event.execution_mode === "mock" ? "模拟" : "来源未知"} · {event.created_at ? new Date(event.created_at).toLocaleString() : "时间未知"}</p>
    {event.quest_id && <button className="text-button" aria-label="查看关联任务" onClick={() => onQuest(event.quest_id!)}>查看关联任务{relatedQuest ? ` · ${relatedQuest.title}` : ""}</button>}
    <dl>{Object.entries(detail).filter(([key]) => !(typeof detail.command === "string" && ["stdout", "output", "stderr"].includes(key))).map(([key, value]) => <div key={key}><dt>{({ path: "路径", tool: "工具", result_sha256: "结果摘要", command: "命令", exit_code: "退出码", scope: "范围", source_count: "来源数", status: "状态", risk: "风险" } as Record<string, string>)[key] || key}</dt><dd>{key === "tool" ? toolLabel(value) : key === "status" ? statusLabel(value) : key === "risk" ? riskLabel(value) : text(value)}</dd></div>)}</dl>
    <small>记录 ID：{event.event_id}</small>
  </>;
  return <AnimatedDetails onInspect={onInspect} scrollKey={`event:${key}`} initialExpanded={expanded}
    onExpandedChange={(value) => { view.expanded = value; setExpanded(value); }}
    className={`event-line${failed ? " failed" : ""}${unknown ? " unknown" : ""}`}
    summaryTitle={[workerIdentity(event, runs), target].filter(Boolean).join("\n")}
    summaryLabel={canReadHistory ? `${target || eventTitle(event, runs)}${event.execution_mode === "mock" ? " · 模拟" : ""}` : undefined}
    summaryAction={filePath && onOpenFile ? <button type="button" className="event-file-open" aria-label={`预览文件 ${filePath}`} title={`预览 ${filePath}`} onClick={openFile}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><rect x="2" y="2.5" width="12" height="11" rx="2" /><path d="M9 3v10" /></svg></button> : undefined}
    summary={<><ActivityIcon event={event} /><span className="event-label">{canReadHistory && expanded ? "已编辑的文件" : target || eventTitle(event, runs)}</span>{(failed || unknown) && <span className="event-failed event-risk"> · {unknown ? "结果未知" : "失败"}{typeof detail.exit_code === "number" ? `（退出码 ${detail.exit_code}）` : ""}</span>}{event.execution_mode === "mock" && <small className="event-mode"> · 模拟</small>}<Chevron /></>}>
    <div className="event-detail">
      {typeof detail.command === "string" && <CommandCard detail={detail} status={event.status} executionMode={event.execution_mode} visible={groupExpanded && expanded} expanded={commandExpanded} onExpandedChange={(value) => { view.commandExpanded = value; setCommandExpanded(value); }} savedScrollTop={memory.commandScroll.get(key) || 0} onScroll={(scrollTop) => memory.commandScroll.set(key, scrollTop)} onInspect={onInspect} />}
      {filePath && !canReadHistory && <button type="button" className="activity-file-row" title={filePath} disabled={!onOpenFile} onClick={openFile}><ActivityIcon event={event} /><span className="activity-file-copy"><span className="activity-file-directory">{fileDirectory}</span><span className="activity-file-name">{fileName}</span></span><span className="activity-file-action">预览</span></button>}
      {canReadHistory && <InlineArtifactDiff connection={connection || null} receiptId={event.event_id} path={filePath} visible={groupExpanded && expanded} memory={view.inline} onInspect={onInspect} onLoaded={onHistoryLoaded} onOpenHistory={onOpenHistory} />}
      <AnimatedDetails className="event-record" initialExpanded={view.recordExpanded || false} onExpandedChange={(value) => { view.recordExpanded = value; }} onInspect={onInspect} summary={<><span>记录详情</span><Chevron /></>}><div className="event-record-content">{record}</div></AnimatedDetails>
    </div></AnimatedDetails>;
}

function EventGroup({ item, quests, runs, memory, onQuest, onOpenFile, onInspect, onOpenHistory, historyStats, connection, onHistoryLoaded }: { item: Extract<DisplayItem, { type: "group" }>; quests: Quest[]; runs: WorkerRun[]; memory: EventViewMemory; onQuest: (id: string) => void; onOpenFile?: (path: string) => void; onInspect: () => void } & HistoryProps) {
  const [expanded, setExpanded] = useState(() => item.events.some((event) => eventView(memory, event).groupExpanded));
  const [filesExpanded, setFilesExpanded] = useState(() => item.events.some((event) => eventView(memory, event).groupFilesExpanded));
  // Members inherit the current group state, so splits and prepended group roots restore it.
  useLayoutEffect(() => {
    for (const event of item.events) {
      eventView(memory, event).groupExpanded = expanded;
      eventView(memory, event).groupFilesExpanded = filesExpanded;
    }
  }, [item.events, memory, expanded, filesExpanded]);
  const summary = activitySummary(item.events, runs);
  const origins = [...new Set(item.events.map((event) => workerIdentity(event, runs)).filter(Boolean))];
  const hasMock = item.events.some((event) => event.execution_mode === "mock");
  const hasFailure = item.events.some((event) => /failed/.test(event.status || "") || typeof (event.detail as Row | undefined)?.exit_code === "number" && Number((event.detail as Row).exit_code) !== 0);
  const hasUnknown = item.events.some((event) => /unknown|uncertain/.test(event.status || ""));
  const recordedFiles = new Map<string, TimelineEvent>();
  for (const event of item.events) {
    const detail = event.detail && typeof event.detail === "object" ? event.detail as Row : {};
    if (event.kind !== "tool_receipt" || event.status !== "succeeded" || event.execution_mode !== "live" || typeof detail.exit_code === "number" && detail.exit_code !== 0 || !["file_write", "file_revise"].includes(text(detail.tool, "")) || typeof detail.path !== "string") continue;
    try { const path = relativeFilePath(detail.path); if (!recordedFiles.has(path)) recordedFiles.set(path, event); } catch { /* An invalid path is not an openable file reference. */ }
  }
  const files = [...recordedFiles.entries()];
  const fileRow = ([path, event]: [string, TimelineEvent]) => {
    const parts = path.split("/"), name = parts.pop() || path, directory = parts.length ? `${parts.join("/")}/` : "";
    return <button type="button" className="activity-file-row" key={path} data-scroll-key={`file:${eventKey(event)}`} title={path} disabled={!onOpenFile} onClick={() => { onInspect(); onOpenFile?.(path); }}><ActivityIcon event={event} /><span className="activity-file-copy"><span className="activity-file-directory">{directory}</span><span className="activity-file-name">{name}</span></span><span className="activity-file-action">预览</span></button>;
  };
  return <AnimatedDetails className="event-group" scrollKey={`group:${eventKey(item.events[0])}`} initialExpanded={expanded} onExpandedChange={(value) => { for (const event of item.events) eventView(memory, event).groupExpanded = value; setExpanded(value); }} onInspect={onInspect} summaryTitle={origins.join("\n")} summaryLabel={item.events.length > 2 ? `查看较早的 ${item.events.length - 2} 条${item.questId ? "任务记录" : "后台活动"}` : undefined} summary={<><ActivityIcon event={item.events[0]} /><span className="event-label">{summary}</span>{hasFailure && <span className="event-risk event-failed">失败</span>}{hasUnknown && <span className="event-risk event-unknown">结果未知</span>}{hasMock && <small className="event-mode"> · {item.events.every((event) => event.execution_mode === "mock") ? "模拟" : "含模拟记录"}</small>}<Chevron /></>}>
    <div className="event-detail">{files.length > 1 && <div className="activity-group-files"><p className="activity-group-files-label">本组记录的文件</p>{files.slice(0, 3).map(fileRow)}
      {files.length > 3 && <AnimatedDetails className="activity-more-files" scrollKey={`group-files:${eventKey(item.events[0])}`} initialExpanded={filesExpanded}
        onExpandedChange={(value) => { for (const event of item.events) eventView(memory, event).groupFilesExpanded = value; setFilesExpanded(value); }} onInspect={onInspect}
        summary={<><span>再显示 {files.length - 3} 个文件</span><Chevron /></>}><div className="activity-more-files-body">{files.slice(3).map(fileRow)}</div></AnimatedDetails>}
    </div>}{item.events.map((event) => <EventLine key={eventKey(event)} event={event} quests={quests} runs={runs} memory={memory} groupExpanded={expanded} onQuest={onQuest} onOpenFile={onOpenFile} onOpenHistory={onOpenHistory} historyStats={historyStats} connection={connection} onHistoryLoaded={onHistoryLoaded} onInspect={onInspect} />)}</div>
  </AnimatedDetails>;
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

export default function ChatTimeline({ messages, events, outgoing, quests, workerRuns, approvals, native,
  connected, historyMore, historyBusy, timelineError, conversationError, approvalError,
  canRetry, scrollRef, onScroll, onInspect, onLoadOlder, onRetry, onQuest, onOpenFile, onOpenHistory, historyStats, connection, onHistoryLoaded, onApproval, approvalsBlocked, onApprovalDecision }: {
  messages: ChatMessage[]; events: TimelineEvent[]; outgoing: Outgoing[]; quests: Quest[]; workerRuns: WorkerRun[]; approvals: Row[];
  native: boolean; connected: boolean; historyMore: boolean; historyBusy: boolean; timelineError: string;
  conversationError: string; approvalError: string; canRetry: boolean; scrollRef: RefObject<HTMLDivElement>;
  onScroll: () => void; onInspect: () => void; onLoadOlder: () => void; onRetry: (item: Outgoing) => void;
  onQuest: (id: string) => void; onApproval: (id: string) => void;
  onOpenFile?: (path: string) => void;
  approvalsBlocked: boolean; onApprovalDecision: (item: Row, decision: "APPROVED" | "REJECTED") => void;
} & HistoryProps) {
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "disconnected";
  const memoryRef = useRef<{ identity: string; value: EventViewMemory }>({ identity, value: { expansion: new Map(), commandScroll: new Map() } });
  if (memoryRef.current.identity !== identity) memoryRef.current = { identity, value: { expansion: new Map(), commandScroll: new Map() } };
  const memory = memoryRef.current.value;
  const items = groupEvents(mergeTimeline(messages, events, outgoing));
  const active = quests.filter((quest) => !isClosedQuest(quest.status)).sort((left, right) => Number(needsQuestAttention(right.status)) - Number(needsQuestAttention(left.status)));
  const attention = active.filter((quest) => needsQuestAttention(quest.status));
  const background = active.filter((quest) => !needsQuestAttention(quest.status));
  const taskRow = (quest: Quest) => <button className="timeline-update" key={quest.id} aria-label={`${quest.title} 状态：${quest.status} · 版本 ${quest.version}`} onClick={() => onQuest(quest.id)}><span><strong>{quest.title}</strong><small>{statusLabel(quest.status)}</small></span><span aria-hidden="true">›</span></button>;
  return <div className="timeline" ref={scrollRef} onScroll={onScroll} onMouseUp={() => { if (window.getSelection()?.toString()) onInspect(); }} aria-label="对话历史">
    {historyMore && <button className="text-button history" disabled={historyBusy} onClick={onLoadOlder}>{historyBusy ? "正在加载…" : "加载更早的消息与记录"}</button>}
    {conversationError && <p className="notice error" role="alert">{conversationError}</p>}
    {timelineError && <p className="notice error" role="alert">过程记录暂不可用：{timelineError}</p>}
    {!items.length && <div className="empty-conversation"><h2>有什么可以帮你？</h2><p>直接说目标，也可以继续之前的任务。</p></div>}
    {items.map((item) => {
      if (item.type === "group") return <EventGroup key={`${identity}|${eventKey(item.events[0])}`} item={item} quests={quests} runs={workerRuns} memory={memory} onQuest={onQuest} onOpenFile={onOpenFile} onOpenHistory={onOpenHistory} historyStats={historyStats} connection={connection} onHistoryLoaded={onHistoryLoaded} onInspect={onInspect} />;
      if (item.type === "outgoing") return <article key={`outgoing:${item.value.requestId}`} data-scroll-key={`message:outgoing:${item.value.requestId}`} className="message from-user"><div className="message-meta"><span className="message-avatar">你</span><strong>你</strong><span>{item.value.status === "sending" ? "发送中" : "发送失败"}</span></div><div className="message-body"><MarkdownMessage content={item.value.content} native={native} onInspect={onInspect} />{item.value.status === "failed" && <div className="outgoing-error" role="alert">{item.value.error || "请求未完成"} · 请求 ID：<code>{item.value.requestId}</code><button className="text-button" disabled={!canRetry} onClick={() => onRetry(item.value)}>使用原请求重试</button></div>}</div></article>;
      const message = item.value, meta = messageMetadata(message.metadata);
      return <article key={message.id} data-scroll-key={`message:${message.id}`} className={`message ${message.role === "user" ? "from-user" : "from-agent"}`}><div className="message-meta"><span className="message-avatar">{message.role === "user" ? "你" : "F"}</span><strong>{message.role === "user" ? "你" : "Router"}</strong>{message.role !== "user" && <span className="reply-model" title={[meta.provider_id, meta.model_id].filter(Boolean).join(" / ")}>{meta.source === "router_notice" ? "任务通知" : text(meta.model_id, "模型未记录")}</span>}{meta.execution_mode === "mock" && <span>模拟</span>}{meta.execution_mode === "live" && <span>真实执行</span>}{message.role !== "user" && meta.execution_mode !== "live" && meta.execution_mode !== "mock" && <span>来源未知</span>}<time>{message.created_at ? new Date(message.created_at).toLocaleString() : ""}</time></div><div className="message-body"><MarkdownMessage content={message.content} native={native} onInspect={onInspect} /></div>{message.role !== "user" && <CopyReply content={message.content} onInspect={onInspect} />}</article>;
    })}
    {connected && (active.length > 0 || approvals.length > 0) && <section className="timeline-updates" aria-label="任务与审批更新">
      {(approvals.length > 0 || attention.length > 0) && <div className="timeline-updates-label">需要你处理</div>}
      {approvals.slice(0, 3).map((item) => {
        const quest = quests.find((candidate) => candidate.id === item.quest_id);
        return <div className="timeline-update approval-card" key={text(item.id)}>
          <button className="approval-summary" aria-label={`需要审批 · ${text(item.action_type)} 风险 ${text(item.risk)} · ${text(item.resource_scope)}`} onClick={() => onApproval(text(item.quest_id, ""))}>
            <span><strong>{quest?.title || "关联任务暂未加载"}</strong><small>{statusLabel(item.status, "待审批")} · {toolLabel(item.action_type)} · {riskLabel(item.risk)}</small></span><span aria-hidden="true">›</span>
          </button>
          <div className="approval-scope"><span>{text(item.resource_scope)}</span><small>计划版本 {text(item.plan_revision)}</small></div>
          <AnimatedDetails className="approval-details" onInspect={onInspect} summary={<><span>操作详情</span><Chevron /></>}><dl><dt>参数摘要</dt><dd className="mono break">{text(item.parameter_digest)}</dd><dt>原因</dt><dd>{text(item.reason)}</dd><dt>过期时间</dt><dd>{text(item.expires_at)}</dd></dl></AnimatedDetails>
          <div className="button-row">{(["APPROVED", "REJECTED"] as const).map((decision) => <button key={decision} className={decision === "APPROVED" ? "button primary" : "button"} disabled={approvalsBlocked || typeof item.parameter_digest !== "string" || typeof item.plan_revision !== "number"} onClick={() => onApprovalDecision(item, decision)}>{decision === "APPROVED" ? "批准" : "拒绝"}</button>)}</div>
        </div>;
      })}
      {approvals.length > 3 && <p className="notice">已加载的审批中另有 {approvals.length - 3} 项待处理，审批页按任务筛选 · <button className="text-button" onClick={() => onApproval(text(approvals[0]?.quest_id, ""))}>按任务查看审批</button></p>}
      {attention.slice(0, 3).map(taskRow)}
      {attention.length > 3 && <p className="notice">另有 {attention.length - 3} 项已加载任务需要处理 · <button className="text-button" onClick={() => onQuest("")}>查看全部任务</button></p>}
      {background.length > 0 && <AnimatedDetails className="background-task-summary" onInspect={onInspect} summaryTitle="仅统计已加载的未结束任务，展开查看当前状态。" summary={<><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><circle cx="8" cy="8" r="5.5" /><path d="M8 4.5V8l2.5 1.5" /></svg><span>{background.length} 个后台任务</span><Chevron /></>}>
        <div className="background-task-list">{background.slice(0, 3).map(taskRow)}{background.length > 3 && <p className="notice">另有 {background.length - 3} 项已加载任务 · <button className="text-button" onClick={() => onQuest("")}>查看全部任务</button></p>}</div>
      </AnimatedDetails>}
    </section>}
    {connected && approvalError && <p className="notice error" role="alert">审批状态暂不可用：{approvalError}</p>}
  </div>;
}
