import { useEffect, useRef, useState } from "react";
import AnimatedDetails from "./AnimatedDetails";
import { request, type Connection } from "./api";
import { errorMessage } from "./logic";
import { statusLabel } from "./presentation";

export interface WorkerRun {
  run_id: string;
  worker_id?: string;
  quest_id?: string;
  role?: string;
  state?: string;
  /** Client display observation; older paged states are not live assertions. */
  last_confirmed?: boolean;
  lease_status?: "active" | "expired" | "none";
  provider_id?: string;
  model_id?: string;
  steps?: number;
  tool_spent?: number;
  created_at?: string;
  output_contract?: string;
}

export interface ActivityState {
  items: WorkerRun[];
  loading: boolean;
  error: string;
  hasMore: boolean;
  connected: boolean;
}

interface ActivityPage { items: WorkerRun[]; has_more?: boolean }
interface ActivitySnapshot { identity: string; state: ActivityState }

const emptyActivity = (connected: boolean): ActivityState => ({
  items: [], loading: connected, error: "", hasMore: false, connected,
});
const connectionIdentity = (connection: Connection | null) => connection
  ? `${connection.base_url}|${connection.workspace_id || ""}|${connection.generation}` : "";

/** This read-only feed runs independently so a slow activity request cannot delay chat. */
export function useWorkerActivity(connection: Connection | null, beforeUpdate?: () => void): ActivityState {
  const identity = connectionIdentity(connection);
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const beforeUpdateRef = useRef(beforeUpdate);
  beforeUpdateRef.current = beforeUpdate;
  const [snapshot, setSnapshot] = useState<ActivitySnapshot>(() => ({ identity, state: emptyActivity(!!connection) }));

  useEffect(() => {
    if (!connection) {
      setSnapshot({ identity, state: emptyActivity(false) });
      return;
    }
    let active = true;
    let inFlight = false;
    let controller: AbortController | null = null;
    setSnapshot({ identity, state: emptyActivity(true) });
    const accepts = () => active && currentIdentity.current === identity;
    const poll = async () => {
      if (!accepts() || inFlight) return;
      inFlight = true;
      const pending = new AbortController();
      controller = pending;
      const timeout = window.setTimeout(() => pending.abort(), 120000);
      try {
        const page = await request<ActivityPage>(connection, "/api/activity?limit=100", { signal: pending.signal });
        if (!Array.isArray(page.items)) throw new Error("Core 返回了无效执行记录。");
        const items = [...new Map(page.items.filter((item) => item && typeof item === "object" && typeof item.run_id === "string" && item.run_id)
          .map((item) => [item.run_id, item])).values()];
        if (accepts()) {
          beforeUpdateRef.current?.();
          setSnapshot({ identity, state: { items, loading: false, error: "", hasMore: page.has_more === true, connected: true } });
        }
      } catch (error) {
        // Old running states must never be presented as the current state after a failed read.
        if (accepts()) {
          beforeUpdateRef.current?.();
          setSnapshot({ identity, state: { items: [], loading: false, error: errorMessage(error), hasMore: false, connected: true } });
        }
      } finally {
        window.clearTimeout(timeout);
        if (controller === pending) controller = null;
        inFlight = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 6000);
    return () => { active = false; window.clearInterval(timer); controller?.abort(); };
  }, [identity, connection?.bearer]);

  // Hide a previous workspace synchronously, before the effect starts its next request.
  return snapshot.identity === identity ? snapshot.state : emptyActivity(!!connection);
}

const field = (value: unknown, fallback: string) => typeof value === "string" && value ? value : fallback;
const RUN_PRIORITY: Record<string, number> = { running: 0, blocked: 1, queued: 2, review_pending: 3, paused: 4 };
const priority = (run: WorkerRun) => RUN_PRIORITY[field(run.state, "")] ?? 5;

const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString() : "未知";

/** Match callers by run_id; a Worker's newer run cannot replace a historical run. */
export function WorkerRunDetails({ run, runId, workerId, historicalEvent, historicalTime, initialExpanded = false, onExpandedChange, onInspect = () => {}, scrollKey }: {
  run?: WorkerRun; runId?: string | null; workerId?: string | null; historicalEvent?: string; historicalTime?: string;
  initialExpanded?: boolean; onExpandedChange?: (expanded: boolean) => void; onInspect?: () => void; scrollKey?: string;
}) {
  const matched = run && typeof runId === "string" && run.run_id === runId ? run : undefined;
  return <AnimatedDetails className="worker-run-details" summaryLabel={`查看 Worker 执行详情${runId ? ` ${runId}` : ""}`} scrollKey={scrollKey}
    initialExpanded={initialExpanded} onExpandedChange={onExpandedChange} onInspect={onInspect}
    summary={<><span>执行详情</span><svg data-chevron viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg></>}>
    <div className="worker-run-details-body">
      {historicalEvent && <dl className="worker-history-facts"><dt>历史事件</dt><dd>{historicalEvent}</dd><dt>记录时间</dt><dd>{historicalTime || "时间未记录"}</dd></dl>}
      {!matched && <p className="worker-details-notice">执行详情暂未加载。</p>}
      {matched && <><dl className="worker-current-facts">
        <dt>角色</dt><dd>{field(matched.role, "未记录")}</dd>
        <dt>供应商</dt><dd>{field(matched.provider_id, "未记录")}</dd>
        <dt>模型</dt><dd>{field(matched.model_id, "模型未记录")}</dd>
        <dt>当前执行状态</dt><dd>{statusLabel(matched.state)}</dd>
        <dt>已记录步数</dt><dd>{count(matched.steps)}</dd>
        <dt>工具调用次数</dt><dd>{count(matched.tool_spent)}</dd>
      </dl>{historicalEvent && <p className="worker-details-notice">当前执行状态来自最近读取的运行记录，历史事件保留当时的记录。</p>}</>}
      <dl className="worker-identity-facts"><dt>Worker</dt><dd>{field(matched?.worker_id || workerId, "未记录")}</dd><dt>Run</dt><dd>{field(runId, "未记录")}</dd></dl>
    </div>
  </AnimatedDetails>;
}

export default function WorkerActivity({ state, quests, onQuest, onActivity }: {
  state: ActivityState;
  quests: { id: string; title: string }[];
  onQuest: (id: string) => void;
  onActivity: () => void;
}) {
  const visible = state.connected && !state.error ? [...state.items].sort((left, right) => priority(left) - priority(right)).slice(0, 3) : [];
  const notice = !state.connected ? "连接 Core 后查看执行记录" : state.loading ? "正在读取执行记录…"
    : state.error ? "执行状态暂不可用" : !state.items.length ? "暂无执行记录" : "";
  const summary = `最近执行${state.connected && !state.loading && !state.error ? ` · 已加载 ${state.items.length} 条${state.hasMore ? " · 还有更早记录" : ""}` : ""}`;
  return <section className="worker-activity" aria-label="Worker 执行记录" aria-busy={state.loading}>
    <div className="worker-activity-header"><div className="worker-activity-heading"><strong>Worker</strong><small title={summary}>{summary}</small></div>
      <button type="button" className="text-button worker-activity-open" disabled={!state.connected} onClick={onActivity}>查看运行活动</button>
    </div>
    {notice && <p className="worker-activity-notice" title={state.error || undefined}>{notice}</p>}
    {visible.length > 0 && <div className="worker-activity-list">{visible.map((run) => {
      const quest = quests.find((item) => item.id === run.quest_id);
      const model = field(run.model_id, "模型未记录");
      const title = `角色：${field(run.role, "未记录")}\n供应商：${field(run.provider_id, "未记录")}\n模型：${model}\nWorker：${field(run.worker_id, "未记录")}\nRun：${run.run_id}\n任务：${quest?.title || field(run.quest_id, "未记录")}`;
      const content = <><span className="worker-run-content"><span className="worker-run-heading"><strong>{field(run.role, "Worker")}</strong><span className="worker-run-model">{model}</span></span><span className="worker-run-quest">{quest?.title || "关联任务未加载"}</span></span>
        <span className="worker-run-state" data-state={field(run.state, "unknown")}>{statusLabel(run.state)}</span></>;
      return <div className="worker-run-entry" key={run.run_id}>
        {quest ? <button type="button" className="worker-run" title={title} onClick={() => onQuest(quest.id)}>{content}</button>
          : <div className="worker-run" title={title}>{content}</div>}
        <WorkerRunDetails run={run} runId={run.run_id} />
      </div>;
    })}</div>}
  </section>;
}
