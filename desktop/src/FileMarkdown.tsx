import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeExternalUrl } from "./logic";
import { relativeFilePath } from "./workspaceFiles";

// File links resolve against this document, never against the WebView origin.
export function resolveDocumentLink(document: string, href: string): string | null {
  if (!href || href.startsWith("#") || /^[a-z][a-z\d+.-]*:/i.test(href) || href.startsWith("/")) return null;
  try {
    const parts = document.split("/").slice(0, -1);
    for (const part of decodeURIComponent(href.split(/[?#]/)[0]).replace(/\\/g, "/").split("/")) {
      if (!part || part === ".") continue;
      if (part === "..") { if (!parts.length) return null; parts.pop(); } else parts.push(part);
    }
    return relativeFilePath(parts.join("/"));
  } catch { return null; }
}

export default function FileMarkdown({ content, path, native, onOpen }: { content: string; path: string; native: boolean; onOpen: (path: string) => void }) {
  const [status, setStatus] = useState("");
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const report = (value: string) => { setStatus(value); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setStatus(""), 2500); };
  return <div className="markdown file-markdown"><ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={{
    a({ href, children }) {
      const external = safeExternalUrl(href);
      if (external) return native ? <button type="button" className="markdown-link" onClick={() => void invoke("open_external", { url: external }).catch(() => report("无法打开链接。"))}>{children}</button> : <a href={external} target="_blank" rel="noopener noreferrer">{children}</a>;
      const relative = href ? resolveDocumentLink(path, href) : null;
      return relative ? <button type="button" className="markdown-link" title={relative} onClick={() => onOpen(relative)}>{children}</button> : <span>{children}</span>;
    },
    img({ alt }) { return <span className="image-placeholder">图片未自动加载{alt ? `：${alt}` : ""}</span>; },
  }}>{content}</ReactMarkdown>{status && <span className="copy-feedback" role="status">{status}</span>}</div>;
}
