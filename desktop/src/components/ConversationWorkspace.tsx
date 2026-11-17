import ChatTimeline from "../ChatTimeline";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import AgentPanel from "./AgentPanel";
import { ConversationAgents, ConversationAgentDetail } from "./ConversationAgents";
import useConversationAgents from "../hooks/useConversationAgents";
import type { WorkerRun } from "../ConversationRuntime";
import ConversationToolbar from "../ConversationToolbar";
import ConversationComposer from "../ConversationComposer";
import FilePreviewWorkspace, { PreviewDivider } from "../FilePreviewWorkspace";
import { isNearBottom, shouldSubmitOnEnter } from "../logic";
import { asRecord } from "../workspaceTypes";
import type { WorkspaceController } from "../hooks/useWorkspaceController";
import "../styles/execution-screen.css";
import { captureTimelineReading, type TimelineReadingAnchor } from "../captureTimelineReading";

export default function ConversationWorkspace({ workspace, connectionSetup }: { workspace: WorkspaceController; connectionSetup?: ReactNode }) {
  const { native, connection, runtimeStatus, workspaceOperation, restoreFenced, view, messages, timelineEvents, timelineHasMore, timelineError, outgoing, hasNew, setHasNew, readingPaused, setReadingPaused, hasMore, historyBusy, conversationError, draft, setDraft, pending, sending, sendError, scrollRef, composerInput, followBottom, manualPause, quests, pendingApprovals, approvalLoadError, actionError, actionBusy, connectionKey, writesBlocked, captureReadingPosition, previewOpen, previewExpanded, setPreviewExpanded, initialPreviewWidth, previewBounds, previewWidthPixels, previewRequest, workspaceChanges, setWorkspaceChanges, historyStats, fileSource, openPreview, closePreview, resizePreview, recordHistoryStats, revealRequest, workspaceRef, previewPresent, loadOlder, sendPending, send, openTaskView, currentWorkspaceName, routerStatus, selectSetting, decideApproval, home: conversationHome, conversationFiles, taskView, settingsOpen } = workspace;
  const chatVisible = view === "chat" || (taskView && !settingsOpen);
  const agents = useConversationAgents(connection, null, captureReadingPosition);
  const home = conversationHome && !agents.items.length;
  const [selectedAgent, setSelectedAgent] = useState<{ identity: string; runId: string } | null>(null);
  const [executionIdentity, setExecutionIdentity] = useState<string | null>(null);
  const executionOpen = executionIdentity === connectionKey && !!connection && !taskView;
  const executionBack = useRef<HTMLButtonElement>(null);
  const executionScroll = useRef<HTMLDivElement>(null);
  type ChatReturn = { identity: string; epoch: number; node: HTMLElement | null; top: number; follow: boolean; manual: boolean; paused: boolean; anchor: TimelineReadingAnchor | null };
  const navigationEpoch = useRef(0);
  const chatReturn = useRef<ChatReturn | null>(null);
  const pendingReturn = useRef<ChatReturn | null>(null);
  const rememberChatReturn = () => {
    pendingReturn.current = null;
    chatReturn.current = { identity: connectionKey, epoch: ++navigationEpoch.current, node: document.activeElement as HTMLElement | null,
      top: scrollRef.current?.scrollTop || 0, follow: followBottom.current, manual: manualPause.current, paused: readingPaused,
      anchor: captureTimelineReading(scrollRef.current, followBottom.current) };
  };
  const openExecution = () => {
    captureReadingPosition();
    rememberChatReturn();
    manualPause.current = true; followBottom.current = false;
    setExecutionIdentity(connectionKey); setSelectedAgent(null); setAgentPreference(false);
    if (previewOpen) closePreview();
  };
  const closeExecution = () => {
    pendingReturn.current = chatReturn.current;
    setExecutionIdentity(null); setSelectedAgent(null);
    if (previewOpen) closePreview();
  };
  // Restore only after the mounted chat is visible, before paint. A return from
  // another workspace/instance or an older navigation must never override the
  // connection hook's fresh follow-latest state or move focus into stale UI.
  useLayoutEffect(() => {
    const saved = pendingReturn.current;
    if (!saved || executionOpen) return;
    pendingReturn.current = null;
    if (saved.identity !== connectionKey || saved.epoch !== navigationEpoch.current) return;
    const timeline = scrollRef.current;
    if (!timeline?.clientHeight) return;
    followBottom.current = saved.follow; manualPause.current = saved.manual; setReadingPaused(saved.paused);
    workspace.readingAnchor.current = null;
    const restorePosition = () => {
      if (saved.follow) timeline.scrollTop = timeline.scrollHeight;
      else {
        const anchor = saved.anchor;
        const node = anchor && Array.from(timeline.querySelectorAll<HTMLElement>("[data-scroll-key]")).find(item => item.dataset.scrollKey === anchor.key);
        timeline.scrollTop = node && anchor ? timeline.scrollTop + node.getBoundingClientRect().top - timeline.getBoundingClientRect().top - anchor.offset : saved.top;
      }
    };
    restorePosition();
    if (saved.follow) { setHasNew(false); setAwayFromLatest(false); }
    workspace.rememberTimelineReading();
    (saved.node?.isConnected && saved.node.getClientRects().length ? saved.node : composerInput.current)?.focus({preventScroll:true});
  });
  useEffect(() => { if (executionOpen && !selectedAgent) executionBack.current?.focus({preventScroll:true}); }, [executionOpen]);
  const navigationRevision = useRef(workspace.conversationNavigation);
  useLayoutEffect(() => {
    if (navigationRevision.current !== workspace.conversationNavigation) {
      navigationRevision.current = workspace.conversationNavigation;
      if (executionIdentity !== null) closeExecution();
    }
  }, [workspace.conversationNavigation]);
  const agentReturnFocus = useRef<HTMLElement | null>(null);
  const detailOpen = executionOpen && selectedAgent?.identity === connectionKey;
  const closeAgentDetail = () => {
    captureReadingPosition(); setSelectedAgent(null);
    requestAnimationFrame(() => {
      const origin = agentReturnFocus.current;
      (origin?.isConnected && origin.getClientRects().length ? origin : executionBack.current)?.focus({ preventScroll: true });
    });
  };
  const [agentPreference, setAgentPreference] = useState(false);
  const [awayFromLatest, setAwayFromLatest] = useState(false);
  useEffect(() => {
    const timeline = scrollRef.current;
    if (!timeline) return;
    // This controls visibility only. Reading pauses and follow behavior retain
    // their existing semantics, including explicit inspection of old records.
    const measure = () => {
      if (timeline.clientHeight > 0) {
        setAwayFromLatest(!isNearBottom(timeline.scrollTop, timeline.clientHeight, timeline.scrollHeight));
      }
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(timeline);
    Array.from(timeline.children).forEach(child => observer.observe(child));
    return () => observer.disconnect();
  }, [connectionKey, messages, timelineEvents, outgoing, quests, pendingApprovals, home, chatVisible]);
  const agentOpen = !home && !taskView && !previewOpen && !executionOpen && agentPreference;
  const agentLayout = `${agentOpen}:${detailOpen}`;
  useLayoutEffect(() => {
    const timeline = scrollRef.current;
    if (!timeline) return;
    if (followBottom.current) timeline.scrollTop = timeline.scrollHeight;
    else {
      const anchor = workspace.readingAnchor.current;
      const node = anchor && Array.from(timeline.querySelectorAll<HTMLElement>("[data-scroll-key]")).find(item => item.dataset.scrollKey === anchor.key);
      if (node && anchor) timeline.scrollTop += node.getBoundingClientRect().top - timeline.getBoundingClientRect().top - anchor.offset;
    }
  }, [agentLayout, agents.items]);
  const toggleAgent = () => {
    captureReadingPosition(); setSelectedAgent(null);
    if (taskView) workspace.openConversation();
    if (previewOpen) closePreview();
    setAgentPreference(!agentOpen);
  };
  const revealRun = (run: WorkerRun) => {
    captureReadingPosition();
    if (!executionOpen) rememberChatReturn();
    agentReturnFocus.current = document.activeElement as HTMLElement | null;
    setExecutionIdentity(connectionKey);
    setSelectedAgent({ identity: connectionKey, runId: run.run_id });
  };
  const sources = [...new Set(timelineEvents.flatMap(event => {
    const detail = asRecord(event.detail);
    return event.kind === "tool_receipt" && event.status === "succeeded" && event.execution_mode === "live" && detail.tool === "file_read" && typeof detail.path === "string" ? [detail.path] : [];
  }))];
  const threadTitle = workspace.conversationTitle || (home ? "FORGE" : "与 FORGE 对话");
  return <div ref={workspaceRef} className={`chat-workspace${agentOpen ? " agent-panel-visible" : ""}${executionOpen ? " execution-screen-visible" : ""}${previewOpen ? " preview-open" : ""}${previewExpanded && previewOpen ? " preview-expanded" : ""}`} hidden={!chatVisible}>
    <main hidden={executionOpen} className={`conversation-shell${home ? " home-state" : ""}`}>
      <ConversationToolbar home={home} status={routerStatus} title={threadTitle} changeCount={conversationFiles.length}
        onOpenChanges={() => openPreview("conversation", undefined, conversationFiles)}
        onOpenTasks={() => openTaskView("quests", "")} onOpenFiles={() => openPreview("files")} agentOpen={agentOpen} onToggleAgent={toggleAgent}
        onOpenSettings={workspace.openSettings} onOpenMemory={() => selectSetting("memory")} onOpenActivity={() => openTaskView("activity", "")}
        onConfigure={() => selectSetting("environment")} pendingCount={pendingApprovals.length} />
      {connectionSetup}
      {writesBlocked && <div className="maintenance-banner" role="status">{restoreFenced === true ? "恢复后写操作已锁定，请先人工核对。" : workspaceOperation?.status === "unknown" && !workspaceOperation.acknowledged ? "维护结果未知，请人工核查；写操作已暂停。" : runtimeStatus?.state === "maintenance" || workspaceOperation?.status === "running" ? "正在备份或恢复，完成后可退出；写操作已暂停。" : "正在核对工作空间恢复状态；写操作暂不可用。"}</div>}
      <ChatTimeline onExecution={openExecution} messages={messages} events={timelineEvents} outgoing={outgoing} quests={quests} workerRuns={agents.items} connection={connection} onHistoryLoaded={recordHistoryStats} workspaceName={connection ? currentWorkspaceName : undefined}
        approvals={pendingApprovals} native={native} connected={!!connection} historyMore={hasMore || timelineHasMore}
        historyBusy={historyBusy} timelineError={timelineError} conversationError={conversationError}
        approvalError={approvalLoadError} canRetry={!!connection && !sending && !writesBlocked} scrollRef={scrollRef}
        onScroll={() => {
          const element = scrollRef.current;
          if (!element || executionOpen) return;
          const nearLatest = isNearBottom(element.scrollTop, element.clientHeight, element.scrollHeight);
          setAwayFromLatest(!nearLatest);
          if (workspace.isRestoringResponsiveReading()) return;
          if (!manualPause.current) {
            followBottom.current = nearLatest;
            if (nearLatest) setHasNew(false);
          }
          workspace.rememberTimelineReading();
        }}
        onInspect={() => { manualPause.current = true; followBottom.current = false; setReadingPaused(true); }}
        onLoadOlder={() => void loadOlder()} onRetry={(item) => void sendPending(item, false)}
        onQuest={(id) => openTaskView("quests", id)}
        onOpenFile={(path) => openPreview("files", path)}
        onOpenHistory={(receiptId, path) => openPreview("history", path, undefined, receiptId)} historyStats={historyStats.identity === connectionKey ? historyStats.files : {}}
        onApproval={(id) => openTaskView("approvals", id)}
        approvalsBlocked={actionBusy || writesBlocked} onApprovalDecision={decideApproval} >
      </ChatTimeline>
      {actionError && <p className="notice error action-error" role="alert">{actionError}</p>}
      {(awayFromLatest || hasNew || readingPaused) && <button type="button" className="new-messages" data-has-new={hasNew}
        title={hasNew ? "有新消息 · 回到最新" : "回到最新"}
        onClick={() => { workspace.cancelResponsiveReading(); manualPause.current = false; followBottom.current = true; setReadingPaused(false); setHasNew(false); setAwayFromLatest(false); if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }}>
        <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M10 4v12m-5-5 5 5 5-5" /></svg>
        <span className="sr-only">{hasNew ? "有新消息 · 回到最新 ↓" : "回到最新 ↓"}</span>
      </button>}
      <ConversationComposer inputRef={composerInput} value={draft} onChange={setDraft}
        onKeyDown={(event) => { if (shouldSubmitOnEnter({ key: event.key, shiftKey: event.shiftKey, isComposing: event.nativeEvent.isComposing, keyCode: event.nativeEvent.keyCode })) { event.preventDefault(); void send(); } }}
        onSend={() => void send()} disabled={!connection || sending || writesBlocked || !draft.trim()} sending={sending}
        model={workspace.routerModel} onFiles={() => openPreview("files")} onConfigure={() => selectSetting("environment")}
        onApprovals={() => openTaskView("approvals", "")} approvalCount={pendingApprovals.length}
        home={home} workspaceName={currentWorkspaceName} native={native} connected={!!connection}
        error={outgoing.some(item => item.status === "failed") ? "" : sendError} requestId={pending?.requestId} />
    </main>
    {executionOpen && <main className="execution-screen" aria-label="本对话执行详情" onKeyDown={event => { if (event.key === "Escape" && !detailOpen) { event.stopPropagation(); closeExecution(); } }}>
      <header className="execution-screen-header"><button type="button" ref={executionBack} onClick={closeExecution}>← 返回对话</button><h2>执行详情</h2></header>
      <div className="execution-screen-content" hidden={detailOpen} onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); closeExecution(); } }}>
        <ChatTimeline executionView messages={messages} events={timelineEvents} outgoing={[]} quests={quests} workerRuns={agents.items} approvals={[]} native={native} connected={!!connection}
          historyMore={hasMore || timelineHasMore} historyBusy={historyBusy} timelineError={timelineError} conversationError="" approvalError="" canRetry={false} scrollRef={executionScroll}
          onScroll={() => {}} onInspect={() => {}} onLoadOlder={() => void loadOlder()} onRetry={() => {}} onQuest={id => openTaskView("quests", id)} onApproval={() => {}}
          approvalsBlocked onApprovalDecision={() => {}} connection={connection} onHistoryLoaded={recordHistoryStats}
          onOpenFile={path => openPreview("files", path)} onOpenHistory={(receiptId,path) => openPreview("history",path,undefined,receiptId)} historyStats={historyStats.identity === connectionKey ? historyStats.files : {}}>
          <ConversationAgents fullPage feed={agents} onSelect={revealRun} />
          {!agents.items.length && <p className="notice">本对话的执行记录可在下方查看。</p>}
        </ChatTimeline>
      </div>
      {detailOpen && connection && selectedAgent && <ConversationAgentDetail fullPage key={`${connectionKey}:${selectedAgent.runId}`} connection={connection} runId={selectedAgent.runId} onBack={closeAgentDetail} onTask={id => openTaskView("quests",id)} onActivity={() => openTaskView("activity", "")} />}
    </main>}
    {previewPresent && <PreviewDivider width={previewWidthPixels} minimum={previewBounds.minimum} maximum={previewBounds.maximum} onResize={resizePreview} onReset={() => resizePreview(initialPreviewWidth)} />}
    <FilePreviewWorkspace source={fileSource} native={native} identity={connectionKey} connection={connection} onRequest={revealRequest} onHistoryLoaded={recordHistoryStats} workspaceName={currentWorkspaceName} open={previewOpen} present={previewPresent} visible={chatVisible} expanded={previewExpanded} onToggleExpanded={() => { captureReadingPosition(); setPreviewExpanded((value) => !value); }} request={previewRequest} onClose={closePreview} onChanges={setWorkspaceChanges} />
    <div id="conversation-agent-panel" className="agent-panel-slot" hidden={!agentOpen} onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); captureReadingPosition(); setAgentPreference(false); document.querySelector<HTMLButtonElement>('.conversation-more-toggle')?.focus(); } }}>
      <AgentPanel key={connectionKey} state={agents} activeMore={agents.activeMore} onActiveOlder={agents.loadActive} onOlder={agents.loadOlder} sources={sources} events={timelineEvents} conversationFiles={conversationFiles.length} onConversationFiles={() => openPreview("conversation", undefined, conversationFiles)} filesChanged={workspaceChanges} onChanges={() => openPreview("changes")} onSource={path => openPreview("files", path || undefined)} onRun={revealRun} onActivity={() => openTaskView("activity", "")} onClose={() => { captureReadingPosition(); setAgentPreference(false); document.querySelector<HTMLButtonElement>('.conversation-more-toggle')?.focus(); }} />
    </div>

    </div>;
}


