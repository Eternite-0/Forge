import Markdown from "../Markdown";
import { messageMetadata, type ChatMessage, type Outgoing } from "../../timeline";

type Props = { native: boolean; onInspect: () => void } & (
  { message: ChatMessage; outgoing?: never; canRetry?: never; onRetry?: never }
  | { outgoing: Outgoing; message?: never; canRetry: boolean; onRetry: (item: Outgoing) => void }
);

export default function UserMessage({ message, outgoing, native, onInspect, canRetry, onRetry }: Props) {
  const metadata = messageMetadata(message?.metadata);
  return <article data-scroll-key={message ? `message:${message.id}` : `message:outgoing:${outgoing.requestId}`} className="message from-user">
    <div className="message-meta"><span className="message-avatar">你</span><strong>你</strong>
      {outgoing ? <span>{outgoing.status === "sending" ? "发送中" : "发送失败"}</span> : <>
        {metadata.execution_mode === "mock" && <span>模拟</span>}{metadata.execution_mode === "live" && <span>真实执行</span>}
        <time dateTime={message?.created_at}>{message?.created_at ? new Date(message.created_at).toLocaleString() : ""}</time>
      </>}
    </div>
    <div className="message-body"><Markdown content={message?.content ?? outgoing?.content ?? ""} native={native} onInspect={onInspect} />
      {outgoing?.status === "failed" && <div className="outgoing-error" role="alert">{outgoing.error || "请求未完成"} · 请求 ID：<code>{outgoing.requestId}</code>
        <button className="text-button" disabled={!canRetry} onClick={() => onRetry?.(outgoing)}>使用原请求重试</button>
      </div>}
    </div>
  </article>;
}

