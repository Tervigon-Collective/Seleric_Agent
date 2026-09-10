import { beforeEach, describe, expect, it } from "vitest";
import { useOffice } from "../store";
import { blankAgents } from "../office/agents";
import type { OfficeSnapshot, SwarmUIEvent } from "../types";

const snapshot = (over: Partial<OfficeSnapshot> = {}): OfficeSnapshot => ({
  missionId: "MS-1",
  query: "Why has CAC increased?",
  status: "running",
  route: "swarm",
  stage: "intake",
  missionLead: null,
  leadAgentId: null,
  initialLead: "performance",
  leadershipEpoch: 0,
  startedAt: "2026-09-09T10:00:00Z",
  lastEventAt: null,
  lastSeq: 0,
  agents: blankAgents(),
  board: { steps: [{ id: "verify", label: "Verify issue", state: "active" }] },
  handoffs: [],
  artifacts: {},
  unresolvedQuestions: [],
  limitations: [],
  finalResponse: null,
  timeline: [],
  ...over,
});

const ev = (seq: number, over: Partial<SwarmUIEvent> = {}): SwarmUIEvent => ({
  eventId: `MS-1:${seq}`,
  seq,
  timestamp: "2026-09-09T10:00:0" + (seq % 10) + "Z",
  missionId: "MS-1",
  eventType: "task_started",
  ...over,
});

beforeEach(() => useOffice.getState().reset());

describe("store hydrate + ingest", () => {
  it("hydrates the full roster and mission fields", () => {
    useOffice.getState().hydrate(snapshot());
    const s = useOffice.getState();
    expect(Object.keys(s.agents)).toHaveLength(blankAgents().length);
    expect(s.query).toMatch(/CAC/);
    expect(s.board[0].id).toBe("verify");
  });

  it("dedupes repeated events by seq (idempotent)", () => {
    useOffice.getState().hydrate(snapshot());
    const e = ev(1, { eventType: "leadership_transferred", agentId: "funnel_agent", metadata: { from_agent: "performance_agent", to_agent: "funnel_agent" } });
    useOffice.getState().ingestEvent(e);
    useOffice.getState().ingestEvent(e);
    useOffice.getState().ingestEvent(e);
    const s = useOffice.getState();
    expect(s.timeline).toHaveLength(1);
    expect(Object.values(s.agents).filter((a) => a.missionLead)).toHaveLength(1);
  });

  it("keeps the timeline ordered when events arrive out of order", () => {
    useOffice.getState().hydrate(snapshot());
    useOffice.getState().ingestEvent(ev(3, { summary: "third" }));
    useOffice.getState().ingestEvent(ev(1, { summary: "first" }));
    useOffice.getState().ingestEvent(ev(2, { summary: "second" }));
    expect(useOffice.getState().timeline.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(useOffice.getState().lastSeq).toBe(3);
  });

  it("a re-emitted snapshot merges the timeline instead of wiping it", () => {
    useOffice.getState().hydrate(snapshot());
    useOffice.getState().ingestEvent(ev(1));
    useOffice.getState().ingestEvent(ev(2));
    // mission-level patch (no agents / timeline) — as the demo provider sends
    useOffice.getState().hydrate(snapshot({ agents: [], timeline: [], stage: "investigating", board: { steps: [{ id: "frontier", label: "Find causal frontier", state: "active" }] } }));
    const s = useOffice.getState();
    expect(s.timeline).toHaveLength(2);
    expect(s.stage).toBe("investigating");
    expect(s.board[0].id).toBe("frontier");
  });

  it("mission_completed flips status to completed", () => {
    useOffice.getState().hydrate(snapshot());
    useOffice.getState().ingestEvent(ev(9, { eventType: "mission_completed", agentId: "coordinator" }));
    expect(useOffice.getState().status).toBe("completed");
  });

  it("leadership ring in the store follows the freshest transfer", () => {
    useOffice.getState().hydrate(snapshot());
    useOffice.getState().ingestEvent(ev(1, { eventType: "leadership_transferred", agentId: "funnel_agent", metadata: { from_agent: "performance_agent" } }));
    expect(useOffice.getState().leadAgentId).toBe("funnel_agent");
    useOffice.getState().ingestEvent(ev(2, { eventType: "leadership_transferred", agentId: "technical_agent", metadata: { from_agent: "funnel_agent" } }));
    expect(useOffice.getState().leadAgentId).toBe("technical_agent");
    expect(useOffice.getState().missionLead).toBe("technical");
  });
});
