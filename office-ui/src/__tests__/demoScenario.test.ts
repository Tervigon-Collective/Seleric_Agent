import { describe, expect, it, vi } from "vitest";
import { DEMO_SCRIPT, demoInitialSnapshot } from "../providers/demoScenario";
import { DemoEventProvider } from "../providers/demo";
import { useOffice } from "../store";
import type { SwarmUIEvent } from "../types";

describe("demo CAC fixture", () => {
  it("initial snapshot has the V3 agent idle and a running mission", () => {
    const s = demoInitialSnapshot();
    expect(s.agents).toHaveLength(1);
    expect(s.agents[0].agentId).toBe("seleric_agent");
    expect(s.agents.every((a) => a.status === "idle")).toBe(true);
    expect(s.status).toBe("running");
    expect(s.route).toBe("v3");
  });

  it("script walks the tool pipeline with no swarm handoffs", () => {
    const types = DEMO_SCRIPT.map((b) => b.event.eventType);
    for (const need of [
      "mission_started",
      "tool_started",
      "tool_completed",
      "agent_thinking",
      "answering",
      "mission_completed",
    ]) {
      expect(types, `missing ${need}`).toContain(need);
    }
    // No retired vocabulary: no leadership transfers, no skeptic, no remediation.
    for (const retired of [
      "leadership_transferred",
      "agent_started",
      "skeptic_review_started",
      "skeptic_revise",
      "skeptic_pass",
      "remediation_created",
      "task_assigned",
    ]) {
      expect(types, `retired ${retired}`).not.toContain(retired);
    }
    const tools = DEMO_SCRIPT.filter((b) => b.event.eventType === "tool_completed");
    expect(tools.length).toBeGreaterThanOrEqual(4);
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
    expect(s.leadAgentId).toBe("seleric_agent");
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

    // Through first tool beats (patches fire after each)
    await vi.advanceTimersByTimeAsync(12_000);
    const mid = useOffice.getState();
    const me = mid.agents["seleric_agent"];
    expect(me.status).not.toBe("idle");
    expect(me.currentAction).toBeTruthy();

    unsub();
    vi.useRealTimers();
  });
});
