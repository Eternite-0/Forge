import type { ReactNode } from "react";
import { capabilityLabel } from "./presentation";

type Row = Record<string, unknown>;
const text = (value: unknown, fallback = "未知"): string => value == null || value === "" ? fallback : String(value);
const object = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};

function support(value: unknown): string {
  const entry = object(value);
  const label = entry.supported === true ? "支持" : entry.supported === false ? "不支持" : "未知";
  return entry.source ? `${label} · ${text(entry.source)}` : label;
}

function price(value: unknown, currency: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString()} ${text(currency, "币种未知")} / 百万 Token` : "未知";
}

function MeasurementList({ value, performance = false }: { value: unknown; performance?: boolean }) {
  const entries = Object.entries(object(value));
  if (!entries.length) return <p className="notice">{performance ? "性能测量：未知" : "能力评分：未知"}</p>;
  const performanceNames: Record<string, string> = { tokens_per_second: "输出速度（Token/s）", ttft_seconds: "首 Token 时间（秒）", latency_p50_seconds: "P50 延迟（秒）", latency_p95_seconds: "P95 延迟（秒）", reliability: "可靠率", timeout_rate: "超时率" };
  return <div className="measurement-list">{entries.map(([name, value]) => {
    const entry = object(value);
    return <div key={name}><span>{performance ? performanceNames[name] || name : capabilityLabel(name)}</span><span>{text(entry.value)} · {text(entry.source)} · {entry.n_samples == null ? entry.source === "user_override" ? "未采样（0）" : "样本未知" : `样本 ${text(entry.n_samples)}`} · 置信度 {text(entry.confidence)}<br />观测时间：{text(entry.observed_at)}{entry.benchmark_version != null && <> · 基准 {text(entry.benchmark_version)}</>}</span></div>;
  })}</div>;
}

export default function ModelProfileSummary({ model, actions, children }: { model: Row; actions?: ReactNode; children?: ReactNode }) {
  const state = model.enabled === true ? "已启用" : model.enabled === false ? "已禁用" : "状态未知";
  const tools = object(model.tool_calling_support).supported;
  const schema = object(model.structured_output_support).supported;
  return <div className="model-profile-summary">
    <div className="model-profile-heading">
      <span className="model-profile-name"><strong>{text(model.display_name, text(model.model_id ?? model.id))}</strong><small>{text(model.provider ?? model.provider_id)} / {text(model.model_id)}</small></span>
      <div className="model-profile-actions"><span className="model-profile-state" data-enabled={String(model.enabled)}>{state}</span>{actions}</div>
    </div>
    <details className="settings-disclosure model-profile-details"><summary>档案与能力详情</summary><div className="settings-disclosure-body">
      <div className="model-profile-capabilities">
        <span>上下文窗口 <b>{text(model.context_window)}</b></span>
        <span>工具调用 <b>{tools === true ? "支持" : tools === false ? "不支持" : "未知"}</b></span>
        <span>结构化输出 <b>{schema === true ? "支持" : schema === false ? "不支持" : "未知"}</b></span>
      </div>
      <dl><dt>档案 ID</dt><dd className="break">{text(model.id)}</dd>
        <dt>上下文窗口</dt><dd>{text(model.context_window)}</dd><dt>最大输出</dt><dd>{text(model.max_output_tokens)}</dd>
        <dt>工具调用</dt><dd>{support(model.tool_calling_support)}</dd><dt>结构化输出</dt><dd>{support(model.structured_output_support)}</dd>
        <dt>输入价格</dt><dd>{price(model.input_cost_per_million, model.currency)}</dd><dt>输出价格</dt><dd>{price(model.output_cost_per_million, model.currency)}</dd>
        <dt>能力来源</dt><dd>{text(object(model.provenance).source)}</dd>
        <dt>可用能力</dt><dd>{Object.keys(object(model.capabilities)).map((name) => capabilityLabel(name)).join("、") || "未知"}</dd>
      </dl>
      <MeasurementList value={model.capability_measurements} />
      <details className="settings-disclosure"><summary>性能与缓存价格</summary><div className="settings-disclosure-body"><dl>
        <dt>缓存输入</dt><dd>{price(model.cached_input_cost_per_million, model.currency)}</dd>
        <dt>输出速度</dt><dd>{text(model.tokens_per_second)} Token/s</dd><dt>首 Token</dt><dd>{text(model.ttft_seconds)} 秒</dd>
        <dt>健康状态</dt><dd>{text(model.health)}</dd>
      </dl><MeasurementList value={model.performance_measurements} performance />
      </div></details>
      {children}
    </div></details>
  </div>;
}
