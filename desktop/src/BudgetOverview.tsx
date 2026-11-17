import { SettingsRow, SettingsSection } from "./SettingsView";

type BudgetRecord = Record<string, unknown>;

const finiteNonnegative = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const budgetText = (value: unknown) => typeof value === "string" && value.trim() ? value : "未知";

export function formatBudgetNumber(value: unknown): string {
  return finiteNonnegative(value) ? value.toLocaleString("zh-CN", { maximumFractionDigits: 6 }) : "未知";
}

export function formatBudgetCost(microunits: unknown, currency: unknown): string {
  const denomination = budgetText(currency);
  if (!finiteNonnegative(microunits)) return denomination === "未知" ? "未知" : `未知（${denomination}）`;
  const amount = (microunits / 1_000_000).toLocaleString("zh-CN", { maximumFractionDigits: 6 });
  return denomination === "未知" ? `${amount}（币种未知）` : `${amount} ${denomination}`;
}

export function formatBudgetRecoveryTime(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未知";
  const date = new Date(value * 1000);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : "未知";
}

export default function BudgetOverview({ budget }: { budget: BudgetRecord }) {
  const enforced = budget.token_budgets_enforced;
  const unsettled = Array.isArray(budget.recent_unsettled) ? budget.recent_unsettled : null;

  return <div className="settings-blocks budget-overview">
    <SettingsSection title="Token 账本">
      <SettingsRow label="已确认用量" description="已经记录的确认 Token 用量。">{formatBudgetNumber(budget.known_tokens)}</SettingsRow>
      <SettingsRow label="未结算预留" description="尚未确认的输入和输出 Token 预留。">{formatBudgetNumber(budget.reserved_tokens)}</SettingsRow>
      <SettingsRow label="迁移期上界" description="历史预算迁移时计入的 Token 上界。">{formatBudgetNumber(budget.opening_tokens)}</SettingsRow>
      <SettingsRow label="保守计入 Token" description={enforced === false ? "已确认用量 + 未结算预留 + 迁移期上界；仅作账本记录，不按累计 Token 停止任务，也不是实际消耗。" : "已确认用量 + 未结算预留 + 迁移期上界；不是实际消耗。"}>{formatBudgetNumber(budget.charged_tokens)}</SettingsRow>
      <SettingsRow label="Token 上限" description={enforced === false ? "不按累计 Token 停止任务；单次上下文、费用和权限限制保持不变。" : "当前工作空间模型调用的累计预算上限；执行策略以 Core 返回为准。"}>{enforced === false ? "未启用" : formatBudgetNumber(budget.token_limit)}</SettingsRow>
      {enforced !== false && budget.token_limit === 0 && <p className="notice">Token 上限为 0，当前预算未开放，模型调用受限。</p>}
    </SettingsSection>

    <SettingsSection title="并发与费用">
      <SettingsRow label="并发占用" description="当前占用的模型调用并发槽 / 并发上限。">{formatBudgetNumber(budget.active_reservations)} / {formatBudgetNumber(budget.concurrency_limit)}</SettingsRow>
      <SettingsRow label="费用上限" description="按配置币种显示费用上限；未提供的金额或币种保持未知。">{formatBudgetCost(budget.cost_limit_microunits, budget.currency)}</SettingsRow>
      <p className="notice">费用信息缺失不表示免费；费用上限也不代表已经发生的费用。</p>
    </SettingsSection>

    <SettingsSection title="最近未结算请求" description="显示当前返回的最近记录。请求超时不表示远端已取消，也不表示预留已经释放。">
      {unsettled === null ? <p className="notice">未结算请求信息未知。</p> : unsettled.length === 0 ? <p className="notice">当前列表没有未结算请求。</p> : unsettled.map((value, index) => {
        const item: BudgetRecord = value && typeof value === "object" && !Array.isArray(value) ? value as BudgetRecord : {};
        return <div className="data-row budget-unsettled-record" key={index}>
          <strong>{budgetText(item.purpose)}</strong>
          <dl>
            <dt>状态</dt><dd>{budgetText(item.code)}</dd>
            <dt>输入预留</dt><dd>{formatBudgetNumber(item.input_reserved)}</dd>
            <dt>输出预留</dt><dd>{formatBudgetNumber(item.output_reserved)}</dd>
            <dt>隔离至</dt><dd>{formatBudgetRecoveryTime(item.recover_after)}</dd>
          </dl>
        </div>;
      })}
    </SettingsSection>
  </div>;
}
