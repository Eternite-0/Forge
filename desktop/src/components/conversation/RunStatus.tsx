export type RunState = "running" | "completed" | "failed" | "unknown" | "recorded";

export function StatusIndicator({ state }: { state: RunState }) {
  return state === "running" ? <span className="loading-spinner" aria-label="运行中" />
      : <svg className="run-status-icon" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {state === "completed" ? <path d="m3.5 8 3 3 6-7" />
          : state === "failed" ? <path d="m4.5 4.5 7 7m0-7-7 7" />
            : state === "unknown" ? <><circle cx="8" cy="8" r="5.5" /><path d="M6.5 6a1.5 1.5 0 0 1 3 .3c0 1.2-1.5 1.2-1.5 2.4m0 2h.01" /></>
              : <><circle cx="8" cy="8" r="5.5" /><path d="M8 4.5V8l2.5 1.5" /></>}
      </svg>;
}

/** Only display states supported by the loaded execution records. */
export default function RunStatus({ label, state = "recorded" }: { label: string; state?: RunState }) {
  return <span className="run-status" data-state={state}>
    <StatusIndicator state={state} />
    <span className="event-label">{label}</span>
  </span>;
}

