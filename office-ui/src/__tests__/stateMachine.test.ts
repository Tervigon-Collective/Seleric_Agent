import { describe, expect, it } from "vitest";
import { blankAgents, blankLegacyAgents } from "../office/agents";
import {
  animationFor,
  applyEventToAgents,
  destinationFor,
  stationForTool,
  STATUS_TOKEN,
} from "../office/stateMachine";
import { SPOTS, homeOf } from "../office/layout";
import type { OfficeAgent, SwarmUIEvent } from "../types";

const asMap = (list: OfficeAgent[]) => Object.fromEntries(list.map((a) => [a.agentId, a]));
const legacy = () => asMap(blankLegacyAgents());
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
  it("V3 tool work walks to the capability station", () => {
    const m = asMap(blankAgents());
    expect(destinationFor({ ...m.seleric_agent, status: "tool_running", currentTool: "query_metrics" }))
      .toEqual(SPOTS.metrics_station);
    expect(destinationFor({ ...m.seleric_agent, status: "working", currentTool: "estimate_effect" }))
      .toEqual(SPOTS.causal_station);
    expect(destinationFor({ ...m.seleric_agent, status: "working", currentTool: "forecast" }))
      .toEqual(SPOTS.forecast_station);
  });
  it("stationForTool covers every registered toolset", () => {
    expect(stationForTool("find_metrics")).toEqual(SPOTS.semantic_station);
    expect(stationForTool("semantic_sql")).toEqual(SPOTS.metrics_station);
    expect(stationForTool("analyze")).toEqual(SPOTS.analytics_station);
    expect(stationForTool("diagnose_metric_change")).toEqual(SPOTS.diagnosis_station);
    expect(stationForTool("propose_action")).toEqual(SPOTS.actions_station);
    expect(stationForTool("search_knowledge")).toEqual(SPOTS.knowledge_station);
    expect(stationForTool("evaluate_experiment")).toEqual(SPOTS.experiments_station);
    expect(stationForTool("something_new")).toEqual(SPOTS.data_terminal);
  });
  it("handoff keeps the agent at their desk (meetings handle the walk)", () => {
    const a: OfficeAgent = { ...legacy().funnel_agent, status: "handoff" };
    expect(destinationFor(a)).toEqual(homeOf("funnel_agent"));
  });
  it("evidence retrieval walks to the data terminal", () => {
    const a: OfficeAgent = { ...legacy().observer_agent, status: "retrieving_evidence" };
    expect(destinationFor(a)).toEqual(SPOTS.data_terminal);
  });
  it("idle returns home", () => {
    const a = legacy().diagnostic_agent;
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
  it("V3 tool beats move the one agent with its tool attached", () => {
    let m = asMap(blankAgents());
    m = applyEventToAgents(m, ev({
      eventType: "tool_started",
      agentId: "seleric_agent",
      metadata: { tool: "query_metrics" },
    }));
    expect(m.seleric_agent.status).toBe("tool_running");
    expect(m.seleric_agent.currentTool).toBe("query_metrics");
    m = applyEventToAgents(m, ev({
      eventType: "tool_completed",
      agentId: "seleric_agent",
      summary: "query_metrics — done",
      metadata: { tool: "query_metrics", success: true },
    }));
    expect(m.seleric_agent.status).toBe("working");
    expect(m.seleric_agent.currentAction).toMatch(/done/);
  });

  it("task_started activates the wave lead + observer + anomaly", () => {
    const next = applyEventToAgents(legacy(), ev({
      eventType: "task_started",
      metadata: { mission_lead: "performance" },
    }));
    expect(next.performance_agent.status).toBe("working");
    expect(next.observer_agent.status).toBe("working");
    expect(next.anomaly_agent.status).toBe("working");
  });

  it("leadership_transferred moves the ring to exactly one agent", () => {
    let m = legacy();
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
    const m = applyEventToAgents(legacy(), ev({ eventType: "skeptic_revise", agentId: "skeptic_agent" }));
    expect(m.skeptic_agent.status).toBe("reviewing");
    expect(m.coordinator.currentAction).toMatch(/remediation/i);
  });

  it("mission_completed settles active agents", () => {
    let m = legacy();
    m = applyEventToAgents(m, ev({ eventType: "task_started", metadata: { mission_lead: "technical" } }));
    m = applyEventToAgents(m, ev({ eventType: "mission_completed" }));
    expect(m.technical_agent.status).toBe("completed");
    expect(m.observer_agent.status).toBe("completed");
  });

  it("mission_completed settles the V3 agent too", () => {
    let m = asMap(blankAgents());
    m = applyEventToAgents(m, ev({ eventType: "tool_started", metadata: { tool: "analyze" } }));
    m = applyEventToAgents(m, ev({ eventType: "mission_completed" }));
    expect(m.seleric_agent.status).toBe("completed");
  });

  it("is a pure function — input map is not mutated", () => {
    const original = legacy();
    const snapshot = JSON.stringify(original);
    applyEventToAgents(original, ev({ eventType: "task_started", metadata: { mission_lead: "performance" } }));
    expect(JSON.stringify(original)).toBe(snapshot);
  });
});
