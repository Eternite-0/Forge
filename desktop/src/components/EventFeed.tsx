import { useEffect, useRef, useState } from "react";
import { request, type Connection } from "../api";
import { errorMessage } from "../logic";
import RecallCard from "./RecallCard";

interface EventRecord { id: string; seq: number; event_type: string; quest_id?: string; worker_id?: string; created_at?: string; payload?: unknown }
export default function EventFeed({ connection }: { connection: Connection | null }) {
  const [data, setData] = useState<{ identity: string; items: EventRecord[]; error: string; more: boolean; loading: boolean }>({ identity: "", items: [], error: "", more: false, loading: false });
  const [open, setOpen] = useState(false), [refresh, setRefresh] = useState(0);
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "";
  const cursor = useRef({ identity, after: 0 });
  const visible = data.identity === identity ? data : { items: [], error: "", loading: open && !!connection, more: false };
  useEffect(() => {
    if (!open || !connection) return;
    if (cursor.current.identity !== identity) cursor.current = { identity, after: 0 };
    const pending = new AbortController(); let active = true, inFlight = false;
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      setData((old) => ({ ...(old.identity === identity ? old : { identity, items: [], error: "", more: false }), loading: true }));
      try {
        const page = await request<EventRecord[]>(connection, `/api/events?after=${cursor.current.after}&limit=100`, { signal: AbortSignal.any([pending.signal, AbortSignal.timeout(15000)]) });
        if (!Array.isArray(page)) throw new Error("事件记录格式无效。");
        if (!active) return;
        const items = page.filter((item) => typeof item.id === "string" && Number.isSafeInteger(item.seq));
        cursor.current.after = Math.max(cursor.current.after, ...items.map((item) => item.seq));
        setData((old) => ({ identity, items: [...new Map([...(old.identity === identity ? old.items : []), ...items].map((item) => [item.id, item])).values()].slice(-500), error: "", more: page.length === 100, loading: false }));
      } catch (problem) { if (active) setData((old) => ({ ...old, identity, loading: false, error: errorMessage(problem) })); }
      finally { inFlight = false; }
    };
    void poll(); const timer = window.setInterval(() => void poll(), 6000);
    return () => { active = false; pending.abort(); window.clearInterval(timer); };
  }, [open, identity, connection?.bearer, refresh]);
  return <details className="event-feed" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}><summary>事件流</summary>
    <p className="notice">全部任务的事件，按序号持续读取；最多保留本次查看的最近 500 条。</p>
    {!connection && <p className="notice">连接 Core 后查看。</p>}{visible.error && <p className="notice error" role="alert">{visible.error}</p>}
    {visible.loading && <p className="notice" role="status">正在读取事件…</p>}
    {!visible.loading && !visible.error && !visible.items.length && <p className="notice">暂无事件。</p>}
    <div className="event-feed-list">{visible.items.map((item) => <details key={item.id}><summary><span>{item.event_type}</span><time>{item.created_at ? new Date(item.created_at).toLocaleString() : "时间未记录"}</time></summary><p className="notice">序号 {item.seq}{item.quest_id ? ` · 任务 ${item.quest_id}` : ""}{item.worker_id ? ` · Worker ${item.worker_id}` : ""}</p><pre>{typeof item.payload === "string" ? item.payload : JSON.stringify(item.payload, null, 2)}</pre><RecallCard connection={connection} initialReference={`event:${item.id}`} /></details>)}</div>
    <button className="button" type="button" disabled={!connection || visible.loading} onClick={() => setRefresh((value) => value + 1)}>{visible.more ? "读取下一批事件" : "刷新事件"}</button>
  </details>;
}
