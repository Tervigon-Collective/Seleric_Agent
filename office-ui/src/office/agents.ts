/** Persistent office characters.
 *
 * V3 (current system): a single `seleric_agent` loop over capability
 * toolsets — that is the live roster. `LEGACY_OFFICE_AGENTS` is the retired
 * swarm_v2 roster (coordinator + domain/specialist characters), kept in
 * `SPEC_BY_ID` so old persisted `route=swarm` snapshots still resolve names,
 * glyphs and desks. New missions snapshot to the one agent only.
 */

import type { AgentRole, OfficeAgent } from "../types";

export interface AgentSpec {
  agentId: string;
  name: string;
  role: AgentRole;
  domain?: string;
  /** short role symbol shown on the nameplate */
  glyph: string;
}

/** The live V3 roster: one agent. */
export const V3_OFFICE_AGENTS: AgentSpec[] = [
  { agentId: "seleric_agent", name: "Seleric", role: "coordinator", glyph: "✦" },
];

/** Retired swarm_v2 roster — read-only compat for old snapshots. */
export const LEGACY_OFFICE_AGENTS: AgentSpec[] = [
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

/** Live roster (V3). Kept under the old name so call sites don't churn. */
export const OFFICE_AGENTS: AgentSpec[] = V3_OFFICE_AGENTS;

export const SPEC_BY_ID: Record<string, AgentSpec> = Object.fromEntries(
  [...V3_OFFICE_AGENTS, ...LEGACY_OFFICE_AGENTS].map((a) => [a.agentId, a]),
);

function toBlank(a: AgentSpec): OfficeAgent {
  return {
    agentId: a.agentId,
    name: a.name,
    role: a.role,
    domain: a.domain,
    status: "idle",
    missionLead: false,
    waitingOn: [],
  };
}

export function blankAgents(): OfficeAgent[] {
  return OFFICE_AGENTS.map(toBlank);
}

/** Retired 14-character roster for old snapshots / legacy tests. */
export function blankLegacyAgents(): OfficeAgent[] {
  return LEGACY_OFFICE_AGENTS.map(toBlank);
}
