import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
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
    <Card size="sm" data-slot="hovercard" className="pointer-events-none fixed z-20 w-72 shadow-lg" style={{ left, top }}>
      <CardContent className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-bold">{spec?.glyph} {agent.name}</span>
          <Badge className="text-[var(--on-accent)]" style={{ background: TOKEN_VAR[token] }}>{agent.status.replace(/_/g, " ")}</Badge>
        </div>
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="m-0">{v}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
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
