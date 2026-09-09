/** Persistent office characters — mirrors OFFICE_AGENTS in the backend gateway. */

import type { AgentRole, OfficeAgent } from "../types";

export interface AgentSpec {
  agentId: string;
  name: string;
  role: AgentRole;
  domain?: string;
  /** short role symbol shown on the nameplate */
  glyph: string;
}

export const OFFICE_AGENTS: AgentSpec[] = [
  { agentId: "coordinator", name: "Coordinator", role: "coordinator", glyph: "◆" },
  { agentId: "observer_agent", name: "Observer", role: "specialist", glyph: "◎" },
  { agentId: "anomaly_agent", name: "Anomaly", role: "specialist", glyph: "⚠" },
  { agentId: "diagnostic_agent", name: "Diagnostic", role: "specialist", glyph: "🔬" },
  { agentId: "prediction_agent", name: "Prediction", role: "specialist", glyph: "∿" },
  { agentId: "strategy_agent", name: "Strategy", role: "specialist", glyph: "⚑" },
  { agentId: "skeptic_agent", name: "Skeptic", role: "specialist", glyph: "🕵" },
  { agentId: "performance_agent", name: "Performance", role: "domain", domain: "performance", glyph: "📈" },
  { agentId: "commerce_agent", name: "Commerce", role: "domain", domain: "commerce", glyph: "🛒" },
  { agentId: "funnel_agent", name: "Funnel", role: "domain", domain: "funnel", glyph: "⧗" },
  { agentId: "finance_agent", name: "Finance", role: "domain", domain: "finance", glyph: "$" },
  { agentId: "inventory_agent", name: "Inventory", role: "domain", domain: "inventory", glyph: "▦" },
  { agentId: "procurement_agent", name: "Procurement", role: "domain", domain: "procurement", glyph: "⇄" },
  { agentId: "technical_agent", name: "Technical", role: "domain", domain: "technical", glyph: "⌘" },
];

export const SPEC_BY_ID: Record<string, AgentSpec> = Object.fromEntries(
  OFFICE_AGENTS.map((a) => [a.agentId, a]),
);

export function blankAgents(): OfficeAgent[] {
  return OFFICE_AGENTS.map((a) => ({
    agentId: a.agentId,
    name: a.name,
    role: a.role,
    domain: a.domain,
    status: "idle",
    missionLead: false,
    waitingOn: [],
  }));
}
