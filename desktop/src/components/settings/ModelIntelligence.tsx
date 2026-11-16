import { useEffect, useRef, useState } from "react";
import type { Connection } from "../../api";
import { errorMessage } from "../../logic";
import { capabilityLabel } from "../../presentation";
import { SettingsSection } from "../../SettingsView";
import { asRecord, rows, text, type Row } from "../../workspaceTypes";
import { modelProbeInput, PROBE_DIMENSIONS } from "./settingsInputs";
import useSettingsRequests from "./useSettingsRequests";

export interface ModelIntelligenceProps {
  connection: Connection | null;
  models: Row[];
  blocked: boolean;
  onModelsChanged: () => void;
}

const metricLabels: Record<string, string> = {
  transport_success_rate: "请求传输成功率", transport_error_rate: "请求错误率", transport_timeout_rate: "请求超时率",
  task_success_rate: "任务成功率", worker_blocked_rate: "执行阻塞率", tool_call_success_rate: "工具调用成功率",
  review_pass_rate: "节点验证通过率", independent_review_verdict_pass_rate: "独立复核通过率", independent_review_error_rate: "独立复核错误率",
  schema_compliance: "输出结构符合率", retry_rate: "重试比例", retry_count: "重试次数", token_consumption: "已确认 Token 消耗",
  average_latency_seconds: "平均延迟", p95_latency_seconds: "P95 延迟", tokens_per_second: "输出速度", cost_microunits: "已确认费用",
};
const healthLabels: Record<string, string> = { available: "近期传输可用", degraded: "近期传输异常", blocked_unknown: "存在未确认的调用", unknown: "尚无健康观测" };
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const percent = (value: number) => `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
const observedAt = (value: unknown) => typeof value === "string" && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : "未记录";

function measurementValue(name: string, entry: Row): string {
  if (!finite(entry.value)) return "未知";
  if (name.endsWith("_rate") || name === "schema_compliance" || (PROBE_DIMENSIONS as readonly string[]).includes(name)) return percent(entry.value);
  const number = entry.value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (name.endsWith("_seconds")) return `${number} 秒`;
  if (name === "tokens_per_second") return `${number} Token/s`;
  if (name === "cost_microunits") return `${(entry.value / 1000000).toLocaleString(undefined, { maximumFractionDigits: 6 })} ${text(entry.currency, "币种未知")}`;
  return number;
}

function Measurement({ name, value }: { name: string; value: unknown }) {
  const entry = asRecord(value);
  const confidence = asRecord(entry.confidence);
  const interval = finite(confidence.lower) && finite(confidence.upper) ? `${percent(confidence.lower)} – ${percent(confidence.upper)}` : null;
  return <div className="model-measurement"><span>{metricLabels[name] || capabilityLabel(name)}</span><div><strong>{measurementValue(name, entry)}</strong><small>{finite(entry.n_samples) ? `${entry.n_samples} 个样本` : "样本未知"}{interval && ` · 95% 区间 ${interval}`}</small></div></div>;
}

function Snapshot({ value }: { value: Row }) {
  const health = asRecord(value.health), performance = asRecord(value.performance), capabilities = asRecord(value.capabilities);
  const measurements = asRecord(performance.measurements), scores = asRecord(capabilities.measurements);
  const healthKnown = text(health.recommendation, "unknown");
  const references = [...new Set([performance, health, capabilities].flatMap((section) => Array.isArray(section.source_refs) ? section.source_refs.filter((value): value is string => typeof value === "string") : []))];
  return <div className="model-observations">
    <div className="settings-result-record"><strong>{healthLabels[healthKnown] || healthKnown}</strong><small>未确认调用 {text(health.unknown_attempts)} · 执行中 {text(health.in_flight_calls)} · 连续失败 {text(health.consecutive_failures)}</small><small>最近观测：{observedAt(health.observed_at)}</small></div>
    <div className="settings-form-content"><h3 className="settings-block-heading">实际运行观测</h3><p className="notice">以下仅描述已记录的执行；传输成功与任务成功分别统计。</p>{Object.keys(measurements).length ? Object.entries(measurements).map(([name, entry]) => <Measurement key={name} name={name} value={entry} />) : <p className="notice">暂无实际运行样本</p>}{finite(performance.unknown_usage_attempts) && performance.unknown_usage_attempts > 0 && <p className="notice">另有 {performance.unknown_usage_attempts} 次调用的用量尚未确认。</p>}</div>
    <div className="settings-form-content"><h3 className="settings-block-heading">固定样例测量</h3><p className="notice">每个维度为固定的公开小样例，不代表总体能力。视觉未受此文字基准支持。</p>{PROBE_DIMENSIONS.map((name) => <Measurement key={name} name={name} value={scores[name]} />)}<p className="notice">测量时间：{observedAt(capabilities.measured_at)} · 基准：{text(capabilities.suite_version)}</p></div>
    {references.length > 0 && <details className="settings-disclosure settings-form-content"><summary>观测来源（{references.length} 条）</summary><div className="settings-disclosure-body"><ul className="model-source-refs">{references.map((reference) => <li key={reference}>{reference}</li>)}</ul>{finite(performance.source_refs_omitted) && performance.source_refs_omitted > 0 && <p className="notice">另有 {performance.source_refs_omitted} 条执行来源未在本次响应中展开。</p>}</div></details>}
  </div>;
}

export default function ModelIntelligence(props: ModelIntelligenceProps) {
  const identity = props.connection ? `${props.connection.base_url}|${props.connection.workspace_id || ""}|${props.connection.generation}` : "disconnected";
  return <ModelControls key={identity} {...props} />;
}

function ModelControls({ connection, models, blocked, onModelsChanged }: ModelIntelligenceProps) {
  const { run, cancel } = useSettingsRequests();
  const [profileId, setProfileId] = useState(() => typeof models[0]?.id === "string" ? models[0].id : "");
  const [dimensions, setDimensions] = useState<string[]>(["mathematics"]);
  const [busy, setBusy] = useState<"discover" | "benchmark" | null>(null);
  const mutationPending = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [snapshot, setSnapshot] = useState<{ profileId: string; value: Row } | null>(null);
  const [report, setReport] = useState<{ profileId: string; value: Row } | null>(null);
  const selected = models.find((model) => model.id === profileId);
  const canBenchmark = !!connection && !blocked && !busy && selected?.enabled === true && !selected.quarantine_reason && dimensions.length > 0;

  useEffect(() => {
    if (!models.some((model) => model.id === profileId)) setProfileId(typeof models[0]?.id === "string" ? models[0].id : "");
  }, [models, profileId]);

  useEffect(() => {
    setLoadError("");
    setSnapshot(null);
    if (!connection || !profileId) { setLoading(false); return; }
    setLoading(true);
    void run<Row>("snapshot", connection, `/api/model-intelligence/${encodeURIComponent(profileId)}`).then((result) => {
      if (result) { setSnapshot({ profileId, value: result.value }); setLoading(false); }
    }).catch((problem) => { setLoadError(errorMessage(problem)); setLoading(false); });
    return () => cancel("snapshot");
  }, [connection, profileId, reload, run, cancel]);

  const mutate = async (operation: "discover" | "benchmark") => {
    if (!connection || blocked || mutationPending.current || operation === "benchmark" && !canBenchmark) return;
    setError(""); setNotice("");
    let init: RequestInit = { method: "POST" };
    try { if (operation === "benchmark") init = { ...init, body: JSON.stringify(modelProbeInput(profileId, dimensions)) }; }
    catch (problem) { setError(errorMessage(problem)); return; }
    mutationPending.current = true; setBusy(operation);
    try {
      const result = await run<unknown>("mutation", connection, `/api/model-intelligence/${operation}`, init);
      if (result) {
        if (operation === "discover") {
          const added = rows(result.value);
          setNotice(added.length ? `已发现 ${added.length} 个新模型，模型库已刷新。` : "未发现新的模型档案。");
          onModelsChanged();
        } else {
          const value = asRecord(result.value);
          setReport({ profileId, value }); setReload((current) => current + 1);
          setNotice("固定样例测量已记录。请查看各维度结果。");
        }
        setBusy(null);
      }
    } catch (problem) { setError(errorMessage(problem)); setBusy(null); }
    finally { mutationPending.current = false; }
  };

  return <div className="settings-blocks model-intelligence">
    <SettingsSection title="模型发现与测量" description="从已配置的供应商发现模型，查看实际运行观测与固定样例结果。">
      <div className="settings-form-content"><button type="button" className="button" disabled={!connection || blocked || busy !== null} onClick={() => void mutate("discover")}>{busy === "discover" ? "正在发现…" : "从供应商发现模型"}</button>{!connection && <p className="notice">连接 Core 后可读取和测量模型。</p>}</div>
      <label className="field-label">观测模型<select aria-label="观测模型" value={profileId} disabled={!models.length || busy !== null} onChange={(event) => { setProfileId(event.target.value); setError(""); setNotice(""); }}><option value="">选择模型档案</option>{models.filter((model) => typeof model.id === "string").map((model) => <option key={text(model.id)} value={text(model.id)}>{text(model.display_name, text(model.model_id, text(model.id)))} · {text(model.provider)}</option>)}</select></label>
      <div className="settings-form-content"><button className="text-button" type="button" disabled={!connection || !profileId || loading} onClick={() => setReload((current) => current + 1)}>{loading ? "正在读取观测…" : "刷新模型观测"}</button>{loadError && <p className="notice error" role="alert">{loadError}</p>}</div>
      {snapshot?.profileId === profileId && <Snapshot value={snapshot.value} />}
    </SettingsSection>
    <SettingsSection title="运行固定样例" description="点击开始后会真实调用所选模型并消耗预算；每个已选维度至多调用一次，失败不会自动重试。">
      <fieldset className="model-probe-dimensions" disabled={!connection || blocked || busy !== null}><legend>测量维度</legend>{PROBE_DIMENSIONS.filter((dimension) => dimension !== "vision").map((dimension) => <label key={dimension}><input type="checkbox" checked={dimensions.includes(dimension)} onChange={(event) => setDimensions((current) => event.target.checked ? [...current, dimension] : current.filter((item) => item !== dimension))} />{capabilityLabel(dimension)}</label>)}<span className="notice">视觉：当前基准不支持</span></fieldset>
      <div className="settings-form-content">{selected && selected.enabled !== true && <p className="notice">此模型尚未启用，请先在模型档案中启用。</p>}{selected?.quarantine_reason != null && selected.quarantine_reason !== "" && <p className="notice">此模型档案已隔离，暂不能运行测量。</p>}<button className="button" type="button" disabled={!canBenchmark} onClick={() => void mutate("benchmark")}>{busy === "benchmark" ? "正在运行样例…" : "开始固定样例测量"}</button>{busy === "benchmark" && <p className="notice" role="status">等待模型返回。本次测量结果将保留在所选模型的观测中。</p>}{error && <p className="notice error" role="alert">{error} 本次请求未自动重试。</p>}{notice && <p className="notice" role="status">{notice}</p>}</div>
      {report?.profileId === profileId && <div className="settings-result-record"><strong>本次测量 · {text(report.value.mode) === "live" ? "真实执行" : text(report.value.mode) === "mock" ? "模拟" : "来源未知"}</strong><small>{observedAt(report.value.created_at)} · {text(report.value.suite_version)}</small>{rows(report.value.results).map((item, index) => <div className="model-measurement" key={text(item.dimension, String(index))}><span>{capabilityLabel(item.dimension)}</span><div><strong>{item.status === "unsupported" ? "不支持" : item.status === "not_attempted" ? "未执行" : item.status === "provider_failed" ? "调用失败" : item.passed === true ? "通过" : item.passed === false ? "未通过" : "未知"}</strong><small>{finite(item.elapsed_seconds) ? `${item.elapsed_seconds.toLocaleString(undefined, { maximumFractionDigits: 2 })} 秒` : "无耗时记录"}{typeof item.reason_code === "string" && ` · ${item.reason_code}`}</small></div></div>)}</div>}
    </SettingsSection>
  </div>;
}
