import { useEffect, useRef, useState } from "react";
import { request, type Connection } from "../api";
import { errorMessage } from "../logic";
import { riskLabel, statusLabel, toolLabel } from "../presentation";
import { text, type Quest, type Row } from "../workspaceTypes";
import { TechnicalDetails } from "./WorkspacePanels";

const actions = [{ id: "publish", label: "发布" }, { id: "browser_get", label: "网络读取" }, { id: "mcp_call", label: "MCP 调用" }, { id: "python_unittest", label: "Python 测试" }, { id: "workspace_write", label: "工作区写入" }, { id: "read", label: "读取" }];
const riskFor = (action: string) => action === "read" ? "R0" : action === "workspace_write" ? "R1" : "R2";

function ApprovalItem({ item, connection, blocked, onAction, onDecision }: {
  item: Row; connection: Connection | null; blocked: boolean;
  onAction: (path: string, body?: unknown) => Promise<unknown>;
  onDecision: (item: Row, decision: "APPROVED" | "REJECTED") => void;
}) {
  const [open, setOpen] = useState(false), [refresh, setRefresh] = useState(0);
  const [detail, setDetail] = useState<{ identity: string; value: Row | null; error: string; loading: boolean }>({ identity: "", value: null, error: "", loading: false });
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}|${text(item.id)}|${text(item.status)}|${text(item.parameter_digest)}|${text(item.plan_revision)}|${text(item.consumed_at, "")}|${text(item.expires_at, "")}` : "";
  useEffect(() => {
    if (!open || !connection) return;
    let active = true; const pending = new AbortController();
    setDetail({ identity, value: null, error: "", loading: true });
    void request<Row>(connection, `/api/approvals/${encodeURIComponent(text(item.id))}`, { signal: AbortSignal.any([pending.signal, AbortSignal.timeout(15000)]) })
      .then(value => { if (active) setDetail({ identity, value, error: "", loading: false }); })
      .catch(error => { if (active) setDetail({ identity, value: null, error: errorMessage(error), loading: false }); });
    return () => { active = false; pending.abort(); };
  }, [identity, connection?.bearer, open, refresh]);
  const loaded = detail.identity === identity ? detail : { value: null, error: "", loading: open };
  const current = loaded.value || item;
  const expired = typeof current.expires_at === "string" && Number.isFinite(Date.parse(current.expires_at)) && Date.parse(current.expires_at) <= Date.now();
  const detailReady = detail.identity !== identity ? !open : !loaded.loading && !loaded.error && !!loaded.value;
  const canDecide = !blocked && detailReady && !expired && !current.consumed_at && current.status === "PENDING" && typeof current.parameter_digest === "string" && /^[a-f0-9]{64}$/.test(current.parameter_digest) && typeof current.plan_revision === "number";
  const canRevoke = !blocked && detailReady && !expired && !current.consumed_at && ["PENDING", "APPROVED"].includes(text(current.status));
  return <div className="data-row approval-item">
    <strong>{toolLabel(current.action_type)} · {statusLabel(current.status)}</strong>
    <dl><dt>风险</dt><dd>{riskLabel(current.risk)}</dd><dt>范围</dt><dd className="break">{text(current.resource_scope)}</dd><dt>计划版本</dt><dd>{text(current.plan_revision)}</dd><dt>参数摘要</dt><dd className="mono break">{text(current.parameter_digest)}</dd><dt>原因</dt><dd>{text(current.reason)}</dd><dt>过期时间</dt><dd>{text(current.expires_at)}</dd></dl>
    <div className="button-row">{current.status === "PENDING" && (["APPROVED", "REJECTED"] as const).map(decision => <button type="button" key={decision} className={decision === "APPROVED" ? "button primary" : "button"} disabled={!canDecide} onClick={() => onDecision(current, decision)}>{decision === "APPROVED" ? "批准" : "拒绝"}</button>)}
      {["PENDING", "APPROVED"].includes(text(current.status)) && <button type="button" className="button" disabled={!canRevoke} onClick={async () => { if (!window.confirm(`撤销此审批？\n操作：${toolLabel(current.action_type)}\n范围：${text(current.resource_scope)}\n计划版本：${text(current.plan_revision)}`)) return; await onAction(`/api/approvals/${encodeURIComponent(text(current.id))}/revoke`); setRefresh(value => value + 1); }}>撤销审批</button>}
    </div>
    {expired && <p className="notice">审批已过期，请刷新记录。</p>}
    <details open={open} onToggle={event => setOpen(event.currentTarget.open)}><summary>审批详情</summary>{loaded.loading && <p className="notice" role="status">正在读取…</p>}{loaded.error && <><p className="notice error" role="alert">{loaded.error} 请刷新详情后再操作。</p><button type="button" className="text-button" onClick={() => setRefresh(value => value + 1)}>重试读取审批详情</button></>}{loaded.value && <TechnicalDetails value={loaded.value} />}</details>
  </div>;
}

export default function ApprovalsPanel({ connection, quest, items, blocked, onAction, onDecision }: {
  connection: Connection | null; quest: Quest | undefined; items: Row[]; blocked: boolean;
  onAction: (path: string, body?: unknown) => Promise<unknown>;
  onDecision: (item: Row, decision: "APPROVED" | "REJECTED") => void;
}) {
  const [create, setCreate] = useState(false), [action, setAction] = useState("publish");
  const [scope, setScope] = useState(""), [reason, setReason] = useState(""), [parameters, setParameters] = useState("");
  const [expires, setExpires] = useState(""), [error, setError] = useState("");
  const submitLock = useRef(false);
  const terminal = !quest || ["completed", "failed", "cancelled", "rejected"].includes(quest.status);
  return <section aria-label="任务审批">
    <button type="button" className="button" aria-expanded={create} disabled={!connection || !quest} onClick={() => setCreate(value => !value)}>创建审批请求</button>
    {create && quest && <form className="model-form" onSubmit={async event => {
      event.preventDefault(); if (blocked || terminal || submitLock.current) return;
      try {
        const parsed: unknown = JSON.parse(parameters, (_key, value: unknown) => {
          if (typeof value === "number" && !Number.isFinite(value)) throw new Error("参数中的数值必须是有限数，不能静默改写为 null。");
          return value;
        });
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("参数必须是 JSON 对象。");
        if (!scope.trim() || !reason.trim()) throw new Error("请填写操作范围和原因。");
        const date = expires ? new Date(expires) : null;
        if (date && (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now())) throw new Error("过期时间必须晚于当前时间。");
        if (!window.confirm(`创建审批请求？\n任务：${quest.title}\n操作：${toolLabel(action)}\n风险：${riskFor(action)}\n范围：${scope.trim()}\n参数：${JSON.stringify(parsed)}`)) return;
        setError(""); submitLock.current = true;
        await onAction("/api/approvals", { quest_id: quest.id, action_type: action, parameters: parsed, resource_scope: scope.trim(), risk: riskFor(action), reason: reason.trim(), ...(date ? { expires_at: date.toISOString() } : {}) });
      } catch (error) { setError(errorMessage(error)); } finally { submitLock.current = false; }
    }}>
      <label className="field-label">操作<select value={action} onChange={event => setAction(event.target.value)}>{actions.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      <p className="notice">风险 {riskFor(action)} · 创建请求后，需要单独作出审批决定。</p>
      <label className="field-label">操作范围<input value={scope} maxLength={4096} onChange={event => setScope(event.target.value)} /></label>
      <label className="field-label">精确参数（JSON）<textarea value={parameters} placeholder="填写此次操作的完整参数对象" onChange={event => setParameters(event.target.value)} /></label>
      <label className="field-label">原因<input value={reason} maxLength={2000} onChange={event => setReason(event.target.value)} /></label>
      <label className="field-label">过期时间（可选）<input type="datetime-local" value={expires} onChange={event => setExpires(event.target.value)} /></label>
      {error && <p className="notice error" role="alert">{error}</p>}
      <button type="submit" className="button primary" disabled={blocked || terminal || !parameters.trim() || !scope.trim() || !reason.trim()}>提交审批请求</button>
    </form>}
    <div className="list">{items.map((item, index) => <ApprovalItem key={text(item.id, String(index))} item={item} connection={connection} blocked={blocked} onAction={onAction} onDecision={onDecision} />)}</div>
  </section>;
}
