import { describe, expect, it, vi } from "vitest";
import { DEMO_SCRIPT, demoInitialSnapshot } from "../providers/demoScenario";
import { DemoEventProvider } from "../providers/demo";
import { useOffice } from "../store";
import type { SwarmUIEvent } from "../types";

describe("demo CAC fixture", () => {
  it("initial snapshot has the whole roster idle and a running mission", () => {
    const s = demoInitialSnapshot();
    expect(s.agents).toHaveLength(14);
    expect(s.agents.every((a) => a.status === "idle")).toBe(true);
    expect(s.status).toBe("running");
  });

  it("script exercises every required beat (brief §68 + §83)", () => {
    const types = DEMO_SCRIPT.map((b) => b.event.eventType);
    for (const need of [
      "mission_started",
      "decomposition_created",
      "decomposition_refined",
      "task_started",
      "leadership_transferred",
      "agent_started",
      "artifact_created",
      "skeptic_review_started",
      "skeptic_revise",
      "remediation_created",
      "task_assigned",
      "task_completed",
      "skeptic_pass",
      "mission_completed",
    ]) {
      expect(types, `missing ${need}`).toContain(need);
    }
    // exactly two leadership handoffs: performance -> funnel -> technical
    const handoffs = DEMO_SCRIPT.filter((b) => b.event.eventType === "leadership_transferred");
    expect(handoffs.map((b) => b.event.agentId)).toEqual(["funnel_agent", "technical_agent"]);
  });

  it("replays through the store to a completed mission with a final answer", async () => {
    vi.useFakeTimers();
    useOffice.getState().reset();
    const provider = new DemoEventProvider({ speed: 100 });
    const events: SwarmUIEvent[] = [];
    const unsub = provider.subscribe("MS-demo-cac", {
      onSnapshot: useOffice.getState().hydrate,
      onEvent: (e) => {
        events.push(e);
        useOffice.getState().ingestEvent(e);
      },
      onState: () => {},
    });

    // Longer script gaps + optional busy-gates; speed 100 keeps this short
    await vi.advanceTimersByTimeAsync(120_000);
    unsub();
    vi.useRealTimers();

    const s = useOffice.getState();
    expect(events.length).toBe(DEMO_SCRIPT.length);
    expect(new Set(events.map((e) => e.seq)).size).toBe(events.length); // unique seqs
    expect(s.status).toBe("completed");
    expect(s.finalResponse).toMatch(/frontend regression/i);
    expect(s.leadAgentId).toBe("technical_agent");
    expect(s.board.every((step) => step.state === "done")).toBe(true);
  });

  it("keeps agent work state after mission patches (does not wipe to idle)", async () => {
    vi.useFakeTimers();
    useOffice.getState().reset();
    const provider = new DemoEventProvider({ speed: 100 });
    const unsub = provider.subscribe("MS-demo-cac", {
      onSnapshot: useOffice.getState().hydrate,
      onEvent: (e) => useOffice.getState().ingestEvent(e),
      onState: () => {},
    });

    // Through first investigation wave + artifact (patches fire after each)
    await vi.advanceTimersByTimeAsync(12_000);
    const mid = useOffice.getState();
    const active = Object.values(mid.agents).filter((a) => a.status !== "idle" && a.status !== "offline");
    expect(active.length).toBeGreaterThan(2);
    expect(active.some((a) => !!a.currentAction)).toBe(true);

    unsub();
    vi.useRealTimers();
  });
});
