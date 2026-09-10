/**
 * Scripted CAC investigation fixture (brief §68 + §83). Exercises: assignment,
 * movement, evidence retrieval, collaboration, two leadership handoffs,
 * hypothesis testing, prediction, strategy, a Skeptic REVISE with a targeted
 * remediation round, then Skeptic PASS and completion.
 *
 * Each beat is one real-shaped UI event plus an optional snapshot patch so the
 * mission board / artifact counts / stage stay in lock-step, exactly as the
 * backend gateway would re-derive them.
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
    ["verify", "Verify issue"],
    ["frontier", "Find causal frontier"],
    ["diagnose", "Diagnose cause"],
    ["forecast", "Forecast impact"],
    ["strategy", "Design strategy"],
    ["skeptic", "Skeptic review"],
  ].map(([id, label]) => ({
    id,
    label,
    state: done.includes(id) ? "done" : id === active ? "active" : "pending",
  }));

const A = (n: number): Record<string, number> => ({
  evidence: n >= 1 ? 4 : 0,
  anomaly: n >= 1 ? 3 : 0,
  hypothesis: n >= 3 ? 1 : 0,
  causal: n >= 4 ? 1 : 0,
  prediction: n >= 5 ? 1 : 0,
  strategy: n >= 6 ? 2 : 0,
  skeptic: n >= 7 ? 2 : 0,
});

export function demoInitialSnapshot(): OfficeSnapshot {
  return {
    missionId: DEMO_MISSION_ID,
    query: DEMO_QUERY,
    status: "running",
    route: "swarm",
    stage: "intake",
    missionLead: null,
    leadAgentId: null,
    initialLead: "performance",
    leadershipEpoch: 0,
    startedAt: new Date().toISOString(),
    lastEventAt: null,
    lastSeq: 0,
    agents: blankAgents(),
    board: { steps: board([], "verify") },
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
    event: { eventType: "mission_started", agentId: "coordinator", status: "planning", summary: "Mission received — planning investigation" },
    patch: { stage: "intake", status: "running" },
  },
  {
    delayMs: 1600,
    event: {
      eventType: "decomposition_created",
      agentId: "coordinator",
      status: "planning",
      summary: "Decomposed: media? funnel? pricing? technical?",
      metadata: { decomposition_id: "DEC-1" },
    },
    patch: { stage: "decomposing", unresolvedQuestions: ["Media drivers?", "Funnel conversion?", "Pricing?", "Technical cause?"] },
  },
  {
    delayMs: 1500,
    event: { eventType: "task_created", agentId: "coordinator", status: "working", summary: "Task plan created (6 tasks)", metadata: { tasks: 6 } },
    patch: { stage: "planning", board: { steps: board([], "verify") } },
  },
  {
    delayMs: 1600,
    event: {
      eventType: "task_started",
      agentId: "performance_agent",
      status: "working",
      summary: "Investigation wave — lead: Performance",
      metadata: { mission_lead: "performance", ready_done: ["observer_agent", "anomaly_agent"] },
    },
    patch: { stage: "investigating", missionLead: "performance", leadAgentId: "performance_agent" },
  },
  {
    delayMs: 1700,
    event: { eventType: "artifact_created", agentId: "anomaly_agent", status: "working", summary: "3 anomalies: Purchase CVR -24%, Mobile CVR -31%, JS errors +771%", artifactRefs: ["AN-1", "AN-2", "AN-3"] },
    patch: { artifacts: A(1), board: { steps: board(["verify"], "frontier") } },
  },
  {
    delayMs: 1700,
    event: {
      eventType: "decomposition_refined",
      agentId: "coordinator",
      status: "thinking",
      summary: "Media metrics stable — Purchase CVR is the frontier",
      metadata: { reason: "Media stable; Purchase CVR down", decomposition_id: "DEC-2" },
    },
  },
  {
    delayMs: 10000,
    event: {
      eventType: "leadership_transferred",
      agentId: "funnel_agent",
      status: "working",
      summary: "Leadership Performance → Funnel: media stable, funnel conversion suspect",
      metadata: { from_agent: "performance_agent", to_agent: "funnel_agent", reason: "media stable; funnel conversion suspect", unresolved_question: "Why did mobile CVR fall?", epoch: 1 },
    },
    patch: {
      missionLead: "funnel",
      leadAgentId: "funnel_agent",
      handoffs: [{ from: "performance_agent", to: "funnel_agent", reason: "media stable; funnel conversion suspect", epoch: 1 }],
      board: { steps: board(["verify", "frontier"], "diagnose") },
    },
  },
  {
    delayMs: 2800,
    event: {
      eventType: "task_started",
      agentId: "funnel_agent",
      status: "working",
      summary: "Funnel wave: sessions, PDP, ATC, checkout, mobile vs desktop",
      metadata: { mission_lead: "funnel" },
    },
  },
  {
    delayMs: 10000,
    event: {
      eventType: "leadership_transferred",
      agentId: "technical_agent",
      status: "working",
      summary: "Leadership Funnel → Technical: mobile-only JS errors spike after deploy",
      metadata: { from_agent: "funnel_agent", to_agent: "technical_agent", reason: "mobile-only JS errors spike", unresolved_question: "What broke on mobile?", epoch: 2 },
    },
    patch: {
      missionLead: "technical",
      leadAgentId: "technical_agent",
      handoffs: [
        { from: "performance_agent", to: "funnel_agent", reason: "media stable; funnel conversion suspect", epoch: 1 },
        { from: "funnel_agent", to: "technical_agent", reason: "mobile-only JS errors spike", epoch: 2 },
      ],
    },
  },
  {
    delayMs: 1600,
    event: {
      eventType: "agent_started",
      agentId: "diagnostic_agent",
      status: "working",
      summary: "Specialists activated: diagnostic, predictive, prescriptive",
      metadata: { intents: ["diagnostic", "predictive", "prescriptive"] },
    },
    patch: { stage: "specialists" },
  },
  {
    delayMs: 1800,
    event: {
      eventType: "artifact_created",
      agentId: "diagnostic_agent",
      status: "working",
      summary: "HYP-21 frontend regression — strongest. Testing 4/6.",
      artifactRefs: ["HYP-21"],
      metadata: { hypothesis: "HYP-21", progress: { current: 4, total: 6, label: "tests" } },
    },
    patch: { artifacts: A(3) },
  },
  {
    delayMs: 1700,
    event: { eventType: "artifact_created", agentId: "diagnostic_agent", status: "working", summary: "Causal validation: temporal precedence ✓, mobile specificity ✓, desktop control ✓", artifactRefs: ["CAU-1"] },
    patch: { artifacts: A(4), board: { steps: board(["verify", "frontier"], "diagnose") } },
  },
  {
    delayMs: 1700,
    event: { eventType: "artifact_created", agentId: "prediction_agent", status: "working", summary: "Forecast: CAC +18% over 7d if unremediated (80% PI ±6%)", artifactRefs: ["PR-1"] },
    patch: { artifacts: A(5), board: { steps: board(["verify", "frontier", "forecast"], "diagnose") } },
  },
  {
    delayMs: 1700,
    event: { eventType: "artifact_created", agentId: "strategy_agent", status: "working", summary: "Options: A rollback deploy · B hotfix JS regression · C throttle mobile spend", artifactRefs: ["ST-1", "ST-2"] },
    patch: { artifacts: A(6), board: { steps: board(["verify", "frontier", "forecast", "strategy"], "diagnose") } },
  },
  {
    delayMs: 9000,
    event: { eventType: "skeptic_review_started", agentId: "skeptic_agent", status: "reviewing", summary: "Skeptic review started", metadata: { claim_id: "CLM-1" } },
    patch: { stage: "review", board: { steps: board(["verify", "frontier", "forecast", "strategy"], "skeptic") } },
  },
  {
    delayMs: 10000,
    event: {
      eventType: "skeptic_revise",
      agentId: "skeptic_agent",
      status: "reviewing",
      summary: "Skeptic: REVISE — missing traffic-mix control",
      metadata: { verdict: "REVISE", claim_id: "CLM-1", reason: "missing traffic-mix control" },
    },
  },
  {
    delayMs: 1500,
    event: { eventType: "remediation_created", agentId: "coordinator", status: "working", summary: "Remediation planned: check traffic mix", metadata: { kinds: ["evidence_gap"] } },
  },
  {
    delayMs: 1600,
    event: {
      eventType: "task_assigned",
      agentId: "performance_agent",
      status: "working",
      summary: "Remediation task → Performance: verify traffic-mix stability",
      metadata: { agent: "performance_agent", remediation_round: 1 },
    },
  },
  {
    delayMs: 1900,
    event: { eventType: "task_completed", agentId: "performance_agent", status: "completed", summary: "Traffic mix flat — control satisfied", metadata: { remediation_round: 1 } },
  },
  {
    delayMs: 8000,
    event: { eventType: "skeptic_review_started", agentId: "skeptic_agent", status: "reviewing", summary: "Skeptic re-review", metadata: { claim_id: "CLM-1" } },
  },
  {
    delayMs: 9000,
    event: { eventType: "skeptic_pass", agentId: "skeptic_agent", status: "completed", summary: "Skeptic: PASS", metadata: { verdict: "PASS", claim_id: "CLM-1" } },
    patch: { artifacts: A(7), board: { steps: board(["verify", "frontier", "forecast", "strategy", "diagnose", "skeptic"], "skeptic") } },
  },
  {
    delayMs: 1600,
    event: { eventType: "mission_completed", agentId: "coordinator", status: "completed", summary: "Mission complete" },
    patch: {
      status: "completed",
      stage: "complete",
      board: { steps: board(["verify", "frontier", "diagnose", "forecast", "strategy", "skeptic"], "skeptic") },
      finalResponse:
        "CAC rose because a frontend regression in the mobile checkout bundle (deployed 3 days ago) cut mobile Purchase CVR ~31%. Media efficiency was stable throughout. If unremediated, CAC trends +18% over 7 days. Recommended: roll back the deploy now, ship a targeted hotfix, and keep mobile spend flat until CVR recovers.",
      unresolvedQuestions: [],
    },
  },
];
