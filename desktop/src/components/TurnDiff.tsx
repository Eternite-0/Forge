import AnimatedDetails from "../AnimatedDetails";
import ShellIcon from "./ShellIcon";
import { Chevron } from "./conversation/Icons";

export interface TurnDiffFile {
  path: string;
  receiptId: string;
  scrollKey: string;
  added?: number;
  removed?: number;
}

export interface TurnDiffProps {
  files: TurnDiffFile[];
  groupKey: string;
  initialExpanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  onInspect: () => void;
  onOpenFile?: (path: string) => void;
  onOpenHistory?: (receiptId: string, path: string) => void;
}

/** A file summary is derived only from successful live receipts in this activity group. */
export default function TurnDiff({ files, groupKey, initialExpanded, onExpandedChange, onInspect, onOpenFile, onOpenHistory }: TurnDiffProps) {
  if (!files.length) return null;
  const hasStats = files.every(file => typeof file.added === "number" && typeof file.removed === "number");
  const row = (file: TurnDiffFile) => <button type="button" className="activity-file-row turn-diff-file" key={file.path} data-scroll-key={file.scrollKey} title={file.path} disabled={!onOpenFile}
    onClick={() => { onInspect(); onOpenFile?.(file.path); }}>
    <ShellIcon name="document" size={15} />
    <span className="turn-diff-path">{file.path}</span>
    {typeof file.added === "number" && typeof file.removed === "number" && <span className="turn-diff-stats" title="此文件已加载回执的增删数"><span className="file-added">+{file.added.toLocaleString()}</span><span className="file-removed">−{file.removed.toLocaleString()}</span></span>}
    <span className="activity-file-action">预览</span>
  </button>;
  return <section className="turn-diff activity-group-files" aria-label="记录的文件变更">
    <p className="activity-group-files-label sr-only">本组记录的文件</p>
    <header className="turn-diff-header"><span className="turn-diff-icon"><ShellIcon name="changes" size={16} /></span><span className="turn-diff-heading"><span className="turn-diff-title">已编辑 {files.length} 个文件</span>{hasStats && <span className="turn-diff-stats"><span className="file-added">+{files.reduce((sum, file) => sum + file.added!, 0).toLocaleString()}</span><span className="file-removed">−{files.reduce((sum, file) => sum + file.removed!, 0).toLocaleString()}</span></span>}</span>
      {onOpenHistory && <div className="turn-diff-actions"><button type="button" title={files[0].path} onClick={() => { onInspect(); onOpenHistory(files[0].receiptId, files[0].path); }}>查看变更<ShellIcon name="chevronRight" size={13} /></button></div>}
    </header>
    <div className="turn-diff-files">{files.slice(0, 3).map(row)}</div>
    {files.length > 3 && <AnimatedDetails className="activity-more-files turn-diff-more" scrollKey={`group-files:${groupKey}`} initialExpanded={initialExpanded}
      onExpandedChange={onExpandedChange} onInspect={onInspect} summary={<><span>再显示 {files.length - 3} 个文件</span><Chevron /></>}>
      <div className="activity-more-files-body">{files.slice(3).map(row)}</div>
    </AnimatedDetails>}
  </section>;
}

