import type { TimelineEvent } from "../../timeline";
import { text, type Row } from "./eventModel";

export function ActivityIcon({ event }: { event?: TimelineEvent }) {
  const detail = event?.detail && typeof event.detail === "object" ? event.detail as Row : {};
  const tool = text(detail.tool, "");
  const shape = event?.kind === "context_archive"
    ? <path d="M2.8 5.2h10.4M2.8 8h10.4M2.8 10.8h6.4" />
    : event?.status?.startsWith("worker.")
      ? <><circle cx="8" cy="5.4" r="2.3" /><path d="M3.2 13.2c.4-2.4 2.4-3.7 4.8-3.7s4.4 1.3 4.8 3.7" /></>
      : ["file_write", "file_revise"].includes(tool)
        ? <><path d="m10.4 2.3 3.3 3.3-7.8 7.8-4 .7.7-4 7.8-7.8Z" /><path d="m8.5 4.2 3.3 3.3M2.6 10.1l3.3 3.3" /></>
        : tool === "file_read"
        ? <><path d="M8.6 1.8H4.4A1.6 1.6 0 0 0 2.8 3.4v9.2a1.6 1.6 0 0 0 1.6 1.6h6.4a1.6 1.6 0 0 0 1.6-1.6V5.4L8.6 1.8Z" /><path d="M8.6 2v3.4h3.6" /></>
        : tool && !["shell_inspect", "python_compile", "git_status", "numeric_ast"].includes(tool)
          ? <path d="M9.6 2.6a3.4 3.4 0 0 0-4.2 4.2L2.6 9.6a1.6 1.6 0 0 0 2.2 2.2l2.8-2.8a3.4 3.4 0 0 0 4.2-4.2L10 6.6 9.4 6 8.8 5.4l1.6-1.6Z" />
          : <><rect x="1.75" y="3" width="12.5" height="10" rx="2" /><path d="m4.4 6.6 1.7 1.4-1.7 1.4m4.2.2h3" /></>;
  return <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shape}</svg>;
}

export function Chevron() {
  return <svg data-chevron viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>;
}


