/**
 * The office state machine: backend agent state -> where the character stands
 * and how it is animated. Movement is representation only and never gates real
 * work (docs/office-ui/04_AGENT_STATES.md).
 */

import type { AgentOfficeState, OfficeAgent, SwarmUIEvent } from "../types";
import type { StatusToken } from "../render/palette";
import { homeOf, SPOTS, type Vec } from "./layout";

export type AnimationName =
  | "idle"
  | "walk"
  | "sit_type"
  | "think"
  | "retrieve"
  | "review"
  | "wait"
  | "blocked"
  | "handoff"
  | "celebrate";

const ANIMATION: Record<AgentOfficeState, AnimationName> = {
  offline: "idle",
  idle: "idle",
  assigned: "walk",
  planning: "think",
  thinking: "think",
  working: "sit_type",
  retrieving_evidence: "retrieve",
  tool_running: "sit_type",
  waiting_for_agent: "wait",
  waiting_for_evidence: "wait",
  collaborating: "sit_type",
  reviewing: "review",
  blocked: "blocked",
  needs_attention: "blocked",
  handoff: "handoff",
  completed: "celebrate",
  failed: "blocked",
};

export function animationFor(status: AgentOfficeState): AnimationName {
  return ANIMATION[status] ?? "idle";
}

/** Resolve the world position a character should move toward for its state. */
export function destinationFor(agent: OfficeAgent): Vec {
  const s = agent.status;
  if (agent.role === "coordinator" && (s === "planning" || s === "thinking")) return SPOTS.planning_board;
  if (agent.role === "coordinator" && s === "working") return SPOTS.mission_room;
  if (s === "handoff") return SPOTS.handoff_area;
  if (s === "collaborating") return SPOTS.handoff_area;
  if (s === "reviewing") return agent.agentId === "skeptic_agent" ? SPOTS.skeptic_desk : SPOTS.handoff_area;
  if (s === "retrieving_evidence" || s === "waiting_for_evidence") return SPOTS.data_terminal;
  if (s === "waiting_for_agent") return SPOTS.lounge;
  if (s === "idle" || s === "offline") return homeOf(agent.agentId);
  return homeOf(agent.agentId);
}

const CALM: AgentOfficeState[] = ["idle", "offline"];
export function isActive(status: AgentOfficeState): boolean {
  return !CALM.includes(status);
}

export const STATUS_TOKEN: Record<AgentOfficeState, StatusToken> = {
  offline: "neutral",
  idle: "neutral",
  assigned: "working",
  planning: "thinking",
  thinking: "thinking",
  working: "working",
  retrieving_evidence: "working",
  tool_running: "working",
  waiting_for_agent: "waiting",
  waiting_for_evidence: "waiting",
  collaborating: "collab",
  reviewing: "thinking",
  blocked: "waiting",
  needs_attention: "waiting",
  handoff: "collab",
  completed: "done",
  failed: "failed",
};

const LEAD_TO_AGENT = (lead?: string | null): string | undefined => {
  if (!lead) return undefined;
  return lead.endsWith("_agent") ? lead : `${lead}_agent`;
};

/**
 * Incremental fold: apply one UI event to the agent view-models between
 * snapshots. Mirrors the backend's `_apply_event_to_agents` so the client
 * stays truthful even if a snapshot is briefly delayed. Returns a new map.
 */
export function applyEventToAgents(
  agents: Record<string, OfficeAgent>,
  ev: SwarmUIEvent,
): Record<string, OfficeAgent> {
  const next = { ...agents };
  const meta = ev.metadata ?? {};
  const set = (id: string | undefined | null, patch: Partial<OfficeAgent>) => {
    if (!id || !next[id]) return;
    next[id] = { ...next[id], ...patch, lastEventAt: ev.timestamp };
  };

  switch (ev.eventType) {
    case "mission_started":
    case "mission_created":
      set("coordinator", { status: "planning", currentAction: "Planning the investigation" });
      break;
    case "decomposition_created":
      set("coordinator", { status: "planning", currentAction: "Decomposing the question" });
      break;
    case "decomposition_refined":
      set("coordinator", { status: "thinking", currentAction: String(meta.reason ?? "Refining decomposition") });
      break;
    case "task_created":
      set("coordinator", { status: "working", currentAction: "Building the task plan" });
      break;
    case "task_started": {
      const lead = LEAD_TO_AGENT(meta.mission_lead as string | undefined);
      set("observer_agent", { status: "retrieving_evidence", currentAction: "Gathering metrics" });
      set("anomaly_agent", { status: "working", currentAction: "Scanning for anomalies" });
      set(lead, { status: "working", currentAction: "Leading the investigation wave" });
      break;
    }
    case "agent_started": {
      const intents = (meta.intents as string[]) ?? [];
      if (intents.includes("diagnostic")) set("diagnostic_agent", { status: "working", currentAction: "Specialist analysis" });
      if (intents.includes("predictive")) set("prediction_agent", { status: "working", currentAction: "Forecasting impact" });
      if (intents.includes("prescriptive")) set("strategy_agent", { status: "working", currentAction: "Designing interventions" });
      break;
    }
    case "leadership_transferred": {
      const from = LEAD_TO_AGENT(meta.from_agent as string | undefined);
      const to = ev.agentId ?? LEAD_TO_AGENT((meta.to_agent as string) ?? (meta.requested_target as string));
      const q = (meta.unresolved_question as string) ?? undefined;
      Object.keys(next).forEach((id) => (next[id] = { ...next[id], missionLead: false }));
      set(from, { status: "handoff", currentAction: "Handing off leadership" });
      set(to, { status: "working", missionLead: true, currentAction: q ?? "Taking mission leadership", subquestion: q });
      break;
    }
    case "skeptic_review_started":
      set("skeptic_agent", { status: "reviewing", currentAction: "Reviewing the claim" });
      break;
    case "skeptic_pass":
      set("skeptic_agent", { status: "completed", currentAction: "Verdict: PASS" });
      break;
    case "skeptic_revise":
    case "skeptic_reject":
      set("skeptic_agent", {
        status: "reviewing",
        currentAction: ev.eventType === "skeptic_revise" ? "Verdict: REVISE" : "Verdict: REJECT",
      });
      set("coordinator", { status: "working", currentAction: "Planning remediation" });
      break;
    case "remediation_created":
      set("coordinator", { status: "working", currentAction: "Remediation planned" });
      break;
    case "evidence_requested":
      set(ev.agentId, {
        status: "waiting_for_evidence",
        currentAction: `Waiting on ${(meta.key as string) ?? (meta.metric as string) ?? "evidence"}`,
      });
      break;
    case "evidence_received":
      set(ev.agentId, { status: "working", currentAction: "Evidence in — resuming" });
      break;
    case "task_assigned": {
      const id = LEAD_TO_AGENT(meta.agent as string | undefined) ?? (meta.agent as string | undefined);
      set(id, { status: "working", currentAction: "Working the remediation task" });
      break;
    }
    case "error":
    case "specialist_error": {
      const id = LEAD_TO_AGENT(meta.agent as string | undefined) ?? (meta.agent as string | undefined);
      set(id, { status: "failed", error: String(meta.error ?? ev.summary ?? "error") });
      break;
    }
    case "mission_blocked":
      set("coordinator", { status: "blocked", error: String(meta.reason ?? "budget exhausted") });
      break;
    case "mission_completed":
      Object.keys(next).forEach((id) => {
        const st = next[id].status;
        if (["working", "thinking", "planning", "reviewing", "handoff", "retrieving_evidence"].includes(st)) {
          next[id] = { ...next[id], status: "completed", currentAction: undefined };
        }
      });
      break;
  }
  return next;
}
