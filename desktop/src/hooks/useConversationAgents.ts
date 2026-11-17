import { useEffect, useRef, useState } from "react";
import { ApiError, request, type Connection } from "../api";
import { scopedAgents, validateAgentPhase, type AgentPage, type AgentDetail, type ConversationAgent } from "../conversationAgents";
import { AgentBuckets } from "../agentBuckets";

const empty = { items: [] as ConversationAgent[], detail: null as ConversationAgent | null, loading: true, error: "", hasMore: false, activeMore: false, legacy: false };
const base = "/api/conversations/main/subagents";
/** Active head polling and historical pagination are independent; global Activity is never a fallback. */
export default function useConversationAgents(connection: Connection | null, selected: string | null, beforeUpdate?: () => void) {
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}|${connection.bearer}` : "";
  const current = useRef(identity); current.current = identity;
  const before = useRef(beforeUpdate); before.current = beforeUpdate;
  const commands = useRef({ refresh: () => {}, loadOlder: () => {}, loadActive: () => {} });
  const [snapshot, setSnapshot] = useState({ identity, selected, ...empty });
  useEffect(() => {
    if (!connection) { setSnapshot({ identity, selected, ...empty, loading: false }); return; }
    let live = true, polling = false, paging = false, initialized = false, legacy = false;
    let terminalCursor: string | null = null, activeCursor: string | null = null;
    let terminalMore = false, activeMore = false, activePaged = false, terminalPaged = false;
    let terminalRefreshed = 0, terminalError = "";
    const controllers = new Set<AbortController>();
    const buckets = new AgentBuckets();
    const accepts = () => live && current.current === identity;
    const publish = (error = terminalError) => {
      if (!accepts()) return;
      before.current?.();
      setSnapshot({ identity, selected, ...empty, loading: paging, error, items: buckets.items, hasMore: terminalMore, activeMore, legacy });
    };
    setSnapshot({ identity, selected, ...empty });
    const get = async <T,>(path: string): Promise<T> => {
      const controller = new AbortController(); controllers.add(controller);
      const timer = window.setTimeout(() => controller.abort(), 12000);
      try { return await request<T>(connection, path, { signal: controller.signal }); }
      finally { window.clearTimeout(timer); controllers.delete(controller); }
    };
    const detail = async (id: string) => {
      const page = await get<AgentDetail>(`${base}/${encodeURIComponent(id)}`);
      const rows = scopedAgents({ ...page, items: [page.item], has_more: false, next_before: null }, "main", connection.workspace_id);
      if (rows.length !== 1 || rows[0].run_id !== id) throw new Error("Invalid scoped run");
      return rows[0];
    };
    const list = async (phase: "active" | "terminal" | "all", cursor: string | null = null) => {
      let page = await get<AgentPage>(`${base}?limit=50${phase === "all" ? "" : `&phase=${phase}`}${cursor ? `&before=${encodeURIComponent(cursor)}` : ""}`);
      validateAgentPhase(page, phase);
      let rows = scopedAgents(page, "main", connection.workspace_id);
      // Old scoped Core ignores phase. Never present its page as complete active coverage.
      if (phase !== "all" && page.phase === undefined) legacy = true;
      else if (phase !== "all" && page.phase !== phase) throw new Error("Invalid phase");
      const visited = new Set<string>();
      for (let hop = 0; !rows.length && page.has_more && page.next_before && hop < 3; hop++) {
        if (visited.has(page.next_before)) throw new Error("Repeated cursor");
        visited.add(page.next_before);
        const next = await get<AgentPage>(`${base}?limit=50${legacy || phase === "all" ? "" : `&phase=${phase}`}&before=${encodeURIComponent(page.next_before)}`);
        if (!legacy && phase !== "all" && next.phase !== phase) throw new Error("Invalid phase");
        validateAgentPhase(next, legacy ? "all" : phase);
        rows = scopedAgents(next, "main", connection.workspace_id); page = next;
      }
      return { rows, more: page.has_more === true, cursor: page.next_before };
    };
    const poll = async () => {
      if (!accepts() || polling) return;
      polling = true;
      try {
        if (selected) {
          const item = await detail(selected);
          if (accepts()) setSnapshot({ identity, selected, ...empty, loading: false, detail: item });
          return;
        }
        const head = await list(legacy ? "all" : "active");
        if (!accepts()) return;
        if (legacy) {
          buckets.acceptActive(head.rows, !head.more);
          if (!terminalPaged) { terminalMore = head.more; terminalCursor = head.cursor; } activeMore = false;
        } else {
          buckets.acceptActive(head.rows, !head.more);
          if (!activePaged) { activeMore = head.more; activeCursor = head.cursor; }
        }
        // Publish disappearance as unknown immediately; at most 8 tracked details per tick.
        publish();
        await Promise.all(buckets.reconcileIds.map(async id => {
          try { const item = await detail(id); if (accepts()) buckets.acceptDetail(item); }
          catch (error) {
            if (!accepts()) return;
            if (error instanceof ApiError && error.status === 404) buckets.remove(id);
            else buckets.defer(id);
          }
        }));
        if ((!initialized || Date.now() - terminalRefreshed >= 18000) && !legacy && accepts()) {
          try {
            const terminal = await list("terminal");
            if (!accepts()) return;
            buckets.acceptTerminal(terminal.rows); terminalError = "";
            if (!terminalPaged) { terminalMore = terminal.more; terminalCursor = terminal.cursor; }
          } catch { terminalError = "已结束记录暂不可用；进行中记录仍独立更新。"; }
          terminalRefreshed = Date.now();
        }
        initialized = true;
        publish(terminalError);
      } catch {
        if (!accepts()) return;
        if (selected) setSnapshot({ identity, selected, ...empty, loading: false, error: "当前运行暂不可用" });
        else { buckets.unavailable(); publish("部分状态暂不可用，未确认的运行已标记未知。"); }
      } finally { polling = false; }
    };
    const older = async (phase: "active" | "terminal") => {
      if (!accepts() || paging || selected) return;
      const cursor = phase === "active" ? activeCursor : terminalCursor;
      if (!cursor) return;
      paging = true; publish();
      try {
        const page = await list(legacy ? "all" : phase, cursor);
        if (!accepts()) return;
        if (phase === "active" && !legacy) {
          for (const item of page.rows) buckets.acceptDetail(item);
          activeMore = page.more; activeCursor = page.cursor; activePaged = true;
        } else {
          buckets.acceptTerminal(page.rows); terminalMore = page.more; terminalCursor = page.cursor; terminalPaged = true; terminalError = "";
        }
        publish();
      } catch (error) {
        if (!accepts()) return;
        if (error instanceof ApiError && error.status === 422) {
          // Scope/version cursors cannot be reused; reset that bucket, never reinterpret it.
          if (phase === "active") { activeCursor = null; activeMore = false; activePaged = false; }
          else { terminalCursor = null; terminalMore = false; terminalPaged = false; initialized = false; }
        }
        if (phase === "terminal") terminalError = "较早结束记录暂不可用，请重新读取。";
        publish(phase === "terminal" ? terminalError : "较早活跃记录暂不可用，请重新读取。");
      } finally { paging = false; if (accepts()) setSnapshot(old => ({ ...old, loading: false })); }
    };
    commands.current = { refresh: () => { initialized = false; void poll(); }, loadOlder: () => { void older("terminal"); }, loadActive: () => { void older("active"); } };
    void poll();
    const timer = window.setInterval(() => void poll(), 6000);
    return () => { live = false; window.clearInterval(timer); controllers.forEach(controller => controller.abort()); };
  }, [identity, selected]);
  const state = snapshot.identity === identity && snapshot.selected === selected ? snapshot : empty;
  return { ...state, connected: !!connection, refresh: () => commands.current.refresh(), loadOlder: () => commands.current.loadOlder(), loadActive: () => commands.current.loadActive() };
}
