import { useOffice } from "../store";
import { SPEC_BY_ID } from "../office/agents";
import { STATUS_TOKEN } from "../office/stateMachine";
import type { OfficeAgent } from "../types";

const TOKEN_VAR: Record<string, string> = {
  neutral: "var(--st-neutral)",
  working: "var(--st-working)",
  thinking: "var(--st-thinking)",
  waiting: "var(--st-waiting)",
  failed: "var(--st-failed)",
  done: "var(--st-done)",
  collab: "var(--st-collab)",
};

export function AgentHoverCard({ x, y }: { x: number; y: number }) {
  const hoveredId = useOffice((s) => s.hoveredAgentId);
  const selectedId = useOffice((s) => s.selectedAgentId);
  const agent = useOffice((s) => (hoveredId ? s.agents[hoveredId] : undefined));
  const query = useOffice((s) => s.query);
  const leadAgentId = useOffice((s) => s.leadAgentId);

  if (!agent || !hoveredId || hoveredId === selectedId) return null;
  const spec = SPEC_BY_ID[agent.agentId];
  const token = STATUS_TOKEN[agent.status];
  const rows = hoverRows(agent, query, leadAgentId);

  const left = Math.min(x + 18, window.innerWidth - 296);
  const top = Math.min(y + 12, window.innerHeight - 220);

  return (
    <div className="hovercard" style={{ left, top }}>
      <div className="hc-top">
        <span className="hc-name">
          {spec?.glyph} {agent.name}
        </span>
        <span className="status-tag" style={{ background: TOKEN_VAR[token] }}>
          {agent.status.replace(/_/g, " ")}
        </span>
      </div>
      {rows.map(([k, v]) => (
        <div className="hc-row" key={k}>
          <span>{k}</span>
          <div>{v}</div>
        </div>
      ))}
    </div>
  );
}

function hoverRows(
  a: OfficeAgent,
  mission: string,
  leadAgentId: string | null,
): [string, string][] {
  const out: [string, string][] = [];
  if (mission) out.push(["Mission", mission]);
  if (a.subquestion) out.push(["Question", a.subquestion]);
  else if (a.task) out.push(["Task", a.task]);
  if (a.currentAction) out.push(["Action", a.currentAction]);
  if (a.currentTool) out.push(["Tool", a.currentTool]);
  if (a.currentHypothesis) out.push(["Hypothesis", a.currentHypothesis]);
  if (a.progress) out.push(["Progress", `${a.progress.current} / ${a.progress.total} ${a.progress.label ?? ""}`.trim()]);
  if (a.missionLead) out.push(["Role", "MISSION LEAD"]);
  else if (leadAgentId) out.push(["Current lead", leadAgentId.replace(/_agent$/, "")]);
  if (a.waitingOn.length) out.push(["Waiting on", a.waitingOn.join(", ")]);
  if (a.error) out.push(["Blocked", a.error]);
  if (a.lastEventAt) out.push(["Last event", timeAgo(a.lastEventAt)]);
  return out;
}

function timeAgo(iso: string): string {
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  return `${Math.floor(s / 60)}m ${s % 60}s ago`;
}
