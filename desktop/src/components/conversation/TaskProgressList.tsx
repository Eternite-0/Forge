import { questDisplayTitle, statusLabel } from "../../presentation";
import type { TaskProgress } from "../../taskProgress";
import "../../styles/task-progress.css";

export default function TaskProgressList({ cards, onQuest, onOpenHistory, visibleHistoryReceipts }: {
  cards: TaskProgress[];
  onQuest: (id: string) => void;
  onOpenHistory?: (receiptId: string, path: string) => void;
  visibleHistoryReceipts?: ReadonlySet<string>;
}) {
  if (!cards.length) return null;
  const completed = cards.find(card => card.quest.status === "completed");
  const visible = completed ? [...cards.filter(card => card !== completed).slice(0, 2), completed] : cards.slice(0, 3);
  return <section className="task-progress-list" aria-label="当前任务进展">
    {visible.map(({ quest, result }) => <div className="task-progress-row" key={quest.id} data-task-id={quest.id}>
      <button type="button" className="task-progress-summary" onClick={() => onQuest(quest.id)}>
        <span className="task-progress-icon" data-state={quest.status} aria-hidden="true"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3">{quest.status === "completed" ? <path d="m3 8 3 3 7-7" /> : <><circle cx="8" cy="8" r="5.5" /><path d="M8 4.5V8l2.5 1.5" /></>}</svg></span>
        <span className="task-progress-title">{questDisplayTitle(quest.title, quest.id, quest.goal)}</span>
        <span className="task-progress-status">{statusLabel(quest.status)}</span>
        <span aria-hidden="true">›</span>
      </button>
      {quest.status === "completed" && !(result && visibleHistoryReceipts?.has(result.receiptId)) && <div className="task-progress-result">{result && onOpenHistory
        ? <button type="button" className="text-button" onClick={() => onOpenHistory(result.receiptId, result.path)}>查看最近文件记录<span>{result.path}</span></button>
        : <span>任务已完成，当前记录中暂无可直接打开的结果。</span>}</div>}
    </div>)}
    {cards.length > 3 && <button type="button" className="text-button task-progress-more" onClick={() => onQuest("")}>另有 {cards.length - 3} 项任务 · 查看全部</button>}
  </section>;
}
