export interface ComponentHeartbeat {
  component: string; owner?: string; state: string; last_seen: number;
  timeout_seconds: number; restart_count?: number; next_restart_at?: number;
}
export type HeartbeatState = "healthy" | "stale" | "unknown" | "attention";

/** The API uses Unix seconds, not JavaScript milliseconds or an ISO timestamp. */
export function heartbeatState(row: ComponentHeartbeat, nowSeconds: number): HeartbeatState {
  if (![row.last_seen, row.timeout_seconds, nowSeconds].every(Number.isFinite) || row.timeout_seconds <= 0 || row.last_seen > nowSeconds + 5) return "unknown";
  if (nowSeconds - row.last_seen >= row.timeout_seconds) return "stale";
  return row.state === "ok" ? "healthy" : "attention";
}
