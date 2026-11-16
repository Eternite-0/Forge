/** Display labels only. Backend values remain the source of lifecycle and permission decisions. */

/** API keys route task actions; they are never useful as human-facing titles. */
export function questDisplayTitle(title: unknown, id?: unknown, goal?: unknown): string {
  const usable = (value: unknown) => {
    const firstLine = typeof value === "string" ? value.trim().split(/\r?\n/, 1)[0]?.trim() : "";
    if (!firstLine || firstLine === id || /^(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/i.test(firstLine)) return "";
    return firstLine;
  };
  const savedTitle = usable(title);
  if (savedTitle) return savedTitle;
  const fallback = usable(goal);
  const characters = Array.from(fallback);
  return characters.length > 72 ? characters.slice(0, 71).join("") + "…" : fallback || "未命名任务";
}

export function displayActivityTime(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "时间未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString();
}

export function taskActivityRole(role: unknown, taskId?: unknown): string {
  if (typeof role !== "string" || !role.trim()) return "执行成员";
  const label = role.trim();
  const withoutTask = typeof taskId === "string" && taskId && label.startsWith(taskId) &&
    (label.length === taskId.length || /^[\s:：/·]/.test(label.slice(taskId.length)))
    ? label.slice(taskId.length).replace(/^[\s:：/·]+/, "") : label;
  const resolved = questDisplayTitle(withoutTask, taskId);
  return resolved === "未命名任务" ? "执行成员" : resolved;
}

const TASK_EVENT_LABELS = new Map([
  ["quest.updated", "任务已更新"], ["quest.proposed", "任务已提出"],
  ["quest.status_changed", "任务状态已更新"], ["quest.priority_changed", "优先级已调整"],
  ["quest.plan_proposed", "计划已提出"], ["quest.plan_approved", "计划已批准"],
  ["quest.plan_rejected", "计划未获批准"], ["quest.replan_requested", "计划待调整"],
  ["quest.retry_proposed", "已提出重试"], ["approval.requested", "等待审批"],
  ["approval.decided", "已记录审批决定"], ["approval.revoked", "审批已撤销"],
  ["approval.expired", "审批已过期"], ["worker.started", "开始执行"],
  ["worker.progress", "执行进度更新"], ["worker.result", "执行结果已返回"],
  ["worker.failed", "执行失败"], ["worker.blocked", "执行遇到阻塞"],
  ["worker.help_requested", "执行需要协助"], ["worker.review_requested", "等待复核"],
  ["worker.artifact_ready", "交付物已就绪"], ["task.verified", "任务已验证"],
  ["task.skipped", "已跳过"], ["tool.receipt_recorded", "工具操作已记录"],
  ["tool.execution_uncertain", "工具执行结果未知"],
]);

export function taskEventLabel(value: unknown): string {
  return typeof value === "string" ? TASK_EVENT_LABELS.get(value) || "运行记录" : "运行记录";
}

const STATUS_LABELS = new Map<string, string>([
  // QuestStatus (domain.py), immutable plan revisions and team proposals.
  ["proposed", "已提案"],
  ["awaiting_approval", "待审批"],
  ["approved", "已批准"],
  ["queued", "排队中"],
  ["running", "运行中"],
  ["blocked", "已阻塞"],
  ["paused", "已暂停"],
  ["needs_input", "需要补充信息"],
  ["reviewing", "复核中"],
  ["completed", "已完成"],
  ["failed", "失败"],
  ["cancelled", "已取消"],
  ["rejected", "已拒绝"],
  ["revoked", "已撤销"],
  ["superseded", "已被替代"],
  // Worker runs and verification/tool records use distinct success and review states.
  ["waiting", "等待中"],
  ["review_pending", "待验证"],
  ["succeeded", "执行成功"],
  ["pending", "待处理"],
  ["checking", "验证中"],
  ["verified", "验证通过"],
  ["skipped", "已跳过"],
  ["prepared", "已准备"],
  ["executing", "执行中"],
  ["uncertain", "结果未知"],
  ["passed", "通过"],
  ["unknown", "未知"],
  // Action approvals are uppercase in the API; PENDING specifically awaits a user decision.
  ["PENDING", "待审批"],
  ["expired", "已过期"],
]);

const RISK_LABELS = new Map<string, string>([
  // governance.ACTION_RISKS: read / workspace_write / external action / denied deletion.
  ["r0", "R0 · 只读"],
  ["r1", "R1 · 工作区写入"],
  ["r2", "R2 · 外部操作"],
  ["r3", "R3 · 禁止操作"],
]);

const TOOL_LABELS = new Map<string, string>([
  // Fixed tool_domain.CONTRACTS; these names do not imply unrestricted shell or web search.
  ["worker_read_action_receipt", "读取上游执行回执"],
  ["file_read", "读取文件"],
  ["file_write", "创建文件"],
  ["file_revise", "修订文件"],
  ["directory_list", "列出目录"],
  ["directory_create", "创建目录"],
  ["shell_inspect", "固定命令诊断"],
  ["python_compile", "检查 Python 语法"],
  ["numeric_ast", "数值计算"],
  ["git_status", "读取 Git 状态"],
  ["browser_get", "读取网页"],
  ["mcp_call", "调用 MCP 工具"],
  // Governance action types can also appear in approval summaries.
  ["read", "读取"],
  ["workspace_write", "工作区写入"],
  ["publish", "发布"],
  ["delete", "删除"],
]);

const CAPABILITY_LABELS = new Map<string, string>([
  // domain.CAPABILITY_NAMES; unknown future capabilities retain their original names.
  ["mathematics", "数学"],
  ["coding", "编程"],
  ["architecture", "架构"],
  ["research", "研究"],
  ["planning", "规划"],
  ["design", "设计"],
  ["vision", "视觉"],
  ["writing", "写作"],
  ["tool_calling", "工具调用"],
  ["instruction_following", "指令遵循"],
]);

function mappedLabel(value: unknown, labels: ReadonlyMap<string, string>, fallback: string): string {
  if (value == null || value === "") return fallback;
  const original = String(value);
  return labels.get(original) ?? labels.get(original.toLowerCase()) ?? original;
}

export function statusLabel(value: unknown, fallback = "未知"): string {
  return mappedLabel(value, STATUS_LABELS, fallback);
}

export function riskLabel(value: unknown, fallback = "未知"): string {
  return mappedLabel(value, RISK_LABELS, fallback);
}

export function toolLabel(value: unknown, fallback = "未知"): string {
  return mappedLabel(value, TOOL_LABELS, fallback);
}

export function capabilityLabel(value: unknown, fallback = "未知"): string {
  return mappedLabel(value, CAPABILITY_LABELS, fallback);
}

export function namesLabel(
  value: unknown,
  label: (item: unknown, fallback?: string) => string = toolLabel,
  fallback = "未知",
): string {
  if (!Array.isArray(value)) return label(value, fallback);
  return value.length ? value.map((item) => label(item, fallback)).join("、") : "无";
}

// Exact QuestStatus terminal set from ALLOWED_QUEST_TRANSITIONS, not Worker/tool success states.
const CLOSED_QUEST_STATES = new Set(["completed", "failed", "cancelled", "rejected"]);

export function isClosedQuest(status: string): boolean {
  return CLOSED_QUEST_STATES.has(status);
}

// An attention badge summarizes known approval, blocker and clarification states only.
// A deliberate pause, an active review or a proposal alone does not establish a user action.
const ATTENTION_QUEST_STATES = new Set(["awaiting_approval", "blocked", "needs_input"]);

export function needsQuestAttention(status: string): boolean {
  return ATTENTION_QUEST_STATES.has(status);
}
