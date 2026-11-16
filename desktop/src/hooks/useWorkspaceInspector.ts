import { useCallback, useEffect, useRef } from "react";
import { ApiError, request } from "../api";
import { errorMessage } from "../logic";
import { Row, Quest, EMPTY, text, rows } from "../workspaceTypes";
import type { WorkspaceState } from "./useWorkspaceState";
import useConversationFeed from "./useConversationFeed";

export default function useWorkspaceInspector(workspace: WorkspaceState & ReturnType<typeof useConversationFeed>) {
  const mutationPending = useRef(false);
  const { connection, maintenanceSubmitting, view, refreshing, setRefreshing, quests, setQuests, selectedId, panel, setPanel, setPanelContext, setQuestPlan, planRefresh, setPlanRefresh, planContext, setPlanContext, setGrants, setGrantContext, setGrantScope, setWorkspaceRoot, setActionError, actionBusy, setActionBusy, actionNotice, setActionNotice, memorySearch, memoryCursor, setMemoryCursor, activityCursor, setActivityCursor, approvalCursor, setApprovalCursor, setModelEditor, setSuggestedRouterModel, refreshSerial, guard, selected, contextKey, questContextKey, writesBlocked, refreshConversation } = workspace;
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
  useEffect(() => { setSuggestedRouterModel(null); }, [connection?.generation]);
  useEffect(() => {
    if (!connection || !selectedId || quests.some((quest) => quest.id === selectedId)) return;
    const ticket = guard.start("selected-quest");
    request<{quest: Quest}>(connection, `/api/quests/${encodeURIComponent(selectedId)}`)
      .then(({ quest }) => { if (guard.accepts(ticket)) setQuests((old) => old.some((item) => item.id === quest.id) ? old : [quest, ...old]); })
      .catch((error) => { if (guard.accepts(ticket)) setActionError(errorMessage(error)); });
  }, [connection, selectedId, quests, guard]);
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
  const perform = async (path: string, body?: unknown) => {
    if (!connection || actionBusy || mutationPending.current || writesBlocked || maintenanceSubmitting.current) return;
    mutationPending.current = true;
    const ticket = guard.start("action");
    setActionBusy(true); setActionError(""); setActionNotice("");
    try {
      await request(connection, path, { method: "POST", ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      if (!guard.accepts(ticket)) return;
      setActionNotice(path.includes("/decision") ? "审批决定已记录" : path.includes("/approvals/") && path.endsWith("/revoke") ? "审批已撤销" : path === "/api/approvals" ? "审批请求已创建" : path.includes("/plan/approve") ? "计划批准已记录" : path.includes("/teams/approve") ? "团队批准已记录" : path.includes("/grants") ? "工作区权限已更新" : path.includes("/memory/") ? "记忆已撤销" : path === "/api/settings" ? "运行配置已保存，重启 Core 后生效" : "更改已保存");
      await refreshConversation(); await refreshPanel();
      if (!guard.accepts(ticket)) return;
      if (path.includes("/grants")) await refreshGrants();
      if (selectedId && (view === "quests" || view === "teams")) {
        try {
          const value = await request<Row>(connection, `/api/quests/${encodeURIComponent(selectedId)}/plan`);
          if (guard.accepts(ticket)) setQuestPlan({ loading: false, error: "", value });
        } catch (error) {
          if (guard.accepts(ticket)) setQuestPlan({ loading: false, error: error instanceof ApiError && error.status === 404 ? "" : errorMessage(error), value: null });
        }
      }
    } catch (error) { if (guard.accepts(ticket)) setActionError(errorMessage(error)); }
    finally { mutationPending.current = false; if (guard.accepts(ticket)) setActionBusy(false); }
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
    if (!connection || actionBusy || mutationPending.current || writesBlocked || maintenanceSubmitting.current) return;
    mutationPending.current = true;
    const ticket = guard.start("model");
    setActionBusy(true); setActionError("");
    try {
      await request(connection, path, { method, body: JSON.stringify(body) });
      if (!guard.accepts(ticket)) return;
      setModelEditor(null);
      await refreshPanel();
    } catch (error) { if (guard.accepts(ticket)) setActionError(errorMessage(error)); }
    finally { mutationPending.current = false; if (guard.accepts(ticket)) setActionBusy(false); }
  };
  return { refreshPanel, refreshGrants, refreshCurrentView, loadPanelPage, perform, openModel, saveModel };
}

