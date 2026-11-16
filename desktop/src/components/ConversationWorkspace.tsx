import ChatTimeline from "../ChatTimeline";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import AgentPanel from "./AgentPanel";
import type { WorkerRun } from "../ConversationRuntime";
import ConversationToolbar from "../ConversationToolbar";
import ConversationComposer from "../ConversationComposer";
import FilePreviewWorkspace, { PreviewDivider } from "../FilePreviewWorkspace";
import { isNearBottom, shouldSubmitOnEnter } from "../logic";
import { asRecord } from "../workspaceTypes";
import type { WorkspaceController } from "../hooks/useWorkspaceController";

export default function ConversationWorkspace({ workspace, connectionSetup }: { workspace: WorkspaceController; connectionSetup?: ReactNode }) {
  const { native, connection, runtimeStatus, workspaceOperation, restoreFenced, view, messages, timelineEvents, timelineHasMore, timelineError, outgoing, hasNew, setHasNew, readingPaused, setReadingPaused, hasMore, historyBusy, conversationError, draft, setDraft, pending, sending, sendError, scrollRef, composerInput, followBottom, manualPause, quests, pendingApprovals, approvalLoadError, actionError, actionBusy, connectionKey, workerActivity, writesBlocked, captureReadingPosition, previewOpen, previewExpanded, setPreviewExpanded, initialPreviewWidth, previewBounds, previewWidthPixels, previewRequest, workspaceChanges, setWorkspaceChanges, historyStats, fileSource, openPreview, closePreview, resizePreview, recordHistoryStats, revealRequest, workspaceRef, previewPresent, loadOlder, sendPending, send, openTaskView, currentWorkspaceName, routerStatus, selectSetting, decideApproval, home, conversationFiles, taskView, settingsOpen } = workspace;
  const chatVisible = view === "chat" || (taskView && !settingsOpen);
  const [compactAgent, setCompactAgent] = useState(() => window.matchMedia("(max-width: 1180px)").matches);
  const [agentPreference, setAgentPreference] = useState(false);
  const agentOpen = !home && !taskView && !previewOpen && agentPreference;
  const previousAgent = useRef(agentOpen);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1180px)");
    const change = () => { captureReadingPosition(); setCompactAgent(media.matches); };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useLayoutEffect(() => {
    if (previousAgent.current === agentOpen) return;
    previousAgent.current = agentOpen;
    const timeline = scrollRef.current;
    if (!timeline) return;
    if (followBottom.current) timeline.scrollTop = timeline.scrollHeight;
    else {
      const anchor = workspace.readingAnchor.current;
      const node = anchor && Array.from(timeline.querySelectorAll<HTMLElement>("[data-scroll-key]")).find(item => item.dataset.scrollKey === anchor.key);
      if (node && anchor) timeline.scrollTop += node.getBoundingClientRect().top - timeline.getBoundingClientRect().top - anchor.offset;
    }
  }, [agentOpen]);
  const toggleAgent = () => {
    captureReadingPosition();
    if (taskView) workspace.openConversation();
    if (previewOpen) closePreview();
    setAgentPreference(!agentOpen);
  };
  const revealRun = (run: WorkerRun) => {
    const entry = Array.from(scrollRef.current?.querySelectorAll<HTMLDetailsElement>(".agent-activity") || []).find(item => item.dataset.runId === run.run_id || Array.from(item.querySelectorAll<HTMLElement>("[data-run-id]")).some(node => node.dataset.runId === run.run_id));
    if (!entry) { if (run.quest_id) openTaskView("quests", run.quest_id); return; }
    manualPause.current = true; followBottom.current = false; setReadingPaused(true);
    const process = entry.closest<HTMLDetailsElement>(".process-transcript");
    if (process && !process.open) process.querySelector<HTMLElement>(":scope > summary")?.click();
    if (!entry.open) entry.querySelector<HTMLElement>("summary")?.click();
    if (compactAgent) setAgentPreference(false);
    requestAnimationFrame(() => {
      const record = Array.from(entry.querySelectorAll<HTMLElement>(".agent-activity-record")).find(item => item.dataset.runId === run.run_id);
      (record || entry).scrollIntoView({ block: "center", behavior: "instant" }); entry.querySelector<HTMLElement>("summary")?.focus({ preventScroll: true });
    });
  };
  const sources = [...new Set(timelineEvents.flatMap(event => {
    const detail = asRecord(event.detail);
    return event.kind === "tool_receipt" && event.status === "succeeded" && event.execution_mode === "live" && detail.tool === "file_read" && typeof detail.path === "string" ? [detail.path] : [];
  }))];
  const latestUserText = [...messages].reverse().find(message => message.role === "user")?.content;
  const threadTitle = latestUserText?.replace(/\s+/g, " ").trim().slice(0, 180) || (home ? "FORGE" : "与 FORGE 对话");
  const threadContext = connection ? [currentWorkspaceName, messages.length ? `${messages.length} 条已加载消息` : ""].filter(Boolean).join(" · ") : "";
  return <div ref={workspaceRef} className={`chat-workspace${agentOpen ? " agent-panel-visible" : ""}${previewOpen ? " preview-open" : ""}${previewExpanded && previewOpen ? " preview-expanded" : ""}`} hidden={!chatVisible}>
    <main className={`conversation-shell${home ? " home-state" : ""}`}>
      <ConversationToolbar home={home} status={routerStatus} title={threadTitle} context={threadContext}
        activityCount={timelineEvents.length} changeCount={conversationFiles.length} workerCount={workerActivity.items.length}
        onOpenChanges={() => openPreview("conversation", undefined, conversationFiles)}
        onOpenTasks={() => openTaskView("quests", "")} onOpenFiles={() => openPreview("files")} agentOpen={agentOpen} onToggleAgent={toggleAgent}
        onOpenSettings={workspace.openSettings} onOpenMemory={() => selectSetting("memory")} onOpenActivity={() => openTaskView("activity", "")}
        onConfigure={() => selectSetting("environment")} pendingCount={pendingApprovals.length} />
      {connectionSetup}
      {writesBlocked && <div className="maintenance-banner" role="status">{restoreFenced === true ? "恢复后写操作已锁定，请先人工核对。" : workspaceOperation?.status === "unknown" && !workspaceOperation.acknowledged ? "维护结果未知，请人工核查；写操作已暂停。" : runtimeStatus?.state === "maintenance" || workspaceOperation?.status === "running" ? "正在备份或恢复，完成后可退出；写操作已暂停。" : "正在核对工作空间恢复状态；写操作暂不可用。"}</div>}
      <ChatTimeline messages={messages} events={timelineEvents} outgoing={outgoing} quests={quests} workerRuns={workerActivity.items} connection={connection} onHistoryLoaded={recordHistoryStats}
        approvals={pendingApprovals} native={native} connected={!!connection} historyMore={hasMore || timelineHasMore}
        historyBusy={historyBusy} timelineError={timelineError} conversationError={conversationError}
        approvalError={approvalLoadError} canRetry={!!connection && !sending && !writesBlocked} scrollRef={scrollRef}
        onScroll={() => { const element = scrollRef.current; if (!element || manualPause.current) return; followBottom.current = isNearBottom(element.scrollTop, element.clientHeight, element.scrollHeight); if (followBottom.current) setHasNew(false); }}
        onInspect={() => { manualPause.current = true; followBottom.current = false; setReadingPaused(true); }}
        onLoadOlder={() => void loadOlder()} onRetry={(item) => void sendPending(item, false)}
        onQuest={(id) => openTaskView("quests", id)}
        onOpenFile={(path) => openPreview("files", path)}
        onOpenHistory={(receiptId, path) => openPreview("history", path, undefined, receiptId)} historyStats={historyStats.identity === connectionKey ? historyStats.files : {}}
        onApproval={(id) => openTaskView("approvals", id)}
        approvalsBlocked={actionBusy || writesBlocked} onApprovalDecision={decideApproval} />
      {actionError && <p className="notice error action-error" role="alert">{actionError}</p>}
      {(hasNew || readingPaused) && <button className="new-messages" onClick={() => { manualPause.current = false; followBottom.current = true; setReadingPaused(false); setHasNew(false); if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }}>{hasNew ? "有新消息 · 回到最新 ↓" : "回到最新 ↓"}</button>}
      <ConversationComposer inputRef={composerInput} value={draft} onChange={setDraft}
        onKeyDown={(event) => { if (shouldSubmitOnEnter({ key: event.key, shiftKey: event.shiftKey, isComposing: event.nativeEvent.isComposing, keyCode: event.nativeEvent.keyCode })) { event.preventDefault(); void send(); } }}
        onSend={() => void send()} disabled={!connection || sending || writesBlocked || !draft.trim()} sending={sending}
        model={workspace.routerModel} onFiles={() => openPreview("files")} onConfigure={() => selectSetting("environment")}
        home={home} workspaceName={currentWorkspaceName} native={native} connected={!!connection}
        error={outgoing.some(item => item.status === "failed") ? "" : sendError} requestId={pending?.requestId} />
    </main>
    {previewPresent && <PreviewDivider width={previewWidthPixels} minimum={previewBounds.minimum} maximum={previewBounds.maximum} onResize={resizePreview} onReset={() => resizePreview(initialPreviewWidth)} />}
    <FilePreviewWorkspace source={fileSource} native={native} identity={connectionKey} connection={connection} onRequest={revealRequest} onHistoryLoaded={recordHistoryStats} workspaceName={currentWorkspaceName} open={previewOpen} present={previewPresent} visible={chatVisible} expanded={previewExpanded} onToggleExpanded={() => { captureReadingPosition(); setPreviewExpanded((value) => !value); }} request={previewRequest} onClose={closePreview} onChanges={setWorkspaceChanges} />
    <div id="conversation-agent-panel" className="agent-panel-slot" hidden={!agentOpen} onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); captureReadingPosition(); setAgentPreference(false); document.querySelector<HTMLButtonElement>('.conversation-more-toggle')?.focus(); } }}>
      <AgentPanel key={connectionKey} state={workerActivity} sources={sources} events={timelineEvents} conversationFiles={conversationFiles.length} onConversationFiles={() => openPreview("conversation", undefined, conversationFiles)} filesChanged={workspaceChanges} onChanges={() => openPreview("changes")} onSource={path => openPreview("files", path || undefined)} onRun={revealRun} onActivity={() => openTaskView("activity", "")} onClose={() => { captureReadingPosition(); setAgentPreference(false); document.querySelector<HTMLButtonElement>('.conversation-more-toggle')?.focus(); }} />
    </div>

    </div>;
}


