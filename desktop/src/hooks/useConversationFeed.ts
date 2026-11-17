import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ConversationCursor } from "../conversationCursor";
import { IndependentRefresh } from "../independentRefresh";
import { ApiError, request } from "../api";
import { appendUnique, errorMessage, preservedScrollTop, retainIfEqual } from "../logic";
import { messageMetadata } from "../timeline";
import { captureTimelineReading } from "../captureTimelineReading";
import { Row, TimelinePage, Page, Quest, QuestPage, TASK_VIEWS, eventKey, mergeEvents } from "../workspaceTypes";
import type { WorkspaceState } from "./useWorkspaceState";
import useWorkspacePreview from "./useWorkspacePreview";

export default function useConversationFeed(workspace: WorkspaceState & ReturnType<typeof useWorkspacePreview>) {
  const { connection, setConnection, coreSnapshot, setCoreSnapshot, setRestoreFenced, setRestoreStatus, view, messages, setMessages, timelineEvents, setTimelineEvents, timelineBefore, setTimelineBefore, setTimelineHasMore, setTimelineError, outgoing, setOutgoing, setHasNew, before, setBefore, setHasMore, historyBusy, setHistoryBusy, setConversationError, scrollRef, followBottom, historyAnchor, readingAnchor, seenMessages, seenEvents, seenInitialized, historyLoaded, questsLoaded, setQuests, questsBefore, setQuestsBefore, setQuestLoadError, setPendingApprovals, setApprovalLoadError, guard, previewOpen, previewWidth } = workspace;
  const currentIdentity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "";
  const identityRef = useRef(currentIdentity);
  identityRef.current = currentIdentity;
  const [feedSnapshot, setFeedSnapshot] = useState<{ identity: string; channels: Record<string, { updatedAt?: number; error?: string }> }>({ identity: "", channels: {} });
  const feedHealth = feedSnapshot.identity === currentIdentity ? feedSnapshot.channels : {};
  const cursor = useRef(new ConversationCursor());
  cursor.current.select(currentIdentity);
  const refreshes = useRef(new IndependentRefresh());
  const context = useRef<{ identity: string; settings: Row | null; restored: Row | null }>({ identity: "", settings: null, restored: null });
  const refreshConversation = useCallback(async () => {
    if (!connection) return;
    const identity = `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}`;
    if (context.current.identity !== identity) context.current = { identity, settings: null, restored: null };
    const preserveReading = () => {
      const anchor = captureTimelineReading(scrollRef.current, followBottom.current);
      if (anchor) readingAnchor.current = anchor;
    };
    const updateFence = () => {
      const { restored, settings } = context.current;
      setRestoreFenced(restored && typeof restored.fenced === "boolean" ? restored.fenced : settings && typeof settings.restore_fenced === "boolean" ? settings.restore_fenced : null);
    };
    // Each channel commits as soon as its own read completes. Coalescing avoids
    // superseding a slow request every six seconds; guards reject old sessions.
    const read = <T,>(channel: string, path: string, commit: (value: T) => void, fail: (message: string) => void) =>
      refreshes.current.run(identity, channel, async () => {
        const ticket = guard.start(`feed:${channel}`);
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 30000);
        try {
          const value = await request<T>(connection, path, { signal: controller.signal });
          if (!guard.accepts(ticket) || identityRef.current !== identity) return;
          commit(value);
          setFeedSnapshot(old => ({ identity, channels: { ...(old.identity === identity ? old.channels : {}), [channel]: { updatedAt: Date.now() } } }));
        } catch (error) {
          if (!guard.accepts(ticket) || identityRef.current !== identity) return;
          if (channel === "conversation" && error instanceof ApiError && error.status === 409 && cursor.current.select(identity) !== null) {
            cursor.current.reset(identity);
            // An older-page request from the invalid transcript may still be in flight.
            guard.start("history");
            setHistoryBusy(false);
            historyAnchor.current = null;
            readingAnchor.current = null;
            setMessages([]); seenMessages.current.clear(); historyLoaded.current = false;
            setBefore(null); setHasMore(false);
            window.setTimeout(() => { if (identityRef.current === identity) void refreshConversation(); }, 0);
          }
          fail(errorMessage(error));
          setFeedSnapshot(old => ({ identity, channels: { ...(old.identity === identity ? old.channels : {}), [channel]: { ...(old.identity === identity ? old.channels[channel] : {}), error: errorMessage(error) } } }));
          if (error instanceof ApiError && error.status === 401) setConnection(null);
        } finally { window.clearTimeout(timeout); }
      });
    void read<TimelinePage>("timeline", "/api/timeline?limit=100", page => {
      preserveReading();
      if (seenInitialized.current && page.items.some(item => !seenEvents.current.has(eventKey(item))) && !followBottom.current) setHasNew(true);
      page.items.forEach(item => seenEvents.current.add(eventKey(item)));
      setTimelineEvents(existing => mergeEvents(existing, page.items));
      if (!historyLoaded.current) { setTimelineBefore(page.next_before ?? null); setTimelineHasMore(!!page.has_more); }
      setTimelineError("");
    }, setTimelineError);
    void read<Quest[] | QuestPage>("quests", "/api/quests?limit=100", page => {
      const data = Array.isArray(page) ? page : page.items;
      preserveReading();
      setQuests(existing => retainIfEqual(existing, questsLoaded.current ? [...data, ...existing.filter(item => !data.some(fresh => fresh.id === item.id))] : data));
      if (!questsLoaded.current) setQuestsBefore(Array.isArray(page) ? null : page.next_before ?? null);
      setQuestLoadError("");
    }, setQuestLoadError);
    void read<{ items: Row[] }>("approvals", "/api/approvals/pending?limit=20", page => {
      preserveReading();
      setPendingApprovals(existing => retainIfEqual(existing, page.items)); setApprovalLoadError("");
    }, setApprovalLoadError);
    void read<Row>("settings", "/api/settings", settings => {
      context.current.settings = settings;
      setCoreSnapshot(old => ({ key: identity, settings, router: old.key === identity ? old.router : {} }));
      updateFence();
    }, () => { context.current.settings = null; updateFence(); });
    void read<Row>("restore", "/api/restore-status", restored => {
      context.current.restored = restored; setRestoreStatus(restored); updateFence();
    }, () => { context.current.restored = null; setRestoreStatus(null); updateFence(); });
    // Sending only waits for chat reconciliation, never ancillary panel reads.
    const after = cursor.current.select(identity);
    await read<Page & { mode?: "snapshot" | "delta"; resume_after?: string; has_newer?: boolean }>("conversation", `/api/conversation?limit=100${after !== null ? `&after=${encodeURIComponent(after)}` : ""}`, page => {
      if (!Array.isArray(page.messages)) throw new Error("Core 返回了无效对话记录。");
      cursor.current.accept(identity, page);
      preserveReading();
      const fresh = page.messages.some(item => !seenMessages.current.has(item.id));
      if (seenInitialized.current && fresh && !followBottom.current) setHasNew(true);
      page.messages.forEach(item => seenMessages.current.add(item.id));
      seenInitialized.current = true;
      setMessages(existing => retainIfEqual(existing, appendUnique(existing, page.messages)));
      const committed = new Set(page.messages.map(item => messageMetadata(item.metadata).request_id));
      setOutgoing(existing => { const filtered = existing.filter(item => !committed.has(item.requestId)); return filtered.length === existing.length ? existing : filtered; });
      if (page.mode !== "delta" && !historyLoaded.current) { setBefore(page.next_before ?? null); setHasMore(!!page.has_more); }
      if (page.has_newer && typeof page.resume_after === "string" && page.resume_after !== after) {
        window.setTimeout(() => { if (identityRef.current === identity) void refreshConversation(); }, 0);
      }
      setCoreSnapshot(old => ({ key: identity, settings: old.key === identity ? old.settings : {}, router: page.router || {} }));
      setConversationError("");
    }, setConversationError);
  }, [connection, guard]);

  useEffect(() => {
    if (!connection) return;
    void refreshConversation();
    const timer = window.setInterval(() => void refreshConversation(), 6000);
    return () => window.clearInterval(timer);
  }, [connection, refreshConversation]);

  useLayoutEffect(() => {
    if (view !== "chat" && !TASK_VIEWS.includes(view)) return;
    const element = scrollRef.current;
    if (!element) return;
    if (historyAnchor.current) {
      element.scrollTop = preservedScrollTop(historyAnchor.current.top, historyAnchor.current.height, element.scrollHeight);
      historyAnchor.current = null;
      readingAnchor.current = null;
    } else if (readingAnchor.current && !followBottom.current) {
      const anchor = readingAnchor.current;
      readingAnchor.current = null;
      const node = Array.from(element.querySelectorAll<HTMLElement>("[data-scroll-key]")).find((candidate) => candidate.dataset.scrollKey === anchor.key);
      element.scrollTop = node ? element.scrollTop + node.getBoundingClientRect().top - element.getBoundingClientRect().top - anchor.offset : anchor.top;
    } else if (followBottom.current) {
      readingAnchor.current = null;
      element.scrollTop = element.scrollHeight;
    }
  }, [messages, timelineEvents, outgoing, view, coreSnapshot, previewOpen, previewWidth, workspace.quests, workspace.pendingApprovals]);

  const loadOlder = async () => {
    if (!connection || (!before && !timelineBefore) || historyBusy) return;
    const ticket = guard.start("history");
    setHistoryBusy(true);
    try {
      const [page, history] = await Promise.all([
        before ? request<Page>(connection, `/api/conversation?limit=100&before=${encodeURIComponent(before)}`) : Promise.resolve(null),
        timelineBefore ? request<TimelinePage>(connection, `/api/timeline?limit=100&before=${encodeURIComponent(timelineBefore)}`) : Promise.resolve(null),
      ]);
      if (!guard.accepts(ticket)) return;
      if (scrollRef.current) historyAnchor.current = { top: scrollRef.current.scrollTop, height: scrollRef.current.scrollHeight };
      if (page) { page.messages.forEach((item) => seenMessages.current.add(item.id)); setMessages((current) => appendUnique(page.messages || [], current)); setBefore(page.next_before ?? null); setHasMore(!!page.has_more); }
      if (history) { history.items.forEach((item) => seenEvents.current.add(eventKey(item))); setTimelineEvents((current) => mergeEvents(current, history.items)); setTimelineBefore(history.next_before ?? null); setTimelineHasMore(!!history.has_more); }
      historyLoaded.current = true;
      setConversationError(""); setTimelineError("");
    } catch (error) { if (guard.accepts(ticket)) setConversationError(errorMessage(error)); }
    finally { if (guard.accepts(ticket)) setHistoryBusy(false); }
  };

  const loadMoreQuests = async () => {
    if (!connection || !questsBefore) return;
    const ticket = guard.start("quests");
    try {
      const page = await request<QuestPage>(connection, `/api/quests?limit=100&before=${encodeURIComponent(questsBefore)}`);
      if (!guard.accepts(ticket)) return;
      setQuests((old) => appendUnique(old, page.items));
      questsLoaded.current = true;
      setQuestsBefore(page.next_before ?? null);
      setQuestLoadError("");
    } catch (error) { if (guard.accepts(ticket)) setQuestLoadError(errorMessage(error)); }
  };
  return { refreshConversation, loadOlder, loadMoreQuests, feedHealth };
}


