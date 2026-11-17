import { useEffect, useRef, useState } from "react";
import type { Connection } from "./api";
import AnimatedDetails from "./AnimatedDetails";
import CodePreview from "./CodePreview";
import { errorMessage } from "./logic";
import { readArtifactDiff, type ArtifactDiff, type ArtifactExpectation } from "./artifactHistory";

export interface ArtifactHistoryPreviewProps {
  connection: Connection | null;
  receiptId: string;
  expected?: ArtifactExpectation;
  onClose: () => void;
  onRequest?: (messageId: string) => void | boolean;
  onLoaded?: (record: ArtifactDiff) => void;
  visible?: boolean;
}

export default function ArtifactHistoryPreview(props: ArtifactHistoryPreviewProps) {
  const { connection, receiptId } = props;
  const identity = connection ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}|${connection.bearer}` : "disconnected";
  // A different workspace, Core generation or receipt must never paint a previous artifact.
  return <ArtifactHistoryDocument key={`${identity}|${receiptId}|${props.expected?.runId || ""}|${props.expected?.path || ""}|${props.expected?.sha256 || ""}|${props.expected?.executionMode || ""}`} {...props} />;
}

function ArtifactHistoryDocument({ connection, receiptId, expected, onClose, onRequest, onLoaded, visible = true }: ArtifactHistoryPreviewProps) {
  const [state, setState] = useState<{ loading: boolean; error: string; document?: ArtifactDiff }>({ loading: !!connection, error: "" });
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState("");
  const requestSerial = useRef(0);
  const loaded = useRef(onLoaded), noticeTimer = useRef<number>();
  loaded.current = onLoaded;
  useEffect(() => () => window.clearTimeout(noticeTimer.current), []);
  const revealRequest = (messageId: string) => {
    if (onRequest?.(messageId) === false) {
      setNotice("关联消息尚未加载，请先加载更早消息。");
      window.clearTimeout(noticeTimer.current);
      noticeTimer.current = window.setTimeout(() => setNotice(""), 3500);
    }
  };
  useEffect(() => {
    const serial = ++requestSerial.current;
    if (!connection) { setState({ loading: false, error: "" }); return; }
    const pending = new AbortController();
    const timeout = window.setTimeout(() => pending.abort(), 30000);
    let current = true;
    setState((old) => ({ ...old, loading: true, error: "" }));
    void readArtifactDiff(connection, receiptId, pending.signal, expected)
      .then((document) => { if (current && serial === requestSerial.current) { setState({ loading: false, error: "", document }); loaded.current?.(document); } })
      .catch((problem) => { if (current && serial === requestSerial.current) setState((old) => ({ ...old, loading: false, error: errorMessage(problem) })); })
      .finally(() => window.clearTimeout(timeout));
    return () => { current = false; pending.abort(); window.clearTimeout(timeout); };
  }, [connection?.base_url, connection?.workspace_id, connection?.generation, connection?.bearer, receiptId, expected?.receiptId, expected?.runId, expected?.path, expected?.sha256, expected?.executionMode, refresh]);
  const document = state.document, available = !!document && !state.loading && !state.error;
  const originalRequest = document?.origin?.original_request_message_id, goalUpdate = document?.origin?.goal_update_message_id;
  return <section className="artifact-history-preview" aria-label="文件历史更改" aria-busy={state.loading} hidden={!visible}>
    <header className="artifact-history-heading"><div><strong>记录的更改</strong><span title={document?.path}>{available ? document.path : "工具回执"}</span></div><div className="artifact-history-actions">
      <button type="button" className="icon-button" aria-label="重新读取记录的更改" title="重新读取记录的更改" disabled={!connection || state.loading} onClick={() => setRefresh((value) => value + 1)}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><path d="M13 7a5 5 0 1 0-1.4 4.5M13 3v4H9" /></svg></button>
      <button type="button" className="icon-button" aria-label="关闭记录的更改" title="关闭记录的更改" onClick={onClose}>×</button>
    </div></header>
    {!connection && <p className="preview-notice" role="status">连接 Core 后查看记录的更改。</p>}
    {state.loading && <p className="preview-notice" role="status">正在读取记录的更改…</p>}
    {state.error && <div className="artifact-history-error"><p className="preview-notice error" role="alert">{state.error}</p><button type="button" className="text-button" disabled={!connection || state.loading} onClick={() => setRefresh((value) => value + 1)}>重试</button></div>}
    {notice && <p className="preview-notice" role="status">{notice}</p>}
    <div className="artifact-history-record" hidden={!available}>
      {document && <><div className="artifact-history-summary"><span>{document.change === "created" ? "创建文件" : "修订文件"}</span><span className="file-added">+{document.added.toLocaleString()}</span><span className="file-removed">−{document.removed.toLocaleString()}</span><span className={`artifact-history-mode${document.execution_mode === "mock" ? " is-mock" : ""}`}>{document.execution_mode === "live" ? "真实执行" : "模拟记录"}</span></div>
      <div className="artifact-history-context"><span>不可变工具回执</span><time dateTime={document.created_at}>{new Date(document.created_at).toLocaleString()}</time>{document.execution_mode === "mock" && <span>此回执标记为模拟模式，不作为真实模型执行证明。</span>}</div>
      {onRequest && (originalRequest || goalUpdate) && <div className="artifact-history-origin"><span>关联任务的请求</span>{originalRequest && <button type="button" className="text-button" onClick={() => revealRequest(originalRequest)}>查看原始请求</button>}{goalUpdate && goalUpdate !== originalRequest && <button type="button" className="text-button" onClick={() => revealRequest(goalUpdate)}>查看任务目标更新</button>}</div>}
      <AnimatedDetails className="artifact-history-details" onInspect={() => {}} summary={<><span>记录信息</span><svg data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></>}><dl><dt>回执</dt><dd>{document.receipt_id}</dd>{document.base_receipt_id && <><dt>基础回执</dt><dd>{document.base_receipt_id}</dd></>}<dt>任务</dt><dd>{document.quest_id}</dd><dt>Run</dt><dd>{document.run_id}</dd><dt>SHA256</dt><dd>{document.sha256}</dd>{document.base_sha256 && <><dt>基础 SHA256</dt><dd>{document.base_sha256}</dd></>}</dl></AnimatedDetails>
      {document.truncated && <p className="preview-notice" role="status">更改内容较长，仅显示已读取部分；增删数以完整记录为准。</p>}
      {!document.text && <p className="preview-notice">此记录没有文本行差异。</p>}
      </>}
    </div>
    <div className="artifact-history-source" hidden={!available || !document?.text}>{document && <CodePreview text={document.text} path={`${document.path}:receipt:${document.receipt_id}`} diff />}</div>
  </section>;
}
