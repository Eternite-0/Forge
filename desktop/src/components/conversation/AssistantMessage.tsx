import Markdown from "../Markdown";
import { messageMetadata, type ChatMessage } from "../../timeline";
import { text } from "./eventModel";
import useTransientStatus from "./useTransientStatus";

function CopyReply({ content, onInspect }: { content: string; onInspect: () => void }) {
  const [status, setStatus] = useTransientStatus();
  const copy = async () => {
    onInspect();
    try { await navigator.clipboard.writeText(content); setStatus("已复制回复"); }
    catch { setStatus("无法复制，请手动选择回复。"); }
  };
  return <div className="message-actions"><button type="button" className="icon-button" aria-label="复制回复" title="复制回复" onClick={() => void copy()}><svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" /><path d="M13 7V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h3" /></svg></button>{status && <span className="copy-feedback" role="status">{status}</span>}</div>;
}


export default function AssistantMessage({ message, native, onInspect }: { message: ChatMessage; native: boolean; onInspect: () => void }) {
  const meta = messageMetadata(message.metadata);
  return <article data-scroll-key={`message:${message.id}`} className="message from-agent">
    <div className="message-meta"><span className="message-avatar">F</span><strong>Router</strong>
      <span className="reply-model" title={[meta.provider_id, meta.model_id].filter(Boolean).join(" / ")}>{meta.source === "router_notice" ? "任务通知" : text(meta.model_id, "模型未记录")}</span>
      {meta.execution_mode === "mock" && <span>模拟</span>}{meta.execution_mode === "live" && <span>真实执行</span>}
      {meta.execution_mode !== "live" && meta.execution_mode !== "mock" && <span>来源未知</span>}
      <time dateTime={message.created_at}>{message.created_at ? new Date(message.created_at).toLocaleString() : ""}</time>
    </div>
    <div className="message-body"><Markdown content={message.content} native={native} onInspect={onInspect} /></div>
    <CopyReply content={message.content} onInspect={onInspect} />
  </article>;
}

