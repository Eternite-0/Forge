import { useCallback, useEffect, useLayoutEffect } from "react";
import { ApiError, request } from "../api";
import { appendUnique, errorMessage, preservedScrollTop, retainIfEqual } from "../logic";
import { messageMetadata } from "../timeline";
import { captureTimelineReading } from "../captureTimelineReading";
import { Row, TimelinePage, Page, Quest, QuestPage, TASK_VIEWS, eventKey, mergeEvents } from "../workspaceTypes";
import type { WorkspaceState } from "./useWorkspaceState";
import useWorkspacePreview from "./useWorkspacePreview";

export default function useConversationFeed(workspace: WorkspaceState & ReturnType<typeof useWorkspacePreview>) {
  const { connection, setConnection, coreSnapshot, setCoreSnapshot, setRestoreFenced, setRestoreStatus, view, messages, setMessages, timelineEvents, setTimelineEvents, timelineBefore, setTimelineBefore, setTimelineHasMore, setTimelineError, outgoing, setOutgoing, setHasNew, before, setBefore, setHasMore, historyBusy, setHistoryBusy, setConversationError, scrollRef, followBottom, historyAnchor, readingAnchor, seenMessages, seenEvents, seenInitialized, historyLoaded, questsLoaded, setQuests, questsBefore, setQuestsBefore, setQuestLoadError, setPendingApprovals, setApprovalLoadError, guard, previewOpen, previewWidth } = workspace;
  const refreshConversation = useCallback(async () => {
    if (!connection) return;
    const ticket = guard.start("conversation");
    try {
      const [page, questResult, approvals, settings, timeline, restored] = await Promise.all([
        request<Page>(connection, "/api/conversation?limit=100"),
        request<Quest[] | QuestPage>(connection, "/api/quests?limit=100")
          .then((value) => ({ value, error: "" }))
          .catch((error) => ({ value: null, error: errorMessage(error) })),
        request<{items: Row[]}>(connection, "/api/approvals/pending?limit=20")
          .then((value) => ({ value, error: "" }))
          .catch((error) => ({ value: { items: [] }, error: errorMessage(error) })),
        request<Row>(connection, "/api/settings").catch(() => null),
        request<TimelinePage>(connection, "/api/timeline?limit=100")
          .then((value) => ({ value, error: "" }))
          .catch((error) => ({ value: null, error: errorMessage(error) })),
        request<Row>(connection, "/api/restore-status").catch(() => null),
      ]);
      if (!guard.accepts(ticket)) return;
      const anchor = captureTimelineReading(scrollRef.current, followBottom.current);
      if (anchor) readingAnchor.current = anchor;
      const fresh = page.messages.some((item) => !seenMessages.current.has(item.id)) ||
        !!timeline.value?.items.some((item) => !seenEvents.current.has(eventKey(item)));
      if (seenInitialized.current && fresh && !followBottom.current) setHasNew(true);
      page.messages.forEach((item) => seenMessages.current.add(item.id));
      timeline.value?.items.forEach((item) => seenEvents.current.add(eventKey(item)));
      seenInitialized.current = true;
      setMessages((existing) => retainIfEqual(existing, appendUnique(existing, page.messages || [])));
      if (timeline.value) setTimelineEvents((existing) => mergeEvents(existing, timeline.value!.items));
      setTimelineError(timeline.error);
      const committed = new Set(page.messages.map((item) => messageMetadata(item.metadata).request_id));
      setOutgoing((existing) => { const filtered = existing.filter((item) => !committed.has(item.requestId)); return filtered.length === existing.length ? existing : filtered; });
      if (!historyLoaded.current) { setBefore(page.next_before ?? null); setHasMore(!!page.has_more); }
      if (!historyLoaded.current && timeline.value) { setTimelineBefore(timeline.value.next_before ?? null); setTimelineHasMore(!!timeline.value.has_more); }
      if (questResult.value) {
        const questData = Array.isArray(questResult.value) ? questResult.value : questResult.value.items;
        setQuests((existing) => questsLoaded.current ? [...questData, ...existing.filter((item) => !questData.some((fresh) => fresh.id === item.id))] : questData);
        if (!questsLoaded.current) setQuestsBefore(Array.isArray(questResult.value) ? null : questResult.value.next_before ?? null);
      }
      setQuestLoadError(questResult.error);
      setPendingApprovals(approvals.value.items); setApprovalLoadError(approvals.error);
      setRestoreStatus(restored);
      setCoreSnapshot({ key: `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}`, settings: settings || {}, router: page.router || {} });
      setRestoreFenced(restored && typeof restored.fenced === "boolean" ? restored.fenced : settings && typeof settings.restore_fenced === "boolean" ? settings.restore_fenced : null);
      setConversationError("");
    } catch (error) {
      if (!guard.accepts(ticket)) return;
      setConversationError(errorMessage(error));
      if (error instanceof ApiError && error.status === 401) setConnection(null);
    }
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
  }, [messages, timelineEvents, outgoing, view, coreSnapshot, previewOpen, previewWidth, workspace.workerActivity.items]);

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
  return { refreshConversation, loadOlder, loadMoreQuests };
}


