import { useEffect, useRef, useState } from "react";
import { request, type Connection } from "../api";
import { validateAgentArtifacts, type AgentArtifactPage, type AgentArtifact } from "../agentArtifacts";
import ArtifactHistoryPreview from "../ArtifactHistoryPreview";

/** Historical receipts are fetched by explicit disclosure only, not by the agent polling loop. */
export default function AgentArtifactFiles({ connection, runId }: { connection: Connection; runId: string }) {
  const identity = `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}|${connection.bearer}|${runId}`;
  return <Files key={identity} connection={connection} runId={runId} />;
}
function Files({ connection, runId }: { connection: Connection; runId: string }) {
  const [expanded, setExpanded] = useState(false), [revision, refresh] = useState(0);
  const [state, setState] = useState<{ loading: boolean; loaded: boolean; error: boolean; page: AgentArtifactPage | null }>({ loading: false, loaded: false, error: false, page: null });
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<AgentArtifact | null>(null);
  const disclosure = useRef<HTMLButtonElement>(null), origin = useRef<HTMLButtonElement | null>(null), returnButton = useRef<HTMLButtonElement>(null);
  const closeFile = () => { (origin.current?.isConnected ? origin.current : disclosure.current)?.focus({ preventScroll: true }); setSelected(null); requestAnimationFrame(() => (origin.current?.isConnected ? origin.current : disclosure.current)?.focus({ preventScroll: true })); };
  useEffect(() => { if (selected) returnButton.current?.focus({ preventScroll: true }); }, [selected]);
  useEffect(() => {
    if (!expanded || state.loaded) return;
    let current = true;
    const controller = new AbortController(), timeout = window.setTimeout(() => controller.abort(), 12000);
    setState(old => ({ ...old, loading: true, error: false }));
    void request<AgentArtifactPage>(connection, `/api/conversations/main/subagents/${encodeURIComponent(runId)}/artifacts?limit=10${cursor ? `&before=${encodeURIComponent(cursor)}` : ""}`, { signal: controller.signal })
      .then(value => { const page = validateAgentArtifacts(value, runId, connection.workspace_id); if (cursor && page.has_more && page.next_before === cursor) throw new Error("Repeated historical cursor"); return page; })
      .then(page => { if (current) setState(old => ({ loading: false, loaded: true, error: false, page: cursor && old.page ? { ...page, unavailable: old.page.unavailable || page.unavailable, items: [...new Map([...old.page.items, ...page.items].map(item => [item.receipt_id, item])).values()] } : page })); })
      .catch(() => { if (current) setState(old => ({ ...old, loading: false, loaded: false, error: true })); })
      .finally(() => window.clearTimeout(timeout));
    return () => { current = false; controller.abort(); window.clearTimeout(timeout); };
  }, [expanded, revision, cursor, connection.base_url, connection.workspace_id, connection.generation, connection.bearer, runId]);
  const page = state.page;
  return <section className="agent-artifact-files" aria-label="子智能体历史文件" onKeyDown={event => { if (event.key === "Escape" && selected) { event.stopPropagation(); event.preventDefault(); closeFile(); } }}>
    <h4><button ref={disclosure} type="button" aria-expanded={expanded} onClick={() => { if (expanded) setSelected(null); setExpanded(!expanded); }}>查看返回文件 {expanded ? "⌃" : "⌄"}</button></h4>
    {expanded && <>
      <p className="conversation-agent-caption">显示本次执行时保存的版本。</p>
      {state.loading && <p role="status">正在读取历史文件…</p>}
      {state.error && <p role="status">历史文件暂不可用，不能据此判断没有文件。<button type="button" onClick={() => refresh(n => n + 1)}>重试</button></p>}
      {!state.loading && !state.error && page && <>
        {page.unavailable && <p role="status">部分历史回执不可用，仅显示已核对的记录。</p>}
        {!page.items.length && !page.unavailable && !page.has_more && <p>没有符合条件的历史文件回执。</p>}
        {!page.items.length && page.has_more && <p>本页没有可显示的回执，可继续读取较早记录。</p>}
        <div className="agent-artifact-list">{page.items.map(item => <button type="button" key={item.receipt_id} onClick={event => { origin.current = event.currentTarget; setSelected(item); }}><span>{item.path}</span><small>{item.change === "created" ? "创建" : "修订"} · {item.execution_mode === "mock" ? "模拟记录" : "真实执行回执"}</small></button>)}</div>
        {page.has_more && page.next_before && <button type="button" onClick={() => { setCursor(page.next_before); setState(old => ({ ...old, loaded: false })); }}>加载更早历史文件</button>}
        <button type="button" onClick={() => { setSelected(null); setCursor(null); setState({ loading: false, loaded: false, error: false, page: null }); refresh(n => n + 1); }}>刷新历史文件</button>
      </>}
      {selected && <div className="agent-artifact-document"><button ref={returnButton} type="button" onClick={closeFile}>返回历史文件</button><ArtifactHistoryPreview connection={connection} receiptId={selected.receipt_id} expected={{ receiptId: selected.receipt_id, runId, path: selected.path, sha256: selected.sha256, executionMode: selected.execution_mode }} onClose={closeFile} /></div>}
    </>}
  </section>;
}
