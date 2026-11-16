import { useEffect, useRef, useState } from "react";
import { request, type Connection } from "../api";
import type { WorkerRun } from "../ConversationRuntime";
import { errorMessage } from "../logic";
import { statusLabel } from "../presentation";
import { teamReassignmentProposal, userQuestTransitions } from "../questControls";
import { asRecord, rows, text, type Quest, type Row } from "../workspaceTypes";
import { TechnicalDetails } from "./WorkspacePanels";

interface QuestData { identity: string; history: Row[]; teams: Row[]; workers: Row[]; preview: Row[]; loading: boolean; error: string }

/** Advanced task operations remain attached to the conversation's task drawer. */
export default function QuestControls({ connection, quest, plan, runs, blocked, onAction }: {
  connection: Connection | null; quest: Quest; plan: Row | null; runs: WorkerRun[]; blocked: boolean;
  onAction: (path: string, body?: unknown) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false), [refresh, setRefresh] = useState(0);
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}|${quest.id}` : "";
  const current = useRef(identity); current.current = identity;
  const [data, setData] = useState<QuestData>({ identity: "", history: [], teams: [], workers: [], preview: [], loading: false, error: "" });
  const [error, setError] = useState("");
  const [role, setRole] = useState(""), [model, setModel] = useState("");
  const [runId, setRunId] = useState(""), [reason, setReason] = useState(""), [teamId, setTeamId] = useState("");
  const [strategy, setStrategy] = useState("greedy");
  const [remaining, setRemaining] = useState(false), [expansion, setExpansion] = useState("");
  const visible = data.identity === identity ? data : { history: [], teams: [], workers: [], preview: [], loading: open, error: "" };
  const questPath = `/api/quests/${encodeURIComponent(quest.id)}`;
  const terminal = ["completed", "failed", "cancelled", "rejected"].includes(quest.status);
  const disabled = blocked || !connection;
  const canReplan = !!plan && ["approved", "paused"].includes(text(plan.status)) && ["approved", "queued", "paused", "blocked", "needs_input", "running"].includes(quest.status);
  const currentRuns = runs.filter((run) => run.quest_id === quest.id);
  useEffect(() => {
    if (!open || !connection) return;
    const pending = new AbortController(); let active = true;
    setData({ identity, history: [], teams: [], workers: [], preview: [], loading: true, error: "" });
    const read = (path: string) => request<unknown>(connection, path, { signal: AbortSignal.any([pending.signal, AbortSignal.timeout(15000)]) }).then(value => ({ value, error: "" })).catch(error => ({ value: null, error: errorMessage(error) }));
    void Promise.all([read(`${questPath}/plan/history`), read(`${questPath}/teams`), read(questPath), read("/api/team/preview")]).then(([history, teams, detail, preview]) => {
      if (!active || current.current !== identity) return;
      setData({ identity, history: rows(history.value), teams: rows(teams.value), workers: rows(asRecord(detail.value).workers), preview: rows(preview.value), loading: false,
        error: [history.error, teams.error, detail.error, preview.error].filter(Boolean).join("；") });
    });
    return () => { active = false; pending.abort(); };
  }, [identity, connection?.bearer, open, quest.version, refresh]);
  const act = async (label: string, path: string, body?: unknown) => {
    if (disabled || !window.confirm(`${label}？\n任务：${quest.title}\n任务版本：${quest.version}`)) return;
    const submittedIdentity = identity;
    setError("");
    await onAction(path, body);
    if (current.current === submittedIdentity) setRefresh(value => value + 1);
  };
  const version = { expected_version: quest.version };
  const planActions = [
    ...(plan?.status === "approved" && ["approved", "queued", "running"].includes(quest.status) ? [{ action: "pause", label: "暂停计划" }] : []),
    ...(plan?.status === "paused" && quest.status === "paused" ? [{ action: "resume", label: "恢复计划" }] : []),
    ...(plan && ["proposed", "approved", "paused"].includes(text(plan.status)) && !terminal && quest.status !== "reviewing" ? [{ action: "cancel", label: "取消计划" }] : []),
    ...(plan && quest.status === "failed" ? [{ action: "retry", label: "为失败任务创建重试提案" }] : []),
  ];
  return <details className="settings-disclosure quest-controls" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>任务操作与历史</summary>
    <div className="settings-disclosure-body">
      <div className="button-row">{planActions.map(item => <button type="button" className="button" key={item.action} disabled={disabled} onClick={() => void act(item.label, `${questPath}/plan/control`, { ...version, action: item.action })}>{item.label}</button>)}</div>
      {!terminal && <label className="field-label">任务优先级<select value={typeof quest.priority === "number" ? quest.priority : ""} disabled={disabled} onChange={event => void act("调整优先级", `${questPath}/priority`, { ...version, priority: Number(event.target.value) })}><option value="" disabled>未记录</option>{[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}{value === 1 ? " · 最高" : value === 5 ? " · 最低" : ""}</option>)}</select></label>}
      {userQuestTransitions(quest.status).length > 0 && <label className="field-label">调整任务状态<select value="" disabled={disabled} onChange={event => void act(`将任务设为${statusLabel(event.target.value)}`, `${questPath}/transition`, { ...version, status: event.target.value })}><option value="">选择状态…</option>{userQuestTransitions(quest.status).map(value => <option value={value} key={value}>{statusLabel(value)}</option>)}</select></label>}
      {!terminal && <form onSubmit={event => { event.preventDefault(); void act(remaining ? "重新生成剩余计划" : "生成计划", `${questPath}/plan/generate`, { ...version, remaining_dag: remaining, ...(remaining && expansion.trim() ? { expansion_parent: expansion.trim() } : {}) }); }}>
        <label className="check-label"><input type="checkbox" checked={remaining} onChange={event => setRemaining(event.target.checked)} />保留已完成节点，重新规划剩余工作</label>
        {remaining && <label className="field-label">展开节点（可选）<input value={expansion} onChange={event => setExpansion(event.target.value)} placeholder="计划中的节点键" /></label>}
        <button type="submit" className="button" disabled={disabled || (remaining ? !canReplan : !["proposed", "awaiting_approval"].includes(quest.status))}>生成计划</button>
      </form>}
      {visible.loading && <p className="notice" role="status">正在读取任务历史…</p>}
      {visible.error && <p className="notice error" role="alert">{visible.error}</p>}
      <section><h3>计划历史</h3>{!visible.loading && !visible.history.length && <p className="notice">暂无计划历史</p>}{visible.history.map(item => <div className="plan-node" key={text(item.id)}><strong>版本 {text(item.revision)} · {statusLabel(item.status)}</strong><small>{text(item.created_at)}</small><TechnicalDetails value={item} /></div>)}</section>
      <section><h3>任务团队</h3>{visible.teams.map(item => <div className="plan-node" key={text(item.id)}><strong>{statusLabel(item.status)} · 计划 {text(item.plan_revision)}</strong><small>{rows(asRecord(item.body).members).map(member => `${text(member.role_id)} / ${text(member.model_id)}`).join("、")}</small><TechnicalDetails value={item} /></div>)}
        {!terminal && <form onSubmit={event => { event.preventDefault(); try { const team = visible.teams.find(item => item.id === teamId); if (!team || !currentRuns.some(run => run.run_id === runId) || !reason.trim()) throw new Error("请选择已有团队、执行记录并说明原因。"); const proposal = teamReassignmentProposal(team.body, strategy); void act("保留角色、权限和预算，提出团队重新分配", `${questPath}/teams/reassign`, { ...version, run_id: runId, reason: reason.trim(), proposal }); } catch (error) { setError(errorMessage(error)); } }}>
          <label className="field-label">沿用的团队合约<select value={teamId} onChange={event => setTeamId(event.target.value)}><option value="">选择已有团队…</option>{visible.teams.map(item => <option key={text(item.id)} value={text(item.id)}>{text(item.id)} · {statusLabel(item.status)}</option>)}</select></label>
          <label className="field-label">需要重新分配的执行<select value={runId} onChange={event => setRunId(event.target.value)}><option value="">选择执行记录…</option>{currentRuns.map(run => <option key={run.run_id} value={run.run_id}>{run.role || run.run_id} · {statusLabel(run.state)}</option>)}</select></label>
          <label className="field-label">重新分配原因<input value={reason} maxLength={1000} onChange={event => setReason(event.target.value)} /></label>
          <label className="field-label">选型策略<select value={strategy} onChange={event => setStrategy(event.target.value)}><option value="greedy">综合能力</option><option value="single_model">单一模型</option><option value="coverage">能力覆盖</option><option value="complementarity">互补协作</option></select></label>
          <button type="submit" className="button" disabled={disabled || !teamId || !runId || !reason.trim()}>提出重新分配</button>
        </form>}
      </section>
      <details><summary>模型候选预览</summary><p className="notice">按模型库能力匹配的候选，仅供参考。</p>{visible.preview.map((item, index) => <div className="plan-node" key={text(item.role_id, String(index))}><strong>{text(item.role_name, text(item.role_id))}</strong>{rows(item.model_candidates).map((model, index) => <small key={index}>{text(model.provider)} / {text(model.model_id)} · 匹配 {text(model.fit_score)}</small>)}</div>)}</details>
      <details><summary>子智能体提案</summary>{visible.workers.map(item => <div className="plan-node" key={text(item.id)}><strong>{text(item.role)} · {statusLabel(item.status)}</strong><small>{text(item.model_id, "模型未指定")}</small></div>)}
        {!terminal && <form onSubmit={event => { event.preventDefault(); if (!role.trim()) return; const query = new URLSearchParams({ role: role.trim(), ...(model.trim() ? { model_id: model.trim() } : {}) }); void act("创建子智能体提案", `${questPath}/workers?${query}`); }}>
          <label className="field-label">角色<input value={role} maxLength={100} onChange={event => setRole(event.target.value)} /></label><label className="field-label">模型 ID（可选）<input value={model} maxLength={512} onChange={event => setModel(event.target.value)} /></label><button type="submit" className="button" disabled={disabled || !role.trim()}>创建提案</button>
        </form>}
      </details>
      {error && <p className="notice error" role="alert">{error}</p>}
    </div>
  </details>;
}
