import type { Connection } from "../../api";
import type { ArtifactDiff } from "../../artifactHistory";
import type { InlineArtifactMemory } from "../../InlineArtifactDiff";
import type { WorkerRun } from "../../ConversationRuntime";
import { statusLabel, toolLabel } from "../../presentation";
import type { TimelineEvent, TimelineItem } from "../../timeline";

export type Row = Record<string, unknown>;
export type Quest = { id: string; title: string; status: string; version: number };
export const text = (value: unknown, fallback = "未知") => value == null || value === "" ? fallback : String(value);
export type DisplayItem = Exclude<TimelineItem, { type: "event" }> | { type: "group"; questId: string | null; events: TimelineEvent[] };
export type EventViewState = { expanded?: boolean; recordExpanded?: boolean; groupExpanded?: boolean; commandExpanded?: boolean; workerExpanded?: boolean; groupFilesExpanded?: boolean; inline?: InlineArtifactMemory };
export type SubagentViewState = { expanded?: boolean; manual?: boolean; detailsExpanded?: boolean };
export type EventViewMemory = { expansion: Map<string, EventViewState>; commandScroll: Map<string, number>; subagents?: Map<string, SubagentViewState>; processes?: Map<string, { expanded: boolean }> };
export type HistoryProps = { connection?: Connection | null; onHistoryLoaded?: (record: ArtifactDiff) => void; onOpenHistory?: (receiptId: string, path: string) => void; historyStats?: Record<string, { added: number; removed: number }> };
export const eventKey = (event: TimelineEvent) => `${event.kind}:${event.event_id}`;

export function eventView(memory: EventViewMemory, event: TimelineEvent): EventViewState {
  const key = eventKey(event);
  let state = memory.expansion.get(key);
  if (!state) { state = {}; memory.expansion.set(key, state); }
  return state;
}

export function eventTitle(event: TimelineEvent, runs: WorkerRun[] = []): string {
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

export function activitySummary(events: TimelineEvent[], runs: WorkerRun[]): string {
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
  }).filter(Boolean).join(" · ");
}

/** Native transcript summaries name actions; counts and full targets remain in the disclosure. */
export function transcriptSummary(events: TimelineEvent[], runs: WorkerRun[]): string {
  const labels = new Set<string>();
  const actions = events.filter(event => event.kind === "tool_receipt" || event.kind === "context_archive" || event.kind === "semantic_test"
    || /failed|unknown|uncertain|blocked|help_requested|approval\.(requested|revoked)/.test(event.status || ""));
  for (const event of actions.length ? actions : events.slice(-1)) {
    const detail = event.detail as Row | undefined;
    const success = event.kind === "tool_receipt" && event.status === "succeeded" && !(typeof detail?.exit_code === "number" && detail.exit_code !== 0);
    if (!success && event.kind === "tool_receipt") labels.add(activitySummary([event], runs));
    else labels.add(eventTitle(event, runs));
  }
  return [...labels].join("、");
}


export function workerIdentity(event: TimelineEvent, runs: WorkerRun[]): string {
  const run = event.run_id ? runs.find((item) => item.run_id === event.run_id) : undefined;
  if (!event.worker_id && !run) return "";
  return ["Worker", run?.role, event.worker_id, event.run_id, run?.provider_id, run?.model_id].filter(Boolean).join(" · ");
}


export function groupEvents(items: TimelineItem[]): DisplayItem[] {
  const grouped: DisplayItem[] = [];
  for (const item of items) {
    if (item.type !== "event") { grouped.push(item); continue; }
    const last = grouped[grouped.length - 1], questId = item.value.quest_id || null;
    if (last?.type === "group" && last.questId === questId) last.events.push(item.value);
    else grouped.push({ type: "group", questId, events: [item.value] });
  }
  return grouped;
}


