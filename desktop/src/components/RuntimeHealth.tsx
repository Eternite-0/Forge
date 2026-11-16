import { useCallback, useEffect, useRef, useState } from "react";
import DisclosurePopover from "../DisclosurePopover";
import { request, type Connection } from "../api";
import { errorMessage } from "../logic";
import { heartbeatState, type ComponentHeartbeat } from "../runtimeHealth";

export default function RuntimeHealth({ connection, inline = false }: { connection: Connection | null; inline?: boolean }) {
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "";
  const [snapshot, setSnapshot] = useState<{ identity: string; items: ComponentHeartbeat[]; error: string }>({ identity: "", items: [], error: "" });
  const [open, setOpen] = useState(false), [now, setNow] = useState(Date.now() / 1000);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    setOpen(false);
    if (!connection) return;
    let active = true, inFlight = false;
    const pending = new AbortController();
    const poll = async () => {
      setNow(Date.now() / 1000);
      if (inFlight) return;
      inFlight = true;
      try {
        const data = await request<{ components: ComponentHeartbeat[] }>(connection, "/api/runtime/health", { signal: AbortSignal.any([pending.signal, AbortSignal.timeout(15000)]) });
        if (!Array.isArray(data.components)) throw new Error("组件健康记录格式无效。");
        if (active) setSnapshot({ identity, items: data.components, error: "" });
      } catch (error) { if (active) setSnapshot({ identity, items: [], error: errorMessage(error) }); }
      finally { inFlight = false; }
    };
    void poll(); const timer = window.setInterval(() => void poll(), 6000);
    return () => { active = false; pending.abort(); window.clearInterval(timer); };
  }, [identity, connection?.bearer]);
  const data = snapshot.identity === identity ? snapshot : { items: [], error: "" };
  const states = data.items.map((row) => heartbeatState(row, now));
  const healthy = connection && states.length > 0 && !data.error && states.every((state) => state === "healthy");
  const label = !connection ? "Core 未连接" : data.error ? "健康状态不可用" : !states.length ? "暂无心跳记录" : healthy ? "运行正常" : "运行需关注";
  const stateLabels = { healthy: "正常", stale: "心跳已过期", unknown: "心跳时间未知", attention: "需要关注" };
  const content = <div className="runtime-health-content"><strong>组件心跳</strong><p className="notice">最近读取的运行记录；模型和操作权限另行配置。</p>
        {!connection && <p className="notice">连接 Core 后查看。</p>}{data.error && <p className="notice error" role="alert">{data.error}</p>}
        {connection && !data.error && !data.items.length && <p className="notice">尚未读取到组件心跳。</p>}
        {data.items.map((row) => <div className="runtime-health-row" key={`${row.owner || ""}:${row.component}`}><div><strong>{row.component}</strong><small>{row.owner || "运行实例未记录"} · 重启 {row.restart_count ?? "未知"} 次</small></div><span data-health={heartbeatState(row, now)}>{stateLabels[heartbeatState(row, now)]}{row.state !== "ok" ? ` · ${row.state}` : ""}</span></div>)}
      </div>;
  return inline ? <details className="settings-disclosure runtime-health-inline"><summary>查看运行健康</summary>{content}</details> : <div className="runtime-health-anchor">
    <button ref={trigger} type="button" className="runtime-health-toggle" aria-label="查看运行健康" aria-expanded={open} aria-controls="runtime-health-panel" onClick={() => setOpen(!open)}><span className={`status-dot${healthy ? " on" : ""}`} />{label}</button>
    <DisclosurePopover open={open} onClose={close} trigger={trigger} id="runtime-health-panel" label="运行健康" className="runtime-health-panel">
      {content}
    </DisclosurePopover>
  </div>;
}
