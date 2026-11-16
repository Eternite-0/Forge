import { invoke } from "@tauri-apps/api/core";
import { Children, isValidElement, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeExternalUrl } from "../logic";

function contentText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (isValidElement<{ children?: ReactNode }>(node)) return contentText(node.props.children);
  return Children.toArray(node).map(contentText).join("");
}

export default function Markdown({ content, native, onInspect = () => {} }: { content: string; native: boolean; onInspect?: () => void }) {
  const [status, setStatus] = useState("");
  const copy = async (value: string) => {
    onInspect();
    try { await navigator.clipboard.writeText(value); setStatus("已复制"); }
    catch { setStatus("无法复制，请手动选择代码。"); }
  };
  const link = (href: string | undefined, children: ReactNode) => {
    const safe = safeExternalUrl(href);
    if (!safe) return <span>{children}</span>;
    if (native) return <button type="button" className="markdown-link" onClick={() => { void invoke("open_external", { url: safe }).catch(() => setStatus("无法在系统浏览器中打开链接。")); }}>{children}</button>;
    return <a href={safe} target="_blank" rel="noopener noreferrer">{children}</a>;
  };
  return <div className="markdown"><ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={{
    a({ href, children }) {
      return link(href, children);
    },
    img({ src, alt }) { return <span className="image-placeholder">图片未自动加载{alt ? `：${alt}` : ""}{safeExternalUrl(src) && <> · {link(src, "打开图片链接")}</>}</span>; },
    pre({ children }) { const code = contentText(children); return <div className="code-block"><div className="code-toolbar"><span>代码</span><button type="button" onClick={() => void copy(code)}>复制</button></div><pre>{children}</pre></div>; },
  }}>{content}</ReactMarkdown>{status && <span className="copy-status" role="status">{status}</span>}</div>;
}


