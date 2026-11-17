import type { ActivityState } from "../ConversationRuntime";
import "../styles/conversation-context.css";

interface ContextProps {
  workspace: string;
  model: string;
  connected: boolean;
  activity: ActivityState;
  approvals: number;
  health: Record<string, { updatedAt?: number; error?: string }>;
  onActivity: () => void;
  onApprovals: () => void;
  onFiles: () => void;
}

/** Context belongs to the permanent conversation, not the selected inspector.
 * Each source declares its own availability; a connected Core is not proof
 * that tasks, approval state or model configuration have finished loading. */
export default function ConversationContext({ workspace, model, connected, activity, approvals, health, onActivity, onApprovals, onFiles }: ContextProps) {
  const running = activity.items.filter(run => ["running", "queued", "starting"].includes(run.state || "")).length;
  const status = (key: string) => !connected ? "未连接" : health[key]?.error ? "读取失败" : health[key]?.updatedAt ? "已更新" : "读取中";
  const approvalLabel = !connected || !health.approvals?.updatedAt || health.approvals?.error ? `审批 · ${status("approvals")}` : `待审批 ${approvals}`;
  return <section className="conversation-context" aria-label="当前对话上下文">
    <details>
      <summary><span>当前上下文</span><span className="context-workspace" title={workspace}>{workspace}</span><span aria-hidden="true">⌄</span></summary>
      <div className="context-details">
        <dl><div><dt>工作空间</dt><dd>{workspace}</dd></div><div><dt>对话模型</dt><dd>{model}</dd></div></dl>
        <p>对话和草稿保持在此工作空间；打开任务或文件不会新建对话。</p>
        <div className="context-sources" aria-label="独立数据源状态">
          {([["conversation", "对话"], ["timeline", "执行记录"], ["quests", "任务"], ["settings", "配置"]] as const).map(([key, label]) => <span key={key} title={health[key]?.error}>{label} · {status(key)}</span>)}
        </div>
        <button type="button" onClick={onFiles} disabled={!connected}>查看工作空间文件</button>
      </div>
    </details>
    <div className="context-actions">
      <button type="button" onClick={onActivity} disabled={!connected} title={activity.error || "查看后台活动；不会中断当前对话"}>{activity.error ? "活动读取失败" : activity.loading ? "活动读取中" : running ? `执行中 ${running}` : "后台活动"}</button>
      <button type="button" onClick={onApprovals} disabled={!connected}>{approvalLabel}</button>
    </div>
  </section>;
}
