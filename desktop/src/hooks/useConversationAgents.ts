import { useEffect, useRef, useState } from "react";
import { request, type Connection } from "../api";
import { errorMessage } from "../logic";
import { scopedAgents, type AgentPage, type AgentDetail, type ConversationAgent } from "../conversationAgents";

const empty = { items: [] as ConversationAgent[], detail: null as ConversationAgent | null, loading: true, error: "", hasMore: false };
/** Scope is supplied by Core's verified delegation projection, never inferred from global activity. */
export default function useConversationAgents(connection: Connection | null, selected: string | null, beforeUpdate?: () => void) {
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}|${connection.bearer}` : "";
  const before = useRef(beforeUpdate); before.current = beforeUpdate;
  const current = useRef(identity); current.current = identity;
  const [snapshot, setSnapshot] = useState({ identity, selected, ...empty });
  const [revision, refresh] = useState(0);
  const [pages, setPages] = useState({ identity, count: 1 });
  const pageCount = pages.identity === identity ? pages.count : 1;
  useEffect(() => {
    if (!connection) { setSnapshot({ identity, selected, ...empty, loading: false }); return; }
    let active = true, busy = false;
    let controller: AbortController | null = null;
    setSnapshot(old => old.identity === identity && old.selected === selected ? { ...old, loading: true } : { identity, selected, ...empty });
    const poll = async () => {
      if (!active || busy) return;
      busy = true;
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller?.abort(), 15000);
      try {
        const path = `/api/conversations/main/subagents${selected ? `/${encodeURIComponent(selected)}` : "?limit=50"}`;
        const page = await request<AgentPage | AgentDetail>(connection, path, { signal: controller.signal });
        if (!selected) {
          const first = page as AgentPage;
          const items = scopedAgents(first, "main", connection.workspace_id);
          const visited = new Set<string>();
          let loaded = items.length ? 1 : 0;
          // Advance through at most three empty source-filtered pages automatically.
          for (let index = 1; index < pageCount + 3 && loaded < pageCount && first.has_more && first.next_before; index++) {
            if (visited.has(first.next_before)) throw new Error("子智能体分页游标重复。");
            visited.add(first.next_before);
            const next = await request<AgentPage>(connection, `${path}&before=${encodeURIComponent(first.next_before)}`, { signal: controller.signal });
            const nextItems = scopedAgents(next, "main", connection.workspace_id);
            items.push(...nextItems);
            if (nextItems.length) loaded++;
            first.has_more = next.has_more; first.next_before = next.next_before;
          }
          first.items = items;
        }
        if (!active || current.current !== identity) return;
        before.current?.();
        if (selected) {
          const detail = page as AgentDetail;
          const items = scopedAgents({ ...detail, items: [detail.item], has_more: false, next_before: null }, "main", connection.workspace_id);
          if (items.length !== 1 || items[0].run_id !== selected) throw new Error("子智能体记录与所选运行不一致。");
          setSnapshot({ identity, selected, ...empty, loading: false, detail: items[0] });
        } else {
          const list = page as AgentPage;
          setSnapshot({ identity, selected, ...empty, loading: false, items: scopedAgents(list, "main", connection.workspace_id), hasMore: list.has_more === true });
        }
      } catch (error) {
        if (active && current.current === identity) { before.current?.(); setSnapshot({ identity, selected, ...empty, loading: false, error: errorMessage(error) }); }
      } finally { window.clearTimeout(timeout); busy = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 6000);
    return () => { active = false; window.clearInterval(timer); controller?.abort(); };
  }, [identity, selected, revision, pageCount]);
  const state = snapshot.identity === identity && snapshot.selected === selected ? snapshot : empty;
  return { ...state, connected: !!connection, refresh: () => refresh(n => n + 1), loadOlder: () => setPages({ identity, count: pageCount + 1 }) };
}
