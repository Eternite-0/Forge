import { useState } from "react";
import { View, Row, VIEW_NAMES, SETTING_VIEWS, TASK_VIEWS, text, rows } from "../workspaceTypes";
import type { SettingId } from "../SettingsView";
import { relativeFilePath } from "../workspaceFiles";
import { taskProgress } from "../taskProgress";
import useWorkspaceState from "./useWorkspaceState";
import useConversationViewport from "./useConversationViewport";
import useWorkspacePreview from "./useWorkspacePreview";
import useForgeConnection from "./useForgeConnection";
import useConversationFeed from "./useConversationFeed";
import useWorkspaceInspector from "./useWorkspaceInspector";
import useWorkspaceOperations from "./useWorkspaceOperations";
import useConversationSend from "./useConversationSend";

/** Assemble state and behavior; presentation stays in workspace components. */
export default function useWorkspaceController() {
  const state = useWorkspaceState();
  const viewport = useConversationViewport(state);
  const preview = useWorkspacePreview({ ...state, ...viewport });
  const session = useForgeConnection(state);
  const feed = useConversationFeed({ ...state, ...preview });
  const inspector = useWorkspaceInspector({ ...state, ...feed });
  const operations = useWorkspaceOperations({ ...state, ...session });
  const conversation = useConversationSend({ ...state, ...feed });
  const workspace = { ...state, ...viewport, ...preview, ...session, ...feed, ...inspector, ...operations, ...conversation };
  const { connection, view, setView, setSettingsOpen, sidebarCollapsed, setSetting, messages, timelineEvents, outgoing, sending, selectedId, setSelectedId, setActionError, coreInfo, visiblePanel, visiblePlan, previewOpen, previewExpanded, perform, setSuggestedRouterModel } = workspace;
  const plan = visiblePlan.value as Row | null;
  const panelItems = (view === "memory" || view === "approvals") && visiblePanel.value && typeof visiblePanel.value === "object" && !Array.isArray(visiblePanel.value) ? rows((visiblePanel.value as Row).items) :
    view === "activity" && visiblePanel.value && typeof visiblePanel.value === "object" && !Array.isArray(visiblePanel.value) ? rows((visiblePanel.value as Row).items ?? (visiblePanel.value as Row).events) : rows(visiblePanel.value);
  const title = VIEW_NAMES[view];
  const taskView = TASK_VIEWS.includes(view);
  const [taskDetailIntent, setTaskDetailIntent] = useState<{ id: string; identity: string } | null>(null);
  const taskDetailRequest = taskDetailIntent?.identity === workspace.connectionKey ? taskDetailIntent : null;
  const openTaskView = (destination: View, id?: string) => {
    setTaskDetailIntent(destination === "quests" ? { id: id || "", identity: workspace.connectionKey } : null);
    setSettingsOpen(false); setSelectedId(id ?? selectedId); setView(destination); setActionError("");
  };
  const openConversation = () => { setTaskDetailIntent(null); setSettingsOpen(false); setView("chat"); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="消息内容"]')?.focus()); };
  const currentWorkspace = connection ? text(coreInfo.workspace_root, connection.workspace_id) : "尚未连接";
  const currentWorkspaceName = currentWorkspace.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "工作空间";
  const routerModel = !connection ? "尚未连接" : coreInfo.provider_configured === false ? "未配置模型" : text(coreInfo.router_model_id, "模型信息未读取");
  const routerStatus = !connection ? "未连接 Core" : sending ? "正在回复" : coreInfo.provider_configured === false ? "供应商未配置" : "Core 已连接";
  const selectSetting = (id: SettingId) => { setSettingsOpen(true); setSuggestedRouterModel(null); setSetting(id); setView(SETTING_VIEWS[id] || "settings"); setActionError(""); };
  const decideApproval = (item: Row, decision: "APPROVED" | "REJECTED") => {
    if (window.confirm(`${decision === "APPROVED" ? "批准" : "拒绝"}此操作？\n风险：${text(item.risk)}\n范围：${text(item.resource_scope)}\n计划版本：${text(item.plan_revision)}\n参数摘要：${text(item.parameter_digest)}`)) void perform(`/api/approvals/${encodeURIComponent(text(item.id))}/decision`, { decision, expected_digest: item.parameter_digest, expected_plan_revision: item.plan_revision });
  };
  const compactNavigation = sidebarCollapsed || previewOpen && previewExpanded;
  const hasTaskUpdates = !!connection && (workspace.pendingApprovals.length > 0 || taskProgress(workspace.quests, timelineEvents, messages).length > 0);
  const home = !messages.length && !timelineEvents.length && !outgoing.length && !hasTaskUpdates;
  const lastRequest = [...messages].reverse().find(message => message.role === "user");
  const conversationTitle = lastRequest?.content?.replace(/\s+/g, " ").trim().slice(0, 180) || undefined;
  const conversationFiles = [...new Set(timelineEvents.flatMap((event) => {
    const detail = event.detail && typeof event.detail === "object" ? event.detail as Row : {};
    if (event.kind !== "tool_receipt" || event.status !== "succeeded" || event.execution_mode !== "live" || typeof detail.exit_code === "number" && detail.exit_code !== 0 || !["file_write", "file_revise"].includes(text(detail.tool, "")) || typeof detail.path !== "string") return [];
    try { return [relativeFilePath(detail.path)]; } catch { return []; }
  }))];
  return { ...workspace, plan, panelItems, title, taskView, taskDetailRequest, openTaskView, openConversation, currentWorkspace, currentWorkspaceName, routerModel, routerStatus, selectSetting, decideApproval, compactNavigation, home, conversationTitle, conversationFiles };
}
export type WorkspaceController = ReturnType<typeof useWorkspaceController>;

