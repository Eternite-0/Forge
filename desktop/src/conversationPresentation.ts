/** Only explicitly marked background commentary is removed from ordinary chat.
 * Unmarked assistant replies (including questions) remain visible. */
export function visibleConversationMessage(message: { role: string; metadata?: unknown }): boolean {
  let metadata = message.metadata;
  if (typeof metadata === "string") { try { metadata = JSON.parse(metadata); } catch { metadata = null; } }
  const meta = metadata && typeof metadata === "object" && !Array.isArray(metadata) ? metadata as Record<string, unknown> : {};
  // Router notices also carry completed deliveries and requests for help.
  // Suppress only the explicitly tagged verification-progress variant, not
  // the whole source; an unclassified user-facing update must remain readable.
  return message.role === "user" || message.role === "assistant" && meta.notice_phase !== "review_pending" && meta.phase !== "commentary" && meta.channel !== "commentary";
}

/** This is navigation, never completion evidence or permission to execute.
 * Only the Core-authored oversized-delivery notice may point at an already
 * loaded task in the current workspace. Ordinary/model/mock notices stay plain. */
export function completionNoticeQuestId(message: { role: string; metadata?: unknown }, quests: readonly { id: string }[]): string | null {
  let metadata = message.metadata;
  if (typeof metadata === "string") { try { metadata = JSON.parse(metadata); } catch { return null; } }
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) return null;
  const meta = metadata as Record<string, unknown>;
  if (message.role !== "assistant" || meta.source !== "router_notice" || meta.execution_mode !== "programmatic"
    || meta.notice_phase !== "completion_summary_unavailable" || meta.completion_execution_mode !== "live"
    || typeof meta.quest_id !== "string" || !meta.quest_id.trim()) return null;
  return quests.some(quest => quest.id === meta.quest_id) ? meta.quest_id : null;
}
