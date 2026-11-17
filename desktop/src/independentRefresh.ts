/** Coalesce each read independently. A slow context request must never gate chat.
 * Identity is part of the key: reconnects may start immediately while old reads
 * settle. Callers still guard commits against their current connection. */
export class IndependentRefresh {
  private pending = new Map<string, Promise<void>>();

  run(identity: string, channel: string, read: () => Promise<void>): Promise<void> {
    const key = JSON.stringify([identity, channel]);
    const existing = this.pending.get(key);
    if (existing) return existing;
    const pending = Promise.resolve().then(read).finally(() => {
      if (this.pending.get(key) === pending) this.pending.delete(key);
    });
    this.pending.set(key, pending);
    return pending;
  }
}
