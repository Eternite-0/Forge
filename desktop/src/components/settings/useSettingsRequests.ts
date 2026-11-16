import { useCallback, useEffect, useRef } from "react";
import { request, type Connection } from "../../api";

/** Cancel obsolete reads and ignore late responses after a settings page unmounts. */
export default function useSettingsRequests() {
  const alive = useRef(false);
  const channels = useRef(new Map<string, AbortController>());
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; channels.current.forEach((controller) => controller.abort()); channels.current.clear(); };
  }, []);
  const cancel = useCallback((channel: string) => { channels.current.get(channel)?.abort(); channels.current.delete(channel); }, []);
  const run = useCallback(async <T,>(channel: string, connection: Connection, path: string, init?: RequestInit): Promise<{ value: T } | null> => {
    channels.current.get(channel)?.abort();
    const controller = new AbortController();
    channels.current.set(channel, controller);
    const current = () => alive.current && !controller.signal.aborted && channels.current.get(channel) === controller;
    try {
      const value = await request<T>(connection, path, { ...init, signal: AbortSignal.any([controller.signal, AbortSignal.timeout(120000)]) });
      return current() ? { value } : null;
    } catch (error) {
      if (current()) throw error;
      return null;
    } finally {
      if (channels.current.get(channel) === controller) channels.current.delete(channel);
    }
  }, []);
  return { run, cancel };
}
