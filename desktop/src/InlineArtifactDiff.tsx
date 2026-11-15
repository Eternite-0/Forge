import { useEffect, useRef, useState } from "react";
import type { Connection } from "./api";
import { readArtifactDiff, type ArtifactDiff } from "./artifactHistory";
import CodePreview from "./CodePreview";
import { errorMessage } from "./logic";
import { relativeFilePath } from "./workspaceFiles";

export interface InlineArtifactMemory {
  record?: ArtifactDiff;
  reading?: { top: number; left: number };
}

interface Props {
  connection: Connection | null;
  receiptId: string;
  path: string;
  visible: boolean;
  memory: InlineArtifactMemory;
  onInspect: () => void;
  onLoaded?: (record: ArtifactDiff) => void;
  onOpenHistory?: (receiptId: string, path: string) => void;
}

/** The inline view uses the same immutable receipt as the full history preview. */
export default function InlineArtifactDiff(props: Props) {
  const identity = props.connection ? `${props.connection.base_url}|${props.connection.workspace_id || ""}|${props.connection.generation}` : "disconnected";
  return <ReceiptDiff key={`${identity}|${props.receiptId}`} {...props} />;
}

function ReceiptDiff({ connection, receiptId, path, visible, memory, onInspect, onLoaded, onOpenHistory }: Props) {
  const [state, setState] = useState<{ loading: boolean; error: string; record?: ArtifactDiff }>(() => ({ loading: false, error: "", record: memory.record }));
  const [refresh, setRefresh] = useState(0);
  const [copyStatus, setCopyStatus] = useState("");
  const completed = useRef(memory.record ? 0 : -1), callback = useRef(onLoaded), timer = useRef<number>();
  callback.current = onLoaded;
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (!visible || !connection || completed.current === refresh) return;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    let current = true;
    setState((old) => ({ ...old, loading: true, error: "" }));
    void readArtifactDiff(connection, receiptId, controller.signal).then((record) => {
      if (!current) return;
      if (record.path !== relativeFilePath(path)) throw new Error("回执中的文件路径不匹配。");
      completed.current = refresh;
      memory.record = record;
      setState({ loading: false, error: "", record });
      callback.current?.(record);
    }).catch((problem) => {
      if (!current) return;
      completed.current = refresh;
      setState({ loading: false, error: errorMessage(problem) });
    }).finally(() => window.clearTimeout(timeout));
    return () => { current = false; controller.abort(); window.clearTimeout(timeout); };
  }, [connection?.base_url, connection?.workspace_id, connection?.generation, connection?.bearer, receiptId, path, visible, refresh]);
  const copy = async () => {
    if (!state.record) return;
    onInspect();
    try { await navigator.clipboard.writeText(state.record.text); setCopyStatus("已复制更改"); }
    catch { setCopyStatus("无法复制，请手动选择代码。"); }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopyStatus(""), 2000);
  };
  return <div className="inline-artifact-diff" aria-label={`${path} 记录的更改`} aria-busy={state.loading}>
    <header className="inline-diff-heading"><span className="inline-diff-path" title={path}>{path}</span>{state.record && <><span className="file-added">+{state.record.added}</span><span className="file-removed">−{state.record.removed}</span>{state.record.execution_mode === "mock" && <small>模拟</small>}</>}<div className="inline-diff-actions">
      {onOpenHistory && <button type="button" className="text-button" aria-label={`查看文件历史 ${path}`} onClick={() => { onInspect(); onOpenHistory(receiptId, path); }}>记录的更改</button>}
      <button type="button" className="icon-button" aria-label={`复制文件更改 ${path}`} title="复制更改" disabled={!state.record} onClick={() => void copy()}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><rect x="5" y="5" width="8" height="9" rx="1.5" /><path d="M10 5V3.5A1.5 1.5 0 0 0 8.5 2h-5A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" /></svg></button>
    </div></header>
    {!connection && <p className="inline-diff-notice">连接 Core 后查看记录的更改。</p>}
    {state.loading && <p className="inline-diff-notice" role="status">正在读取更改…</p>}
    {state.error && <div className="inline-diff-notice"><p role="alert">{state.error}</p><button type="button" className="text-button" onClick={() => { onInspect(); setRefresh((value) => value + 1); }}>重试</button></div>}
    {state.record && !state.loading && !state.error && <>
      {state.record.text ? <CodePreview text={state.record.text} path={`${state.record.path}:receipt:${receiptId}`} diff compact initialScroll={memory.reading} onReadingPosition={(position) => { memory.reading = position; }} /> : <p className="inline-diff-notice">此记录没有文本行差异。</p>}
      {state.record.truncated && <p className="inline-diff-notice" role="status">仅显示已读取部分；增删数以完整记录为准。</p>}
    </>}
    {copyStatus && <span className="inline-diff-copy-status" role="status">{copyStatus}</span>}
  </div>;
}
