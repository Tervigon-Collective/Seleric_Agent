import type { MissionRef, OfficeSnapshot, SwarmUIEvent } from "../types";
import type { ProviderHandlers, SwarmEventProvider } from "./types";
import {
  DEMO_MISSION_ID,
  DEMO_QUERY,
  DEMO_SCRIPT,
  demoInitialSnapshot,
} from "./demoScenario";

/**
 * Replays the scripted CAC fixture with no backend. Same interface as
 * `SelericEventProvider`, so the office cannot tell the difference.
 */
export class DemoEventProvider implements SwarmEventProvider {
  readonly mode = "demo" as const;
  private speed: number;

  constructor(opts: { speed?: number } = {}) {
    this.speed = opts.speed ?? 1;
  }

  async listMissions(): Promise<MissionRef[]> {
    return [
      { missionId: DEMO_MISSION_ID, query: DEMO_QUERY, status: "running", route: "swarm", missionLead: null, lastSeq: 0 },
      {
        missionId: "MS-demo-inv",
        query: "Which SKUs will stock out before the promo?",
        status: "completed",
        route: "swarm",
        missionLead: "inventory",
        lastSeq: 12,
      },
    ];
  }

  async getSnapshot(missionId?: string): Promise<OfficeSnapshot> {
    if (missionId === "MS-demo-inv") return this.secondarySnapshot();
    return demoInitialSnapshot();
  }

  private secondarySnapshot(): OfficeSnapshot {
    const s = demoInitialSnapshot();
    return {
      ...s,
      missionId: "MS-demo-inv",
      query: "Which SKUs will stock out before the promo?",
      status: "completed",
      stage: "complete",
      missionLead: "inventory",
      leadAgentId: "inventory_agent",
      board: {
        steps: s.board.steps.map((st) => ({ ...st, state: "done" as const })),
      },
      agents: s.agents.map((a) =>
        a.agentId === "inventory_agent"
          ? { ...a, status: "completed", missionLead: true, currentAction: "Delivered stock-out forecast" }
          : a.agentId === "coordinator"
            ? { ...a, status: "completed" }
            : a,
      ),
      finalResponse:
        "7 SKUs are projected to stock out before the promo window; 3 are high-velocity. Recommended: expedite POs for SKU-4412 / SKU-8890 / SKU-2201 and cap promo depth on the rest.",
      unresolvedQuestions: [],
    };
  }

  subscribe(missionId: string, h: ProviderHandlers): () => void {
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];

    // Secondary demo mission is a finished, static snapshot — no event stream.
    if (missionId === "MS-demo-inv") {
      h.onState("connecting");
      const t = setTimeout(() => {
        if (cancelled) return;
        h.onSnapshot(this.secondarySnapshot());
        h.onState("live");
        h.onDone?.("completed");
        h.onState("closed");
      }, 200);
      return () => {
        cancelled = true;
        clearTimeout(t);
      };
    }

    let snap = demoInitialSnapshot();

    h.onState("connecting");
    const boot = setTimeout(() => {
      if (cancelled) return;
      h.onSnapshot(snap);
      h.onState("live");

      let seq = 0;
      let acc = 0;
      for (const beat of DEMO_SCRIPT) {
        acc += beat.delayMs / this.speed;
        const at = acc;
        const thisSeq = ++seq;
        timers.push(
          setTimeout(() => {
            if (cancelled) return;
            const ev: SwarmUIEvent = {
              eventId: `${DEMO_MISSION_ID}:${thisSeq}`,
              seq: thisSeq,
              timestamp: new Date().toISOString(),
              missionId: DEMO_MISSION_ID,
              ...beat.event,
            };
            h.onEvent(ev);
            if (beat.patch) {
              snap = { ...snap, ...beat.patch, lastSeq: thisSeq, timeline: [] };
              h.onSnapshot(snap);
            }
            if (beat.event.eventType === "mission_completed") {
              h.onDone?.("completed");
              h.onState("closed");
            }
          }, at),
        );
      }
    }, 300);
    timers.push(boot);

    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }
}
