import type { MissionRef, OfficeSnapshot, SwarmUIEvent } from "../types";
import type { ProviderHandlers, SwarmEventProvider } from "./types";

/**
 * Live bridge to the Seleric swarm via the read-only office gateway
 * (`/v1/office/*`). Snapshot + SSE stream; auto-reconnects with backoff and
 * resumes from the last seq it saw (events are idempotent in the store, so a
 * small replay overlap is harmless).
 */
export class SelericEventProvider implements SwarmEventProvider {
  readonly mode = "seleric" as const;
  private base: string;

  constructor(opts: { baseUrl?: string } = {}) {
    // "" -> same origin (Vite dev-proxies /v1 to the API).
    this.base = opts.baseUrl ?? "";
  }

  async listMissions(): Promise<MissionRef[]> {
    const r = await fetch(`${this.base}/v1/office/missions`);
    if (!r.ok) throw new Error(`listMissions ${r.status}`);
    const j = await r.json();
    return (j.missions ?? []) as MissionRef[];
  }

  async getSnapshot(missionId: string): Promise<OfficeSnapshot> {
    const r = await fetch(`${this.base}/v1/office/missions/${encodeURIComponent(missionId)}/snapshot`);
    if (!r.ok) throw new Error(`getSnapshot ${r.status}`);
    return (await r.json()) as OfficeSnapshot;
  }

  subscribe(missionId: string, h: ProviderHandlers): () => void {
    let closed = false;
    let es: EventSource | null = null;
    let backoff = 1000;

    const open = () => {
      if (closed) return;
      h.onState(backoff === 1000 ? "connecting" : "reconnecting");
      es = new EventSource(
        `${this.base}/v1/office/missions/${encodeURIComponent(missionId)}/stream`,
      );

      es.addEventListener("open", () => {
        backoff = 1000;
        h.onState("live");
      });
      es.addEventListener("snapshot", (e) => {
        try {
          h.onSnapshot(JSON.parse((e as MessageEvent).data) as OfficeSnapshot);
        } catch { /* ignore malformed frame */ }
      });
      es.addEventListener("event", (e) => {
        try {
          h.onEvent(JSON.parse((e as MessageEvent).data) as SwarmUIEvent);
        } catch { /* ignore malformed frame */ }
      });
      es.addEventListener("done", (e) => {
        try {
          h.onDone?.(JSON.parse((e as MessageEvent).data).status ?? "done");
        } catch { h.onDone?.("done"); }
        closed = true;
        es?.close();
        h.onState("closed");
      });
      es.addEventListener("error", () => {
        es?.close();
        if (closed) return;
        h.onState("reconnecting");
        setTimeout(open, backoff);
        backoff = Math.min(backoff * 2, 15000);
      });
    };

    open();
    return () => {
      closed = true;
      es?.close();
      h.onState("closed");
    };
  }
}
