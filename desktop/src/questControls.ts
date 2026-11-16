type RecordValue = Record<string, unknown>;

const transitions: Record<string, string[]> = {
  proposed: ["awaiting_approval", "rejected", "cancelled"],
  awaiting_approval: ["rejected", "cancelled"],
  approved: ["paused", "cancelled"], queued: ["paused", "cancelled"],
  running: ["blocked", "paused", "needs_input", "failed", "cancelled"],
  blocked: ["cancelled", "failed"], paused: ["cancelled"], needs_input: ["cancelled"],
  reviewing: ["failed"],
};

/** Execution and completion transitions belong to the trusted Core runtime. */
export const userQuestTransitions = (status: string): readonly string[] => transitions[status] || [];

const record = (value: unknown): RecordValue => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("团队合约不完整，请刷新团队记录。");
  return value as RecordValue;
};
const strings = (value: unknown): string[] => {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) throw new Error("团队合约缺少有效的角色或工具范围。");
  return value;
};
const integer = (value: unknown, minimum: number, maximum: number): number => {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) throw new Error("团队预算或执行上限不完整。");
  return value;
};
const requiredText = (value: unknown): string => {
  if (typeof value !== "string" || !value.trim()) throw new Error("团队角色合约不完整。");
  return value;
};

/** Convert a recorded contract to the existing TeamWire without inventing authority. */
export function teamReassignmentProposal(value: unknown, strategy: string) {
  const body = record(value);
  if (!Array.isArray(body.roles) || !body.roles.length) throw new Error("请选择包含完整角色合约的团队记录。");
  if (!["single_model", "greedy", "coverage", "complementarity"].includes(strategy)) throw new Error("请选择团队选型策略。");
  const roles = body.roles.map((value) => {
    const role = record(value);
    const input = integer(role.input_tokens, 0, 1000000), output = integer(role.output_tokens, 1, 1000000);
    if (role.execution_token_budget != null && role.execution_token_budget !== input + output) {
      throw new Error("当前团队的累计 Token 分配无法通过此接口完整保留，请在长期对话中请求重新分配。");
    }
    const capabilities = record(role.required_capabilities);
    const requiredCapabilities = Object.entries(capabilities).map(([name, minimum]) => {
      if (typeof minimum !== "number" || !Number.isFinite(minimum) || minimum < 0 || minimum > 100) throw new Error("团队能力要求无效。");
      return { name, minimum };
    });
    if (typeof role.dynamic !== "boolean") throw new Error("团队角色来源未记录。");
    const schema = record(role.output_schema);
    return {
      id: requiredText(role.id), task_node_key: requiredText(role.task_node_key),
      required_capabilities: requiredCapabilities, tools: strings(role.tools),
      input_tokens: input, output_tokens: output,
      max_steps: integer(role.max_steps, 1, 32), max_tool_calls: integer(role.max_tool_calls, 0, 64),
      max_duration_seconds: integer(role.max_duration_seconds, 1, 3600), depth: integer(role.depth, 0, 8),
      dynamic: role.dynamic, responsibility: requiredText(role.responsibility),
      input_contract: strings(role.input_contract), output_schema_json: JSON.stringify(schema),
      acceptance_criteria: strings(role.acceptance_criteria),
    };
  });
  const limits = record(body.limits);
  if (limits.currency !== null && (typeof limits.currency !== "string" || !/^[A-Z]{3}$/.test(limits.currency))) throw new Error("团队预算币种未记录。");
  const wireLimits = {
    max_members: integer(limits.max_members, 1, 16), max_depth: integer(limits.max_depth, 0, 8),
    max_concurrency: integer(limits.max_concurrency, 1, 16), max_total_tokens: integer(limits.max_total_tokens, 1, 16000000),
    max_total_tool_calls: integer(limits.max_total_tool_calls, 0, 1024),
    max_cost_microunits: limits.max_cost_microunits === null ? null : integer(limits.max_cost_microunits, 0, Number.MAX_SAFE_INTEGER),
    currency: limits.currency, allowed_tools: strings(limits.allowed_tools),
  };
  return { roles, limits: wireLimits, strategy };
}
