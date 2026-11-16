import { useCallback, useEffect } from "react";
import { browserConnection, type Connection } from "../api";
import { invoke } from "@tauri-apps/api/core";
import { errorMessage } from "../logic";
import { NativeConnection, NativeStatus } from "../workspaceTypes";
import type { WorkspaceState } from "./useWorkspaceState";

export default function useForgeConnection(workspace: WorkspaceState) {
  const { native, setConnection, setConnectionError, setRuntimeStatus, setRestoreFenced, secretInput, nativeIdentity, nativePollSerial, setMessages, setTimelineEvents, setTimelineBefore, setTimelineHasMore, setOutgoing, setReadingPaused, setBefore, setHasMore, setHistoryBusy, setPending, setSending, followBottom, manualPause, readingAnchor, seenMessages, seenEvents, seenInitialized, historyLoaded, questsLoaded, setQuests, setQuestsBefore, setPendingApprovals, setSelectedId } = workspace;
  const syncNative = useCallback(async () => {
    if (!native) return;
    const serial = ++nativePollSerial.current;
    void invoke<NativeStatus>("runtime_status").then((value) => { if (serial === nativePollSerial.current) setRuntimeStatus(value); }).catch(() => { if (serial === nativePollSerial.current) setRuntimeStatus(null); });
    try {
      const nativeConnection = await invoke<NativeConnection>("connection_get");
      if (serial !== nativePollSerial.current) return;
      const next: Connection = { base_url: nativeConnection.baseUrl, bearer: nativeConnection.bearer,
        workspace_id: nativeConnection.workspaceId, generation: nativeConnection.generation, state: nativeConnection.state };
      if (!next.bearer || !next.base_url || !next.generation) throw new Error("Core 尚未就绪");
      if (nativeIdentity.current?.generation !== next.generation) {
        if (nativeIdentity.current && nativeIdentity.current.workspace !== next.workspace_id) setPending(null);
        nativeIdentity.current = { generation: next.generation, workspace: next.workspace_id || "" };
        historyLoaded.current = false; questsLoaded.current = false;
        setMessages([]); setTimelineEvents([]); setBefore(null); setHasMore(false); setTimelineBefore(null); setTimelineHasMore(false); setHistoryBusy(false); setSending(false); setRestoreFenced(null);
        seenMessages.current.clear(); seenEvents.current.clear(); seenInitialized.current = false; followBottom.current = true; manualPause.current = false; readingAnchor.current = null; setReadingPaused(false);
        setOutgoing((old) => old.map((item) => item.status === "sending" ? { ...item, status: "failed", error: "连接已更新，结果未知；可使用原请求 ID 重试。" } : item));
        setQuests([]); setQuestsBefore(null); setPendingApprovals([]); setSelectedId("");
      }
      setConnection((old) => old?.generation === next.generation && old.base_url === next.base_url ? old : next);
      setConnectionError("");
    } catch (error) {
      if (serial !== nativePollSerial.current) return;
      setConnectionError(errorMessage(error));
      setConnection(null);
    }
  }, [native]);

  useEffect(() => {
    if (!native) return;
    void syncNative();
    const timer = window.setInterval(() => void syncNative(), 4000);
    return () => { nativePollSerial.current += 1; window.clearInterval(timer); };
  }, [native, syncNative]);
  const connectBrowser = async () => {
    const secret = secretInput.current?.value || "";
    if (secretInput.current) secretInput.current.value = "";
    try {
      const next = await browserConnection("http://127.0.0.1:8765", secret);
      historyLoaded.current = false; questsLoaded.current = false;
      setMessages([]); setTimelineEvents([]); setBefore(null); setTimelineBefore(null); setHasMore(false); setTimelineHasMore(false); setHistoryBusy(false); setSending(false); setQuests([]); setQuestsBefore(null); setPendingApprovals([]); setSelectedId("");
      seenMessages.current.clear(); seenEvents.current.clear(); seenInitialized.current = false; followBottom.current = true; manualPause.current = false; readingAnchor.current = null; setReadingPaused(false);
      setOutgoing((old) => old.map((item) => item.status === "sending" ? { ...item, status: "failed", error: "连接已更新，结果未知；可使用原请求 ID 重试。" } : item));
      setConnection(next); setConnectionError("");
    }
    catch (error) { setConnectionError(errorMessage(error)); }
  };
  return { syncNative, connectBrowser };
}

