import type { SVGProps } from "react";

export type ShellIconName = "compose" | "clock" | "memory" | "activity" | "settings" | "folder" | "search" | "document" | "plus" | "chevronDown" | "arrowUp" | "tasks" | "ellipsis";

/** One optical grid for shell controls. These are original outlines, not font glyphs. */
export default function ShellIcon({ name, size = 18, ...props }: SVGProps<SVGSVGElement> & { name: ShellIconName; size?: number }) {
  const paths = {
    compose: <><path d="M9 3.5H5A1.5 1.5 0 0 0 3.5 5v10A1.5 1.5 0 0 0 5 16.5h10a1.5 1.5 0 0 0 1.5-1.5v-4" /><path d="m9 11 1-3 5.5-5.5a1.4 1.4 0 0 1 2 2L12 10l-3 1ZM14 4l2 2" /></>,
    clock: <><circle cx="10" cy="10" r="7" /><path d="M10 5.8v4.5l2.9 1.8" /></>,
    memory: <><path d="M5 4h8.5A1.5 1.5 0 0 1 15 5.5V16H6a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1ZM4 13h11M7 4v9M10 7h2M10 9.5h2" /></>,
    activity: <><path d="m4 6 3 3-3 3M10 13h5" /><rect x="2.5" y="3.5" width="15" height="13" rx="2" /></>,
    settings: <><path d="m8 2.8-.5 1.8-1.5.9-1.8-.4-2 3.5 1.3 1.3v1.8l-1.3 1.3 2 3.5 1.8-.4 1.5.9.5 1.8h4l.5-1.8 1.5-.9 1.8.4 2-3.5-1.3-1.3V9.9l1.3-1.3-2-3.5-1.8.4-1.5-.9-.5-1.8Z" /><circle cx="10" cy="10.8" r="2.5" /></>,
    folder: <path d="M2.5 6.5V5a1.5 1.5 0 0 1 1.5-1.5h3.4l2 2H16A1.5 1.5 0 0 1 17.5 7v7.5A1.5 1.5 0 0 1 16 16H4a1.5 1.5 0 0 1-1.5-1.5v-8h15" />,
    search: <><circle cx="8.5" cy="8.5" r="5" /><path d="m12.2 12.2 4.3 4.3" /></>,
    document: <><path d="M11.5 2.5H5A1.5 1.5 0 0 0 3.5 4v12A1.5 1.5 0 0 0 5 17.5h10a1.5 1.5 0 0 0 1.5-1.5V7.5l-5-5Z" /><path d="M11.5 2.5v5h5M7 11h6M7 14h4" /></>,
    plus: <path d="M10 4v12M4 10h12" />,
    chevronDown: <path d="m6 8 4 4 4-4" />,
    arrowUp: <path d="M10 16V4M5.5 8.5 10 4l4.5 4.5" />,
    tasks: <><rect x="3" y="3" width="14" height="14" rx="2.5" /><path d="m6 8.5.9.9 1.7-1.8M10.5 8.5h3.2m-3.2 3.6h3.2M6.4 12.1h.6" /></>,
    ellipsis: <g fill="currentColor" stroke="none"><circle cx="4.8" cy="10" r="1.1" /><circle cx="10" cy="10" r="1.1" /><circle cx="15.2" cy="10" r="1.1" /></g>,
  };
  return <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...props}>{paths[name]}</svg>;
}
