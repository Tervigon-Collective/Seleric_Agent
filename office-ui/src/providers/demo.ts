import type { MissionRef, OfficeSnapshot, SwarmUIEvent } from "../types";
import type { ProviderHandlers, SwarmEventProvider } from "./types";
import {
  DEMO_MISSION_ID,
  DEMO_QUERY,
  DEMO_SCRIPT,
  demoInitialSnapshot,
} from "./demoScenario";

/** Beats that spawn walk-meet-return scenes — wait for the floor to free up. */
const MEETING_EVENT_TYPES = new Set([
  "leadership_transferred",
  "skeptic_review_started",
  "skeptic_revise",
  "skeptic_reject",
  "skeptic_pass",
  "task_assigned",
]);

function sleep(ms: number, cancelled: () => boolean): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    if (cancelled()) {
      clearTimeout(t);
      resolve();
    }
  });
}

function officeBusy(): boolean {
  return !!(window as unknown as { __officeBusy?: boolean }).__officeBusy;
}

/** Poll until the canvas finishes its current meeting choreography. */
async function waitUntilOfficeFree(cancelled: () => boolean, capMs: number): Promise<void> {
  const start = Date.now();
  while (!cancelled() && Date.now() - start < capMs) {
    if (!officeBusy()) return;
    await sleep(100, cancelled);
  }
}

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
    const isCancelled = () => cancelled;

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

      void (async () => {
        let seq = 0;
        for (const beat of DEMO_SCRIPT) {
          const gap = beat.delayMs / this.speed;
          await sleep(gap, isCancelled);
          if (cancelled) return;

          if (MEETING_EVENT_TYPES.has(beat.event.eventType)) {
            // Wait for walk→talk→return to finish (hard cap so demos never hang)
            await waitUntilOfficeFree(isCancelled, 25_000);
            if (cancelled) return;
          }

          const thisSeq = ++seq;
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
            // Mission-level patch only — empty agents keeps event-folded office
            // state (statuses, currentAction, lead). Re-sending blankAgents()
            // was wiping every agent back to idle between beats.
            h.onSnapshot({ ...snap, agents: [], timeline: [] });
          }
          if (beat.event.eventType === "mission_completed") {
            h.onDone?.("completed");
            h.onState("closed");
          }
        }
      })();
    }, 300);
    timers.push(boot);

    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }
}
