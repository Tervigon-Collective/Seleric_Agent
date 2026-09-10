import { describe, expect, it } from "vitest";
import { blankAgents } from "../office/agents";
import {
  animationFor,
  applyEventToAgents,
  destinationFor,
  STATUS_TOKEN,
} from "../office/stateMachine";
import { SPOTS, homeOf } from "../office/layout";
import type { OfficeAgent, SwarmUIEvent } from "../types";

const asMap = (list: OfficeAgent[]) => Object.fromEntries(list.map((a) => [a.agentId, a]));
const ev = (over: Partial<SwarmUIEvent>): SwarmUIEvent => ({
  eventId: "e",
  seq: 1,
  timestamp: "2026-09-09T10:00:00Z",
  missionId: "MS-1",
  eventType: "mission_started",
  ...over,
});

describe("state -> destination", () => {
  it("planning coordinator walks to the planning board", () => {
    const c: OfficeAgent = { ...blankAgents()[0], status: "planning" };
    expect(destinationFor(c)).toEqual(SPOTS.planning_board);
  });
  it("handoff keeps the agent at their desk (meetings handle the walk)", () => {
    const a: OfficeAgent = { ...asMap(blankAgents()).funnel_agent, status: "handoff" };
    expect(destinationFor(a)).toEqual(homeOf("funnel_agent"));
  });
  it("evidence retrieval walks to the data terminal", () => {
    const a: OfficeAgent = { ...asMap(blankAgents()).observer_agent, status: "retrieving_evidence" };
    expect(destinationFor(a)).toEqual(SPOTS.data_terminal);
  });
  it("idle returns home", () => {
    const a = asMap(blankAgents()).diagnostic_agent;
    expect(destinationFor(a)).toEqual(homeOf("diagnostic_agent"));
  });
});

describe("animation + token mapping", () => {
  it("working -> typing, reviewing -> review, failed -> blocked", () => {
    expect(animationFor("working")).toBe("sit_type");
    expect(animationFor("reviewing")).toBe("review");
    expect(animationFor("failed")).toBe("blocked");
  });
  it("every state has a status token", () => {
    for (const st of Object.keys(STATUS_TOKEN)) expect(STATUS_TOKEN[st as keyof typeof STATUS_TOKEN]).toBeTruthy();
  });
});

describe("applyEventToAgents", () => {
  it("task_started activates the wave lead + observer + anomaly", () => {
    const next = applyEventToAgents(asMap(blankAgents()), ev({
      eventType: "task_started",
      metadata: { mission_lead: "performance" },
    }));
    expect(next.performance_agent.status).toBe("working");
    expect(next.observer_agent.status).toBe("working");
    expect(next.anomaly_agent.status).toBe("working");
  });

  it("leadership_transferred moves the ring to exactly one agent", () => {
    let m = asMap(blankAgents());
    m = applyEventToAgents(m, ev({ eventType: "task_started", metadata: { mission_lead: "performance" } }));
    m = applyEventToAgents(m, ev({
      eventType: "leadership_transferred",
      agentId: "funnel_agent",
      metadata: { from_agent: "performance_agent", to_agent: "funnel_agent", unresolved_question: "Why did mobile CVR fall?" },
    }));
    const leads = Object.values(m).filter((a) => a.missionLead).map((a) => a.agentId);
    expect(leads).toEqual(["funnel_agent"]);
    expect(m.performance_agent.status).toBe("handoff");
    expect(m.funnel_agent.subquestion).toBe("Why did mobile CVR fall?");
  });

  it("skeptic_revise puts skeptic in review and coordinator on remediation", () => {
    const m = applyEventToAgents(asMap(blankAgents()), ev({ eventType: "skeptic_revise", agentId: "skeptic_agent" }));
    expect(m.skeptic_agent.status).toBe("reviewing");
    expect(m.coordinator.currentAction).toMatch(/remediation/i);
  });

  it("mission_completed settles active agents", () => {
    let m = asMap(blankAgents());
    m = applyEventToAgents(m, ev({ eventType: "task_started", metadata: { mission_lead: "technical" } }));
    m = applyEventToAgents(m, ev({ eventType: "mission_completed" }));
    expect(m.technical_agent.status).toBe("completed");
    expect(m.observer_agent.status).toBe("completed");
  });

  it("is a pure function — input map is not mutated", () => {
    const original = asMap(blankAgents());
    const snapshot = JSON.stringify(original);
    applyEventToAgents(original, ev({ eventType: "task_started", metadata: { mission_lead: "performance" } }));
    expect(JSON.stringify(original)).toBe(snapshot);
  });
});
