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
