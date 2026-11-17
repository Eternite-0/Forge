import type { WorkerRun } from "./ConversationRuntime";

export interface AgentMilestone { event_id: string; event_type: string; title: string; created_at: string; seq: number }
export interface ConversationAgent extends WorkerRun {
  task_title: string; task_objective: string;
  execution_mode: string; lease_status: "active" | "expired" | "none";
  delegation_state: string; parent_message_id: string | null;
  source: { kind: "message" | "trigger"; id: string };
  outcome: { kind: string; label: string }; milestones: AgentMilestone[];
}
export interface AgentPage { phase?: "all" | "active" | "terminal"; conversation_id: string; workspace_id?: string; items: ConversationAgent[]; has_more: boolean; next_before: string | null }
export interface AgentDetail { conversation_id: string; workspace_id?: string; item: ConversationAgent }

export function scopedAgents(page: AgentPage, conversation = "main", workspace?: string): ConversationAgent[] {
  if (workspace && page.workspace_id !== workspace) throw new Error("子智能体记录不属于当前工作空间。");
  if (page.conversation_id !== conversation || !Array.isArray(page.items)) throw new Error("子智能体记录不属于当前对话。");
  return [...new Map(page.items.filter(item => item && typeof item.run_id === "string" && item.run_id && item.source && ["message", "trigger"].includes(item.source.kind)).map(item => [item.run_id, item])).values()];
}
const roles: Record<string, string> = { architect: "架构", coding: "编码", experiment: "实验", reviewer: "审查", math: "数学", research: "研究", researcher: "研究" };
export function agentRole(role?: string): string {
  if (!role) return "子智能体";
  if (roles[role]) return roles[role];
  const numbered = /^([a-z]+)_(\d+)$/.exec(role);
  return numbered && roles[numbered[1]] ? `${roles[numbered[1]]} ${numbered[2]}` : role;
}
export function agentState(run: WorkerRun): string | null {
  if (run.last_confirmed && run.state === "running" && run.lease_status === "expired") return "上次确认：租约已过期 · 等待恢复";
  if (run.last_confirmed && run.state !== "unknown") {
    const labels: Record<string, string> = { queued: "等待执行", running: "运行中", paused: "已暂停", blocked: "受阻", review_pending: "等待验证" };
    return `上次确认：${labels[run.state || ""] || "状态待确认"}`;
  }
  if (run.state === "running" && run.lease_status === "expired") return "租约已过期 · 等待恢复";
  return null;
}

const outcomes: Record<string, string> = { queued: "等待执行", running: "正在处理委派任务", paused: "已暂停", blocked: "受阻，等待处理", awaiting_review: "等待独立验证", failed: "执行失败", cancelled: "已取消", verified: "子智能体结果已验证", lease_expired: "租约已过期，等待恢复", unknown: "结果暂不可用" };
export const agentOutcome = (kind?: string) => outcomes[kind || ""] || "尚无已记录结果";
const milestones: Record<string, string> = { "task.verified": "结果已通过验证", "worker.proposed": "提出子任务", "worker.started": "开始执行", "worker.blocked": "遇到阻塞", "worker.failed": "执行失败", "worker.result": "提交结果，等待验证", "worker.retry": "重新尝试", "worker.help_requested": "请求协助", "worker.pause": "暂停执行", "worker.resume": "恢复执行", "worker.cancel": "取消执行", "worker.progress": "记录工作进展", "worker.model_response_rejected": "结果需要修正" };
export const agentMilestone = (kind: string) => milestones[kind] || "更新运行记录";
export const agentActive = (run: ConversationAgent) => !["failed", "cancelled", "succeeded"].includes(run.state || "");

/** Assignment identifies a specialist; the parent task title stays in detail. */
export const agentTask = (run: Pick<ConversationAgent, "task_objective" | "task_title">) =>
  (run.task_objective?.trim() || run.task_title?.trim() || "已委派任务").replace(/\s+/g, " ");

export function validateAgentPhase(page: AgentPage, phase: "active" | "terminal" | "all"): void {
  if (phase === "all" || page.phase === undefined) return;
  if (page.phase !== phase) throw new Error("子智能体分页范围不一致。");
  const allowed = phase === "active" ? ["queued", "running", "paused", "blocked", "review_pending"] : ["succeeded", "failed", "cancelled"];
  if (page.items.some(item => !allowed.includes(item.state || ""))) throw new Error("子智能体分页状态不一致。");
}

/** Preserve stable order within each bucket, while newly admitted active work stays visible. */
export const orderedAgents = (items: ConversationAgent[]) => [
  ...items.filter(agentActive), ...items.filter(item => !agentActive(item)),
];

/** Only fresh, unexpired execution observations can drive live UI claims. */
export const agentRunning = (run: WorkerRun) => run.state === "running" && !run.last_confirmed && run.lease_status !== "expired";
