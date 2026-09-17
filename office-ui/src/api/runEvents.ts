import type { ActivityEvent } from "./contracts";
import { api, type HttpClient } from "./http";

export interface RunEventHandlers {
  onEvent: (event: ActivityEvent) => void;
  onState?: (state: "connecting" | "live" | "reconnecting" | "closed") => void;
  onError?: (error: unknown) => void;
}

interface ParsedFrame {
  id?: string;
  event?: string;
  data?: string;
}

export function parseSseFrame(raw: string): ParsedFrame | null {
  const frame: ParsedFrame = {};
  for (const line of raw.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const split = line.indexOf(":");
    const field = split < 0 ? line : line.slice(0, split);
    const value = split < 0 ? "" : line.slice(split + 1).replace(/^ /, "");
    if (field === "id") frame.id = value;
    else if (field === "event") frame.event = value;
    else if (field === "data") frame.data = frame.data ? `${frame.data}\n${value}` : value;
  }
  return Object.keys(frame).length ? frame : null;
}

const wait = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    }, { once: true });
  });

/** Fetch-based SSE supports auth headers, unlike EventSource. */
export function subscribeToRunEvents(
  runId: string,
  handlers: RunEventHandlers,
  options: { afterSequence?: number; client?: HttpClient; signal?: AbortSignal } = {},
): () => void {
  const controller = new AbortController();
  const external = options.signal;
  external?.addEventListener("abort", () => controller.abort(), { once: true });
  const client = options.client ?? api;
  let cursor = options.afterSequence ?? 0;

  void (async () => {
    let backoff = 500;
    while (!controller.signal.aborted) {
      handlers.onState?.(cursor ? "reconnecting" : "connecting");
      try {
        const response = await client.fetchImpl(
          `${client.baseUrl}/v1/runs/${encodeURIComponent(runId)}/events/stream?after_sequence=${cursor}`,
          {
            headers: client.headers({ Accept: "text/event-stream", "Last-Event-ID": String(cursor) }),
            signal: controller.signal,
          },
        );
        if (!response.ok || !response.body) {
          const error = new Error(`event stream ${response.status}`);
          if ([401, 403, 404].includes(response.status)) {
            handlers.onError?.(error);
            break;
          }
          throw error;
        }
        handlers.onState?.("live");
        backoff = 500;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!controller.signal.aborted) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() ?? "";
          for (const raw of frames) {
            const frame = parseSseFrame(raw);
            if (!frame?.data) continue;
            try {
              const event = JSON.parse(frame.data) as ActivityEvent;
              const sequence = Number(frame.id ?? event.sequence);
              cursor = Math.max(cursor, Number.isFinite(sequence) ? sequence : 0);
              handlers.onEvent(event);
            } catch { /* skip malformed event without dropping the stream */ }
          }
          if (done) break;
        }
        if (!controller.signal.aborted) handlers.onState?.("reconnecting");
      } catch (error) {
        if (controller.signal.aborted) break;
        handlers.onError?.(error);
        handlers.onState?.("reconnecting");
      }
      await wait(backoff, controller.signal);
      backoff = Math.min(backoff * 2, 10_000);
    }
    handlers.onState?.("closed");
  })();

  return () => controller.abort();
}
