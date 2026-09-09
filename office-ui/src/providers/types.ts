import type { MissionRef, OfficeSnapshot, SwarmUIEvent } from "../types";

export interface ProviderHandlers {
  onSnapshot: (snap: OfficeSnapshot) => void;
  onEvent: (ev: SwarmUIEvent) => void;
  onState: (state: "connecting" | "live" | "reconnecting" | "closed" | "error") => void;
  onDone?: (status: string) => void;
}

/**
 * One interface, two implementations: `DemoEventProvider` (scripted CAC
 * fixture) and `SelericEventProvider` (SSE bridge to the real swarm).
 */
export interface SwarmEventProvider {
  readonly mode: "demo" | "seleric";
  listMissions(): Promise<MissionRef[]>;
  getSnapshot(missionId: string): Promise<OfficeSnapshot>;
  /** Returns an unsubscribe function. */
  subscribe(missionId: string, handlers: ProviderHandlers): () => void;
}
