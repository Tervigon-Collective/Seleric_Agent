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
  // V3: the single agent walks to the capability station for its live tool.
  // `currentTool` is set by tool_started/tool_completed events (and the
  // backend snapshot); without one the agent works from Mission Control.
  const tool = (agent.currentTool ?? "").toLowerCase();
  if (tool && (s === "tool_running" || s === "working")) return stationForTool(tool);
  if (agent.agentId === "seleric_agent") {
    if (s === "planning" || s === "thinking") return SPOTS.planning_board;
    if (s === "reviewing") return SPOTS.validation_desk;
    if (s === "retrieving_evidence" || s === "waiting_for_evidence") return SPOTS.data_terminal;
    return homeOf(agent.agentId);
  }
  if (agent.role === "coordinator" && (s === "planning" || s === "thinking")) return SPOTS.planning_board;
  if (agent.role === "coordinator" && s === "working") return SPOTS.mission_room;
  // After handoffs / collab, stay at desk — meetings handle the walk-and-talk
  if (s === "handoff" || s === "collaborating") return homeOf(agent.agentId);
  if (s === "reviewing") return agent.agentId === "skeptic_agent" ? SPOTS.skeptic_desk : homeOf(agent.agentId);
  // Evidence fetch is a rare single-agent walk; others work at their desk
  if (s === "retrieving_evidence" || s === "waiting_for_evidence") return SPOTS.data_terminal;
  if (s === "waiting_for_agent") return homeOf(agent.agentId);
  if (s === "idle" || s === "offline") return homeOf(agent.agentId);
  return homeOf(agent.agentId);
}

const CALM: AgentOfficeState[] = ["idle", "offline"];
export function isActive(status: AgentOfficeState): boolean {
  return !CALM.includes(status);
}

/**
 * V3 tool name -> capability station. Mirrors the registered agent tools
 * (`src/seleric_swarm/agent/agent.py::TOOLS`); unknown tools fall back to
 * the data terminal so the agent still visibly leaves its desk.
 */
export function stationForTool(tool: string): Vec {
  const t = tool.toLowerCase();
  if (t.includes("find_metric") || t.includes("resolve_brand") || t.includes("metric_definition")) {
    return SPOTS.semantic_station;
  }
  if (t.includes("query_metric") || t.includes("semantic_sql") || t.includes("drilldown")) {
    return SPOTS.metrics_station;
  }
  if (t === "analyze" || t.includes("visualization") || t.includes("run_python")) {
    return SPOTS.analytics_station;
  }
  if (t.includes("diagnose") || t.includes("explore_data")) {
    return SPOTS.diagnosis_station;
  }
  if (t.includes("estimate_effect") || t.includes("refut")) {
    return SPOTS.causal_station;
  }
  if (t.includes("forecast") || t.includes("predict")) {
    return SPOTS.forecast_station;
  }
  if (t.includes("propose_action") || t.includes("commit_action")) {
    return SPOTS.actions_station;
  }
  if (t.includes("knowledge")) {
    return SPOTS.knowledge_station;
  }
  if (t.includes("experiment") || t.includes("sample_size")) {
    return SPOTS.experiments_station;
  }
  return SPOTS.data_terminal;
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
  /** Tool name carried by V3 tool beats (backend puts `tool` in metadata). */
  const toolOf = (m: Record<string, unknown>): string | undefined => {
    const t = m.tool ?? m.tool_name;
    return typeof t === "string" && t ? t : undefined;
  };
  /** V3 beats target the one agent; legacy beats name their own character. */
  const pickTarget = (id: string | undefined | null): string | undefined => {
    if (id && next[id]) return id;
    return next["seleric_agent"] ? "seleric_agent" : undefined;
  };

  switch (ev.eventType) {
    case "mission_started":
    case "mission_created":
      set("coordinator", { status: "planning", currentAction: "Planning the investigation" });
      set("seleric_agent", { status: "planning", currentAction: "Planning the investigation" });
      break;
    case "tool_started": {
      const tool = toolOf(meta);
      const target = pickTarget(ev.agentId);
      set(target, {
        status: "tool_running",
        currentAction: ev.summary ?? (tool ? `Running ${tool}` : "Running a tool"),
        ...(tool ? { currentTool: tool } : {}),
      });
      break;
    }
    case "tool_completed": {
      const tool = toolOf(meta);
      const target = pickTarget(ev.agentId);
      set(target, {
        status: "working",
        currentAction: ev.summary ?? (tool ? `${tool} — done` : "Tool done"),
        ...(tool ? { currentTool: tool } : {}),
      });
      break;
    }
    case "agent_thinking":
      set(pickTarget(ev.agentId), { status: "thinking", currentAction: "Thinking…" });
      break;
    case "answering":
      set(pickTarget(ev.agentId), { status: "working", currentAction: "Writing the answer" });
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
      // Desk work — no mass walk to the terminal
      set("observer_agent", { status: "working", currentAction: "Gathering metrics" });
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
      // Settle every active state — including tool_running, which the V3
      // single-agent flow sits in for most of the run. The last action is
      // preserved so post-run inspection still shows what each agent did.
      // Terminal/quiescent states (completed, failed, blocked, idle,
      // offline, assigned, needs_attention) are left untouched.
      Object.keys(next).forEach((id) => {
        const st = next[id].status;
        if (["planning", "thinking", "working", "retrieving_evidence", "tool_running", "waiting_for_agent", "waiting_for_evidence", "collaborating", "reviewing", "handoff"].includes(st)) {
          next[id] = { ...next[id], status: "completed" };
        }
      });
      break;
  }
  return next;
}
