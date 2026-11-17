import { agentActive, type ConversationAgent } from "./conversationAgents.ts";

/** Stable row identities; missing active rows are uncertain until exact-run reconciliation. */
export class AgentBuckets {
  private rows = new Map<string, ConversationAgent>();
  private pending = new Set<string>();
  private head = new Set<string>();
  get items(): ConversationAgent[] { return [...this.rows.values()]; }
  get reconcileIds(): string[] { return [...this.pending].slice(0, 8); }
  acceptActive(items: ConversationAgent[], complete = true): void {
    const seen = new Set(items.map(item => item.run_id));
    for (const row of this.rows.values()) {
      if (!agentActive(row) || seen.has(row.run_id)) continue;
      // Partial heads say nothing about explicitly loaded older pages. Only a
      // previous head member disappearing (or complete coverage) warrants lookup.
      if (complete || this.head.has(row.run_id)) this.markUnknown(row.run_id);
      else this.rows.set(row.run_id, { ...row, last_confirmed: true });
    }
    this.head = seen;
    for (const item of items) this.acceptDetail(item);
  }
  acceptTerminal(items: ConversationAgent[]): void {
    for (const item of items) this.acceptDetail(item);
  }
  acceptDetail(item: ConversationAgent): void {
    const previous = this.rows.get(item.run_id);
    // A closed run cannot re-enter active: retries have another durable run ID.
    if (previous && !agentActive(previous) && agentActive(item)) return;
    this.rows.set(item.run_id, { ...item, last_confirmed: agentActive(item) && !this.head.has(item.run_id) }); this.pending.delete(item.run_id);
  }
  unavailable(): void { for (const row of this.rows.values()) if (agentActive(row)) this.markUnknown(row.run_id); }
  remove(id: string): void { this.rows.delete(id); this.pending.delete(id); }
  defer(id: string): void { if (this.pending.delete(id)) this.pending.add(id); }
  private markUnknown(id: string): void {
    const row = this.rows.get(id);
    if (!row) return;
    this.rows.set(id, { ...row, state: "unknown", lease_status: "none", outcome: { kind: "unknown", label: "" } });
    this.pending.add(id);
  }
}
