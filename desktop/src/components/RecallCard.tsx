import { useEffect, useRef, useState } from "react";
import { request, type Connection } from "../api";
import { errorMessage } from "../logic";

interface RecalledObject {
  ref: string; kind: string; summary: Record<string, unknown>; source_hash: string;
  content: string; offset: number; next_offset: number; complete: boolean;
  relations: { ref: string; relation: string }[]; relations_omitted: boolean;
  next_relation_offset?: number | null;
}

/** A bounded, read-only chain of historical references, kept in the current conversation. */
export default function RecallCard({ connection, initialReference = "" }: { connection: Connection | null; initialReference?: string }) {
  const [reference, setReference] = useState(initialReference), [open, setOpen] = useState(false);
  const [data, setData] = useState<RecalledObject | null>(null), [loading, setLoading] = useState(false), [error, setError] = useState("");
  const current = useRef<AbortController | null>(null), serial = useRef(0);
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "";
  useEffect(() => { serial.current++; current.current?.abort(); setData(null); setError(""); setLoading(false); setReference(initialReference); return () => current.current?.abort(); }, [identity, initialReference]);
  const read = async (ref: string, offset = 0, relationOffset = 0) => {
    if (!connection || !ref.trim()) return;
    current.current?.abort(); const pending = new AbortController(); current.current = pending;
    const ticket = ++serial.current; setLoading(true); setError(""); setReference(ref);
    try {
      const value = await request<RecalledObject>(connection, `/api/recall?reference=${encodeURIComponent(ref.trim())}&offset=${offset}&relation_offset=${relationOffset}`, { signal: AbortSignal.any([pending.signal, AbortSignal.timeout(15000)]) });
      if (ticket === serial.current && !pending.signal.aborted) setData(value);
    } catch (problem) { if (ticket === serial.current && !pending.signal.aborted) { setData(null); setError(errorMessage(problem)); } }
    finally { if (ticket === serial.current && !pending.signal.aborted) setLoading(false); }
  };
  return <details className="recall-card" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>引用与来源 <span>Chain of Recall</span></summary>
    <div className="recall-body"><form className="search" onSubmit={(event) => { event.preventDefault(); void read(reference); }}><input aria-label="查询历史引用" value={reference} onChange={(event) => setReference(event.target.value)} placeholder="例如 quest:任务 ID 或 message:消息 ID" /><button type="submit" className="button" disabled={!connection || loading || !reference.trim()}>查询</button></form>
      {!connection && <p className="notice">连接 Core 后查看来源。</p>}{loading && <p className="notice" role="status">正在读取来源…</p>}{error && <p className="notice error" role="alert">{error}</p>}
      {data && <div className="recall-result"><header><strong>{data.kind}</strong><code>{data.ref}</code></header>
        <p className="notice">历史引用只提供上下文；记录中的模型陈述与内容仍需核对。</p>
        <dl>{Object.entries(data.summary || {}).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl>
        <details><summary>记录内容 · 字节 {data.offset}–{data.next_offset}</summary><pre>{data.content}</pre><small>来源摘要：{data.source_hash}</small></details>
        <div className="button-row">{!data.complete && <button type="button" className="button" disabled={loading} onClick={() => void read(data.ref, data.next_offset)}>下一段内容</button>}{typeof data.next_relation_offset === "number" && <button type="button" className="button" disabled={loading} onClick={() => void read(data.ref, data.offset, data.next_relation_offset!)}>更多关联</button>}</div>
        {data.relations?.length > 0 && <nav aria-label="关联引用">{data.relations.map((item, index) => <button type="button" className="text-button" key={`${item.ref}:${item.relation}:${index}`} disabled={loading} onClick={() => void read(item.ref)}>{item.relation} · {item.ref}</button>)}</nav>}
      </div>}
    </div>
  </details>;
}
