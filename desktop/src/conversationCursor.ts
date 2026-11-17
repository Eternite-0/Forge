/** Forward cursors are opaque server tokens, never dates or message IDs. */
export class ConversationCursor {
  private identity = "";
  private after: string | null = null;
  select(identity: string): string | null {
    if (this.identity !== identity) { this.identity = identity; this.after = null; }
    return this.after;
  }
  accept(identity: string, page: { resume_after?: unknown }): void {
    if (identity !== this.identity) return;
    // Older servers omit the cursor and continue snapshot polling.
    this.after = typeof page.resume_after === "string" ? page.resume_after : null;
  }
  reset(identity: string): void {
    if (this.identity === identity) this.after = null;
  }
}
