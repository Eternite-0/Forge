export interface PendingMessage {
  content: string;
  requestId: string;
}

/** A retry of unchanged text must keep the original idempotency key. */
export function pendingMessage(
  text: string,
  previous: PendingMessage | null,
  newId: () => string,
): PendingMessage | null {
  const content = text.trim();
  if (!content) return null;
  return previous?.content === content ? previous : { content, requestId: newId() };
}

export function shouldSubmitOnEnter(event: {
  key: string;
  shiftKey: boolean;
  isComposing: boolean;
  keyCode: number;
}): boolean {
  return event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229;
}

export function appendUnique<T extends { id: string }>(older: T[], current: T[]): T[] {
  const seen = new Set(current.map((item) => item.id));
  return [...older.filter((item) => !seen.has(item.id)), ...current];
}

export function retainIfEqual<T>(current: T[], next: T[]): T[] {
  return current.length === next.length && current.every((item, index) => JSON.stringify(item) === JSON.stringify(next[index])) ? current : next;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return typeof error === "string" && /^[A-Z][A-Z0-9_]{1,63}$/.test(error)
    ? `操作未完成（${error}）。`
    : "请求失败，请重试。";
}

export function safeExternalUrl(value: string | undefined): string | null {
  if (!value || /[\u0000-\u001f\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
    return url.href;
  } catch { return null; }
}

export function preservedScrollTop(oldTop: number, oldHeight: number, newHeight: number): number {
  return Math.max(0, oldTop + newHeight - oldHeight);
}

export function isNearBottom(scrollTop: number, clientHeight: number, scrollHeight: number): boolean {
  return scrollHeight - (scrollTop + clientHeight) <= 80;
}

export interface OperatorForm {
  providerEnabled: boolean; providerId: string; kind: string; baseUrl: string; credentialRef: string;
  allowInsecureLoopback: boolean;
  supportsTools: boolean; supportsJsonSchema: boolean; routerModel: string; contextLimit: string;
  tokenLimit: string; concurrencyLimit: string; hosts: string;
}

export interface TokenBudgetPolicy { enforced: boolean | null; savedLimit: number | null }

export function operatorConfig(form: OperatorForm, tokenPolicy: TokenBudgetPolicy) {
  const integer = (value: string, minimum: number, maximum: number, name: string) => {
    if (!/^\d+$/.test(value)) throw new Error(`${name}必须是整数。`);
    const number = Number(value);
    if (!Number.isSafeInteger(number) || number < minimum || number > maximum) throw new Error(`${name}超出允许范围。`);
    return number;
  };
  if (typeof tokenPolicy?.enforced !== "boolean") throw new Error("Token 限制策略未知，请刷新配置后再保存。");
  const tokenLimit = tokenPolicy.enforced === false && tokenPolicy.savedLimit === null && form.tokenLimit === ""
    ? null : integer(form.tokenLimit, 0, 1000000000, "Token 上限");
  let provider = null;
  if (form.providerEnabled) {
    let url: URL;
    try { url = new URL(form.baseUrl); } catch { throw new Error("供应商地址无效。"); }
    if (url.username || url.password || url.search || url.hash) throw new Error("供应商地址不能包含凭据、查询参数或片段。");
    if (url.protocol !== "https:" && !(url.protocol === "http:" && form.allowInsecureLoopback && ["127.0.0.1", "[::1]"].includes(url.hostname))) throw new Error("HTTP 仅在明确允许时支持本机 127.0.0.1 或 ::1。");
    if (!/^[A-Za-z0-9_-]{1,100}$/.test(form.providerId.trim()) || !/^FORGE_PROVIDER_KEY_[A-Z0-9_]{1,128}$/.test(form.credentialRef)) throw new Error("请填写有效的供应商 ID 和凭据引用。");
    provider = { id: form.providerId.trim(), kind: form.kind, base_url: form.baseUrl.trim(),
      credential_ref: form.credentialRef, supports_tools: form.supportsTools, supports_json_schema: form.supportsJsonSchema,
      allow_insecure_loopback: form.allowInsecureLoopback };
  }
  if (form.routerModel.trim() && !provider) throw new Error("配置 Router 模型前请启用供应商。");
  return { provider, router_model_id: form.routerModel.trim() || null,
    router_context_limit: integer(form.contextLimit, 2049, 10000000, "上下文上限"),
    model_token_limit: tokenLimit,
    enforce_token_budgets: tokenPolicy.enforced,
    model_concurrency_limit: integer(form.concurrencyLimit, 1, 64, "并发上限"),
    network_hosts: form.hosts.split(/\r?\n/).map((host) => host.trim()).filter(Boolean) };
}

export interface RequestTicket { connection: string; epoch: number; channel: string; context: string; serial: number }

/** Reject late responses after a session, view, selection, or request changes. */
export class RequestGuard {
  private connection = "";
  private contexts = new Map<string, string>();
  private serials = new Map<string, number>();
  private active = true;
  private epoch = 0;

  activate() { this.active = true; }
  dispose() { this.active = false; this.epoch += 1; this.serials.clear(); }
  setConnection(value: string) {
    if (value !== this.connection) { this.connection = value; this.epoch += 1; this.serials.clear(); }
  }
  setContext(channel: string, value: string) {
    if (this.contexts.get(channel) !== value) {
      this.contexts.set(channel, value);
      this.serials.set(channel, (this.serials.get(channel) || 0) + 1);
    }
  }
  start(channel: string): RequestTicket {
    const serial = (this.serials.get(channel) || 0) + 1;
    this.serials.set(channel, serial);
    return { connection: this.connection, epoch: this.epoch, channel, context: this.contexts.get(channel) || "", serial };
  }
  accepts(ticket: RequestTicket): boolean {
    return this.active && ticket.connection === this.connection && ticket.epoch === this.epoch &&
      ticket.context === (this.contexts.get(ticket.channel) || "") &&
      ticket.serial === this.serials.get(ticket.channel);
  }
}
