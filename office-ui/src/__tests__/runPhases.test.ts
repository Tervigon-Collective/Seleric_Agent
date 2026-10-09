import { describe, expect, it } from "vitest";
import { deriveRunPhases } from "../api/runPhases";
import type { SwarmUIEvent } from "../types";

const ev = (partial: Partial<SwarmUIEvent> & { eventId: string; seq: number }): SwarmUIEvent => ({
  timestamp: "2026-10-09T10:00:00Z",
  missionId: "m1",
  eventType: "agent_stage",
  ...partial,
});

const states = (timeline: SwarmUIEvent[]) =>
  deriveRunPhases(timeline).map((p) => `${p.key}:${p.state}`);

describe("deriveRunPhases", () => {
  it("starts with everything pending except the first active step", () => {
    expect(states([])).toEqual(["understand:active", "retrieve:pending", "answer:pending"]);
  });

  it("advances only on observed stage events", () => {
    const understand = [ev({ eventId: "e1", seq: 1, metadata: { stage: "understand" } })];
    expect(states(understand)).toEqual(["understand:active", "retrieve:pending", "answer:pending"]);
    const prefetch = [...understand, ev({ eventId: "e2", seq: 2, metadata: { stage: "prefetch" } })];
    expect(states(prefetch)).toEqual(["understand:done", "retrieve:active", "answer:pending"]);
    const answer = [...prefetch, ev({ eventId: "e3", seq: 3, metadata: { stage: "answer" } })];
    expect(states(answer)).toEqual(["understand:done", "retrieve:done", "answer:active"]);
  });

  it("lets tool and answering events imply earlier phases without stage events", () => {
    const tools = [ev({ eventId: "e1", seq: 1, eventType: "agent_tool_completed" })];
    expect(states(tools)[0]).toBe("understand:done");
    expect(states(tools)[1]).toBe("retrieve:active");
    const answering = [...tools, ev({ eventId: "e2", seq: 2, eventType: "agent_answering" })];
    expect(states(answering)).toEqual(["understand:done", "retrieve:done", "answer:active"]);
  });

  it("completes every phase only on a terminal event", () => {
    const done = [ev({ eventId: "e1", seq: 1, eventType: "run_completed" })];
    expect(states(done)).toEqual(["understand:done", "retrieve:done", "answer:done"]);
  });

  it("ignores malformed rows instead of advancing or throwing", () => {
    const junk = [
      { eventId: "x1", seq: 1, eventType: null, metadata: null },
      { eventId: "x2", seq: 2, metadata: { stage: 42 } },
    ] as unknown as SwarmUIEvent[];
    expect(states(junk)).toEqual(["understand:active", "retrieve:pending", "answer:pending"]);
  });
});
