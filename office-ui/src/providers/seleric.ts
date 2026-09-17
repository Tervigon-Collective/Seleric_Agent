import type { MissionRef, OfficeSnapshot, SwarmUIEvent } from "../types";
import { api, HttpClient } from "../api/http";
import { parseSseFrame } from "../api/runEvents";
import type { ProviderHandlers, SwarmEventProvider } from "./types";

/**
 * Live bridge to the Seleric swarm via the read-only office gateway
 * (`/v1/office/*`). Snapshot + SSE stream; auto-reconnects with backoff and
 * resumes from the last seq it saw (events are idempotent in the store, so a
 * small replay overlap is harmless).
 */
export class SelericEventProvider implements SwarmEventProvider {
  readonly mode = "seleric" as const;
  private readonly client: HttpClient;

  constructor(opts: { baseUrl?: string; client?: HttpClient } = {}) {
    this.client = opts.client ?? (opts.baseUrl === undefined
      ? api
      : new HttpClient({ baseUrl: opts.baseUrl }));
  }

  async listMissions(): Promise<MissionRef[]> {
    const j = await this.client.request<{ missions?: MissionRef[] }>("/v1/office/missions");
    return (j.missions ?? []) as MissionRef[];
  }

  async getSnapshot(missionId: string): Promise<OfficeSnapshot> {
    return this.client.request(
      `/v1/office/missions/${encodeURIComponent(missionId)}/snapshot`,
    );
  }

  subscribe(missionId: string, h: ProviderHandlers): () => void {
    const controller = new AbortController();
    let backoff = 1000;

    void (async () => {
      while (!controller.signal.aborted) {
        h.onState(backoff === 1000 ? "connecting" : "reconnecting");
        try {
          const response = await this.client.fetchImpl(
            `${this.client.baseUrl}/v1/office/missions/${encodeURIComponent(missionId)}/stream`,
            {
              headers: this.client.headers({ Accept: "text/event-stream" }),
              signal: controller.signal,
            },
          );
          if (!response.ok || !response.body) throw new Error(`office stream ${response.status}`);
          h.onState("live");
          backoff = 1000;
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let doneEvent = false;
          while (!controller.signal.aborted) {
            const chunk = await reader.read();
            buffer += decoder.decode(chunk.value, { stream: !chunk.done });
            const frames = buffer.split(/\r?\n\r?\n/);
            buffer = frames.pop() ?? "";
            for (const raw of frames) {
              const frame = parseSseFrame(raw);
              if (!frame?.data) continue;
              try {
                const data = JSON.parse(frame.data) as Record<string, unknown>;
                if (frame.event === "snapshot") h.onSnapshot(data as unknown as OfficeSnapshot);
                else if (frame.event === "event") h.onEvent(data as unknown as SwarmUIEvent);
                else if (frame.event === "done") {
                  h.onDone?.(typeof data.status === "string" ? data.status : "done");
                  doneEvent = true;
                  controller.abort();
                }
              } catch { /* ignore malformed frame */ }
            }
            if (chunk.done || doneEvent) break;
          }
        } catch {
          if (controller.signal.aborted) break;
          h.onState("reconnecting");
        }
        if (!controller.signal.aborted) {
          await new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, backoff);
            controller.signal.addEventListener("abort", () => {
              clearTimeout(timer);
              resolve();
            }, { once: true });
          });
          backoff = Math.min(backoff * 2, 15_000);
        }
      }
      h.onState("closed");
    })();
    return () => {
      controller.abort();
    };
  }
}
