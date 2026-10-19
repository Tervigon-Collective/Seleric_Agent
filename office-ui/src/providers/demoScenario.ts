/**
 * Scripted CAC investigation fixture for the V3 single-agent office.
 *
 * Same business question as the retired swarm walkthrough, but the beats
 * are the live V3 vocabulary: one `seleric_agent` plans, then walks its
 * capability stations (semantic → metrics → analytics → diagnosis →
 * causal → forecast → validation) as tool beats land, then answers.
 *
 * Each beat is one real-shaped UI event plus an optional snapshot patch so
 * the mission board / artifact counts / stage stay in lock-step, exactly as
 * the backend gateway would re-derive them.
 */

import type { BoardStep, OfficeSnapshot, SwarmUIEvent } from "../types";
import { blankAgents } from "../office/agents";

export const DEMO_MISSION_ID = "MS-demo-cac";
export const DEMO_QUERY =
  "Why has CAC increased over the last three days, what happens if it continues, and what should we do?";

export interface Beat {
  delayMs: number;
  event: Omit<SwarmUIEvent, "eventId" | "seq" | "timestamp" | "missionId">;
  patch?: Partial<OfficeSnapshot>;
}

const board = (done: string[], active: string): BoardStep[] =>
  [
    ["understand", "Understand question"],
    ["fetch", "Fetch metrics"],
    ["analyze", "Analyze"],
    ["diagnose", "Diagnose cause"],
    ["forecast", "Forecast impact"],
    ["validate", "Validate & answer"],
  ].map(([id, label]) => ({
    id,
    label,
    state: done.includes(id) ? "done" : id === active ? "active" : "pending",
  }));

const A = (n: number): Record<string, number> => ({
  evidence: n >= 1 ? 4 : 0,
  hypothesis: n >= 2 ? 1 : 0,
  causal: n >= 3 ? 1 : 0,
  prediction: n >= 4 ? 1 : 0,
});

export function demoInitialSnapshot(): OfficeSnapshot {
  return {
    missionId: DEMO_MISSION_ID,
    query: DEMO_QUERY,
    status: "running",
    route: "v3",
    stage: "intake",
    missionLead: null,
    leadAgentId: null,
    initialLead: "seleric_agent",
    leadershipEpoch: 0,
    startedAt: new Date().toISOString(),
    lastEventAt: null,
    lastSeq: 0,
    agents: blankAgents(),
    board: { steps: board([], "understand") },
    handoffs: [],
    artifacts: A(0),
    unresolvedQuestions: ["Why has CAC increased?"],
    limitations: [],
    finalResponse: null,
    timeline: [],
  };
}

export const DEMO_SCRIPT: Beat[] = [
  {
    delayMs: 600,
    event: { eventType: "mission_started", agentId: "seleric_agent", status: "planning", summary: "Mission received — planning investigation" },
    patch: { stage: "planning", status: "running", missionLead: "seleric_agent", leadAgentId: "seleric_agent" },
  },
  {
    delayMs: 1600,
    event: {
      eventType: "tool_started",
      agentId: "seleric_agent",
      status: "tool_running",
      summary: "Running find_metrics",
      metadata: { tool: "find_metrics" },
    },
    patch: { stage: "fetching", board: { steps: board(["understand"], "fetch") } },
  },
  {
    delayMs: 1700,
    event: {
      eventType: "tool_completed",
      agentId: "seleric_agent",
      status: "working",
      summary: "query_metrics — done: spend, CVR and checkout volume by device",
      metadata: { tool: "query_metrics", success: true },
    },
    patch: { artifacts: A(1), board: { steps: board(["understand", "fetch"], "analyze") } },
  },
  {
    delayMs: 1700,
    event: {
      eventType: "tool_completed",
      agentId: "seleric_agent",
      status: "working",
      summary: "analyze — done: mobile Purchase CVR -31%, JS errors +771%",
      metadata: { tool: "analyze", success: true },
    },
    patch: { stage: "analyzing", artifacts: A(2), board: { steps: board(["understand", "fetch", "analyze"], "diagnose") } },
  },
  {
    delayMs: 1700,
    event: {
      eventType: "tool_started",
      agentId: "seleric_agent",
      status: "tool_running",
      summary: "Running diagnose_metric_change",
      metadata: { tool: "diagnose_metric_change" },
    },
    patch: { stage: "diagnosing" },
  },
  {
    delayMs: 1800,
    event: {
      eventType: "tool_completed",
      agentId: "seleric_agent",
      status: "working",
      summary: "diagnose_metric_change — done: frontend regression is the driver",
      metadata: { tool: "diagnose_metric_change", success: true },
    },
    patch: { artifacts: A(2) },
  },
  {
    delayMs: 1700,
    event: {
      eventType: "tool_started",
      agentId: "seleric_agent",
      status: "tool_running",
      summary: "Running estimate_effect",
      metadata: { tool: "estimate_effect" },
    },
  },
  {
    delayMs: 1900,
    event: {
      eventType: "tool_completed",
      agentId: "seleric_agent",
      status: "working",
      summary: "estimate_effect — done: temporal precedence holds, desktop control clean",
      metadata: { tool: "estimate_effect", success: true },
    },
    patch: { artifacts: A(3), board: { steps: board(["understand", "fetch", "analyze", "diagnose"], "forecast") } },
  },
  {
    delayMs: 1700,
    event: {
      eventType: "tool_started",
      agentId: "seleric_agent",
      status: "tool_running",
      summary: "Running forecast",
      metadata: { tool: "forecast" },
    },
  },
  {
    delayMs: 1900,
    event: {
      eventType: "tool_completed",
      agentId: "seleric_agent",
      status: "working",
      summary: "forecast — done: CAC +18% over 7d if unremediated (80% PI ±6%)",
      metadata: { tool: "forecast", success: true },
    },
    patch: { artifacts: A(4), board: { steps: board(["understand", "fetch", "analyze", "diagnose", "forecast"], "validate") } },
  },
  {
    delayMs: 1500,
    event: { eventType: "agent_thinking", agentId: "seleric_agent", status: "thinking", summary: "Thinking…" },
  },
  {
    delayMs: 1500,
    event: { eventType: "answering", agentId: "seleric_agent", status: "working", summary: "Writing the answer" },
  },
  {
    delayMs: 1600,
    event: { eventType: "mission_completed", agentId: "seleric_agent", status: "completed", summary: "Mission complete" },
    patch: {
      status: "completed",
      stage: "complete",
      board: { steps: board(["understand", "fetch", "analyze", "diagnose", "forecast", "validate"], "validate") },
      finalResponse:
        "CAC rose because a frontend regression in the mobile checkout bundle (deployed 3 days ago) cut mobile Purchase CVR ~31%. Media efficiency was stable throughout. If unremediated, CAC trends +18% over 7 days. Recommended: roll back the deploy now, ship a targeted hotfix, and keep mobile spend flat until CVR recovers.",
      unresolvedQuestions: [],
    },
  },
];
