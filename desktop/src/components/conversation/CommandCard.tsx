import { useLayoutEffect, useRef, useState } from "react";
import { toolLabel } from "../../presentation";
import { text, type Row } from "./eventModel";
import useTransientStatus from "./useTransientStatus";

function updateOutputEdges(element: HTMLDivElement) {
  const overflow = Math.max(0, element.scrollHeight - element.clientHeight);
  // A reversed terminal starts at the bottom (0) and scrolls upward to negative offsets.
  const reverse = getComputedStyle(element).flexDirection === "column-reverse";
  const fromTop = Math.max(0, Math.min(overflow, reverse ? overflow + element.scrollTop : element.scrollTop));
  element.dataset.canScrollUp = String(fromTop > 1);
  element.dataset.canScrollDown = String(overflow - fromTop > 1);
}

export default function CommandCard({ detail, status, executionMode, visible, expanded, onExpandedChange, savedScrollTop, onScroll, onInspect }: { detail: Row; status?: string | null; executionMode?: string | null; visible: boolean; expanded: boolean; onExpandedChange: (value: boolean) => void; savedScrollTop: number; onScroll: (scrollTop: number) => void; onInspect: () => void }) {
  const [copyStatus, setCopyStatus] = useTransientStatus();
  const [longCommand, setLongCommand] = useState(false);
  const commandRef = useRef<HTMLButtonElement>(null);
  const outputRef = useRef<HTMLDivElement>(null);
  const command = text(detail.command, "");
  // The current Core normally returns a receipt digest, not stdout. Never infer output from it.
  const output = [detail.stdout, detail.output, detail.stderr].filter((value): value is string => typeof value === "string").join("\n");
  useLayoutEffect(() => {
    const element = commandRef.current;
    if (!element || !visible) return;
    const measure = () => setLongCommand(element.scrollHeight > 48);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [command, visible, expanded]);
  useLayoutEffect(() => {
    if (visible && outputRef.current) {
      outputRef.current.scrollTop = savedScrollTop;
      updateOutputEdges(outputRef.current);
    }
  }, [visible, savedScrollTop, output]);
  useLayoutEffect(() => {
    const element = outputRef.current;
    if (!element) return;
    const update = () => updateOutputEdges(element);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => observer.disconnect();
  }, [visible, output]);
  const copy = async () => {
    onInspect();
    try { await navigator.clipboard.writeText(command); setCopyStatus("已复制"); }
    catch { setCopyStatus("无法复制，请手动选择代码。"); }
  };
  const copyOutput = async () => {
    onInspect();
    try { await navigator.clipboard.writeText(output); setCopyStatus("已复制输出"); }
    catch { setCopyStatus("无法复制，请手动选择代码。"); }
  };
  const failed = status === "failed" || typeof detail.exit_code === "number" && detail.exit_code !== 0;
  const result = failed ? typeof detail.exit_code === "number" && detail.exit_code !== 0 ? `退出码 ${detail.exit_code}` : "失败" : status === "succeeded" && detail.exit_code === 0 ? "成功" : status === "cancelled" ? "已停止" : ["running", "prepared", "pending"].includes(status || "") ? "" : "结果未知";
  return <div className="command-card"><header><span>{detail.tool === "shell_inspect" ? "Shell" : toolLabel(detail.tool, "命令")}</span>{output && <button type="button" className="command-output-copy" aria-label="复制命令输出" title="复制输出" onClick={() => void copyOutput()}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><rect x="5" y="5" width="8" height="9" rx="1.5" /><path d="M10 5V3.5A1.5 1.5 0 0 0 8.5 2h-5A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" /></svg></button>}</header>
    <div className="command-line"><button ref={commandRef} type="button" className="cmd" data-expanded={expanded} aria-label={`$ ${command}`} aria-expanded={longCommand || expanded ? expanded : undefined} title={longCommand || expanded ? expanded ? "收起完整命令" : "查看完整命令" : command} onClick={() => { if (longCommand || expanded) { onInspect(); onExpandedChange(!expanded); } }}>{command}</button><button type="button" className="copy-btn" aria-label="复制" onClick={() => void copy()}><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5" /><path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" /></svg></button></div>
    {output ? <div ref={outputRef} className="out vertical-scroll-fade-mask" data-can-scroll-up="false" data-can-scroll-down="false" onScroll={(event) => { updateOutputEdges(event.currentTarget); if (visible) onScroll(event.currentTarget.scrollTop); }}><code>{output}</code></div> : <p className="command-no-output">暂无输出</p>}
    <footer className="command-result"><span className={failed ? "event-failed" : result === "结果未知" ? "event-unknown" : undefined}>{result === "成功" && <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true"><path d="m3.5 8 3 3 6-7" /></svg>}{result}{executionMode === "mock" && " · 模拟"}</span></footer>
    {copyStatus && <span className="copy-status" role="status">{copyStatus}</span>}
  </div>;
}


