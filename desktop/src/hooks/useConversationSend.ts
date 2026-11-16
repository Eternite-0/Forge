import { ApiError, request } from "../api";
import { appendUnique, errorMessage, pendingMessage, type PendingMessage } from "../logic";
import { type ChatMessage as Message } from "../timeline";
import type { WorkspaceState } from "./useWorkspaceState";
import useConversationFeed from "./useConversationFeed";

export default function useConversationSend(workspace: WorkspaceState & ReturnType<typeof useConversationFeed>) {
  const { connection, setConnection, maintenanceSubmitting, setMessages, outgoing, setOutgoing, setHasNew, draft, setDraft, pending, setPending, sending, setSending, setSendError, followBottom, seenMessages, guard, writesBlocked, refreshConversation } = workspace;
  const sendPending = async (next: PendingMessage, clearComposer: boolean) => {
    if (!connection || sending || writesBlocked || maintenanceSubmitting.current) return;
    const ticket = guard.start("send");
    setPending(next); setSending(true); setSendError("");
    if (!followBottom.current) setHasNew(true);
    setOutgoing((old) => old.some((item) => item.requestId === next.requestId)
      ? old.map((item) => item.requestId === next.requestId ? { ...item, status: "sending", error: undefined } : item)
      : [...old, { ...next, status: "sending", createdAt: new Date().toISOString() }]);
    if (clearComposer) setDraft((current) => current.trim() === next.content ? "" : current);
    try {
      const result = await request<{user_message: Message; assistant_message: Message}>(connection, "/api/conversation/message", { method: "POST", body: JSON.stringify({ content: next.content, request_id: next.requestId }) });
      if (!guard.accepts(ticket)) return;
      if (!result.user_message?.id || !result.assistant_message?.id) throw new Error("Core 返回的消息记录不完整。");
      seenMessages.current.add(result.user_message.id); seenMessages.current.add(result.assistant_message.id);
      setMessages((current) => appendUnique(current, [result.user_message, result.assistant_message]));
      setOutgoing((old) => old.filter((item) => item.requestId !== next.requestId));
      setDraft((current) => current.trim() === next.content ? "" : current);
      setPending(null);
      await refreshConversation();
    } catch (error) {
      if (!guard.accepts(ticket)) return;
      const detail = errorMessage(error);
      setSendError(detail);
      setOutgoing((old) => old.map((item) => item.requestId === next.requestId ? { ...item, status: "failed", error: detail } : item));
      setDraft((current) => current ? current : next.content);
      if (error instanceof ApiError && error.status === 401) setConnection(null);
    } finally { if (guard.accepts(ticket)) setSending(false); }
  };

  const send = () => {
    const failed = outgoing.find((item) => item.status === "failed" && item.content === draft.trim());
    const next = pendingMessage(draft, failed || pending, () => crypto.randomUUID());
    if (next) void sendPending(next, true);
  };
  return { sendPending, send };
}

