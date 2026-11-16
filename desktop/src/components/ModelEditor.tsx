import { useState } from "react";
import { errorMessage } from "../logic";
import { Row, text } from "../workspaceTypes";

const MODEL_CAPABILITIES: [string, string][] = [["coding", "编程"], ["research", "研究"], ["planning", "规划"], ["architecture", "架构"], ["mathematics", "数学"], ["design", "设计"], ["vision", "视觉"], ["writing", "写作"], ["tool_calling", "工具调用"], ["instruction_following", "指令遵循"]];
export default function ModelEditor({ initial, version, blocked, save, cancel, defaultProvider = "" }: { initial: Row | null; version: string | null; blocked: boolean; save: (method: "POST" | "PATCH", path: string, body: unknown) => void; cancel: () => void; defaultProvider?: string }) {
  const support = (value: unknown) => value && typeof value === "object" && "supported" in value ? ((value as Row).supported === true ? "yes" : "no") : "unknown";
  const [id, setId] = useState(() => text(initial?.id, `model_${crypto.randomUUID().replace(/-/g, "")}`));
  const [provider, setProvider] = useState(text(initial?.provider, defaultProvider));
  const [modelId, setModelId] = useState(text(initial?.model_id, ""));
  const [label, setLabel] = useState(text(initial?.display_name, ""));
  const [context, setContext] = useState(text(initial?.context_window, ""));
  const [output, setOutput] = useState(text(initial?.max_output_tokens, ""));
  const [tools, setTools] = useState(support(initial?.tool_calling_support));
  const [schema, setSchema] = useState(support(initial?.structured_output_support));
  const [enabled, setEnabled] = useState(initial?.enabled !== false);
  const [scores, setScores] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [options, setOptions] = useState(false);
  const parseNumber = (value: string) => {
    if (!value.trim()) return null;
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < 1) throw new Error("上下文和最大输出必须为正整数或留空。");
    return Number(value);
  };
  const supportValue = (value: string) => value === "unknown" ? null : { supported: value === "yes", source: "user_override" };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    try {
      if (!label.trim()) throw new Error("请填写显示名称。");
      const fields: Row = { enabled, display_name: label.trim(), context_window: parseNumber(context), max_output_tokens: parseNumber(output), tool_calling_support: supportValue(tools), structured_output_support: supportValue(schema) };
      if (initial) {
        if (!version) throw new Error("模型版本未知，请重新打开编辑。");
        const changes: Row = { expected_updated_at: version };
        for (const field of ["enabled", "display_name", "context_window", "max_output_tokens"]) if (fields[field] !== initial[field]) changes[field] = fields[field];
        if (tools !== support(initial.tool_calling_support)) changes.tool_calling_support = fields.tool_calling_support;
        if (schema !== support(initial.structured_output_support)) changes.structured_output_support = fields.structured_output_support;
        if (Object.keys(changes).length === 1) throw new Error("没有需要保存的修改。");
        save("PATCH", `/api/models/${encodeURIComponent(text(initial.id))}`, changes);
      } else {
        if (!id.trim() || !provider.trim() || !modelId.trim()) throw new Error("请填写档案 ID、供应商 ID 和模型 ID。");
        const capabilities: Record<string, number> = {};
        for (const [name] of MODEL_CAPABILITIES) {
          const raw = scores[name]?.trim();
          if (!raw) continue;
          const value = Number(raw);
          if (!Number.isFinite(value) || value < 0 || value > 100) throw new Error("能力分数需在 0 到 100 之间。");
          capabilities[name] = value;
        }
        save("POST", "/api/models", { ...fields, id: id.trim(), provider: provider.trim(), model_id: modelId.trim(), capabilities });
      }
      setError("");
    } catch (problem) { setOptions(true); setError(errorMessage(problem)); }
  };
  return <form className="model-form" onSubmit={submit} onInvalidCapture={(event) => { const details = event.currentTarget.querySelector<HTMLDetailsElement>(".model-options"); if (details) details.open = true; setOptions(true); }}><div className="section-heading"><span>{initial ? "编辑模型档案" : "添加模型档案"}</span></div>
    <label className="field-label">显示名称<input value={label} onChange={(event) => setLabel(event.target.value)} /></label>
    <label className="field-label">模型 ID<input value={modelId} disabled={!!initial} placeholder="填写服务提供的模型名称" onChange={(event) => setModelId(event.target.value)} /></label>
    <label className="field-label">供应商 ID<input value={provider} disabled={!!initial} onChange={(event) => setProvider(event.target.value)} /></label>
    <label className="check-label"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />启用模型</label>
    <details className="settings-disclosure model-options" open={options} onToggle={(event) => setOptions(event.currentTarget.open)}>
      <summary>模型选项</summary><div className="model-options-content">
    <p className="notice">档案标识已自动生成。能力和上下文按实际资料填写，留空或未知不会自动视为支持。</p>
    <label className="field-label">档案 ID<input value={id} disabled={!!initial} onChange={(event) => setId(event.target.value)} /></label>
    <label className="field-label">上下文窗口（未知可留空）<input type="number" min="1" value={context} onChange={(event) => setContext(event.target.value)} /></label>
    <label className="field-label">最大输出 Token（未知可留空）<input type="number" min="1" value={output} onChange={(event) => setOutput(event.target.value)} /></label>
    <label className="field-label">工具调用支持<select value={tools} onChange={(event) => setTools(event.target.value)}><option value="unknown">未知</option><option value="yes">支持</option><option value="no">不支持</option></select></label>
    <label className="field-label">结构化输出支持<select value={schema} onChange={(event) => setSchema(event.target.value)}><option value="unknown">未知</option><option value="yes">支持</option><option value="no">不支持</option></select></label>
    {!initial && <details><summary>可选：手动能力评分</summary><p className="notice">留空表示未知；输入的 0–100 分仅为用户覆盖，未采样（0），并非基准测评。</p>{MODEL_CAPABILITIES.map(([name, title]) => <label className="field-label" key={name}>{title}<input type="number" min="0" max="100" step="any" value={scores[name] || ""} onChange={(event) => setScores((old) => ({ ...old, [name]: event.target.value }))} /></label>)}</details>}
    {initial && <p className="notice">现有能力测量及来源保持不变；协议支持修改标记为用户覆盖。</p>}
    </div></details>
    {error && <p className="notice error" role="alert">{error}</p>}
    <div className="button-row"><button className="button primary" type="submit" disabled={blocked}>保存</button><button className="button" type="button" onClick={cancel}>取消</button></div>
  </form>;
}

