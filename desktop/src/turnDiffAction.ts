/** A receipt summary prefers its immutable change, never silently falls back
 * to current disk after a failed history read. Other file browsers opt out. */
export function turnDiffAction(file: { path: string; receiptId: string }, preferHistory: boolean,
  onOpenHistory?: (receiptId: string, path: string) => void, onOpenFile?: (path: string) => void) {
  if (preferHistory && onOpenHistory) return { label: "历史变更", open: () => onOpenHistory(file.receiptId, file.path) };
  return { label: "预览", open: onOpenFile ? () => onOpenFile(file.path) : undefined };
}
