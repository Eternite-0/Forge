import { messageMetadata, type ChatMessage, type TimelineEvent } from "./timeline.ts";

export interface ProgressQuest { id: string; title: string; status: string; goal?: string }
export interface TaskProgress { quest: ProgressQuest; result?: { receiptId: string; path: string }; updatedAt: number }
const active = new Set(["proposed", "approved", "queued", "running", "reviewing"]);
const attention = new Set(["awaiting_approval", "blocked", "needs_input", "failed"]);
const timestamp = (value?: string) => { const time = Date.parse(value || ""); return Number.isFinite(time) ? time : 0; };
function resultPath(value: unknown): string | undefined {
  if (typeof value !== "string") return;
  const path = value.replace(/\\/g, "/").replace(/^\.\//, "");
  if (!path || path.startsWith("/") || path.split("/").some(part => !part || part === ".." || part.includes(":")) || /[\x00-\x1f]/.test(path)) return;
  return path;
}

/** The current backend state wins over historical lifecycle events. No filename,
 * message proximity or worker-result inference establishes a task deliverable. */
export function taskProgress(quests: ProgressQuest[], events: TimelineEvent[], messages: ChatMessage[]): TaskProgress[] {
  const latestUser = Math.max(0, ...messages.filter(message => message.role === "user").map(message => timestamp(message.created_at)));
  const unique = new Map(quests.map(quest => [quest.id, quest]));
  const cards: TaskProgress[] = [];
  for (const quest of unique.values()) {
    const linked = events.filter(event => event.quest_id === quest.id);
    const updatedAt = Math.max(0, ...linked.map(event => timestamp(event.created_at)));
    // Closed history is not a second transcript. Only one completion from the
    // latest loaded user turn is eligible, and no timestamp means no inference.
    const recentCompletion = quest.status === "completed" && latestUser > 0 && updatedAt >= latestUser;
    if (quest.status === "failed" && !(latestUser > 0 && updatedAt >= latestUser)) continue;
    if (!active.has(quest.status) && !attention.has(quest.status) && !recentCompletion) continue;
    let result: TaskProgress["result"];
    if (quest.status === "completed") {
      for (const event of [...linked].sort((a, b) => timestamp(b.created_at) - timestamp(a.created_at))) {
        const detail = messageMetadata(event.detail);
        const path = resultPath(detail.path);
        if (event.kind !== "tool_receipt" || event.status !== "succeeded" || event.execution_mode !== "live" || !event.event_id || !path ||
          !["file_write", "file_revise"].includes(String(detail.tool)) || (typeof detail.exit_code === "number" && detail.exit_code !== 0)) continue;
        result = { receiptId: event.event_id, path }; break;
      }
    }
    cards.push({ quest, result, updatedAt });
  }
  const priority = (card: TaskProgress) => attention.has(card.quest.status) ? 0 : card.quest.status === "completed" ? 2 : 1;
  cards.sort((a, b) => priority(a) - priority(b) || b.updatedAt - a.updatedAt);
  let completed = false;
  return cards.filter(card => {
    if (card.quest.status !== "completed") return true;
    if (completed) return false;
    completed = true; return true;
  });
}
